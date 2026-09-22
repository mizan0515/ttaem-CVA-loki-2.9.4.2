"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

import hashlib

import json

import re

from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "chzz.broadcast_map_evidence.v1"


PROMPT_SCHEMA_VERSION = "chzz.broadcast_map_evidence.prompt.v1"


ALLOWED_ROLES = {
    "primary_semantic",
    "context_naming",
    "support_signal",
    "candidate_proposal",
    "provenance_only",
    "projection_only",
    "forbidden_answer",
}


FORBIDDEN_ANSWER_SOURCES = (
    "previous_summary",
    "same_vod_manager_correction",
    "alignment_truth",
)


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


CANONICAL_EVIDENCE_SCHEMA_VERSION = "chzz.canonical_evidence_ledger.v2"


_CANONICAL_EVIDENCE_AVAILABILITY = {
    "present",
    "missing",
    "invalid",
    "excluded",
    "partial",
    "unavailable",
}


_CANONICAL_TIMED_STT_LINE = re.compile(
    r"^\s*\[(\d{2}:\d{2}:\d{2})\]\s*(.+?)\s*$"
)


_CANONICAL_SOURCE_TYPES = (
    "stt",
    "replay_chat",
    "timeline_comment",
    "audio_reaction",
    "viewer_clip",
    "visual_scene",
    "keyframe_visual",
    "previous_summary",
    "same_vod_manager_correction",
    "alignment_truth",
)


def _canonical_stt_rows(chunks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return de-duplicated source rows without making chunk layout authoritative."""

    rows: dict[tuple[int, str], dict[str, Any]] = {}
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        for line in str(chunk.get("text") or "").splitlines():
            match = _CANONICAL_TIMED_STT_LINE.match(line)
            if not match:
                continue
            excerpt = match.group(2).strip()
            if not excerpt:
                continue
            sec = _hms_to_sec(match.group(1))
            rows.setdefault((sec, excerpt), {"start_sec": sec, "text": excerpt})
    return [rows[key] for key in sorted(rows)]


def _canonical_chat_rows(chats: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chat in chats:
        if not isinstance(chat, Mapping):
            continue
        try:
            milliseconds = int(chat.get("ms"))
        except (TypeError, ValueError):
            continue
        excerpt = str(chat.get("msg") or "").strip()
        if milliseconds < 0 or not excerpt:
            continue
        rows.append(
            {
                "ms": milliseconds,
                "uid": str(chat.get("uid") or ""),
                "msg": excerpt,
            }
        )
    return rows


def _canonical_source_revision(source_type: str, rows: Iterable[Mapping[str, Any]]) -> str:
    return _canonical_sha256({"source_type": source_type, "rows": list(rows)})


def _source_disposition(
    source_type: str,
    value: Any,
    *,
    item_count: int = 0,
    excluded: bool = False,
    revision: str = "",
) -> dict[str, Any]:
    if excluded:
        availability = "excluded"
    elif value is None:
        availability = "missing"
    elif source_type in {"stt", "replay_chat", "viewer_clip"} and not isinstance(value, list):
        availability = "invalid"
    elif source_type in {"timeline_comment", "audio_reaction"} and not isinstance(value, Mapping):
        availability = "invalid"
    else:
        availability = "present"
    return {
        "source_type": source_type,
        "source_revision": revision,
        "availability": availability,
        "item_count": max(0, int(item_count)),
    }


def _navigation_availability(status: str, rows: list[Mapping[str, Any]]) -> str:
    normalized = str(status or "").strip().casefold()
    if rows:
        return "present" if normalized == "available" else "partial"
    if normalized in {"absent_in_source", "not_collected"}:
        return "unavailable"
    if normalized in {"collection_failed", "stale", "partial"}:
        return "partial"
    return "missing"


def _canonical_source_time(row: Mapping[str, Any]) -> tuple[float, float]:
    start_value = next(
        (
            row.get(key)
            for key in ("start_sec", "center_sec", "sec", "time_sec", "offset_sec")
            if row.get(key) is not None
        ),
        None,
    )
    if start_value is None and row.get("ms") is not None:
        start_value = float(row["ms"]) / 1000.0
    start = float(start_value)
    end_value = row.get("end_sec")
    if end_value is None:
        duration = float(row.get("duration") or 0)
        end_value = start + max(0.0, duration)
    end = max(start, float(end_value))
    return start, end


def _canonical_source_excerpt(source_type: str, row: Mapping[str, Any]) -> str:
    for key in ("text", "msg", "label", "title", "selection_reason", "source_signal"):
        value = " ".join(str(row.get(key) or "").split()).strip()
        if value:
            return value
    return {
        "timeline_comment": "timeline comment anchor",
        "audio_reaction": "audio reaction",
        "viewer_clip": "viewer clip",
        "visual_scene": "visual scene signal",
        "keyframe_visual": "keyframe/OCR sample",
    }.get(source_type, source_type)


def _canonical_source_key(source_type: str, row: Mapping[str, Any]) -> str:
    for key in ("source_key", "clip_uid", "event_id", "sample_id", "pointer"):
        value = str(row.get(key) or "").strip()
        if value and not re.search(r"(?:[A-Za-z]:[\\/]|/Users/|/home/)", value):
            return value[:256]
    return source_type


def _canonical_source_relation(row: Mapping[str, Any]) -> dict[str, Any]:
    relation = row.get("source_relation")
    if isinstance(relation, Mapping):
        thread_key = str(relation.get("thread_key") or "")[:256]
        raw_thread_order = relation.get("thread_order")
    else:
        thread_key = str(row.get("thread_key") or "")[:256]
        raw_thread_order = row.get("thread_order")
    if not thread_key:
        return {}
    result: dict[str, Any] = {"thread_key": thread_key}
    if raw_thread_order is None:
        return result
    try:
        thread_order = max(0, int(raw_thread_order))
    except (TypeError, ValueError):
        return result
    result.update(
        thread_order=thread_order,
        relation_kind="parent" if thread_order == 0 else "reply",
    )
    return result


def _canonical_navigation_record(
    *,
    video_no: str,
    source_type: str,
    row: Mapping[str, Any],
    revision: str,
    parent_observation_ref: str,
    parent_d1_ref: str,
    parent_d2_ref: str,
) -> dict[str, Any]:
    start, end = _canonical_source_time(row)
    pointer = str(row.get("pointer") or _canonical_source_key(source_type, row))
    exact_source_ref = f"{source_type}:{revision[:16]}:{pointer}"
    evidence_id = "evidence-" + _canonical_sha256(
        {
            "video_no": str(video_no),
            "source_type": source_type,
            "source_revision": revision,
            "source_pointer": pointer,
            "start_sec": start,
            "end_sec": end,
        }
    )[:24]
    confidence_value = row.get("confidence")
    if isinstance(confidence_value, str):
        confidence_value = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(
            confidence_value.casefold(), 0.0
        )
    try:
        confidence = max(0.0, min(1.0, float(confidence_value)))
    except (TypeError, ValueError):
        confidence = 1.0
    return {
        "evidence_id": evidence_id,
        "video_no": str(video_no),
        "source_type": source_type,
        "canonical_time_sec": round(start, 3),
        "source_end_sec": round(end, 3),
        "source_revision": revision,
        "exact_source_ref": exact_source_ref,
        "source_key": _canonical_source_key(source_type, row),
        "source_relation": _canonical_source_relation(row),
        "display_excerpt": _canonical_source_excerpt(source_type, row),
        "availability": "present",
        "confidence": confidence,
        "parent_observation_ref": str(parent_observation_ref or ""),
        "parent_d1_ref": str(parent_d1_ref or ""),
        "parent_d2_ref": str(parent_d2_ref or ""),
    }


def _observation_dispositions(observations: Iterable[Any]) -> list[dict[str, Any]]:
    dispositions: list[dict[str, Any]] = []
    for index, raw in enumerate(observations, 1):
        if isinstance(raw, Mapping):
            observation_ref = str(raw.get("observation_id") or f"observation-{index:03d}")
            text = str(raw.get("text") or "").strip()
        else:
            observation_ref = f"observation-{index:03d}"
            text = str(raw or "").strip()
        dispositions.append(
            {
                "observation_ref": observation_ref,
                "inspection_status": "inspected",
                "availability": "present" if text else "invalid",
                "evidence_refs": [],
                "reason_code": (
                    "e1_semantic_linking_deferred"
                    if text
                    else "observation_text_missing"
                ),
            }
        )
    return dispositions


def _ledger_digest_payload(ledger: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in ledger.items() if key != "ledger_sha256"}


def build_canonical_evidence_ledger(
    *,
    video_no: str,
    chunks: list[dict[str, Any]] | None,
    chats: list[dict[str, Any]] | None,
    timeline_comment_context: Mapping[str, Any] | None = None,
    audio_reaction_metadata: Mapping[str, Any] | None = None,
    viewer_clips: list[Any] | None = None,
    keyframe_manifest_present: bool = False,
    observations: Iterable[Any] = (),
    source_rows: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    source_statuses: Mapping[str, Mapping[str, Any] | str] | None = None,
) -> dict[str, Any]:
    """Build a compact source ledger; raw records are materialized only by window."""

    stt_rows = _canonical_stt_rows(chunks or []) if isinstance(chunks, list) else []
    chat_rows = _canonical_chat_rows(chats or []) if isinstance(chats, list) else []
    stt_revision = _canonical_source_revision("stt", stt_rows) if chunks is not None else ""
    chat_revision = (
        _canonical_source_revision("replay_chat", chat_rows) if chats is not None else ""
    )
    timeline_count = (
        len(timeline_comment_context.get("anchors") or [])
        if isinstance(timeline_comment_context, Mapping)
        else 0
    )
    audio_count = len(
        audio_reaction_metadata.get("audio_reaction_peaks")
        or audio_reaction_metadata.get("reaction_peaks")
        or []
    ) if isinstance(audio_reaction_metadata, Mapping) else 0
    sources = [
        _source_disposition("stt", chunks, item_count=len(stt_rows), revision=stt_revision),
        _source_disposition(
            "replay_chat", chats, item_count=len(chat_rows), revision=chat_revision
        ),
        _source_disposition(
            "timeline_comment",
            timeline_comment_context,
            item_count=timeline_count,
            revision=(
                _canonical_sha256(timeline_comment_context)
                if isinstance(timeline_comment_context, Mapping)
                else ""
            ),
        ),
        _source_disposition(
            "audio_reaction",
            audio_reaction_metadata,
            item_count=audio_count,
            revision=(
                _canonical_sha256(audio_reaction_metadata)
                if isinstance(audio_reaction_metadata, Mapping)
                else ""
            ),
        ),
        _source_disposition(
            "viewer_clip",
            viewer_clips,
            item_count=len(viewer_clips or []) if isinstance(viewer_clips, list) else 0,
            revision=(
                _canonical_sha256(viewer_clips) if isinstance(viewer_clips, list) else ""
            ),
        ),
        _source_disposition("visual_scene", None),
        _source_disposition(
            "keyframe_visual",
            True if keyframe_manifest_present else None,
            item_count=1 if keyframe_manifest_present else 0,
            revision="manifest-present" if keyframe_manifest_present else "",
        ),
    ]
    sources.extend(
        _source_disposition(source_type, None, excluded=True)
        for source_type in FORBIDDEN_ANSWER_SOURCES
    )
    if isinstance(source_rows, Mapping):
        normalized_rows = {
            str(source_type): [dict(row) for row in rows if isinstance(row, Mapping)]
            for source_type, rows in source_rows.items()
            if str(source_type) in _CANONICAL_SOURCE_TYPES
        }
        status_rows = source_statuses if isinstance(source_statuses, Mapping) else {}
        by_type = {str(row["source_type"]): row for row in sources}
        for source_type, rows in normalized_rows.items():
            status_payload = status_rows.get(source_type, "")
            if isinstance(status_payload, Mapping):
                source_status = str(status_payload.get("status") or "")
                reason_code = str(status_payload.get("reason") or "")
            else:
                source_status = str(status_payload or "")
                reason_code = ""
            by_type[source_type] = {
                "source_type": source_type,
                "source_revision": _canonical_source_revision(source_type, rows),
                "availability": _navigation_availability(source_status, rows),
                "item_count": len(rows),
                "source_status": source_status or ("available" if rows else "not_collected"),
                "reason_code": reason_code,
            }
        sources = [by_type[source_type] for source_type in _CANONICAL_SOURCE_TYPES]
    ledger: dict[str, Any] = {
        "schema_version": CANONICAL_EVIDENCE_SCHEMA_VERSION,
        "video_no": str(video_no),
        "sources": sources,
        "records": [],
        "observation_dispositions": _observation_dispositions(observations),
    }
    ledger["ledger_sha256"] = _canonical_sha256(_ledger_digest_payload(ledger))
    validate_canonical_evidence_ledger(ledger)
    return ledger


def materialize_canonical_evidence_window(
    ledger: Mapping[str, Any],
    *,
    chunks: list[dict[str, Any]] | None = None,
    chats: list[dict[str, Any]] | None = None,
    start_sec: int | float,
    end_sec: int | float,
    parent_observation_ref: str = "",
    parent_d1_ref: str = "",
    parent_d2_ref: str = "",
    max_records: int = 4_000,
    source_rows: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Materialize a bounded private lookup window using existing timed identities."""

    from .timed_evidence import build_timeline_source_evidence

    validate_canonical_evidence_ledger(ledger)
    start = max(0.0, float(start_sec))
    end = max(start, float(end_sec))
    if end - start > 900:
        raise ValueError("canonical evidence window exceeds 900 seconds")
    sources = {str(row.get("source_type") or ""): row for row in ledger["sources"]}
    if isinstance(source_rows, Mapping):
        normalized_navigation_rows = {
            str(source_type): [dict(row) for row in rows if isinstance(row, Mapping)]
            for source_type, rows in source_rows.items()
            if str(source_type) in _CANONICAL_SOURCE_TYPES
        }
        records: list[dict[str, Any]] = []
        for source_type, rows in normalized_navigation_rows.items():
            revision = _canonical_source_revision(source_type, rows)
            if revision != str(sources[source_type].get("source_revision") or ""):
                raise ValueError(
                    f"{source_type} source revision does not match canonical ledger"
                )
            for row in rows:
                try:
                    row_start, row_end = _canonical_source_time(row)
                except (TypeError, ValueError):
                    continue
                if min(row_start, row_end) >= end or max(row_start, row_end) < start:
                    continue
                records.append(
                    _canonical_navigation_record(
                        video_no=str(ledger.get("video_no") or ""),
                        source_type=source_type,
                        row=row,
                        revision=revision,
                        parent_observation_ref=parent_observation_ref,
                        parent_d1_ref=parent_d1_ref,
                        parent_d2_ref=parent_d2_ref,
                    )
                )
        if len(records) > max(1, int(max_records)):
            raise ValueError("canonical evidence window exceeds record limit")
        result = dict(ledger)
        result["records"] = sorted(
            records,
            key=lambda row: (
                float(row["canonical_time_sec"]),
                str(row["source_type"]),
                str(row["evidence_id"]),
            ),
        )
        result["ledger_sha256"] = _canonical_sha256(_ledger_digest_payload(result))
        validate_canonical_evidence_ledger(result)
        return result

    stt_rows = _canonical_stt_rows(chunks or [])
    chat_rows = _canonical_chat_rows(chats or [])
    if _canonical_source_revision("stt", stt_rows) != sources["stt"]["source_revision"]:
        raise ValueError("stt source revision does not match canonical ledger")
    if (
        _canonical_source_revision("replay_chat", chat_rows)
        != sources["replay_chat"]["source_revision"]
    ):
        raise ValueError("replay chat source revision does not match canonical ledger")

    selected_stt = [row for row in stt_rows if start <= int(row["start_sec"]) <= end]
    selected_chat = [
        row for row in chat_rows if start <= int(row["ms"]) // 1000 <= end
    ]
    if len(selected_stt) + len(selected_chat) > max(1, int(max_records)):
        raise ValueError("canonical evidence window exceeds record limit")

    records: list[dict[str, Any]] = []
    for source_type, rows, revision in (
        ("subtitle", selected_stt, sources["stt"]["source_revision"]),
        ("chat", selected_chat, sources["replay_chat"]["source_revision"]),
    ):
        for row in rows:
            manifest = build_timeline_source_evidence(
                video_id=str(ledger.get("video_no") or ""),
                duration_sec=None,
                subtitle_cues=[row] if source_type == "subtitle" else [],
                chat_records=[row] if source_type == "chat" else [],
            )
            item = next(iter(manifest.get("items") or []), None)
            if not item:
                continue
            canonical_type = "stt" if source_type == "subtitle" else "replay_chat"
            source_identity = str(item.get("source_identity") or "")
            exact_source_ref = f"{canonical_type}:{revision[:16]}:{source_identity}"
            evidence_id = "evidence-" + _canonical_sha256(
                {
                    "source_revision": revision,
                    "source_identity": source_identity,
                }
            )[:24]
            records.append(
                {
                    "evidence_id": evidence_id,
                    "video_no": str(ledger.get("video_no") or ""),
                    "source_type": canonical_type,
                    "canonical_time_sec": int(float(item.get("start_sec") or 0)),
                    "source_end_sec": int(float(item.get("end_sec") or item.get("start_sec") or 0)),
                    "source_revision": revision,
                    "exact_source_ref": exact_source_ref,
                    "source_key": source_identity,
                    "source_relation": {},
                    "display_excerpt": str(row.get("text") or row.get("msg") or ""),
                    "availability": "present",
                    "confidence": float(item.get("confidence") or 0.0),
                    "parent_observation_ref": str(parent_observation_ref or ""),
                    "parent_d1_ref": str(parent_d1_ref or ""),
                    "parent_d2_ref": str(parent_d2_ref or ""),
                }
            )
    result = dict(ledger)
    result["records"] = sorted(
        records,
        key=lambda row: (
            int(row["canonical_time_sec"]),
            str(row["source_type"]),
            str(row["evidence_id"]),
        ),
    )
    result["ledger_sha256"] = _canonical_sha256(_ledger_digest_payload(result))
    validate_canonical_evidence_ledger(result)
    return result


def validate_canonical_evidence_ledger(ledger: Mapping[str, Any]) -> None:
    if str(ledger.get("schema_version") or "") != CANONICAL_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unexpected canonical evidence ledger schema")
    expected_digest = _canonical_sha256(_ledger_digest_payload(ledger))
    if str(ledger.get("ledger_sha256") or "") != expected_digest:
        raise ValueError("canonical evidence ledger digest mismatch")
    sources = list(ledger.get("sources") or [])
    if {str(row.get("source_type") or "") for row in sources} != set(
        _CANONICAL_SOURCE_TYPES
    ):
        raise ValueError("canonical evidence source dispositions are incomplete")
    for source in sources:
        if source.get("availability") not in _CANONICAL_EVIDENCE_AVAILABILITY:
            raise ValueError("canonical evidence source availability is invalid")
    seen: set[str] = set()
    required = {
        "evidence_id",
        "video_no",
        "source_type",
        "canonical_time_sec",
        "source_end_sec",
        "source_revision",
        "exact_source_ref",
        "source_key",
        "source_relation",
        "display_excerpt",
        "availability",
        "confidence",
        "parent_observation_ref",
        "parent_d1_ref",
        "parent_d2_ref",
    }
    for row in ledger.get("records") or []:
        if set(row) != required:
            raise ValueError("canonical evidence row has an unexpected contract")
        evidence_id = str(row.get("evidence_id") or "")
        if not evidence_id.startswith("evidence-") or evidence_id in seen:
            raise ValueError("canonical evidence IDs must be unique source-derived IDs")
        if row.get("availability") != "present":
            raise ValueError("semantic evidence records must be present")
        if str(row.get("video_no") or "") != str(ledger.get("video_no") or ""):
            raise ValueError("canonical evidence video identity must match ledger")
        if float(row.get("canonical_time_sec")) < 0:
            raise ValueError("canonical evidence time must be non-negative")
        if float(row.get("source_end_sec")) < float(row.get("canonical_time_sec")):
            raise ValueError("canonical evidence end time precedes start")
        if not str(row.get("source_revision") or ""):
            raise ValueError("canonical evidence source revision is required")
        exact_source_ref = str(row.get("exact_source_ref") or "")
        if not exact_source_ref or re.search(r"(?:[A-Za-z]:[\\/]|/Users/|/home/)", exact_source_ref):
            raise ValueError("canonical evidence locator must be portable")
        if not str(row.get("display_excerpt") or "").strip():
            raise ValueError("canonical evidence excerpt must be source-derived")
        relation = row.get("source_relation")
        if not isinstance(relation, Mapping):
            raise ValueError("canonical evidence source relation must be an object")
        seen.add(evidence_id)
    for row in ledger.get("observation_dispositions") or []:
        if row.get("inspection_status") != "inspected":
            raise ValueError("every observation must have an inspection disposition")


def _availability(value: Any, *, count: int | None = None) -> str:
    if value is None:
        return "missing"
    if count is not None:
        return "present_nonzero" if count > 0 else "present_zero"
    if isinstance(value, (str, bytes, list, tuple, dict, set)):
        return "present_nonzero" if len(value) > 0 else "present_zero"
    return "present_nonzero"


def _entry(
    source_id: str,
    role: str,
    value: Any,
    *,
    count: int | None = None,
    refs: Iterable[str] = (),
    authority: str,
    highlight_authority: str | None = None,
) -> dict[str, Any]:
    if role not in ALLOWED_ROLES:
        raise ValueError(f"unsupported BroadcastMap evidence role: {role}")
    result = {
        "source_id": source_id,
        "stage_role": role,
        "availability": _availability(value, count=count),
        "item_count": int(count if count is not None else len(value or [])),
        "refs": [str(ref) for ref in refs if str(ref).strip()],
        "authority": authority,
    }
    if highlight_authority is not None:
        result["highlight_authority"] = highlight_authority
    return result


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


_CONTEXT_NAMING_ANSWER_LINE = re.compile(
    r"""(?ix)
    ^\s*(?:(?:[-*•>]+|\#{1,6}|[0-9]{1,3}[.)])\s*)?(?:
        \[(?:D[12]|Point|Candidate|포인트|후보|실제\s*목차|주요\s*장면|Point\s*검토창|하이라이트\s*후보\s*구간)\]
        |(?:D[12]|Point|Candidate|포인트|후보)(?=\s|:|[을를은는이가])
        |(?:실제\s*목차|주요\s*장면|하이라이트\s*후보\s*구간)(?=\s|:|$)
    )
    """
)


_CONTEXT_NAMING_INSTRUCTION_LINE = re.compile(
    r"""(?ix)(?:
        ^\s*(?:system|assistant|user|prompt)\s*:
        |\b(?:ignore|must|follow|instruction|prompt|output|generate|write|summarize)\b
        |(?:무시(?:하|해|하고)?|반드시|지시(?:문)?|따르(?:라|세요)?|작성(?:하|해|하세요)|
           출력(?:하|해|하세요)|생성(?:하|해|하세요)|요약(?:하|해|하세요)|사용하세요)
    )"""
)


def _safe_context_naming_text(value: Any) -> str:
    """Keep source layout while removing explicit prompt/answer-shaped lines.

    A timestamp is valid source context, not an answer by itself.  Custody keeps
    previous summaries/corrections out of this argument; this sanitizer removes
    explicit D1/D2/Point/Candidate answers and instructions without flattening
    paragraph boundaries, dividers, or timestamp-following descriptions.
    """

    safe_lines: list[str] = []
    for raw_line in str(value or "").splitlines():
        line = raw_line.rstrip()
        if (
            line.strip()
            and (
                _CONTEXT_NAMING_ANSWER_LINE.match(line)
                or _CONTEXT_NAMING_INSTRUCTION_LINE.search(line)
            )
        ):
            safe_lines.append("")
            continue
        safe_lines.append(line)
    return _bounded_text("\n".join(safe_lines), 3_600)


def _safe_recent_context(context: Mapping[str, Any] | None) -> str:
    if not isinstance(context, Mapping):
        return ""
    try:
        from .streamer_recent_context import format_recent_context_for_prompt

        return _bounded_text(format_recent_context_for_prompt(dict(context)), 2_400)
    except Exception:
        return ""


def _proposal_rows(
    highlights: list[dict[str, Any]] | None,
    *,
    max_rows: int = 20,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in highlights or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("source_alignment_candidate"):
            continue
        try:
            sec = int(round(float(raw.get("sec"))))
        except (TypeError, ValueError):
            continue
        rows.append({
            "time_sec": max(0, sec),
            "rank": int(raw.get("rank") or 0),
            "signal_type": str(raw.get("signal_type") or "chat_reaction"),
            "source_signals": sorted(
                str(value) for value in (raw.get("source_signals") or ["chat"]) if value
            ),
            "composite": round(float(raw.get("composite") or 0.0), 4),
            "producer_run_ref": str(raw.get("producer_run_ref") or ""),
        })
        if len(rows) >= max_rows:
            break
    return rows


def _provenance_ref(source_id: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {"source_id": source_id, "availability": "missing", "sha256": ""}
    return {
        "source_id": source_id,
        "availability": "present_nonzero" if payload else "present_zero",
        "schema_version": str(payload.get("schema_version") or ""),
        "sha256": _canonical_sha256(payload),
    }


def build_broadcast_map_evidence_bundle(
    *,
    video_no: str,
    chunks: list[dict[str, Any]],
    chats: list[dict[str, Any]],
    timeline_comment_context: Mapping[str, Any] | None,
    context_doc_text: str | None,
    lexicon_terms: list[str] | None,
    streamer_recent_context: Mapping[str, Any] | None,
    community_posts: list[Any] | None,
    signal_highlights: list[dict[str, Any]] | None,
    audio_reaction_metadata: Mapping[str, Any] | None,
    viewer_clips: list[Any] | None,
    timed_evidence_manifest: Mapping[str, Any] | None,
    pre_generation_evidence_pack: Mapping[str, Any] | None,
    local_vector_index: Mapping[str, Any] | None,
    keyframe_manifest_present: bool,
) -> dict[str, Any]:
    """Build the sole pre-activation evidence ledger and safe prompt projection."""

    timeline_anchors = list((timeline_comment_context or {}).get("anchors") or [])
    lexicon = [str(value).strip() for value in (lexicon_terms or []) if str(value).strip()]
    proposals = _proposal_rows(signal_highlights)
    audio_points = []
    if isinstance(audio_reaction_metadata, Mapping):
        audio_points = list(
            audio_reaction_metadata.get("audio_reaction_peaks")
            or audio_reaction_metadata.get("reaction_peaks")
            or []
        )
    source_ledger = [
        _entry("raw_stt_chunks", "primary_semantic", chunks, count=len(chunks), refs=("summary_chunks",), authority="may establish semantic events and sustained activity"),
        _entry("raw_replay_chat", "primary_semantic", chats, count=len(chats), refs=("chat_log",), authority="may establish semantic events; aggregate reaction remains support-only"),
        _entry("bounded_chzzk_timetable", "primary_semantic", timeline_anchors, count=len(timeline_anchors), refs=(str((timeline_comment_context or {}).get("schema_version") or "timeline_comment_context.v1"),), authority="selected time-coded comment relationships may establish navigation structure or semantic events; real conflicts stay uncertain, but literal STT/replay-chat wording matches are not required"),
        _entry("context_doc", "context_naming", context_doc_text, refs=("context_doc_snapshot",), authority="naming/background only; cannot create or move structure"),
        _entry("lexicon", "context_naming", lexicon, count=len(lexicon), refs=("summary_lexicon_snapshot",), authority="spelling and naming only"),
        _entry("streamer_recent_context", "context_naming", streamer_recent_context, count=int((streamer_recent_context or {}).get("item_count") or 0), refs=(str((streamer_recent_context or {}).get("schema_version") or ""),), authority="public naming/background only"),
        _entry("community_to_srt", "context_naming", community_posts, count=len(community_posts or []), refs=("broadcast_time_filtered_community",), authority="naming/background only unless independently present in STT/chat"),
        _entry("chat_audio_visual_clip_support", "support_signal", audio_reaction_metadata, count=len(audio_points) + len(viewer_clips or []), refs=("cached_support_package",), authority="may corroborate or prioritize; cannot create or move D1/D2/Point, set the Story clock, force Highlight identity/count, or set source-span boundaries", highlight_authority="none"),
        _entry("signal_highlights", "candidate_proposal", proposals, count=len(proposals), refs=tuple(sorted({str(row.get("producer_run_ref") or "") for row in proposals})), authority="Point attention proposal only; requires semantic event corroboration"),
        _entry("timed_evidence_pack", "provenance_only", timed_evidence_manifest, refs=("timed_evidence_manifest",), authority="retrieval and lineage only"),
        _entry("pre_generation_pack", "provenance_only", pre_generation_evidence_pack, refs=("summary_pre_generation_evidence",), authority="retrieval and lineage only"),
        _entry("local_vector_index", "provenance_only", local_vector_index, refs=("summary_local_vector_index",), authority="retrieval only; cannot own structure"),
        _entry("keyframe_multimodal", "support_signal", True if keyframe_manifest_present else None, count=1 if keyframe_manifest_present else 0, refs=("keyframe_manifest_snapshot",) if keyframe_manifest_present else (), authority="visual/OCR support only; cannot create or move structure"),
    ]
    forbidden = [
        {
            "source_id": source_id,
            "stage_role": "forbidden_answer",
            "availability": "quarantined",
            "included": False,
            "authority": "must never enter BroadcastMap generation",
        }
        for source_id in FORBIDDEN_ANSWER_SOURCES
    ]
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "video_no": str(video_no),
        "authority": {
            "structure_owner": "pipeline.manager_outline.finalize_manager_outline",
            "official_clock_owner": "pipeline.manager_outline.finalize_manager_outline",
            "saved_revision_owner": "pipeline.broadcast_map_authority.resolve_authoritative_broadcast_map",
            "all_other_roles": "support/proposal/provenance/projection only",
        },
        "source_ledger": source_ledger,
        "canonical_evidence_ledger": build_canonical_evidence_ledger(
            video_no=str(video_no),
            chunks=chunks,
            chats=chats,
            timeline_comment_context=timeline_comment_context,
            audio_reaction_metadata=audio_reaction_metadata,
            viewer_clips=viewer_clips,
            keyframe_manifest_present=keyframe_manifest_present,
        ),
        "context_naming": {
            "context_doc": _safe_context_naming_text(context_doc_text),
            "lexicon_terms": lexicon[:60],
            "recent_public_context": _safe_recent_context(streamer_recent_context),
        },
        "candidate_proposals": proposals,
        "provenance_refs": [
            _provenance_ref("timed_evidence_manifest", timed_evidence_manifest),
            _provenance_ref("pre_generation_evidence_pack", pre_generation_evidence_pack),
            _provenance_ref("local_vector_index", local_vector_index),
        ],
        "forbidden_answer_guard": forbidden,
    }
    body["bundle_sha256"] = _canonical_sha256(body)
    validate_broadcast_map_evidence_bundle(body)
    return body


def validate_broadcast_map_evidence_bundle(bundle: Mapping[str, Any]) -> None:
    if str(bundle.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError("unexpected BroadcastMap evidence schema")
    if not str(bundle.get("bundle_sha256") or ""):
        raise ValueError("BroadcastMap evidence bundle is missing its digest")
    for row in bundle.get("source_ledger") or []:
        if str(row.get("stage_role") or "") not in ALLOWED_ROLES:
            raise ValueError("BroadcastMap evidence ledger contains an unknown role")
    validate_canonical_evidence_ledger(bundle.get("canonical_evidence_ledger") or [])
    guards = {str(row.get("source_id") or ""): row for row in bundle.get("forbidden_answer_guard") or []}
    for source_id in FORBIDDEN_ANSWER_SOURCES:
        row = guards.get(source_id)
        if not row or row.get("included") is not False or "payload" in row:
            raise ValueError(f"forbidden answer source was not quarantined: {source_id}")


def project_broadcast_map_evidence_for_prompt(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded, stage-qualified view permitted in manager prompts."""

    validate_broadcast_map_evidence_bundle(bundle)
    return {
        "schema_version": PROMPT_SCHEMA_VERSION,
        "bundle_sha256": str(bundle.get("bundle_sha256") or ""),
        "authority": dict(bundle.get("authority") or {}),
        "source_ledger": list(bundle.get("source_ledger") or []),
        "context_naming": dict(bundle.get("context_naming") or {}),
        "candidate_proposals": list(bundle.get("candidate_proposals") or []),
        "provenance_refs": list(bundle.get("provenance_refs") or []),
        "forbidden_answer_guard": list(bundle.get("forbidden_answer_guard") or []),
    }


def _hms_to_sec(value: str) -> int:
    parts = [int(part) for part in str(value).split(":")]
    if len(parts) != 3:
        raise ValueError(f"invalid HH:MM:SS value: {value}")
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _sec_to_hms(value: int) -> str:
    value = max(0, int(value))
    return f"{value // 3600:02d}:{(value % 3600) // 60:02d}:{value % 60:02d}"


_TIMED_STT_LINE = re.compile(r"^\s*\[(\d{2}:\d{2}:\d{2})\]\s+\S")


def _timed_stt_rows(chunks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return raw-free, exact-clock STT references from already-loaded chunks."""

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for chunk in chunks:
        chunk_ref = f"chunk:{int(chunk.get('index') or 0):02d}"
        for line in str(chunk.get("text") or "").splitlines():
            match = _TIMED_STT_LINE.match(line)
            if not match:
                continue
            sec = _hms_to_sec(match.group(1))
            if sec in seen:
                continue
            seen.add(sec)
            rows.append({"sec": sec, "ref": f"stt-sec:{sec}", "chunk_ref": chunk_ref})
    return sorted(rows, key=lambda row: int(row["sec"]))


def _timed_chat_rows(chats: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return raw-free replay-chat clock references; never copy message text."""

    rows: list[dict[str, Any]] = []
    for chat in chats:
        if not str(chat.get("msg") or "").strip():
            continue
        ms = max(0, int(chat.get("ms") or 0))
        rows.append({"sec": ms // 1000, "ref": f"chat-ms:{ms}"})
    return sorted(rows, key=lambda row: (int(row["sec"]), str(row["ref"])))


def _phase_row(rows: list[dict[str, Any]], *, basis: str) -> dict[str, Any]:
    secs = [int(row["sec"]) for row in rows]
    return {
        "start": _sec_to_hms(min(secs)),
        "end": _sec_to_hms(max(secs)),
        "refs": [str(row["ref"]) for row in rows],
        "basis": basis,
    }


def _fallback_geometry(
    point: Mapping[str, Any],
    *,
    duration_sec: int,
    container: Mapping[str, Any] | None,
) -> dict[str, Any]:
    sec = int(point.get("source_sec") or 0)
    lower = max(0, _hms_to_sec(str((container or {}).get("start") or "00:00:00")))
    upper = min(
        max(0, duration_sec),
        _hms_to_sec(str((container or {}).get("end") or _sec_to_hms(duration_sec))),
    )
    return {
        "point_id": str(point["point_id"]),
        "mode": "fixed_point_review_window",
        "classification": "review_only",
        "counts_as_highlight": False,
        "window": [_sec_to_hms(max(lower, sec - 20)), _sec_to_hms(min(upper, sec + 40))],
        "causal_phases": None,
        "setup": None,
        "event": None,
        "reaction": None,
        "payoff": None,
        "fallback_reason": "evidence_window_unavailable",
        "causal_phase_status": "unproven",
        "container_ref": str((container or {}).get("id") or ""),
        "start_boundary_refs": [],
        "end_boundary_refs": [],
        "support_refs": list(point.get("support_refs") or []),
        "proposal_refs": list(point.get("proposal_refs") or []),
    }


def _causal_observation_rows(
    observations: Iterable[str],
    *,
    ref_prefix: str = "causal-observation",
    source_priority: int = 0,
    max_span_sec: int = 240,
) -> list[dict[str, Any]]:
    """Parse raw-free causal retrieval proposals from validated chunk observations."""

    from .outline.schema import CAUSAL_WINDOW_RE

    rows: list[dict[str, Any]] = []
    for observation_index, observation in enumerate(observations, 1):
        for line in str(observation or "").splitlines():
            match = CAUSAL_WINDOW_RE.fullmatch(line)
            if not match:
                continue
            phases = {
                name: _hms_to_sec(match.group(name))
                for name in ("setup", "event", "reaction", "payoff")
            }
            if list(phases.values()) != sorted(phases.values()):
                continue
            if phases["payoff"] - phases["setup"] > max(1, int(max_span_sec)):
                continue
            rows.append({
                **phases,
                "source_priority": int(source_priority),
                "ref": (
                    f"{ref_prefix}:{observation_index}:"
                    + hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]
                ),
            })
    return rows


def _rows_near(
    rows: list[dict[str, Any]],
    sec: int,
    *,
    tolerance: int,
    limit: int = 4,
) -> list[dict[str, Any]]:
    return sorted(
        (row for row in rows if abs(int(row["sec"]) - sec) <= tolerance),
        key=lambda row: (abs(int(row["sec"]) - sec), int(row["sec"]), str(row["ref"])),
    )[:limit]


def _semantic_causal_geometry(
    point: Mapping[str, Any],
    *,
    duration_sec: int,
    container: Mapping[str, Any] | None,
    stt_rows: list[dict[str, Any]],
    chat_rows: list[dict[str, Any]],
    causal_rows: list[dict[str, Any]],
    accepted_point_secs: set[int] | None = None,
) -> dict[str, Any] | None:
    """Accept a causal proposal only after every phase rebinds to primary evidence."""

    if not container or str(container.get("level") or "") != "D2":
        return None
    point_sec = int(point.get("source_sec") or 0)
    point_secs = accepted_point_secs or {point_sec}
    lower = max(0, _hms_to_sec(str(container.get("start") or "00:00:00")))
    upper = min(
        max(0, duration_sec),
        _hms_to_sec(str(container.get("end") or _sec_to_hms(duration_sec))),
    )
    candidates = [
        row for row in causal_rows
        if (
            abs(int(row["setup"]) - point_sec) <= 15
            or (
                not any(
                    abs(int(row["setup"]) - accepted_sec) <= 15
                    for accepted_sec in point_secs
                )
                and abs(int(row["event"]) - point_sec) <= 15
            )
        )
        and lower <= int(row["setup"])
        and int(row["payoff"]) <= upper
    ]
    for proposal in sorted(
        candidates,
        key=lambda row: (
            abs(int(row["setup"]) - point_sec),
            -int(row.get("source_priority") or 0),
            str(row["ref"]),
        ),
    ):
        setup = _rows_near(stt_rows, int(proposal["setup"]), tolerance=10)
        event = _rows_near(stt_rows, int(proposal["event"]), tolerance=8)
        reaction_stt = _rows_near(stt_rows, int(proposal["reaction"]), tolerance=10)
        reaction_chat = _rows_near(chat_rows, int(proposal["reaction"]), tolerance=15, limit=8)
        payoff = _rows_near(stt_rows, int(proposal["payoff"]), tolerance=10)
        if not (setup and event and reaction_stt and reaction_chat and payoff):
            continue
        reaction = sorted(
            reaction_stt + reaction_chat,
            key=lambda row: (int(row["sec"]), str(row["ref"])),
        )
        phases = {
            "setup": _phase_row(setup, basis="chunk semantic proposal rebound to timestamped STT"),
            "event": _phase_row(event, basis="proposal event rebound to timestamped STT; Point owns context start"),
            "reaction": _phase_row(reaction, basis="chunk semantic proposal rebound to broadcaster STT and replay chat"),
            "payoff": _phase_row(payoff, basis="chunk semantic proposal rebound to later timestamped STT"),
        }
        return {
            "point_id": str(point["point_id"]),
            "mode": "semantic_causal_window_shadow",
            "classification": "review_only",
            "counts_as_highlight": False,
            "window": [
                _sec_to_hms(max(lower, int(proposal["setup"]) - 2)),
                _sec_to_hms(min(upper, int(proposal["payoff"]) + 3)),
            ],
            "causal_phases": phases,
            **phases,
            "fallback_reason": None,
            "causal_phase_status": "primary_semantic_corroborated",
            "causal_observation_ref": str(proposal["ref"]),
            "container_ref": str(container.get("id") or ""),
            "start_boundary_refs": list(phases["setup"]["refs"]),
            "end_boundary_refs": list(phases["payoff"]["refs"]),
            "support_refs": list(point.get("support_refs") or []),
            "proposal_refs": list(point.get("proposal_refs") or []),
        }
    return None


def _contextual_geometry(
    point: Mapping[str, Any],
    *,
    duration_sec: int,
    container: Mapping[str, Any] | None,
    stt_rows: list[dict[str, Any]],
    chat_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Propose an evidence-bounded edit window, or retain the honest fallback.

    The accepted Point supplies the context-start semantics. Geometry may use
    only source-clock evidence: its containing D2, real gaps between timestamped
    STT turns, post-event broadcaster speech, replay-chat reaction, and later STT.
    Relative position alone is not called semantic proof; the resulting phase
    rows remain a shadow that parent review must compare with the source text.
    """

    sec = int(point.get("source_sec") or 0)
    lower = max(0, _hms_to_sec(str((container or {}).get("start") or "00:00:00")))
    upper = min(
        max(0, duration_sec),
        _hms_to_sec(str((container or {}).get("end") or _sec_to_hms(duration_sec))),
    )
    if not container or str(container.get("level") or "") != "D2":
        return _fallback_geometry(point, duration_sec=duration_sec, container=container)

    pre_rows = [
        row for row in stt_rows
        if max(lower, sec - 120) <= int(row["sec"]) <= sec - 5
    ]
    event = sorted(
        (row for row in stt_rows if abs(int(row["sec"]) - sec) <= 8),
        key=lambda row: (abs(int(row["sec"]) - sec), int(row["sec"])),
    )[:3]
    reaction_stt = [
        row for row in stt_rows
        if sec + 1 <= int(row["sec"]) <= min(upper, sec + 30)
    ][:4]
    reaction_chat = [
        row for row in chat_rows
        if sec <= int(row["sec"]) <= min(upper, sec + 45)
    ][:8]
    post_rows = [
        row for row in stt_rows
        if sec + 20 <= int(row["sec"]) <= min(upper, sec + 120)
    ]
    if not (pre_rows and event and reaction_stt and reaction_chat and post_rows):
        return _fallback_geometry(point, duration_sec=duration_sec, container=container)

    start = None
    start_boundary_refs: list[str] = []
    for left, right in zip(pre_rows, pre_rows[1:]):
        if int(right["sec"]) - int(left["sec"]) >= 12:
            start = max(lower, int(right["sec"]) - 2)
            start_boundary_refs = [str(left["ref"]), str(right["ref"])]
    if start is None and lower >= sec - 120:
        start = lower
        start_boundary_refs = [f"d2-boundary:{str(container.get('id') or '')}:start"]
    if start is None:
        return _fallback_geometry(point, duration_sec=duration_sec, container=container)

    end = None
    end_boundary_refs: list[str] = []
    payoff: list[dict[str, Any]] = []
    post_scan = [
        row for row in stt_rows
        if sec + 1 <= int(row["sec"]) <= min(upper, sec + 120)
    ]
    for left, right in zip(post_scan, post_scan[1:]):
        if int(left["sec"]) < sec + 20:
            continue
        if int(right["sec"]) - int(left["sec"]) >= 12:
            end = min(upper, int(left["sec"]) + 3)
            end_boundary_refs = [str(left["ref"]), str(right["ref"])]
            payoff = [row for row in post_rows if int(row["sec"]) <= int(left["sec"])][-4:]
            break
    if end is None and upper <= sec + 120:
        end = upper
        end_boundary_refs = [f"d2-boundary:{str(container.get('id') or '')}:end"]
        payoff = post_rows[-4:]
    if end is None or not payoff:
        return _fallback_geometry(point, duration_sec=duration_sec, container=container)

    setup = [row for row in pre_rows if int(row["sec"]) >= start][-4:]
    if not setup:
        return _fallback_geometry(point, duration_sec=duration_sec, container=container)
    reaction = sorted(reaction_stt + reaction_chat, key=lambda row: (int(row["sec"]), str(row["ref"])))
    if not (start < sec < end) or end - start > 240:
        return _fallback_geometry(point, duration_sec=duration_sec, container=container)

    phases = {
        "setup": _phase_row(
            setup,
            basis="timestamped STT after a D2/speech-turn start boundary",
        ),
        "event": _phase_row(
            event,
            basis="accepted Point with exact nearby timestamped STT",
        ),
        "reaction": _phase_row(
            reaction,
            basis="post-event timestamped broadcaster speech plus replay chat",
        ),
        "payoff": _phase_row(
            payoff,
            basis="later timestamped STT before a D2/speech-turn end boundary",
        ),
    }
    return {
        "point_id": str(point["point_id"]),
        "mode": "evidence_window_shadow",
        "classification": "review_only",
        "counts_as_highlight": False,
        "window": [_sec_to_hms(start), _sec_to_hms(end)],
        "causal_phases": phases,
        **phases,
        "fallback_reason": None,
        "causal_phase_status": "source_clock_evidence_present_parent_semantic_review_required",
        "container_ref": str((container or {}).get("id") or ""),
        "start_boundary_refs": start_boundary_refs,
        "end_boundary_refs": end_boundary_refs,
        "support_refs": list(point.get("support_refs") or []),
        "proposal_refs": list(point.get("proposal_refs") or []),
    }


def _build_canonical_point_story_input(
    *,
    ranges: list[dict[str, Any]],
    points: list[dict[str, Any]],
    duration_sec: int,
    chunks: list[dict[str, Any]],
    chats: list[dict[str, Any]],
    evidence_bundle: Mapping[str, Any],
    chunk_observations: list[str] | None,
    story_batch_size: int,
) -> dict[str, Any]:
    """Build one evidence-bounded Story packet for every canonical Point."""

    from .story_packet_selector import (
        audit_primary_evidence_dispositions,
        build_story_point_selector_batches,
    )

    canonical_base = evidence_bundle.get("canonical_evidence_ledger") or {}
    d1_rows = [row for row in ranges if row.get("level") == "D1"]
    d2_rows = [row for row in ranges if row.get("level") == "D2"]
    ledger: dict[str, dict[str, Any]] = {}
    packets: list[dict[str, Any]] = []
    claimed_source_refs: set[str] = set()
    seen_point_refs: set[str] = set()

    def nearest(
        rows: list[dict[str, Any]], *, center: int, limit: int,
    ) -> list[dict[str, Any]]:
        chosen = sorted(
            rows,
            key=lambda row: (
                abs(int(row["canonical_time_sec"]) - center),
                int(row["canonical_time_sec"]),
                str(row["evidence_id"]),
            ),
        )[:limit]
        return sorted(
            chosen,
            key=lambda row: (
                int(row["canonical_time_sec"]),
                str(row["source_type"]),
                str(row["evidence_id"]),
            ),
        )

    for order, point in enumerate(points):
        point_ref = str(point.get("id") or "")
        if not point_ref or point_ref in seen_point_refs:
            raise ValueError("canonical Points require unique non-empty IDs")
        seen_point_refs.add(point_ref)
        sec = _hms_to_sec(str(point.get("timestamp") or "00:00:00"))
        d2 = next((
            row for row in sorted(d2_rows, key=lambda r: _hms_to_sec(r["start"]), reverse=True)
            if _hms_to_sec(str(row.get("start") or "00:00:00")) <= sec
            <= _hms_to_sec(str(row.get("end") or _sec_to_hms(duration_sec)))
        ), None)
        d2_ref = str((d2 or {}).get("id") or "")
        d1_ref = str((d2 or {}).get("parent_id") or "")
        if not d1_ref:
            d1 = next((
                row for row in sorted(d1_rows, key=lambda r: _hms_to_sec(r["start"]), reverse=True)
                if _hms_to_sec(str(row.get("start") or "00:00:00")) <= sec
                <= _hms_to_sec(str(row.get("end") or _sec_to_hms(duration_sec)))
            ), None)
            d1_ref = str((d1 or {}).get("id") or "")
        start = max(
            0,
            _hms_to_sec(str((d2 or {}).get("start") or "00:00:00")),
            sec - 90,
        )
        end = min(
            max(0, int(duration_sec)),
            _hms_to_sec(
                str((d2 or {}).get("end") or _sec_to_hms(duration_sec))
            ),
            sec + 120,
        )
        if end <= start:
            end = min(max(0, int(duration_sec)), start + 1)
        local = materialize_canonical_evidence_window(
            canonical_base,
            chunks=chunks,
            chats=chats,
            start_sec=start,
            end_sec=end,
            parent_observation_ref=f"canonical-point:{point_ref}",
            parent_d1_ref=d1_ref,
            parent_d2_ref=d2_ref,
        )
        records = [
            row for row in local.get("records") or []
            if row.get("source_type") in {"stt", "replay_chat"}
            and str(row.get("evidence_id") or "") not in claimed_source_refs
        ]
        selected_records = sorted(
            nearest(
                [row for row in records if row.get("source_type") == "stt"],
                center=sec,
                limit=18,
            )
            + nearest(
                [row for row in records if row.get("source_type") == "replay_chat"],
                center=sec,
                limit=10,
            ),
            key=lambda row: (
                int(row["canonical_time_sec"]),
                str(row["source_type"]),
                str(row["evidence_id"]),
            ),
        )
        evidence_ids: list[str] = []
        for row in selected_records:
            evidence_id = str(row["evidence_id"])
            claimed_source_refs.add(evidence_id)
            evidence_ids.append(evidence_id)
            source_type = str(row["source_type"])
            ledger[evidence_id] = {
                "evidence_id": evidence_id,
                "role": "primary_semantic",
                "point_start_eligible": False,
                "source_type": source_type,
                "time_sec": int(row["canonical_time_sec"]),
                "source_text": str(row["display_excerpt"]),
                "exact_source_ref": str(row["exact_source_ref"]),
            }
        point_evidence_id = f"broadcast-map-point:{point_ref}"
        point_text = " — ".join(
            value for value in (
                str(point.get("title") or "").strip(),
                str(point.get("content") or "").strip(),
            ) if value
        ) or point_ref
        ledger[point_evidence_id] = {
            "evidence_id": point_evidence_id,
            "role": "primary_semantic",
            "point_start_eligible": False,
            "source_type": "broadcast_map_point",
            "time_sec": sec,
            "source_text": point_text,
            "exact_source_ref": f"broadcast_map:point:{point_ref}",
        }
        evidence_ids.append(point_evidence_id)
        body: dict[str, Any] = {
            "point_ref": point_ref,
            "point_order": order,
            "point_title": str(point.get("title") or ""),
            "point_content": str(point.get("content") or ""),
            "d1_ref": d1_ref,
            "d2_ref": d2_ref,
            "evidence_ids": evidence_ids,
        }
        packet_id = "story-packet-" + _canonical_sha256(body)[:24]
        body["story_packet_id"] = packet_id
        body["primary_dispositions"] = [{
            "evidence_id": evidence_id,
            "disposition": "included_primary",
            "owner_packet_id": packet_id,
        } for evidence_id in evidence_ids]
        packets.append(body)

    audit = audit_primary_evidence_dispositions(
        packets=packets, evidence_ledger=ledger,
    ) if packets else {
        "owned_primary_count": 0,
        "overlap_context_count": 0,
        "explicit_exclusion_count": 0,
        "silent_drop_count": 0,
        "owner_by_evidence_id": {},
        "explicit_exclusions": {},
    }
    request_error = None
    try:
        batches = build_story_point_selector_batches(
            packets=packets,
            evidence_ledger=ledger,
            max_packets_per_batch=story_batch_size,
        ) if packets else []
    except ValueError as exc:
        batches = []
        request_error = str(exc)
    return {
        "story_packets": packets,
        "story_evidence_ledger": ledger,
        "story_observation_dispositions": [{
            "observation_ref": f"observation-{index:03d}",
            "disposition": "candidate_lane_only_not_story_authority",
            "packet_ids": [],
        } for index, _ in enumerate(chunk_observations or [], 1)],
        "story_primary_evidence_audit": audit,
        "story_point_selector_batches": batches,
        "story_point_selector_request": (
            str(batches[0]["request"]) if len(batches) == 1 else ""
        ),
        "story_point_selector_request_error": request_error,
    }


def build_point_candidate_shadow_receipt(
    *,
    outline_text: str,
    duration_sec: int,
    chunks: list[dict[str, Any]],
    chats: list[dict[str, Any]],
    timeline_comment_context: Mapping[str, Any] | None,
    evidence_bundle: Mapping[str, Any],
    chunk_observations: list[str] | None = None,
    max_candidates: int = 5,
    story_batch_size: int = 5,
) -> dict[str, Any]:
    """Explain accepted Points and compare P3S selection with P3G geometry.

    The review-candidate comparison remains the current first-five/-20/+40 fallback.
    Story packets cover every canonical Point in bounded batches, while candidate
    ranking and geometry stay separate so they cannot acquire Point authority.
    """

    from .manager_outline import parse_manager_outline

    outline = parse_manager_outline(outline_text, duration_sec=duration_sec or None)
    ranges = list(outline.get("ranges") or [])
    points = list(outline.get("points") or [])
    proposals = list(evidence_bundle.get("candidate_proposals") or [])
    anchors = list((timeline_comment_context or {}).get("anchors") or [])
    stt_rows = _timed_stt_rows(row for row in chunks if isinstance(row, Mapping))
    chat_rows = _timed_chat_rows(row for row in chats if isinstance(row, Mapping))
    causal_rows = _causal_observation_rows(chunk_observations or [])
    causal_rows.extend(
        _causal_observation_rows(
            [outline_text],
            ref_prefix="final-causal-proposal",
            source_priority=1,
            max_span_sec=600,
        )
    )

    point_rows: list[dict[str, Any]] = []
    for order, point in enumerate(points):
        sec = _hms_to_sec(str(point.get("timestamp") or "00:00:00"))
        containing = [
            row for row in ranges
            if _hms_to_sec(str(row.get("start") or "00:00:00")) <= sec
            <= _hms_to_sec(str(row.get("end") or "00:00:00"))
        ]
        containing.sort(key=lambda r: _hms_to_sec(r["start"]), reverse=True)
        d1 = next((row for row in containing if row.get("level") == "D1"), None)
        d2 = next((row for row in containing if row.get("level") == "D2"), None)
        nearest_proposal = min(
            proposals,
            key=lambda row: abs(int(row.get("time_sec") or 0) - sec),
            default=None,
        )
        proposal_distance = (
            abs(int(nearest_proposal.get("time_sec") or 0) - sec)
            if nearest_proposal
            else None
        )
        chunk_refs = [
            f"chunk:{int(row.get('index') or 0):02d}"
            for row in chunks
            if int(row.get("start_ms") or 0) <= sec * 1000 <= int(row.get("end_ms") or 0)
        ]
        exact_stt = sorted(
            (row for row in stt_rows if abs(int(row["sec"]) - sec) <= 8),
            key=lambda row: (abs(int(row["sec"]) - sec), int(row["sec"])),
        )[:3]
        exact_chat = [row for row in chat_rows if abs(int(row["sec"]) - sec) <= 30][:5]
        stt_refs = [str(row["ref"]) for row in exact_stt]
        chat_refs = [str(row["ref"]) for row in exact_chat]
        anchor_refs = [
            f"timetable:{int(row.get('time_sec') or 0)}:{str(row.get('lane') or 'chapter_timetable')}"
            for row in anchors
            if isinstance(row, dict)
            and abs(int(row.get("time_sec") or 0) - sec) <= 60
        ][:5]
        proposal_refs = []
        if nearest_proposal is not None and proposal_distance is not None and proposal_distance <= 90:
            proposal_refs.append(
                "proposal:"
                + str(nearest_proposal.get("producer_run_ref") or "unscoped")
                + f":{int(nearest_proposal.get('time_sec') or 0)}"
            )
        proposal_score = (
            round(float(nearest_proposal.get("composite") or 0.0), 4)
            if proposal_refs
            else 0.0
        )
        score_components = {
            "exact_stt": len(stt_refs),
            "replay_chat": len(chat_refs),
            "timeline_support": len(anchor_refs),
            "proposal_score": proposal_score,
        }
        point_rows.append({
            "point_id": str(point.get("id") or f"point-{order}"),
            "order": order,
            "source_sec": sec,
            "timestamp": str(point.get("timestamp") or ""),
            "title": str(point.get("title") or ""),
            "d1_ref": str((d1 or {}).get("id") or ""),
            "d2_ref": str((d2 or {}).get("id") or ""),
            "evidence_refs": stt_refs + chat_refs,
            "stt_refs": stt_refs,
            "chat_refs": chat_refs,
            "chunk_provenance_refs": chunk_refs,
            "support_refs": anchor_refs,
            "proposal_refs": proposal_refs,
            "proposal_score": proposal_score,
            "selection_score_components": score_components,
            "selection_score": round(
                min(5, len(chat_refs)) * 0.2
                + min(2, len(anchor_refs)) * 0.35
                + min(1.0, proposal_score) * 3.0,
                4,
            ),
        })

    baseline_selection = point_rows[:max_candidates]
    corroborated_proposals = [
        row
        for row in point_rows
        if row.get("proposal_refs") and row.get("evidence_refs")
    ]
    proposal_hypothesis = sorted(
        corroborated_proposals,
        key=lambda row: (-float(row.get("proposal_score") or 0.0), int(row["order"])),
    )
    proposal_hypothesis.extend(
        row for row in point_rows if row not in corroborated_proposals
    )
    proposal_hypothesis = proposal_hypothesis[:max_candidates]

    geometry_by_point: dict[str, dict[str, Any]] = {}
    accepted_point_secs = {int(row["source_sec"]) for row in point_rows}
    for point in point_rows:
        container = next(
            (row for row in ranges if row.get("id") == point.get("d2_ref")),
            None,
        )
        semantic_geometry = _semantic_causal_geometry(
            point,
            duration_sec=duration_sec,
            container=container,
            stt_rows=stt_rows,
            chat_rows=chat_rows,
            causal_rows=causal_rows,
            accepted_point_secs=accepted_point_secs,
        )
        geometry_by_point[str(point["point_id"])] = (
            semantic_geometry
            if semantic_geometry is not None
            else _contextual_geometry(
                point,
                duration_sec=duration_sec,
                container=container,
                stt_rows=stt_rows,
                chat_rows=chat_rows,
            )
        )

    evidence_eligible = [
        row
        for row in point_rows
        if row.get("d1_ref")
        and row.get("d2_ref")
        and row.get("stt_refs")
        and row.get("chat_refs")
        and (
            row.get("support_refs")
            or row.get("proposal_refs")
            or len(row.get("chat_refs") or []) >= 3
        )
    ]
    evidence_ranked = sorted(
        evidence_eligible,
        key=lambda row: (-float(row.get("selection_score") or 0.0), int(row["order"])),
    )
    diverse_ranked: list[dict[str, Any]] = []
    covered_d2: set[tuple[str, str]] = set()
    for row in evidence_ranked:
        coverage_key = (str(row.get("d1_ref") or ""), str(row.get("d2_ref") or ""))
        if coverage_key in covered_d2:
            continue
        diverse_ranked.append(row)
        covered_d2.add(coverage_key)
    diverse_ranked.extend(row for row in evidence_ranked if row not in diverse_ranked)

    target_count = min(max_candidates, len(point_rows))
    if target_count > 0 and target_count == len(point_rows):
        selected_points = list(point_rows)
        selection_mode = "all_accepted_points"
        selection_fallback = None
    elif target_count > 0 and len(evidence_eligible) >= target_count:
        selected_points = diverse_ranked[:target_count]
        selection_mode = "evidence_ranked_shadow"
        selection_fallback = None
    else:
        selected_points = baseline_selection
        selection_mode = "fixed_fallback"
        selection_fallback = "selection_evidence_unproven"
    geometry_rows = [geometry_by_point[str(point["point_id"])] for point in selected_points]
    try:
        story_shadow_input = _build_canonical_point_story_input(
            ranges=ranges,
            points=points,
            duration_sec=duration_sec,
            chunks=chunks,
            chats=chats,
            evidence_bundle=evidence_bundle,
            chunk_observations=chunk_observations,
            story_batch_size=story_batch_size,
        )
    except ValueError as exc:
        story_shadow_input = {
            "story_packets": [],
            "story_evidence_ledger": {},
            "story_observation_dispositions": [],
            "story_primary_evidence_audit": {"silent_drop_count": 0},
            "story_point_selector_batches": [],
            "story_point_selector_request": "",
            "story_point_selector_request_error": str(exc),
        }

    receipt = {
        "schema_version": "chzz.broadcast_map_point_review_shadow.v2",
        "authority": {
            "point_owner": "pipeline.manager_outline.finalize_manager_outline",
            "selection_shadow": "P3S",
            "geometry_shadow": "P3G",
            "production_default": (
                "all canonical Points receive one Story in bounded batches; "
                "the first-five limit applies only to the separate review-candidate comparison"
            ),
            "highlight_authority": "none; this artifact cannot produce or pass an editorial Highlight",
            "adoption_status": "shadow_only",
        },
        "broadcast_map_sha256": hashlib.sha256(outline_text.encode("utf-8")).hexdigest(),
        "evidence_bundle_sha256": str(evidence_bundle.get("bundle_sha256") or ""),
        "points": point_rows,
        "causal_observation_count": len(causal_rows),
        "selection": {
            "comparison_window_mode": "fixed_fallback_for_P3S_only",
            "baseline_point_ids": [row["point_id"] for row in baseline_selection],
            "proposal_ranked_point_ids": [row["point_id"] for row in proposal_hypothesis],
            "evidence_ranked_point_ids": [row["point_id"] for row in diverse_ranked],
            "evidence_eligible_point_ids": [row["point_id"] for row in evidence_eligible],
            "proposal_eligible_point_ids": [
                row["point_id"] for row in corroborated_proposals
            ],
            "selected_point_ids": [row["point_id"] for row in selected_points],
            "mode": selection_mode,
            "fallback_reason": selection_fallback,
            "selection_evidence": {
                row["point_id"]: {
                    "score": row["selection_score"],
                    "components": dict(row["selection_score_components"]),
                    "primary_refs": list(row["evidence_refs"]),
                    "coverage": {"d1_ref": row["d1_ref"], "d2_ref": row["d2_ref"]},
                }
                for row in selected_points
            },
            "same_selection": [row["point_id"] for row in baseline_selection]
            == [row["point_id"] for row in selected_points],
        },
        "geometry_selection_frozen_point_ids": [
            row["point_id"] for row in selected_points
        ],
        **story_shadow_input,
        "point_review_windows": geometry_rows,
        "geometry": geometry_rows,
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return receipt


def build_highlight_local_evidence_packet(
    *,
    story_seed: Mapping[str, Any],
    chunks: list[dict[str, Any]],
    chats: list[dict[str, Any]],
    evidence_bundle: Mapping[str, Any],
    max_stt_rows: int = 160,
    max_chat_rows: int = 200,
) -> dict[str, Any]:
    """Retrieve source rows inside one or more frozen, caller-owned ranges.

    The packet may contain raw STT/chat for the private model request.  Callers
    must never place the packet itself on Kanban or in public evidence; the
    returned digest and counts are the raw-free receipt surface.  Retrieval is
    read-only: neither a broad discovery scope nor later bounded ranges gain
    Point, Story, Highlight, or clock authority here.
    """

    source_ranges = list(story_seed.get("source_ranges") or [])

    def in_scope(sec: int) -> bool:
        return any(
            int(row.get("start_sec") or 0) <= sec <= int(row.get("end_sec") or 0)
            for row in source_ranges
            if isinstance(row, Mapping)
        )

    stt_rows: list[dict[str, Any]] = []
    for chunk in chunks:
        for line in str(chunk.get("text") or "").splitlines():
            match = re.match(r"^\s*\[(\d{2}:\d{2}:\d{2})\]\s*(\S.*)$", line)
            if not match:
                continue
            sec = _hms_to_sec(match.group(1))
            if not in_scope(sec):
                continue
            stt_rows.append(
                {"ref": f"stt-sec:{sec}", "time_sec": sec, "text": match.group(2)}
            )
    all_stt_rows = list(stt_rows)
    def stratified(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        """Keep boundaries and Point neighborhoods instead of the first N rows."""

        if limit <= 0 or not rows:
            return []
        ordered = sorted(rows, key=lambda row: (int(row["time_sec"]), str(row["ref"])))
        if len(ordered) <= limit:
            return ordered
        anchors = [
            int(raw.get(key) or 0)
            for raw in source_ranges
            if isinstance(raw, Mapping)
            for key in ("start_sec", "end_sec")
        ]
        anchors.extend(
            int(row.get("time_sec") or 0)
            for row in story_seed.get("point_anchors") or []
            if isinstance(row, Mapping)
        )
        anchors.extend(
            int(row.get("time_sec") or 0)
            for row in story_seed.get("semantic_anchors") or []
            if isinstance(row, Mapping)
        )
        chosen: set[int] = {0, len(ordered) - 1}
        nearest_indexes: list[int] = []
        for anchor in anchors:
            nearest = min(
                range(len(ordered)),
                key=lambda index: abs(int(ordered[index]["time_sec"]) - anchor),
            )
            nearest_indexes.append(nearest)
            if len(chosen) < limit:
                chosen.add(nearest)
        for distance in range(1, 4):
            for nearest in nearest_indexes:
                for index in (nearest - distance, nearest + distance):
                    if 0 <= index < len(ordered) and len(chosen) < limit:
                        chosen.add(index)
        slots = max(1, limit - len(chosen))
        for slot in range(slots):
            if len(chosen) >= limit:
                break
            index = round(slot * (len(ordered) - 1) / max(1, slots - 1))
            chosen.add(index)
        if len(chosen) < limit:
            for index in range(len(ordered)):
                chosen.add(index)
                if len(chosen) >= limit:
                    break
        return [ordered[index] for index in sorted(chosen)[:limit]]

    stt_rows = stratified(stt_rows, max_stt_rows)
    chat_rows: list[dict[str, Any]] = []
    for row in chats:
        text = str(row.get("msg") or "").strip()
        if not text:
            continue
        ms = max(0, int(row.get("ms") or 0))
        if not in_scope(ms // 1000):
            continue
        chat_rows.append({"ref": f"chat-ms:{ms}", "time_sec": ms // 1000, "text": text})
    all_chat_rows = list(chat_rows)
    chat_rows = stratified(chat_rows, max_chat_rows)

    coverage_ranges = [
        row
        for row in story_seed.get("coverage_ranges") or []
        if isinstance(row, Mapping)
        and int(row.get("end_sec") or 0) > int(row.get("start_sec") or 0)
    ]
    d2_ranges = sorted(
        (row for row in coverage_ranges if str(row.get("level") or "") == "D2"),
        key=lambda row: (int(row.get("start_sec") or 0), int(row.get("end_sec") or 0)),
    )
    scan_scopes: list[dict[str, Any]] = []
    for source_index, source_range in enumerate(source_ranges):
        if not isinstance(source_range, Mapping):
            continue
        source_start = int(source_range.get("start_sec") or 0)
        source_end = int(source_range.get("end_sec") or 0)
        cursor = source_start
        overlaps = [
            row for row in d2_ranges
            if int(row.get("end_sec") or 0) > source_start
            and int(row.get("start_sec") or 0) < source_end
        ]
        for row in overlaps:
            batch_start = max(cursor, source_start, int(row.get("start_sec") or 0))
            batch_end = min(source_end, int(row.get("end_sec") or 0))
            if batch_start > cursor:
                scan_scopes.append({
                    "ref": f"source-range-{source_index}-gap-{cursor}",
                    "level": "D1_or_broadcast_remainder",
                    "start_sec": cursor,
                    "end_sec": batch_start,
                })
            if batch_end > batch_start:
                scan_scopes.append({
                    "ref": str(row.get("ref") or f"d2-scan-{batch_start}"),
                    "level": "D2",
                    "start_sec": batch_start,
                    "end_sec": batch_end,
                })
                cursor = max(cursor, batch_end)
        if cursor < source_end:
            scan_scopes.append({
                "ref": f"source-range-{source_index}-remainder-{cursor}",
                "level": "D1_or_broadcast_remainder",
                "start_sec": cursor,
                "end_sec": source_end,
            })
    if not scan_scopes:
        scan_scopes = [
            {
                "ref": f"source-range-{index}",
                "level": "source_range",
                "start_sec": int(row.get("start_sec") or 0),
                "end_sec": int(row.get("end_sec") or 0),
            }
            for index, row in enumerate(source_ranges)
            if isinstance(row, Mapping)
            and int(row.get("end_sec") or 0) > int(row.get("start_sec") or 0)
        ]

    for scope in scan_scopes:
        start_sec = int(scope["start_sec"])
        end_sec = int(scope["end_sec"])
        batch_stt = stratified(
            [row for row in all_stt_rows if start_sec <= int(row["time_sec"]) <= end_sec],
            1 if int(max_stt_rows) > 0 else 0,
        )
        batch_chat = stratified(
            [row for row in all_chat_rows if start_sec <= int(row["time_sec"]) <= end_sec],
            1 if int(max_chat_rows) > 0 else 0,
        )
        stt_rows.extend(batch_stt)
        chat_rows.extend(batch_chat)

    stt_rows = sorted(
        {str(row["ref"]): row for row in stt_rows}.values(),
        key=lambda row: (int(row["time_sec"]), str(row["ref"])),
    )
    chat_rows = sorted(
        {str(row["ref"]): row for row in chat_rows}.values(),
        key=lambda row: (int(row["time_sec"]), str(row["ref"])),
    )
    temporal_scan_batches: list[dict[str, Any]] = []
    rows_per_coverage_batch = 8
    for scope in scan_scopes:
        scope_start = int(scope["start_sec"])
        scope_end = int(scope["end_sec"])
        scoped_rows = sorted(
            [
                *(
                    {**row, "_modality": "stt"}
                    for row in stt_rows
                    if scope_start <= int(row["time_sec"]) <= scope_end
                ),
                *(
                    {**row, "_modality": "chat"}
                    for row in chat_rows
                    if scope_start <= int(row["time_sec"]) <= scope_end
                ),
            ],
            key=lambda row: (int(row["time_sec"]), str(row["ref"])),
        )
        chunks_in_scope = [
            scoped_rows[index:index + rows_per_coverage_batch]
            for index in range(0, len(scoped_rows), rows_per_coverage_batch)
        ] or [[]]
        for chunk_index, rows_in_batch in enumerate(chunks_in_scope):
            prior_rows = chunks_in_scope[chunk_index - 1] if chunk_index else []
            next_rows = (
                chunks_in_scope[chunk_index + 1]
                if chunk_index + 1 < len(chunks_in_scope) else []
            )
            batch_start = (
                scope_start
                if not prior_rows
                else int(prior_rows[-1]["time_sec"])
            )
            batch_end = (
                scope_end
                if not next_rows
                else int(next_rows[0]["time_sec"])
            )
            temporal_scan_batches.append({
                "batch_index": len(temporal_scan_batches),
                "range_ref": str(scope["ref"]),
                "range_level": str(scope["level"]),
                "start_sec": batch_start,
                "end_sec": batch_end,
                "stt_refs": [
                    str(row["ref"])
                    for row in rows_in_batch
                    if row["_modality"] == "stt"
                ],
                "chat_refs": [
                    str(row["ref"])
                    for row in rows_in_batch
                    if row["_modality"] == "chat"
                ],
            })
    support = [
        dict(row)
        for row in evidence_bundle.get("candidate_proposals") or []
        if isinstance(row, Mapping) and in_scope(int(row.get("time_sec") or 0))
    ]
    body = {
        "schema_version": "chzz.highlight_local_evidence.v1",
        "story_seed": dict(story_seed),
        "stt_rows": stt_rows,
        "chat_rows": chat_rows,
        "temporal_scan_batches": temporal_scan_batches,
        "context_naming": dict(evidence_bundle.get("context_naming") or {}),
        "support_proposals": support,
        "provenance_refs": list(evidence_bundle.get("provenance_refs") or []),
        "forbidden_answer_guard": list(evidence_bundle.get("forbidden_answer_guard") or []),
        "allowed_evidence_refs": sorted(
            {str(row["ref"]) for row in [*stt_rows, *chat_rows]}
        ),
    }
    body["packet_sha256"] = _canonical_sha256(body)
    return body
