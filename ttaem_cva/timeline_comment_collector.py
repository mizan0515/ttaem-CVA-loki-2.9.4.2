"""Baseline comment selection and private evidence projection; no browser profile access."""
from __future__ import annotations

import contextlib

import hashlib

import json

import logging

import math

import os

import re

from dataclasses import asdict, dataclass, field

from datetime import datetime, timezone

from pathlib import Path

from typing import Any, Optional

from .timeline_comment_anchors import (
    CHAPTER_TIMETABLE,
    HIGHLIGHT_HINT,
    TimelineAnchor,
    classify_comment_lane,
    cluster_timeline_anchors,
    coverage_bands_for_seconds,
    extract_anchors_from_comment_text,
    format_private_anchor_prompt_section,
    timecode_to_seconds,
)

CACHE_SCHEMA_VERSION = 4

CANDIDATE_LEARNING_SCHEMA_VERSION = "comment_timeline_candidate_learning.v1"

DEFAULT_MAX_CANDIDATES = 80

DEFAULT_MAX_SELECTED = 6

DEFAULT_MIN_TIMECODES = 2

DEFAULT_MIN_SCORE = 12.0

MAX_PRIVATE_SEMANTIC_SOURCE_BYTES = 128_000

LIKE_SCORE_WEIGHT = 18.0

LIKE_SCORE_CAP = 120.0

REPLY_SCORE_WEIGHT = 5.0

REPLY_SCORE_CAP = 30.0

_TIMECODE_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}(?::\d{2})?)(?!\d)")

_TIMELINE_HINT_RE = re.compile(
    r"(타임라인|타임\s*스탬프|timestamp|time\s*stamp|timeline|챕터|chapter|목차|하이라이트)",
    re.IGNORECASE,
)

_GENERATED_SUMMARY_COMMENT_RE = re.compile(
    r"(요약\s*웹페이지|(?:ttaem\.com|[a-z0-9-]+\.pages\.dev)/"
    r"(?:vods|bundles)/[^ \t\r\n]+/report)",
    re.IGNORECASE,
)

_COUNT_TOKEN_RE = re.compile(r"(?P<num>\d+(?:[,.]\d+)?)(?P<unit>[KkMmBb만억천]?)")

@dataclass
class TimelineComment:
    source: str
    text: str
    timecodes: list[str] = field(default_factory=list)
    like_count: Optional[int] = None
    reply_count: Optional[int] = None
    comment_id: str = ""
    score: float = 0.0
    rank_reason: list[str] = field(default_factory=list)
    lane: str = CHAPTER_TIMETABLE
    coverage_bands: list[str] = field(default_factory=list)
    anchor_count: int = 0
    source_order: int = 0
    thread_key: str = ""
    thread_order: int = 0

@dataclass
class TimelineCommentBundle:
    video_no: str
    source_url: str = ""
    platform: str = "chzzk"
    fetched_at: str = ""
    status: str = "ok"
    total_comments_seen: int = 0
    timeline_comment_count: int = 0
    selected_count: int = 0
    comments: list[TimelineComment] = field(default_factory=list)
    anchors: list[TimelineAnchor] = field(default_factory=list)
    candidate_learning: dict[str, Any] = field(default_factory=dict)
    schema_version: int = CACHE_SCHEMA_VERSION

def extract_timecodes(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _TIMECODE_RE.finditer(str(text or "")):
        tc = match.group(1)
        if tc not in seen:
            seen.add(tc)
            out.append(tc)
    return out

def _near_duplicate_timetable(
    left: TimelineComment,
    right: TimelineComment,
    *,
    tolerance_sec: int = 10,
) -> bool:
    """Detect independently formatted copies of the same long timetable."""

    if left.lane != CHAPTER_TIMETABLE or right.lane != CHAPTER_TIMETABLE:
        return False
    if min(len(left.timecodes), len(right.timecodes)) < 8:
        return False
    if abs(len(left.timecodes) - len(right.timecodes)) > max(
        2, int(max(len(left.timecodes), len(right.timecodes)) * 0.15)
    ):
        return False
    try:
        left_secs = [timecode_to_seconds(value) for value in left.timecodes]
        right_secs = [timecode_to_seconds(value) for value in right.timecodes]
    except ValueError:
        return False
    paired = min(len(left_secs), len(right_secs))
    near_ratio = sum(
        abs(left_secs[index] - right_secs[index]) <= tolerance_sec
        for index in range(paired)
    ) / paired
    if near_ratio < 0.8:
        return False

    def label_tokens(text: str) -> set[str]:
        without_times = _TIMECODE_RE.sub(" ", text)
        return {
            token.casefold()
            for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", without_times)
        }

    left_tokens = label_tokens(left.text)
    right_tokens = label_tokens(right.text)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
    return overlap >= 0.45

def _parse_count(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip()
    if not text:
        return None
    match = _COUNT_TOKEN_RE.search(text.replace(",", ""))
    if not match:
        return None
    try:
        number = float(match.group("num"))
    except ValueError:
        return None
    unit = match.group("unit") or ""
    multiplier = {
        "천": 1_000,
        "만": 10_000,
        "억": 100_000_000,
        "K": 1_000,
        "k": 1_000,
        "M": 1_000_000,
        "m": 1_000_000,
        "B": 1_000_000_000,
        "b": 1_000_000_000,
    }.get(unit, 1)
    return max(0, int(number * multiplier))

def _count_from_text(text: str, labels: tuple[str, ...]) -> Optional[int]:
    body = str(text or "")
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}[ \t]*[:：]?[ \t]*([0-9][0-9,.]*(?:[KkMmBb만억천])?)",
            body,
            flags=re.IGNORECASE,
        )
        if match:
            parsed = _parse_count(match.group(1))
            if parsed is not None:
                return parsed
    for label in labels:
        match = re.search(
            rf"(?<![\d:.,])([0-9][0-9,.]*(?:[KkMmBb만억천])?)[ \t]*{re.escape(label)}(?![0-9A-Za-z가-힣])",
            body,
            flags=re.IGNORECASE,
        )
        if match:
            parsed = _parse_count(match.group(1))
            if parsed is not None:
                return parsed
    return None

def _normalize_comment_text(text: Any) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t\f\v]+", " ", normalized)
    return normalized.strip()

def _private_prompt_comment_body(text: Any) -> str:
    """Keep the selected comment's semantic layout, without surrounding UI metadata."""

    lines: list[str] = []
    for raw_line in _normalize_comment_text(text).splitlines():
        line = raw_line.strip()
        if re.fullmatch(r"(?:작성자(?:\s+.+)?|메뉴|답글\s*쓰기|댓글\s*쓰기|더보기)", line):
            continue
        if re.fullmatch(
            r"(?:추천|좋아요|버프|답글|댓글)\s*[:：]?\s*[0-9][0-9,.]*(?:[KkMmBb만억천])?",
            line,
            flags=re.IGNORECASE,
        ):
            continue
        if not line:
            lines.append("")
            continue
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)

def build_private_comment_semantic_context(
    bundle: TimelineCommentBundle | None,
    *,
    max_bytes: int = MAX_PRIVATE_SEMANTIC_SOURCE_BYTES,
) -> dict[str, Any]:
    """Build complete private-only time-coded comments for relational reading.

    Paragraphs and line order are evidence. No delimiter, label or content
    category is assigned a fixed meaning here. The model infers the video's
    activity relationships from the whole body, keeps conflicts with other
    time-aligned sources uncertain, and does not require the same wording to be
    repeated in STT or replay chat. The high local-source ceiling is not a
    provider request budget: callers distribute long bodies over the existing
    chronological pre-D1/D2 observation calls.
    """

    rows: list[dict[str, Any]] = []
    total_bytes = 0
    if bundle is None or max_bytes <= 0:
        return {}
    ordered = sorted(
        bundle.comments,
        key=lambda comment: (
            int(comment.source_order or 0),
            str(comment.thread_key or ""),
            int(comment.thread_order or 0),
        ),
    )
    for comment in ordered:
        if comment.lane not in {CHAPTER_TIMETABLE, HIGHLIGHT_HINT}:
            continue
        body = _private_prompt_comment_body(comment.text)
        if not body or not _TIMECODE_RE.search(body):
            continue
        body_bytes = len(body.encode("utf-8"))
        if total_bytes + body_bytes > int(max_bytes):
            raise ValueError(
                "private timeline semantic source exceeds local source hard limit: "
                f"source_bytes={total_bytes + body_bytes}; "
                f"max_source_bytes={int(max_bytes)}; raw_free=true"
            )
        seconds = []
        for timecode in extract_timecodes(body):
            try:
                seconds.append(timecode_to_seconds(timecode))
            except ValueError:
                continue
        rows.append({
            "lane": comment.lane,
            "body": body,
            "body_utf8_bytes": body_bytes,
            "raw_text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "source_order": int(comment.source_order or 0),
            "thread_key": str(comment.thread_key or ""),
            "thread_order": int(comment.thread_order or 0),
            "first_time_sec": min(seconds) if seconds else None,
            "last_time_sec": max(seconds) if seconds else None,
            "timecode_count": len(seconds),
            "structure": {
                "line_count": len(body.splitlines()),
                "blank_line_count": sum(not line for line in body.splitlines()),
                "divider_count": sum(
                    bool(re.fullmatch(r"\s*-{3,}\s*", line))
                    for line in body.splitlines()
                ),
            },
        })
        total_bytes += body_bytes
    if not rows:
        return {}
    return {
        "schema_version": "timeline_comment_semantic_context.v1",
        "visibility": "private_model_input_only",
        "untrusted": True,
        "source_bytes": total_bytes,
        "source_complete": True,
        "usage_policy": (
            "Read the full line/paragraph/thread order to infer activity continuity, "
            "closure, transition, important events and causal context. A separator, "
            "blank line, label or content category has no fixed meaning by itself. "
            "Treat names and groupings as untrusted evidence, mark real conflicts with "
            "other time-aligned sources uncertain, do not reject a relation merely "
            "because other sources use different words, and never quote or expose the comment."
        ),
        "comments": rows,
    }

def private_comment_blocks_for_range(
    semantic_context: dict[str, Any] | None,
    *,
    start_sec: int,
    end_sec: int,
) -> list[dict[str, Any]]:
    """Return complete source lines relevant to one existing transcript chunk.

    Untimed headers, blank lines and dividers stay attached to the next timed
    row. This is a view over the existing private source, not a new authority or
    provider call. Chunk ranges are half-open so a boundary row has one owner.
    """

    blocks: list[dict[str, Any]] = []
    for comment in (semantic_context or {}).get("comments") or []:
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("body") or "")
        if not body:
            continue
        pending: list[str] = []
        selected_lines: list[str] = []
        last_selected = False
        for line in body.splitlines():
            match = _TIMECODE_RE.search(line)
            if not match:
                pending.append(line)
                continue
            try:
                line_sec = timecode_to_seconds(match.group(1))
            except ValueError:
                pending.append(line)
                continue
            in_range = int(start_sec) <= line_sec < int(end_sec)
            if in_range:
                selected_lines.extend(pending)
                selected_lines.append(line)
            pending = []
            last_selected = in_range
        if last_selected and pending:
            selected_lines.extend(pending)
        while selected_lines and not selected_lines[-1]:
            selected_lines.pop()
        if not selected_lines:
            continue
        block_body = "\n".join(selected_lines)
        blocks.append({
            "thread_key": str(comment.get("thread_key") or ""),
            "thread_order": int(comment.get("thread_order") or 0),
            "source_order": int(comment.get("source_order") or 0),
            "body": block_body,
            "body_utf8_bytes": len(block_body.encode("utf-8")),
            "untrusted": True,
        })
    return blocks

def private_comment_source_fully_covered(
    semantic_context: dict[str, Any] | None,
    *,
    chunk_ranges: list[tuple[int, int]],
) -> bool:
    """Prove every selected timed source line belongs to a provider chunk."""

    ranges = [
        (max(0, int(start_sec)), max(0, int(end_sec)))
        for start_sec, end_sec in chunk_ranges
        if int(end_sec) > int(start_sec)
    ]
    if not ranges:
        return False
    saw_timed_line = False
    for comment in (semantic_context or {}).get("comments") or []:
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("body") or "")
        for timecode in extract_timecodes(body):
            try:
                line_sec = timecode_to_seconds(timecode)
            except ValueError:
                return False
            saw_timed_line = True
            if not any(start_sec <= line_sec < end_sec for start_sec, end_sec in ranges):
                return False
    return saw_timed_line

def _generated_summary_comment_skip_reason(text: str) -> str:
    """Detect comments produced by this report system before they re-enter summaries."""

    body = str(text or "")
    if not body:
        return ""
    if _GENERATED_SUMMARY_COMMENT_RE.search(body):
        return "generated_summary_comment"
    return ""

def _count_bucket(value: Optional[int]) -> str:
    if value is None:
        return "unknown"
    value = max(0, int(value))
    if value == 0:
        return "zero"
    if value <= 4:
        return "low"
    if value <= 19:
        return "medium"
    if value <= 99:
        return "high"
    return "very_high"

def _length_bucket(text: str) -> str:
    length = len(str(text or ""))
    if length < 40:
        return "short"
    if length < 160:
        return "medium"
    if length < 600:
        return "long"
    return "very_long"

def _density_bucket(timecodes: list[str], text: str) -> str:
    tc_count = len(timecodes or [])
    line_count = max(1, len([line for line in str(text or "").splitlines() if line.strip()]))
    ratio = tc_count / line_count
    if tc_count <= 0:
        return "none"
    if ratio >= 0.75:
        return "dense"
    if ratio >= 0.35:
        return "structured"
    return "sparse"

def _score_bucket(score: float) -> str:
    value = float(score or 0.0)
    if value < DEFAULT_MIN_SCORE:
        return "below_min"
    if value < 40:
        return "low"
    if value < 90:
        return "medium"
    return "high"

def _score_component_names(reason: list[str]) -> list[str]:
    names: list[str] = []
    for item in reason:
        head = str(item or "").split("=", 1)[0].strip()
        if not head:
            continue
        if head == "likes_high_weight":
            head = "like_bucket_weight"
        elif head == "replies":
            head = "reply_bucket_weight"
        if head not in names:
            names.append(head)
    return names

def _candidate_learning_row(
    *,
    candidate_id: str,
    platform: str,
    status: str,
    skip_reason: str = "",
    selected_rank: Optional[int] = None,
    timecodes: Optional[list[str]] = None,
    text: str = "",
    like_count: Optional[int] = None,
    reply_count: Optional[int] = None,
    score: float = 0.0,
    reason: Optional[list[str]] = None,
    lane: str = CHAPTER_TIMETABLE,
    coverage_bands: Optional[list[str]] = None,
    anchor_count: int = 0,
) -> dict[str, Any]:
    safe_reason = [str(item) for item in (reason or [])]
    safe_timecodes = [str(tc) for tc in (timecodes or [])]
    return {
        "schema_version": CANDIDATE_LEARNING_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "platform": platform if platform in {"chzzk", "youtube"} else "other",
        "selection_status": status,
        "skip_reason": str(skip_reason or ""),
        "selected_rank": selected_rank,
        "lane": lane if lane in {CHAPTER_TIMETABLE, HIGHLIGHT_HINT} else CHAPTER_TIMETABLE,
        "like_bucket": _count_bucket(like_count),
        "buff_bucket": _count_bucket(like_count),
        "reply_bucket": _count_bucket(reply_count),
        "timecode_count": len(safe_timecodes),
        "comment_length_bucket": _length_bucket(text),
        "timeline_density": _density_bucket(safe_timecodes, text),
        "coverage_bands": [
            band for band in (coverage_bands or []) if band in {"early", "mid", "late"}
        ],
        "anchor_count": max(0, int(anchor_count or 0)),
        "collector_score": round(float(score or 0.0), 3),
        "collector_score_bucket": _score_bucket(score),
        "score_component_names": _score_component_names(safe_reason),
        "component_weight_policy": {
            "like_signal": "log_bucketed_capped",
            "reply_signal": "log_bucketed_capped",
            "coverage_signal": "early_mid_late_bucket",
            "text_signal": "timecode_count_and_structure",
        },
        "privacy_flags": [
            "raw_comment_text_excluded",
            "author_excluded",
            "real_comment_id_excluded",
            "source_label_excluded",
            "engagement_counts_bucketed",
        ],
    }

def _score_comment(
    *,
    text: str,
    timecodes: list[str],
    like_count: Optional[int],
    reply_count: Optional[int],
    coverage_bands: list[str] | None = None,
    span_sec: int = 0,
    lane: str = CHAPTER_TIMETABLE,
) -> tuple[float, list[str]]:
    reason: list[str] = []
    tc_count = len(timecodes)
    score = float(tc_count * 10)
    reason.append(f"timecodes={tc_count}")

    if _TIMELINE_HINT_RE.search(text):
        score += 8
        reason.append("timeline_hint")
    if like_count is not None:
        like_score = min(LIKE_SCORE_CAP, math.log1p(max(0, like_count)) * LIKE_SCORE_WEIGHT)
        score += like_score
        reason.append(f"likes_high_weight={like_count}")
    if reply_count is not None:
        score += min(REPLY_SCORE_CAP, math.log1p(max(0, reply_count)) * REPLY_SCORE_WEIGHT)
        reason.append(f"replies={reply_count}")

    bands = [band for band in (coverage_bands or []) if band in {"early", "mid", "late"}]
    if bands:
        score += len(set(bands)) * 12
        reason.append(f"coverage={','.join(bands)}")
        if "early" in bands and "late" in bands:
            score += 12
            reason.append("early_late_span")
    if span_sec >= 3600:
        score += min(24, math.log1p(span_sec / 3600.0) * 8)
        reason.append(f"span_hours={span_sec / 3600.0:.1f}")
    if lane == HIGHLIGHT_HINT:
        score -= 10
        reason.append("lane=highlight_hint")
    else:
        reason.append("lane=chapter_timetable")

    line_count = len([line for line in text.splitlines() if line.strip()])
    if line_count >= max(2, tc_count):
        score += min(8, line_count)
        reason.append(f"lines={line_count}")
    if len(text) < 20:
        score -= 8
        reason.append("too_short_penalty")
    return score, reason

def select_timeline_comments_with_learning(
    raw_comments: list[dict[str, Any]],
    *,
    platform: str,
    max_selected: int = DEFAULT_MAX_SELECTED,
    min_timecodes: int = DEFAULT_MIN_TIMECODES,
    min_score: float = DEFAULT_MIN_SCORE,
) -> tuple[list[TimelineComment], dict[str, Any]]:
    """Filter arbitrary DOM comments and emit a non-raw private feature ledger."""

    candidate_rows: list[dict[str, Any]] = []
    learning_rows: list[dict[str, Any]] = []
    ignored_reasons: dict[str, int] = {}
    seen_text: set[str] = set()

    def record_skip(skip_reason: str, candidate_id: str, **kwargs: Any) -> None:
        ignored_reasons[skip_reason] = ignored_reasons.get(skip_reason, 0) + 1
        learning_rows.append(_candidate_learning_row(
            candidate_id=candidate_id,
            platform=platform,
            status="skipped",
            skip_reason=skip_reason,
            **kwargs,
        ))

    for raw_index, raw in enumerate(raw_comments, 1):
        candidate_id = f"candidate_{raw_index:04d}"
        if not isinstance(raw, dict):
            record_skip("parse_invalid", candidate_id)
            continue
        text = _normalize_comment_text(raw.get("text"))
        if not text:
            record_skip("no_text", candidate_id)
            continue
        generated_skip_reason = _generated_summary_comment_skip_reason(text)
        if generated_skip_reason:
            record_skip(generated_skip_reason, candidate_id, text=text)
            continue
        key = re.sub(r"\s+", " ", text).casefold()
        if key in seen_text:
            record_skip("duplicate_text", candidate_id, text=text)
            continue
        seen_text.add(key)

        timecodes = extract_timecodes(text)

        combined = "\n".join(
            str(raw.get(k) or "") for k in ("text", "aria", "metadata_text")
        )
        like_count = _parse_count(raw.get("like_count"))
        reply_count = _parse_count(raw.get("reply_count"))
        if like_count is None:
            like_count = _count_from_text(
                combined,
                ("추천", "좋아요", "버프", "Like", "likes", "buff", "recommend"),
            )
        if reply_count is None:
            reply_count = _count_from_text(combined, ("답글", "댓글", "reply", "replies"))

        def _shadow_score_kwargs() -> dict[str, Any]:
            """Score a gate-skipped candidate so the ledger records what the
            score function WOULD have given instead of a misleading 0.0.

            Coverage/span stay empty because the structure gate fires before
            global duration is known; with <2 usable timecodes both are 0
            anyway, so the shadow score is exact for the rows it covers.
            """

            lane = classify_comment_lane(text, timecodes=timecodes)
            score, reason = _score_comment(
                text=text,
                timecodes=timecodes,
                like_count=like_count,
                reply_count=reply_count,
                coverage_bands=[],
                span_sec=0,
                lane=lane,
            )
            return {
                "like_count": like_count,
                "reply_count": reply_count,
                "score": score,
                "reason": reason,
                "lane": lane,
            }

        if len(timecodes) < min_timecodes and not (
            timecodes and _TIMELINE_HINT_RE.search(text)
        ):
            record_skip(
                "no_timecode" if not timecodes else "low_structure",
                candidate_id,
                timecodes=timecodes,
                text=text,
                **_shadow_score_kwargs(),
            )
            continue
        timecode_secs: list[int] = []
        for tc in timecodes:
            try:
                timecode_secs.append(timecode_to_seconds(tc))
            except ValueError:
                continue
        if not timecode_secs:
            record_skip(
                "parse_invalid",
                candidate_id,
                timecodes=timecodes,
                text=text,
                **_shadow_score_kwargs(),
            )
            continue

        candidate_rows.append({
            "candidate_id": candidate_id,
            "raw": raw,
            "text": text,
            "timecodes": timecodes,
            "timecode_secs": timecode_secs,
            "like_count": like_count,
            "reply_count": reply_count,
            "source_order": int(raw.get("source_order") or raw_index),
            "thread_key": str(raw.get("thread_key") or ""),
            "thread_order": int(raw.get("thread_order") or 0),
        })

    global_duration_sec = 0
    for row in candidate_rows:
        secs = row.get("timecode_secs") or []
        if secs:
            global_duration_sec = max(global_duration_sec, max(secs))

    selected: list[TimelineComment] = []
    for row in candidate_rows:
        raw = row["raw"]
        candidate_id = row["candidate_id"]
        text = row["text"]
        timecodes = row["timecodes"]
        timecode_secs = row["timecode_secs"]
        like_count = row["like_count"]
        reply_count = row["reply_count"]
        source_order = row["source_order"]
        thread_key = row["thread_key"]
        thread_order = row["thread_order"]
        lane = classify_comment_lane(text, timecodes=timecodes)
        coverage_bands = (
            coverage_bands_for_seconds(
                timecode_secs,
                duration_sec=global_duration_sec or None,
            )
            if global_duration_sec >= 1800
            else []
        )
        span_sec = (max(timecode_secs) - min(timecode_secs)) if len(timecode_secs) >= 2 else 0
        score, reason = _score_comment(
            text=text,
            timecodes=timecodes,
            like_count=like_count,
            reply_count=reply_count,
            coverage_bands=coverage_bands,
            span_sec=span_sec,
            lane=lane,
        )
        if score < min_score:
            record_skip(
                "low_score",
                candidate_id,
                timecodes=timecodes,
                text=text,
                like_count=like_count,
                reply_count=reply_count,
                score=score,
                reason=reason,
                lane=lane,
                coverage_bands=coverage_bands,
            )
            continue
        anchor_count = len(extract_anchors_from_comment_text(
            text,
            lane=lane,
            comment_score=score,
            like_count=like_count,
            reply_count=reply_count,
        ))
        selected.append(TimelineComment(
            source=f"{platform}_vod_comments_browser",
            text=text,
            timecodes=timecodes,
            like_count=like_count,
            reply_count=reply_count,
            comment_id=candidate_id,
            score=round(score, 3),
            rank_reason=reason,
            lane=lane,
            coverage_bands=coverage_bands,
            anchor_count=anchor_count,
            source_order=source_order,
            thread_key=thread_key,
            thread_order=thread_order,
        ))

    selected.sort(
        key=lambda c: (
            1 if c.lane == CHAPTER_TIMETABLE else 0,
            len(c.coverage_bands),
            c.score,
            c.like_count if c.like_count is not None else -1,
            len(c.timecodes),
        ),
        reverse=True,
    )
    unique: list[TimelineComment] = []
    seen_timecode_sequences: set[tuple[str, ...]] = set()
    for comment in selected:
        sequence = tuple(comment.timecodes)
        if len(sequence) >= 2 and sequence in seen_timecode_sequences:
            record_skip(
                "duplicate_timecode_sequence",
                comment.comment_id or f"candidate_selected_{len(learning_rows) + 1:04d}",
                timecodes=comment.timecodes,
                text=comment.text,
                like_count=comment.like_count,
                reply_count=comment.reply_count,
                score=comment.score,
                reason=comment.rank_reason,
                lane=comment.lane,
                coverage_bands=comment.coverage_bands,
                anchor_count=comment.anchor_count,
            )
            continue
        if any(_near_duplicate_timetable(comment, previous) for previous in unique):
            record_skip(
                "near_duplicate_timetable",
                comment.comment_id or f"candidate_selected_{len(learning_rows) + 1:04d}",
                timecodes=comment.timecodes,
                text=comment.text,
                like_count=comment.like_count,
                reply_count=comment.reply_count,
                score=comment.score,
                reason=comment.rank_reason,
                lane=comment.lane,
                coverage_bands=comment.coverage_bands,
                anchor_count=comment.anchor_count,
            )
            continue
        if sequence:
            seen_timecode_sequences.add(sequence)
        if len(unique) >= max(0, int(max_selected)):
            record_skip(
                "cap_skipped",
                comment.comment_id or f"candidate_selected_{len(learning_rows) + 1:04d}",
                timecodes=comment.timecodes,
                text=comment.text,
                like_count=comment.like_count,
                reply_count=comment.reply_count,
                score=comment.score,
                reason=comment.rank_reason,
                lane=comment.lane,
                coverage_bands=comment.coverage_bands,
                anchor_count=comment.anchor_count,
            )
            continue
        unique.append(comment)
        learning_rows.append(_candidate_learning_row(
            candidate_id=comment.comment_id,
            platform=platform,
            status="selected",
            selected_rank=len(unique),
            timecodes=comment.timecodes,
            text=comment.text,
            like_count=comment.like_count,
            reply_count=comment.reply_count,
            score=comment.score,
            reason=comment.rank_reason,
            lane=comment.lane,
            coverage_bands=comment.coverage_bands,
            anchor_count=comment.anchor_count,
        ))

    learning = {
        "schema_version": CANDIDATE_LEARNING_SCHEMA_VERSION,
        "raw_comment_text_included": False,
        "author_included": False,
        "real_comment_id_included": False,
        "source_label_included": False,
        "engagement_counts_included": False,
        "candidate_count": len(raw_comments),
        "selected_count": len(unique),
        "ignored_reason_counts": dict(sorted(ignored_reasons.items())),
        "candidates": learning_rows[: max(1, int(DEFAULT_MAX_CANDIDATES))],
    }
    return unique, learning

def _bundle_to_json(bundle: TimelineCommentBundle) -> dict[str, Any]:
    def _comment_cache_row(comment: TimelineComment) -> dict[str, Any]:
        return {
            "source": "private_comment_candidate",
            "private_prompt_body": _private_prompt_comment_body(comment.text),
            "timecodes": [str(tc) for tc in (comment.timecodes or [])],
            "like_bucket": _count_bucket(comment.like_count),
            "buff_bucket": _count_bucket(comment.like_count),
            "reply_bucket": _count_bucket(comment.reply_count),
            "score_bucket": _score_bucket(comment.score),
            "score_component_names": _score_component_names(comment.rank_reason),
            "lane": comment.lane,
            "coverage_bands": list(comment.coverage_bands or []),
            "anchor_count": int(comment.anchor_count or 0),
            "candidate_id": comment.comment_id or "",
            "source_order": int(comment.source_order or 0),
            "thread_key": str(comment.thread_key or ""),
            "thread_order": int(comment.thread_order or 0),
            "privacy_flags": [
                "private_prompt_body_retained",
                "public_output_forbidden",
                "author_excluded",
                "real_comment_id_excluded",
                "source_label_excluded",
                "engagement_counts_bucketed",
            ],
        }

    return {
        "schema_version": bundle.schema_version,
        "video_no": bundle.video_no,
        "source_url": bundle.source_url,
        "platform": bundle.platform,
        "fetched_at": bundle.fetched_at,
        "status": bundle.status,
        "total_comments_seen": bundle.total_comments_seen,
        "timeline_comment_count": bundle.timeline_comment_count,
        "selected_count": bundle.selected_count,
        "comments": [_comment_cache_row(c) for c in bundle.comments],
        "anchors": [asdict(a) for a in bundle.anchors],
        "candidate_learning": bundle.candidate_learning,
    }

def _anchor_from_json(raw: dict[str, Any]) -> Optional[TimelineAnchor]:
    if not isinstance(raw, dict):
        return None
    try:
        tc = str(raw.get("tc") or "")
        sec = int(raw.get("sec") if raw.get("sec") is not None else timecode_to_seconds(tc))
        return TimelineAnchor(
            tc=tc,
            sec=sec,
            label=str(raw.get("label") or "챕터 후보"),
            lane=str(raw.get("lane") or CHAPTER_TIMETABLE),
            internal_weight=float(raw.get("internal_weight") or 0.0),
            comment_score=float(raw.get("comment_score") or 0.0),
            like_weight=float(raw.get("like_weight") or 0.0),
            aggregate_weight=float(raw.get("aggregate_weight") or 0.0),
            max_weight=float(raw.get("max_weight") or 0.0),
            cluster_size=int(raw.get("cluster_size") or 1),
            source_key=str(raw.get("source_key") or ""),
            privacy_flags=tuple(str(x) for x in (raw.get("privacy_flags") or ())),
        )
    except (TypeError, ValueError):
        return None

def _bundle_from_json(data: dict[str, Any]) -> Optional[TimelineCommentBundle]:
    if not isinstance(data, dict):
        return None
    try:
        schema_v = int(data.get("schema_version") or 0)
    except (TypeError, ValueError):
        return None
    if schema_v != CACHE_SCHEMA_VERSION:
        return None
    status = str(data.get("status") or "ok")
    if status in {"no_timeline_comments", "no_comments"} and not (
        data.get("source_url")
        and data.get("platform") in {"chzzk", "youtube", "other"}
        and data.get("fetched_at")
        and all(key in data for key in (
            "total_comments_seen", "timeline_comment_count", "selected_count",
            "comments", "anchors", "candidate_learning",
        ))
        and isinstance(data.get("comments"), list)
        and isinstance(data.get("anchors"), list)
        and isinstance(data.get("candidate_learning"), dict)
        and data["candidate_learning"].get("schema_version")
        == CANDIDATE_LEARNING_SCHEMA_VERSION
        and data["candidate_learning"].get("raw_comment_text_included") is False
    ):
        return None
    comments: list[TimelineComment] = []
    for raw in data.get("comments") or []:
        if not isinstance(raw, dict):
            continue
        comments.append(TimelineComment(
            source=str(raw.get("source") or ""),
            text=_normalize_comment_text(raw.get("private_prompt_body")),
            timecodes=[str(x) for x in (raw.get("timecodes") or [])],
            like_count=None,
            reply_count=None,
            comment_id=str(raw.get("candidate_id") or ""),
            score=float(raw.get("score") or 0),
            rank_reason=[str(x) for x in (raw.get("score_component_names") or [])],
            lane=str(raw.get("lane") or CHAPTER_TIMETABLE),
            coverage_bands=[str(x) for x in (raw.get("coverage_bands") or [])],
            anchor_count=int(raw.get("anchor_count") or 0),
            source_order=int(raw.get("source_order") or 0),
            thread_key=str(raw.get("thread_key") or ""),
            thread_order=int(raw.get("thread_order") or 0),
        ))
    anchors = [
        anchor
        for anchor in (_anchor_from_json(raw) for raw in (data.get("anchors") or []))
        if anchor is not None
    ]
    try:
        return TimelineCommentBundle(
            video_no=str(data.get("video_no") or ""),
            source_url=str(data.get("source_url") or ""),
            platform=str(data.get("platform") or "chzzk"),
            fetched_at=str(data.get("fetched_at") or ""),
            status=str(data.get("status") or "ok"),
            total_comments_seen=int(data.get("total_comments_seen") or 0),
            timeline_comment_count=int(data.get("timeline_comment_count") or len(comments)),
            selected_count=int(data.get("selected_count") or len(comments)),
            comments=comments,
            anchors=anchors,
            candidate_learning=data.get("candidate_learning") if isinstance(data.get("candidate_learning"), dict) else {},
            schema_version=CACHE_SCHEMA_VERSION,
        )
    except (TypeError, ValueError):
        return None

def build_private_timeline_anchors_for_bundle(
    bundle: TimelineCommentBundle | None,
    *,
    cluster_window_sec: int = 75,
) -> list[TimelineAnchor]:
    """Build clustered private anchors from selected comments."""

    if bundle is None:
        return []
    if bundle.anchors and int(cluster_window_sec) == 75:
        return list(bundle.anchors)
    if not bundle.comments:
        return []
    anchors: list[TimelineAnchor] = []
    for index, comment in enumerate(bundle.comments, 1):
        lane = comment.lane or classify_comment_lane(comment.text, timecodes=comment.timecodes)
        anchors.extend(extract_anchors_from_comment_text(
            comment.text,
            lane=lane,
            comment_score=comment.score,
            like_count=comment.like_count,
            reply_count=comment.reply_count,
            source_key=comment.comment_id or f"selected:{index}",
        ))
    return cluster_timeline_anchors(anchors, window_sec=cluster_window_sec)
