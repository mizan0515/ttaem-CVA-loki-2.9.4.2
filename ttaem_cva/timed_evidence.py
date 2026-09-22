"""Portable timed evidence manifests for summary production.

This module converts chunker output into the shared evidence-spine shape used
by summaries, Q&A, future local retrieval, and manager review. It deliberately
stores hashes, timing, token counts, and portable path refs instead of raw
transcript/chat text or machine-specific absolute paths.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


ITEM_SCHEMA_VERSION = "timed_evidence_item.v1"
MANIFEST_SCHEMA_VERSION = "summary_timed_evidence_manifest.v1"
TIMELINE_SOURCE_TYPES = frozenset({"subtitle", "chat", "viewer_clip"})
TIMELINE_SOURCE_DIAGNOSTICS_SCHEMA_VERSION = "timeline_source_evidence_diagnostics.v1"

_TOKEN_ENCODERS: dict[str, Any] = {}
_WORD_RE = re.compile(r"\S+")
_SEMANTIC_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_SAFE_CLIP_UID_RE = re.compile(r"^[A-Za-z0-9_-]{3,80}$")
_WEAK_SEMANTIC_TOKENS = frozenset(
    {"시청자", "클립", "인기", "장면", "타임라인", "레전드", "ㅋㅋ", "ㅋㅋㅋ"}
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:^|[\s\"'])(?:[A-Za-z]:[\\/]|/(?:home|Users|private|var|tmp)/)",
    re.IGNORECASE,
)
_UNTRUSTED_INSTRUCTION_RE = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
    r"follow\s+these\s+instructions|이전\s+지시|시스템\s+프롬프트|명령을\s+따라)",
    re.IGNORECASE,
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _field(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _seconds_value(value: float) -> int | float:
    rounded = round(float(value), 3)
    return int(rounded) if rounded.is_integer() else rounded


def _chat_timestamp_sec(row: Any) -> float | None:
    for key in ("ms", "timestamp_ms", "time_ms", "offset_ms", "relative_ms"):
        value = _safe_float(_field(row, key))
        if value is not None:
            return value / 1000.0
    for key in ("sec", "timestamp_sec", "time_sec", "offset_sec", "timestamp"):
        value = _safe_float(_field(row, key))
        if value is not None:
            return value
    return None


def _source_text(row: Any) -> str:
    for key in ("text", "msg", "message", "title"):
        value = str(_field(row, key, "") or "").strip()
        if value:
            return value
    return ""


def _diagnostic_code_for_ignored_text(text: str) -> str:
    if text and _PRIVATE_PATH_RE.search(text):
        return "PRIVATE_TEXT_IGNORED"
    if text and _UNTRUSTED_INSTRUCTION_RE.search(text):
        return "UNTRUSTED_INSTRUCTION_TEXT_IGNORED"
    return ""


def _semantic_tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in _SEMANTIC_TOKEN_RE.findall(text)
        if token.casefold() not in _WEAK_SEMANTIC_TOKENS
    }


def _tokens_overlap(left: set[str], right: set[str]) -> bool:
    return any(
        left_token in right_token or right_token in left_token
        for left_token in left
        for right_token in right
    )


def _timeline_source_item(
    *,
    video_id: str,
    source_type: str,
    start_sec: float,
    end_sec: float | None,
    source_identity: str,
    identity_material: str,
    confidence: float,
    clip_uid: str | None = None,
    clip_title: str | None = None,
    clip_engagement: dict[str, int] | None = None,
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    evidence_hash = _sha256_text(
        f"{video_id}|{source_type}|{source_identity}|{identity_material}"
    )
    item = {
        "schema_version": ITEM_SCHEMA_VERSION,
        "evidence_id": f"tle_{evidence_hash[:24]}",
        "video_id": video_id,
        "source_type": source_type,
        "start_sec": _seconds_value(start_sec),
        "end_sec": _seconds_value(end_sec) if end_sec is not None else None,
        "display_text": None,
        "source_identity": source_identity,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 6),
        "match_reason": "actual_source_timestamp",
        "public_safe": True,
        "raw_content_included": False,
        "manager_review_required": bool(risk_flags),
        "risk_flags": list(risk_flags or []),
    }
    if source_type == "viewer_clip" and clip_uid:
        item["clip_uid"] = clip_uid
        item["clip_url"] = f"https://chzzk.naver.com/clips/{clip_uid}"
        item["clip_title"] = " ".join(str(clip_title or "").split()).strip()[:180]
        item["clip_engagement"] = {
            key: max(0, _safe_int((clip_engagement or {}).get(key)))
            for key in ("like_count", "play_count", "read_count")
        }
    return item


def build_timeline_source_evidence(
    *,
    video_id: str,
    duration_sec: int | float | None,
    subtitle_cues: list[Any] | None = None,
    chat_records: list[Any] | None = None,
    viewer_clips: list[Any] | None = None,
) -> dict[str, Any]:
    """Normalize real source timestamps into raw-free ``timed_evidence_item.v1`` rows.

    Timeline-card timestamps are deliberately not accepted as input. Subtitle
    rows require cue timestamps, chat rows require record timestamps, and
    viewer clips require a same-VOD ``offset_status=ok`` anchor.
    """

    expected_video_id = str(video_id or "")
    duration = _safe_float(duration_sec)
    if duration is not None and duration <= 0:
        duration = None
    rows_by_identity: dict[str, dict[str, Any]] = {}
    semantic_sources: list[dict[str, Any]] = []
    code_counts: dict[str, int] = {}
    input_counts = {
        "subtitle": len(subtitle_cues or []),
        "chat": len(chat_records or []),
        "viewer_clip": len(viewer_clips or []),
    }

    def diagnose(code: str) -> None:
        code_counts[code] = code_counts.get(code, 0) + 1

    def accept(item: dict[str, Any]) -> None:
        identity = str(item.get("source_identity") or "")
        if identity and identity not in rows_by_identity:
            rows_by_identity[identity] = item
        elif identity:
            diagnose("DUPLICATE_SOURCE_IDENTITY")

    def valid_range(start: float, end: float | None) -> bool:
        if start < 0 or (end is not None and end < start):
            diagnose("SOURCE_TIMESTAMP_INVALID")
            return False
        if duration is not None and (
            start > duration or (end is not None and end > duration)
        ):
            diagnose("SOURCE_TIMESTAMP_OUT_OF_RANGE")
            return False
        return True

    for cue in subtitle_cues or []:
        start = _safe_float(_field(cue, "start_sec"))
        end = _safe_float(_field(cue, "end_sec"))
        if start is None:
            diagnose("SUBTITLE_TIMESTAMP_MISSING")
            continue
        if end is not None and end < start:
            diagnose("SOURCE_TIMESTAMP_INVALID")
            continue
        if not valid_range(start, end):
            continue
        text = _source_text(cue)
        text_hash = _sha256_text(text)
        risk_code = _diagnostic_code_for_ignored_text(text)
        risks = [risk_code.lower()] if risk_code else []
        if risk_code:
            diagnose(risk_code)
        cue_confidence = _safe_float(_field(cue, "confidence"))
        identity_hash = _sha256_text(
            f"{expected_video_id}|subtitle|{_seconds_value(start)}|"
            f"{_seconds_value(end) if end is not None else ''}|{text_hash}"
        )
        item = _timeline_source_item(
            video_id=expected_video_id,
            source_type="subtitle",
            start_sec=start,
            end_sec=end,
            source_identity=f"subtitle:{identity_hash[:20]}",
            identity_material=text_hash,
            confidence=1.0 if cue_confidence is None else cue_confidence,
            risk_flags=risks,
        )
        accept(item)
        if text and not risks:
            semantic_sources.append(
                {
                    "evidence_id": item["evidence_id"],
                    "source_type": "subtitle",
                    "start_sec": start,
                    "end_sec": end,
                    "tokens": _semantic_tokens(text),
                }
            )

    for record in chat_records or []:
        start = _chat_timestamp_sec(record)
        if start is None:
            diagnose("CHAT_TIMESTAMP_MISSING")
            continue
        if not valid_range(start, None):
            continue
        text = _source_text(record)
        text_hash = _sha256_text(text)
        stable_uid = str(
            _field(record, "uid", "")
            or _field(record, "chat_id", "")
            or _field(record, "id", "")
        ).strip()
        identity_hash = _sha256_text(
            f"{expected_video_id}|chat|{_seconds_value(start)}|{stable_uid}|{text_hash}"
        )
        risk_code = _diagnostic_code_for_ignored_text(text)
        risks = [risk_code.lower()] if risk_code else []
        if risk_code:
            diagnose(risk_code)
        item = _timeline_source_item(
            video_id=expected_video_id,
            source_type="chat",
            start_sec=start,
            end_sec=None,
            source_identity=f"chat:{identity_hash[:20]}",
            identity_material=f"{stable_uid}|{text_hash}",
            confidence=1.0,
            risk_flags=risks,
        )
        accept(item)
        if text and not risks:
            semantic_sources.append(
                {
                    "evidence_id": item["evidence_id"],
                    "source_type": "chat",
                    "start_sec": start,
                    "end_sec": None,
                    "tokens": _semantic_tokens(text),
                }
            )

    for clip in viewer_clips or []:
        clip_video_id = str(_field(clip, "video_no", "") or "")
        if not clip_video_id:
            diagnose("VIEWER_CLIP_VIDEO_ID_MISSING")
            continue
        if clip_video_id != expected_video_id:
            diagnose("VIEWER_CLIP_WRONG_VOD")
            continue
        if str(_field(clip, "offset_status", "") or "").casefold() != "ok":
            diagnose("VIEWER_CLIP_OFFSET_UNVERIFIED")
            continue
        if str(_field(clip, "manual_override", "") or "").casefold() == "ignore":
            diagnose("VIEWER_CLIP_IGNORED_OVERRIDE")
            continue
        start = _safe_float(_field(clip, "offset_sec"))
        if start is None:
            diagnose("VIEWER_CLIP_TIMESTAMP_MISSING")
            continue
        clip_uid = str(_field(clip, "clip_uid", "") or "").strip()
        if not _SAFE_CLIP_UID_RE.fullmatch(clip_uid):
            diagnose("VIEWER_CLIP_UID_INVALID")
            continue
        clip_duration = _safe_float(_field(clip, "duration"))
        end = start + clip_duration if clip_duration is not None and clip_duration > 0 else None
        if not valid_range(start, end):
            continue
        raw_clip_title = " ".join(
            str(_field(clip, "title", "") or "").split()
        ).strip()
        clip_title_risk = _diagnostic_code_for_ignored_text(raw_clip_title)
        if clip_title_risk:
            diagnose(clip_title_risk)
        item = _timeline_source_item(
            video_id=expected_video_id,
            source_type="viewer_clip",
            start_sec=start,
            end_sec=end,
            source_identity=f"viewer_clip:{clip_uid}",
            identity_material=clip_uid,
            confidence=1.0,
            clip_uid=clip_uid,
            clip_title="" if clip_title_risk else raw_clip_title,
            clip_engagement={
                "like_count": _safe_int(_field(clip, "like_count", 0)),
                "play_count": _safe_int(
                    _field(clip, "play_count", _field(clip, "read_count", 0))
                ),
                "read_count": _safe_int(_field(clip, "read_count", 0)),
            },
            risk_flags=[clip_title_risk.lower()] if clip_title_risk else [],
        )
        clip_tokens = (
            _semantic_tokens(raw_clip_title) if not clip_title_risk else set()
        )
        semantic_supporting_refs: list[dict[str, Any]] = []
        if clip_tokens:
            support_start = max(0.0, start - 30.0)
            support_end = (end if end is not None else start + 30.0) + 30.0
            for source in semantic_sources:
                source_start = float(source["start_sec"])
                source_end = float(
                    source["end_sec"]
                    if source["end_sec"] is not None
                    else source_start
                )
                if source_end < support_start or source_start > support_end:
                    continue
                if not _tokens_overlap(clip_tokens, set(source["tokens"])):
                    continue
                semantic_supporting_refs.append(
                    {
                        "evidence_id": source["evidence_id"],
                        "source_type": source["source_type"],
                        "start_sec": _seconds_value(source_start),
                        "end_sec": (
                            _seconds_value(source_end)
                            if source["end_sec"] is not None
                            else None
                        ),
                    }
                )
        semantic_supporting_refs = list(
            {
                (row["evidence_id"], row["source_type"]): row
                for row in semantic_supporting_refs
            }.values()
        )
        item["semantic_supporting_evidence"] = semantic_supporting_refs[:8]
        semantic_score = (
            min(
                1.0,
                0.72
                + 0.08
                * len({row["source_type"] for row in semantic_supporting_refs})
                + 0.02 * min(6, len(semantic_supporting_refs)),
            )
            if semantic_supporting_refs
            else 0.0
        )
        item["semantic_support_score"] = round(
            semantic_score,
            6,
        )
        accept(item)

    items = sorted(
        rows_by_identity.values(),
        key=lambda row: (
            float(row.get("start_sec") or 0),
            str(row.get("source_type") or ""),
            str(row.get("evidence_id") or ""),
        ),
    )
    accepted_counts = {
        source_type: sum(1 for row in items if row.get("source_type") == source_type)
        for source_type in sorted(TIMELINE_SOURCE_TYPES)
    }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "item_schema_version": ITEM_SCHEMA_VERSION,
        "timeline_source_contract": "timeline_source_evidence.v1",
        "video_id": expected_video_id,
        "raw_content_included": False,
        "public_safe": True,
        "summary": {
            "item_count": len(items),
            "input_counts": input_counts,
            "accepted_counts": accepted_counts,
            "quarantined_count": sum(code_counts.values()),
        },
        "diagnostics": {
            "schema_version": TIMELINE_SOURCE_DIAGNOSTICS_SCHEMA_VERSION,
            "raw_content_included": False,
            "code_counts": dict(sorted(code_counts.items())),
        },
        "items": items,
    }


def _duration_sec(vod_info: Any) -> int | None:
    duration = _safe_int(getattr(vod_info, "duration", None), 0)
    return duration if duration > 0 else None


def _bounded_seconds(start_ms: int, end_ms: int, duration_sec: int | None) -> tuple[int, int]:
    start_sec = max(0, start_ms // 1000)
    end_sec = max(0, end_ms // 1000)
    if end_sec < start_sec:
        start_sec, end_sec = end_sec, start_sec
    if duration_sec is not None:
        start_sec = min(start_sec, duration_sec)
        end_sec = min(end_sec, duration_sec)
    return start_sec, end_sec


def _derive_streamer_id(vod_info: Any) -> str:
    streamer_id = str(getattr(vod_info, "streamer_id", "") or "").strip()
    if streamer_id:
        return streamer_id
    channel_id = str(getattr(vod_info, "channel_id", "") or "").strip()
    if channel_id:
        return f"channel:{channel_id}"
    channel_name = str(getattr(vod_info, "channel_name", "") or "").strip()
    if channel_name:
        return f"name:{_sha256_text(channel_name)[:12]}"
    return "unknown"


def _token_count(text: str, encoding_name: str) -> tuple[int, str]:
    if not text:
        return 0, encoding_name or "none"
    if encoding_name:
        try:
            encoder = _TOKEN_ENCODERS.get(encoding_name)
            if encoder is None:
                import tiktoken

                encoder = tiktoken.get_encoding(encoding_name)
                _TOKEN_ENCODERS[encoding_name] = encoder
            return len(encoder.encode(text)), encoding_name
        except Exception:
            pass
    return max(1, len(_WORD_RE.findall(text))), "approx_words"


def portable_path_ref(
    path_value: str | Path | None,
    *,
    repo_root: str | Path | None = None,
    work_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    site_dir: str | Path | None = None,
    runtime_dir: str | Path | None = None,
) -> str:
    """Return a portable path ref without serializing private absolute roots."""

    if not path_value:
        return ""
    path = Path(path_value)
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    roots: list[tuple[str, Path]] = []
    for prefix, candidate in (
        ("work", work_dir),
        ("output", output_dir),
        ("site", site_dir),
        ("runtime", runtime_dir),
        ("repo", repo_root),
    ):
        if not candidate:
            continue
        try:
            roots.append((prefix, Path(candidate).resolve()))
        except OSError:
            continue
    for prefix, root in roots:
        try:
            rel = resolved.relative_to(root)
        except ValueError:
            continue
        return f"{prefix}:{rel.as_posix()}"
    if not path.is_absolute():
        return f"relative:{path.as_posix()}"
    return f"external:{resolved.name}"


def _evidence_item(
    *,
    vod_info: Any,
    chunk: dict[str, Any],
    source_ref: str,
    path_ref: str,
    tokenizer_encoding: str,
) -> dict[str, Any]:
    text = str(chunk.get("text") or "")
    token_count, tokenizer = _token_count(text, tokenizer_encoding)
    start_ms = _safe_int(chunk.get("start_ms"), 0)
    end_ms = _safe_int(chunk.get("end_ms"), start_ms)
    start_sec, end_sec = _bounded_seconds(start_ms, end_ms, _duration_sec(vod_info))
    chunk_index = _safe_int(chunk.get("index"), 0)
    text_hash = _sha256_text(text)
    item = {
        "schema_version": ITEM_SCHEMA_VERSION,
        "evidence_id": (
            f"te_{str(getattr(vod_info, 'video_no', '') or 'vod')}_"
            f"{chunk_index}_{start_ms // 1000}_{text_hash[:12]}"
        ),
        "video_id": str(getattr(vod_info, "video_no", "") or ""),
        "streamer_id": _derive_streamer_id(vod_info),
        "source_type": "subtitle_chunk",
        "source_ref": source_ref,
        "route_ref": "chunk_srt.sliding_window",
        "start_sec": start_sec,
        "end_sec": end_sec,
        "start_hhmmss": str(chunk.get("start_hhmmss") or ""),
        "end_hhmmss": str(chunk.get("end_hhmmss") or ""),
        "chunk_index": chunk_index,
        "cue_count": _safe_int(chunk.get("cue_count"), 0),
        "char_count": _safe_int(chunk.get("char_count"), len(text)),
        "text_hash": text_hash,
        "confidence": 1.0 if text.strip() else 0.25,
        "public_safe": True,
        "raw_content_included": False,
        "path_ref": path_ref,
        "token_count": token_count,
        "tokenizer": tokenizer,
        "metadata": {
            "promoted_anchor_count": len(chunk.get("promoted_anchors") or []),
            "synthetic_anchor": _safe_int(chunk.get("cue_count"), 0) == 0,
        },
    }
    return item


def build_timed_evidence_manifest(
    *,
    vod_info: Any,
    chunks: list[dict[str, Any]],
    srt_path: str | Path | None = None,
    repo_root: str | Path | None = None,
    work_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    site_dir: str | Path | None = None,
    runtime_dir: str | Path | None = None,
    tokenizer_encoding: str = "cl100k_base",
) -> dict[str, Any]:
    """Build a raw-free summary evidence manifest from chunker output."""

    path_ref = portable_path_ref(
        srt_path,
        repo_root=repo_root,
        work_dir=work_dir,
        output_dir=output_dir,
        site_dir=site_dir,
        runtime_dir=runtime_dir,
    )
    source_ref = path_ref or "subtitle:runtime"
    items = [
        _evidence_item(
            vod_info=vod_info,
            chunk=chunk,
            source_ref=source_ref,
            path_ref=path_ref,
            tokenizer_encoding=tokenizer_encoding,
        )
        for chunk in chunks
        if isinstance(chunk, dict)
    ]
    token_total = sum(_safe_int(item.get("token_count"), 0) for item in items)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "item_schema_version": ITEM_SCHEMA_VERSION,
        "video_id": str(getattr(vod_info, "video_no", "") or ""),
        "streamer_id": _derive_streamer_id(vod_info),
        "source_type": "subtitle",
        "source_ref": source_ref,
        "route_ref": "chunk_srt.sliding_window",
        "path_ref": path_ref,
        "raw_content_included": False,
        "tokenizer": items[0]["tokenizer"] if items else tokenizer_encoding,
        "summary": {
            "item_count": len(items),
            "token_total": token_total,
            "start_sec": items[0]["start_sec"] if items else 0,
            "end_sec": items[-1]["end_sec"] if items else 0,
            "public_safe_count": sum(1 for item in items if item.get("public_safe") is True),
        },
        "items": items,
    }


def compact_timed_evidence_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Return the metadata/ledger-safe subset of a timed evidence manifest."""

    if not isinstance(manifest, dict):
        return {}
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    return {
        "schema_version": manifest.get("schema_version"),
        "item_schema_version": manifest.get("item_schema_version"),
        "video_id": manifest.get("video_id"),
        "streamer_id": manifest.get("streamer_id"),
        "source_type": manifest.get("source_type"),
        "source_ref": manifest.get("source_ref"),
        "route_ref": manifest.get("route_ref"),
        "path_ref": manifest.get("path_ref"),
        "raw_content_included": bool(manifest.get("raw_content_included") or False),
        "tokenizer": manifest.get("tokenizer"),
        "summary": {
            "item_count": _safe_int(summary.get("item_count"), len(items)),
            "token_total": _safe_int(summary.get("token_total"), 0),
            "start_sec": _safe_int(summary.get("start_sec"), 0),
            "end_sec": _safe_int(summary.get("end_sec"), 0),
            "public_safe_count": _safe_int(summary.get("public_safe_count"), 0),
        },
        "items": [
            {
                "schema_version": item.get("schema_version"),
                "evidence_id": item.get("evidence_id"),
                "video_id": item.get("video_id"),
                "streamer_id": item.get("streamer_id"),
                "source_type": item.get("source_type"),
                "source_ref": item.get("source_ref"),
                "route_ref": item.get("route_ref"),
                "start_sec": item.get("start_sec"),
                "end_sec": item.get("end_sec"),
                "text_hash": item.get("text_hash"),
                "confidence": item.get("confidence"),
                "public_safe": item.get("public_safe"),
                "raw_content_included": bool(item.get("raw_content_included") or False),
                "path_ref": item.get("path_ref"),
                "token_count": item.get("token_count"),
            }
            for item in items
            if isinstance(item, dict)
        ],
    }


__all__ = [
    "ITEM_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "TIMELINE_SOURCE_DIAGNOSTICS_SCHEMA_VERSION",
    "TIMELINE_SOURCE_TYPES",
    "build_timeline_source_evidence",
    "build_timed_evidence_manifest",
    "compact_timed_evidence_manifest",
    "portable_path_ref",
]
