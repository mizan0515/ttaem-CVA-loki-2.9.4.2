"""BroadcastMap outline validation ownership.

This module owns validation and sanitization only.  Parsing, projection, and
model-repair behavior remain in the manager-outline compatibility facade.
"""
from __future__ import annotations

import re
from typing import Any

from .schema import (
    CAUSAL_PROPOSAL_HEADING,
    CAUSAL_WINDOW_RE,
    FINAL_HEADINGS as _REQUIRED_FINAL,
    LEGACY_CANDIDATE_HEADING,
    POINT_RE as _POINT_RE,
    POINT_REVIEW_HEADING,
    RANGE_RE as _RANGE_RE,
    hms_to_seconds as _hms_to_seconds,
    point_review_heading as _point_review_heading,
)
from .limits import MAX_REQUEST_BYTES

MAX_CHUNK_CAUSAL_WINDOW_SEC = 240
MAX_FINAL_CAUSAL_WINDOW_SEC = 600

def sanitize_chunk_observation(text: str) -> str:
    """Drop only malformed optional causal proposals from a source observation.

    A causal row is support-only.  It must fail closed as a proposal without
    discarding the surrounding STT/chat observation or triggering a provider
    retry/fallback for the entire broadcast.
    """

    kept: list[str] = []
    for line in str(text or "").splitlines():
        if "CAUSAL_WINDOW" not in line:
            kept.append(line)
            continue
        match = CAUSAL_WINDOW_RE.fullmatch(line)
        if not match:
            continue
        phases = [_hms_to_seconds(match.group(name)) for name in (
            "setup", "event", "reaction", "payoff"
        )]
        if phases != sorted(phases) or phases[-1] - phases[0] > MAX_CHUNK_CAUSAL_WINDOW_SEC:
            continue
        kept.append(line)
    return "\n".join(kept)

def validate_chunk_observation(text: str) -> None:
    for heading in ("[지속 활동 관찰]", "[전환·중단·재개 관찰]", "[지원 신호 후보]", "[불확실]"):
        if heading not in text:
            raise ValueError(f"missing chunk heading: {heading}")
    forbidden_headings = (
        "[방송 전체 이해]", "[실제 목차]", "[Point]", POINT_REVIEW_HEADING,
        LEGACY_CANDIDATE_HEADING,
        "[짧은 요약]", "[실제 사용한 정보]", "[사용한 콘텐츠 정보]", "[모델/호출]",
    )
    if any(heading in text for heading in forbidden_headings) or re.search(
        r"(?mi)^\s*(?:D1|D2|Point|Candidate)\b", text
    ):
        raise ValueError("chunk observation attempted to own final D1/D2/Point/Candidate")
    for line in sanitize_chunk_observation(text).splitlines():
        if "CAUSAL_WINDOW" not in line:
            continue
        match = CAUSAL_WINDOW_RE.fullmatch(line)
        if not match:
            raise ValueError("invalid causal-window observation shape")
        phases = [_hms_to_seconds(match.group(name)) for name in (
            "setup", "event", "reaction", "payoff"
        )]
        if phases != sorted(phases) or phases[-1] - phases[0] > MAX_CHUNK_CAUSAL_WINDOW_SEC:
            raise AssertionError("sanitizer retained an invalid causal-window clock")

def validate_manager_outline(text: str, *, duration_sec: int | None = None) -> None:
    if len(text.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("outline exceeds 40KB")
    positions = []
    for heading in _REQUIRED_FINAL:
        pos = text.find(heading)
        if pos < 0:
            raise ValueError(f"missing final heading: {heading}")
        positions.append(pos)
    if positions != sorted(positions):
        raise ValueError("final headings are out of order")
    toc = _section(text, "[실제 목차]", "[Point]")
    if not re.search(r"(?m)^D1\s+" + _RANGE_RE.pattern, toc):
        raise ValueError("D1 start-end range is required")
    if re.search(r"(?m)^\s*D(?:3|[4-9]|[1-9]\d+)\b", toc):
        raise ValueError("D3 or deeper Range is forbidden; use Point")
    current_d1: tuple[int, int] | None = None
    previous_d1_end = -1
    previous_d2_end: int | None = None
    for row in (line for line in toc.splitlines() if line.strip()):
        level = re.match(r"^\s*(D[12])\s+", row)
        match = _RANGE_RE.search(row)
        if not level or not match:
            raise ValueError("outline rows must be D1/D2 start-end ranges")
        start_text, end_text = re.split(r"\s*[-–~]\s*", match.group(0))
        start_sec = _hms_to_seconds(start_text)
        end_sec = _hms_to_seconds(end_text)
        if end_sec <= start_sec:
            raise ValueError("outline range end must be after start")
        if duration_sec is not None and end_sec > duration_sec:
            raise ValueError("outline timestamp exceeds VOD duration")
        if not row[match.end():].strip():
            raise ValueError("outline row requires a non-empty navigation title")
        if re.search(r"\b\d{2}:\d{2}:\d{2}\b", row[match.end():]):
            raise ValueError("navigation title must not repeat a timestamp")
        if level.group(1) == "D1":
            if row.startswith((" ", "\t")):
                raise ValueError("D1 must not be indented")
            if start_sec < previous_d1_end:
                raise ValueError("D1 ranges must be ordered and non-overlapping")
            current_d1 = (start_sec, end_sec)
            previous_d1_end = end_sec
            previous_d2_end = None
        else:
            if not row.startswith((" ", "\t")) or current_d1 is None:
                raise ValueError("D2 must follow and be indented under a D1")
            if start_sec < current_d1[0] or end_sec > current_d1[1]:
                raise ValueError("D2 range must be contained by its D1")
            if previous_d2_end is not None and start_sec < previous_d2_end:
                raise ValueError("sibling D2 ranges must be ordered and non-overlapping")
            previous_d2_end = end_sec
    review_heading = _point_review_heading(text)
    point_end = (
        CAUSAL_PROPOSAL_HEADING
        if CAUSAL_PROPOSAL_HEADING in text
        else review_heading
        if review_heading
        else "[짧은 요약]"
    )
    point = _section(text, "[Point]", point_end)
    if _RANGE_RE.search(point):
        raise ValueError("Point cannot be a range")
    point_body = point.strip()
    point_rows = [line.strip() for line in point_body.splitlines() if line.strip()]
    if point_rows == ["없음"]:
        pass
    elif not point_rows or any(not _POINT_RE.fullmatch(line) for line in point_rows):
        raise ValueError("Point must be `없음` or exact timestamped context-start rows")
    elif duration_sec is not None and any(
        _hms_to_seconds(line.split(maxsplit=1)[0]) > duration_sec for line in point_rows
    ):
        raise ValueError("Point timestamp exceeds VOD duration")
    if CAUSAL_PROPOSAL_HEADING in text:
        proposal_end = review_heading or "[짧은 요약]"
        proposal_rows = [
            row.strip()
            for row in _section(text, CAUSAL_PROPOSAL_HEADING, proposal_end).splitlines()
            if row.strip()
        ]
        if proposal_rows != ["없음"]:
            point_times = {row.split(maxsplit=1)[0] for row in point_rows if row != "없음"}
            seen_points: set[str] = set()
            for row in proposal_rows:
                match = CAUSAL_WINDOW_RE.fullmatch(row)
                if not match:
                    raise ValueError("invalid final causal-window proposal shape")
                phases = [_hms_to_seconds(match.group(name)) for name in (
                    "setup", "event", "reaction", "payoff"
                )]
                if phases != sorted(phases) or phases[-1] - phases[0] > MAX_FINAL_CAUSAL_WINDOW_SEC:
                    raise ValueError("final causal-window proposal clock is invalid")
                point_time = next((match.group(phase) for phase in ("setup", "event")
                                   if match.group(phase) in point_times), None)
                if point_time is None:
                    raise ValueError("final causal-window must bind an accepted Point context start")
                if point_time in seen_points:
                    raise ValueError("final causal-window proposal duplicates a Point")
                seen_points.add(point_time)
    summary = _section(text, "[짧은 요약]", "[불확실하거나 빠진 부분]")
    if _short_summary_has_structure(summary):
        raise ValueError("short summary cannot introduce timestamps, structure, or headings")
    if review_heading:
        candidates = _section(text, review_heading, "[짧은 요약]")
        for row in (line.strip() for line in candidates.splitlines() if line.strip()):
            if row == "없음":
                continue
            match = _RANGE_RE.search(row)
            if not match:
                raise ValueError("highlight candidate must be a start-end range")
            start_text, end_text = re.split(r"\s*[-–~]\s*", match.group(0))
            start_sec = _hms_to_seconds(start_text)
            end_sec = _hms_to_seconds(end_text)
            if end_sec <= start_sec:
                raise ValueError("highlight candidate end must be after start")
            if duration_sec is not None and end_sec > duration_sec:
                raise ValueError("highlight candidate end exceeds VOD duration")
    used = _section(text, "[실제 사용한 정보]", "[사용한 콘텐츠 정보]")
    for label in ("STT", "replay chat", "chat reaction/density", "audio", "public timestamp comment", "viewer clip", "visual/OCR"):
        if label not in used:
            raise ValueError(f"missing signal usage row: {label}")
    content_info = _section(text, "[사용한 콘텐츠 정보]", "[모델/호출]").strip()
    if not content_info:
        raise ValueError("content information usage must be explicit")
    model_section = text[text.index("[모델/호출]"):]
    if "retry: 0" not in model_section or "fallback: 0" not in model_section:
        raise ValueError("retry/fallback must be explicitly zero")

def validate_structured_manager_outline(outline: dict[str, Any], *, duration_sec: int | None = None) -> None:
    """Validate editable structure and relationships before any revision is written."""
    if not isinstance(outline, dict):
        raise ValueError("outline must be an object")
    ranges = list(outline.get("ranges") or [])
    points = list(outline.get("points") or [])
    candidates = list(outline.get("candidates") or [])
    if any(not isinstance(item, dict) for item in ranges + points + candidates):
        raise ValueError("every outline row must be an object")
    d1_rows = [item for item in ranges if str(item.get("level") or "") == "D1"]
    d2_rows = [item for item in ranges if str(item.get("level") or "") == "D2"]
    d1_ids = {str(item.get("id") or "") for item in d1_rows}
    all_rows = ranges + points + candidates
    all_ids = [str(item.get("id") or "") for item in all_rows]
    if any(not item_id for item_id in all_ids) or len(set(all_ids)) != len(all_ids):
        raise ValueError("every outline row must retain one unique id")
    if any(not str(item.get("title") or "").strip() for item in all_rows):
        raise ValueError("every outline row must retain a title")
    if len(d1_ids) != len(d1_rows) or any(str(item.get("parent_id") or "") not in d1_ids for item in d2_rows):
        raise ValueError("every D2 must retain a valid D1 parent")
    if len(d1_rows) + len(d2_rows) != len(ranges):
        raise ValueError("outline ranges must be D1 or D2")

    def seconds(value: Any) -> int:
        return _hms_to_seconds(str(value or ""))

    range_by_id = {str(item["id"]): item for item in ranges}
    previous_d1_end = -1
    for parent in d1_rows:
        start_sec, end_sec = seconds(parent.get("start")), seconds(parent.get("end"))
        if end_sec <= start_sec:
            raise ValueError("outline range end must be after start")
        if start_sec < previous_d1_end:
            raise ValueError("D1 ranges must be ordered and non-overlapping")
        if duration_sec is not None and end_sec > duration_sec:
            raise ValueError("outline timestamp exceeds VOD duration")
        previous_d1_end = end_sec
        previous_d2_end = None
        for child in (row for row in d2_rows if str(row.get("parent_id") or "") == str(parent["id"])):
            child_start, child_end = seconds(child.get("start")), seconds(child.get("end"))
            if child_end <= child_start:
                raise ValueError("outline range end must be after start")
            if child_start < start_sec or child_end > end_sec:
                raise ValueError("D2 range must be contained by its D1")
            if previous_d2_end is not None and child_start < previous_d2_end:
                raise ValueError("sibling D2 ranges must be ordered and non-overlapping")
            previous_d2_end = child_end
    for point in points:
        if duration_sec is not None and seconds(point.get("timestamp")) > duration_sec:
            raise ValueError("Point timestamp exceeds VOD duration")
    for candidate in candidates:
        start_sec, end_sec = seconds(candidate.get("start")), seconds(candidate.get("end"))
        if end_sec <= start_sec:
            raise ValueError("highlight candidate end must be after start")
        if duration_sec is not None and end_sec > duration_sec:
            raise ValueError("highlight candidate end exceeds VOD duration")

def _short_summary_has_structure(summary: str) -> bool:
    return bool(
        re.search(r"\b\d{2}:\d{2}:\d{2}\b", summary)
        or re.search(r"(?m)^\s*(?:D1|D2|Point)\b", summary)
        or re.search(r"(?m)^\s*\[[^\n]+\]\s*$", summary)
    )

def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start) + len(start): text.index(end)]

__all__ = [
    "MAX_CHUNK_CAUSAL_WINDOW_SEC", "MAX_FINAL_CAUSAL_WINDOW_SEC",
    "sanitize_chunk_observation", "validate_chunk_observation",
    "validate_manager_outline", "validate_structured_manager_outline",
    "_short_summary_has_structure", "_section",
]
