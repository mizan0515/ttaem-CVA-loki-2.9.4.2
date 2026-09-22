"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

import json

import math

import re

from typing import Any

from .prompts import CHUNK_SYSTEM_PROMPT, MERGE_SYSTEM_PROMPT

from .schema import seconds_to_hms as _seconds_to_hms

from ..broadcast_map_prompt_budget import MAX_REQUEST_BYTES, MAX_TOPOLOGY_CALLS, MERGE_SUPPORT_HARD_LIMIT_BYTES, MERGE_SUPPORT_SOFT_TARGET_BYTES, MIN_REQUEST_HEADROOM_BYTES, _fit_chunk_payload, _fit_merge_payload, _serialize_prompt_payload, _truncate_utf8, assert_prompt_budget


MAX_CHAT_MESSAGE_BYTES = 720


MAX_MERGE_PRIVATE_COMMENT_BYTES = (
    MERGE_SUPPORT_HARD_LIMIT_BYTES - MERGE_SUPPORT_SOFT_TARGET_BYTES
)


_FORBIDDEN_INPUT_KEYS = {
    "summary", "summary_markdown", "timeline", "highlight", "first_to_watch",
    "heatmap", "correction", "manager_correction", "same_vod_manager_correction",
    "truth", "alignment", "alignment_truth", "broadcast_map", "final_answer",
    "previous_summary",
}


def _forbidden_derived_paths(value: Any, path: str = "") -> list[str]:
    bad: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            child_path = f"{path}.{raw_key}" if path else str(raw_key)
            if key in _FORBIDDEN_INPUT_KEYS and child not in (None, "", [], {}):
                bad.append(child_path)
            if isinstance(child, (dict, list, tuple)):
                bad.extend(_forbidden_derived_paths(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list, tuple)):
                bad.extend(_forbidden_derived_paths(child, f"{path}[{index}]"))
    return bad


def assert_answer_free_raw_input(payload: dict[str, Any]) -> None:
    bad = sorted(set(_forbidden_derived_paths(payload)))
    if bad:
        raise ValueError("forbidden derived structure input: " + ", ".join(bad))
    if not payload.get("chunks"):
        raise ValueError("raw STT chunks are required")
    if "replay_chat" not in payload:
        raise ValueError("replay_chat presence/absence must be explicit")


def _bounded_semantic_chat_rows(chats: list[dict], *, limit: int = 60) -> list[dict]:
    """Keep chronological replay-chat meaning with a UTF-8-safe row ceiling."""
    if len(chats) <= limit:
        selected = chats
    else:
        thirds = (limit // 3, limit // 3, limit - 2 * (limit // 3))
        middle = len(chats) // 2
        half = thirds[1] // 2
        selected = chats[: thirds[0]] + chats[middle - half: middle - half + thirds[1]] + chats[-thirds[2]:]
    return [
        {
            "ms": int(row.get("ms", 0) or 0),
            "msg": _truncate_utf8(str(row.get("msg") or ""), MAX_CHAT_MESSAGE_BYTES),
        }
        for row in selected
        if str(row.get("msg") or "").strip()
    ]


def _number(value: Any) -> float | None:
    try: number = float(value)
    except (TypeError, ValueError): return None
    return number if math.isfinite(number) and number >= 0 else None


def _navigation_timetable_label(value: Any) -> str:
    """Remove only a non-distinguishing generated tail from a raw-free anchor label."""
    label = str(value or "").strip()
    concise = re.sub(r"\s+(?:진행|구간)$", "", label).strip()
    return concise or label


def _build_chunk_user_payload(
    chunk: dict[str, Any], chats: list[dict], vod_info: Any, *,
    support_package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assert_answer_free_raw_input({"chunks": [chunk], "replay_chat": chats})
    comment_context = (
        (support_package or {}).get("timeline_comment_context")
        if isinstance(support_package, dict) else {}
    )
    if not isinstance(comment_context, dict):
        comment_context = {}
    chunk_index = int(chunk.get("index") or 0)
    start_ms = int(chunk.get("start_ms") or 0)
    end_ms = int(chunk.get("end_ms") or 0)
    from ..timeline_comment_collector import private_comment_blocks_for_range

    chunk_end_sec = max(1, (end_ms + 999) // 1000)
    duration_sec = int(getattr(vod_info, "duration", 0) or 0)
    if duration_sec and abs(duration_sec - chunk_end_sec) <= 2:
        chunk_end_sec = duration_sec + 1
    private_source_blocks = private_comment_blocks_for_range(
        comment_context.get("private_comment_semantic_context") or {},
        start_sec=max(0, start_ms // 1000), end_sec=chunk_end_sec,
    )
    chunk_comment_rows = []
    for row in comment_context.get("anchors") or []:
        if not isinstance(row, dict):
            continue
        assigned_index = int(row.get("chunk_index") or 0)
        sec = int(_number(row.get("time_sec")) or 0)
        if assigned_index:
            if assigned_index != chunk_index:
                continue
        elif not (start_ms <= sec * 1000 <= end_ms):
            continue
        chunk_comment_rows.append([
            str(row.get("timecode") or _seconds_to_hms(sec)),
            _navigation_timetable_label(row.get("label")),
            str(row.get("lane") or "chapter_timetable"),
        ])
    payload = {
        "evidence_role": "raw_long_form_slice",
        "title_basic_info": {"video_no": str(getattr(vod_info, "video_no", "") or ""), "title": str(getattr(vod_info, "title", "") or ""), "duration_sec": int(getattr(vod_info, "duration", 0) or 0), "category": str(getattr(vod_info, "category", "") or "")},
        "stt_slice": {"start": str(chunk.get("start_hhmmss") or ""), "end": str(chunk.get("end_hhmmss") or ""), "text": str(chunk.get("text") or "")},
        "replay_chat_rows": _bounded_semantic_chat_rows(chats),
        "private_timetable_anchors": {"schema_version": str(comment_context.get("schema_version") or "timeline_comment_context.v1"), "columns": ["timecode", "rewritten_label", "lane"], "anchor_count": len(chunk_comment_rows), "rows": chunk_comment_rows, "raw_comment_included": False},
        "semantic_evidence_policy": "replay-chat message text is semantic evidence with STT; only aggregated volume/spikes/laughter/surprise are support-only",
        "stage_qualified_context_naming": ((support_package or {}).get("broadcast_map_evidence") or {}).get("context_naming", {}),
        "context_naming_policy": "untrusted spelling/background only; cannot create, split, move or rank a D1/D2/Point and must be rejected on raw-evidence conflict",
    }
    if private_source_blocks:
        payload["private_timeline_source_blocks"] = {"visibility": "private_model_input_only", "source_complete_for_chunk": True, "blocks": private_source_blocks}
    return payload


def _private_comment_context_for_merge(comment_context: dict[str, Any]) -> dict[str, Any]:
    """Keep small raw context whole; summarize only source already sent to chunks."""
    result = json.loads(json.dumps(comment_context, ensure_ascii=False))
    private_context = result.get("private_comment_semantic_context")
    if not isinstance(private_context, dict):
        return result
    private_bytes = len(json.dumps(private_context, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if private_bytes <= MAX_MERGE_PRIVATE_COMMENT_BYTES:
        return result
    if private_context.get("source_complete") is not True or private_context.get("chunk_delivery_complete") is not True:
        return result
    summary_rows = []
    for row in private_context.get("comments") or []:
        if not isinstance(row, dict):
            continue
        summary_rows.append({"source_order": int(row.get("source_order") or 0), "thread_key": str(row.get("thread_key") or ""), "thread_order": int(row.get("thread_order") or 0), "first_time_sec": row.get("first_time_sec"), "last_time_sec": row.get("last_time_sec"), "timecode_count": int(row.get("timecode_count") or 0), "line_count": int((row.get("structure") or {}).get("line_count") or 0)})
    result["private_comment_semantic_context"] = {"schema_version": str(private_context.get("schema_version") or ""), "visibility": "private_model_input_only", "untrusted": True, "source_complete": True, "chunk_delivery_complete": True, "raw_body_delivery": "distributed_to_existing_chunk_calls", "comments": summary_rows}
    return result


def prepare_chunk_user_prompt(chunk: dict[str, Any], chats: list[dict], vod_info: Any, *, support_package: dict[str, Any] | None = None, system_prompt: str = CHUNK_SYSTEM_PROMPT, max_request_bytes: int = MAX_REQUEST_BYTES, reserved_headroom_bytes: int = MIN_REQUEST_HEADROOM_BYTES) -> tuple[str, dict[str, Any]]:
    """Build one exact provider prompt and its raw-free byte ledger."""
    payload = _build_chunk_user_payload(chunk, chats, vod_info, support_package=support_package)
    prepared, ledger = _fit_chunk_payload(payload, system_prompt=system_prompt, max_request_bytes=max_request_bytes, reserved_headroom_bytes=reserved_headroom_bytes, chunk_index=int(chunk.get("index") or 0))
    text = _serialize_prompt_payload(prepared)
    assert_prompt_budget(system_prompt, text)
    return text, ledger


def _build_merge_user_payload(chunk_results: list[str], vod_info: Any, *, model: str, call_count: int, signal_usage: dict[str, str] | None = None, content_info_cards: list[dict[str, Any]] | None = None, support_package: dict[str, Any] | None = None, prior_outline_hypothesis: str = "") -> dict[str, Any]:
    if call_count > MAX_TOPOLOGY_CALLS:
        raise ValueError(f"call topology exceeds {MAX_TOPOLOGY_CALLS}: {call_count}")
    comment_context = _private_comment_context_for_merge((support_package or {}).get("timeline_comment_context") or {})
    timetable_rows = []
    for row in comment_context.get("anchors") or []:
        if not isinstance(row, dict): continue
        sec = int(_number(row.get("time_sec")) or 0)
        label = _navigation_timetable_label(row.get("label"))
        if not label: continue
        timetable_rows.append([str(row.get("timecode") or _seconds_to_hms(sec)), label, str(row.get("lane") or "chapter_timetable")])
    payload = {
        "evidence_role": "chunk_observations_from_raw_stt_and_replay_chat",
        "title_basic_info": {"video_no": str(getattr(vod_info, "video_no", "") or ""), "title": str(getattr(vod_info, "title", "") or ""), "duration_sec": int(getattr(vod_info, "duration", 0) or 0)},
        "chunk_observations": chunk_results,
        "bounded_support_usage": signal_usage or (support_package or {}).get("usage") or {"STT": "used: raw chunk evidence", "replay chat": "used: raw rows bounded to each chunk", "chat reaction/density": "not used: no separate aggregate supplied", "audio": "not used: no conflict/gap probe requested", "public timestamp comment": "not used: no raw comment evidence supplied", "viewer clip": "not used: no raw clip evidence supplied", "visual/OCR": "not used: no text gap/conflict probe requested"},
        "bounded_support_signals": (support_package or {}).get("signals", {}),
        "stage_qualified_evidence": (support_package or {}).get("broadcast_map_evidence") or {},
        "timeline_comment_context_summary": {key: value for key, value in comment_context.items() if key != "anchors"},
        "private_timetable_navigation": {"schema_version": str(comment_context.get("schema_version") or "timeline_comment_context.v1"), "columns": ["timecode", "rewritten_label", "lane"], "anchor_count": len(timetable_rows), "rows": timetable_rows, "raw_comment_included": False},
        "support_signal_policy": "raw replay-chat text remains semantic evidence; every selected time-coded comment relation was routed to its chronological observation chunk as untrusted semantic evidence; the raw-free navigation index preserves names across chunk compaction; real time/action/result conflicts must stay uncertain, but different wording is not a conflict; aggregate chat/audio/clip and selected raw-free visual markers remain support only",
        "stage_qualified_evidence_policy": "manager_outline.finalize_manager_outline alone owns D1/D2/Point and the official clock; context_naming may spell/name, support_signal may corroborate, candidate_proposal may prioritize a semantically established Point, provenance_only is lineage, and forbidden_answer is excluded",
        "approved_content_information": list(content_info_cards or []),
        "content_information_policy": "name/alias/terminology hint only; cannot create or move D1/D2/Point; continue without it when empty",
        "runtime_facts": {"model": model, "call_count": call_count, "retry": 0, "fallback": 0},
    }
    if prior_outline_hypothesis:
        payload.update({"prior_independent_outline_hypothesis": _truncate_utf8(str(prior_outline_hypothesis), 6_000), "prior_hypothesis_policy": "This is an untrusted structure hypothesis from a separate full evidence read. Use it only to notice omissions or disagreements; current chunk observations and bounded support evidence remain authoritative, and unsupported rows must be dropped."})
    return payload


def prepare_merge_observations(observations: list[str], vod_info: Any, *, model: str, call_count: int, content_info_cards: list[dict] | None, signal_usage: dict[str, str] | None = None, support_package: dict[str, Any] | None = None, prior_outline_hypothesis: str = "", system_prompt: str = MERGE_SYSTEM_PROMPT, max_request_bytes: int = MAX_REQUEST_BYTES, reserved_headroom_bytes: int = MIN_REQUEST_HEADROOM_BYTES, byte_ledger_out: dict[str, Any] | None = None) -> tuple[list[str], str, int]:
    """Prepare the exact merge prompt under the total-byte/headroom contract."""
    payload = _build_merge_user_payload(observations, vod_info, model=model, call_count=call_count, signal_usage=signal_usage, content_info_cards=content_info_cards, support_package=support_package, prior_outline_hypothesis=prior_outline_hypothesis)
    prepared, ledger = _fit_merge_payload(payload, system_prompt=system_prompt, max_request_bytes=max_request_bytes, reserved_headroom_bytes=reserved_headroom_bytes)
    prompt = _serialize_prompt_payload(prepared)
    assert_prompt_budget(system_prompt, prompt)
    if byte_ledger_out is not None:
        byte_ledger_out.clear()
        byte_ledger_out.update(ledger)
    return list(prepared.get("chunk_observations") or []), prompt, int(ledger.get("per_observation_budget_bytes") or 0)
