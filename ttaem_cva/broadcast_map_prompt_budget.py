"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

import json

from typing import Any

from .outline.prompts import MERGE_SYSTEM_PROMPT


MAX_REQUEST_BYTES = 40_000


MIN_REQUEST_HEADROOM_BYTES = 4_096


MAX_TOPOLOGY_CALLS = 21


MERGE_SUPPORT_SOFT_TARGET_BYTES = 4_096


MERGE_SUPPORT_HARD_LIMIT_BYTES = 16_384


def _thin_full_span(rows: list[Any], limit: int) -> list[Any]:
    if len(rows) <= limit: return rows
    if limit <= 1: return rows[:1]
    return [rows[round(i * (len(rows) - 1) / (limit - 1))] for i in range(limit)]


def coalesce_manager_outline_chunks(
    chunks: list[dict[str, Any]],
    *,
    max_chunks: int = MAX_TOPOLOGY_CALLS - 1,
) -> list[dict[str, Any]]:
    """Pack chronological transcript chunks into the fixed 2.5 call budget.

    The ordinary summary may use more chunks for detail. Pipeline 2.5 needs at
    most twenty observation calls plus one merge call. We therefore combine
    adjacent chunks without dropping text, time coverage, or promoted anchors.
    Exact request-byte preflight remains the final gate; an input that still
    cannot fit fails visibly instead of switching summary engines.
    """

    if max_chunks < 1:
        raise ValueError("manager outline max_chunks must be at least 1")
    ordered = sorted(
        (dict(row) for row in chunks),
        key=lambda row: (int(row.get("start_ms") or 0), int(row.get("index") or 0)),
    )
    if len(ordered) <= max_chunks:
        return ordered

    group_count = min(max_chunks, len(ordered))

    def group_text_bytes(group: list[dict[str, Any]]) -> int:
        return len(
            "\n\n".join(str(row.get("text") or "") for row in group).encode("utf-8")
        )

    sizes = [len(str(row.get("text") or "").encode("utf-8")) for row in ordered]

    def greedy_groups(limit: int) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_bytes = 0
        for row, row_bytes in zip(ordered, sizes):
            added = row_bytes + (2 if current else 0)
            if current and current_bytes + added > limit:
                groups.append(current)
                current = [row]
                current_bytes = row_bytes
            else:
                current.append(row)
                current_bytes += added
        if current:
            groups.append(current)
        return groups

    low = max(sizes)
    high = sum(sizes) + 2 * (len(sizes) - 1)
    while low < high:
        middle = (low + high) // 2
        if len(greedy_groups(middle)) <= group_count:
            high = middle
        else:
            low = middle + 1
    grouped = greedy_groups(low)

    while len(grouped) < group_count:
        candidates = [
            (group_text_bytes(group), -index, index)
            for index, group in enumerate(grouped)
            if len(group) > 1
        ]
        if not candidates:
            break
        _, _, group_index = max(candidates)
        group = grouped[group_index]
        cut = min(
            range(1, len(group)),
            key=lambda position: (
                max(
                    group_text_bytes(group[:position]),
                    group_text_bytes(group[position:]),
                ),
                position,
            ),
        )
        grouped[group_index:group_index + 1] = [group[:cut], group[cut:]]

    packed: list[dict[str, Any]] = []
    for group_index, group in enumerate(grouped):
        merged = dict(group[0])
        merged_text = "\n\n".join(str(row.get("text") or "") for row in group).strip()
        anchors: list[dict[str, Any]] = []
        seen_anchor_keys: set[tuple[int, int]] = set()
        for row in group:
            for anchor in row.get("promoted_anchors") or []:
                anchor_copy = dict(anchor)
                key = (
                    int(anchor_copy.get("ms") or 0),
                    int(anchor_copy.get("sec") or 0),
                )
                if key in seen_anchor_keys:
                    continue
                seen_anchor_keys.add(key)
                anchors.append(anchor_copy)
        anchors.sort(key=lambda row: (int(row.get("ms") or 0), int(row.get("sec") or 0)))
        merged.update({
            "index": group_index + 1,
            "start_ms": min(int(row.get("start_ms") or 0) for row in group),
            "end_ms": max(int(row.get("end_ms") or 0) for row in group),
            "start_hhmmss": str(group[0].get("start_hhmmss") or ""),
            "end_hhmmss": str(group[-1].get("end_hhmmss") or ""),
            "cue_count": sum(int(row.get("cue_count") or 0) for row in group),
            "char_count": len(merged_text),
            "text": merged_text,
            "promoted_anchors": anchors,
            "source_chunk_indexes": [int(row.get("index") or 0) for row in group],
        })
        packed.append(merged)
    return packed


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max(0, max_bytes)].decode("utf-8", errors="ignore").rstrip()


def _serialize_prompt_payload(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _json_bytes(value: Any) -> int:
    return len(_serialize_prompt_payload(value).encode("utf-8"))


def _clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _empty_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, str):
        return ""
    return value


def _shrink_json_value(value: Any) -> Any:
    """Deterministically shrink one optional JSON value without logging its body."""

    if isinstance(value, str):
        size = len(value.encode("utf-8"))
        return _truncate_utf8(value, max(0, size // 2))
    if isinstance(value, list):
        if len(value) > 3:
            return _thin_full_span(value, max(3, len(value) // 2))
        if value:
            sizes = [_json_bytes(row) for row in value]
            index = max(range(len(value)), key=lambda pos: (sizes[pos], -pos))
            result = _clone_json(value)
            result[index] = _shrink_json_value(result[index])
            return result
        return value
    if isinstance(value, dict) and value:
        keys = list(value)
        key = max(keys, key=lambda item: (_json_bytes(value[item]), -keys.index(item)))
        result = _clone_json(value)
        result[key] = _shrink_json_value(result[key])
        if result[key] == value[key]:
            result[key] = _empty_json_value(value[key])
        return result
    return value


def _compact_json_value(value: Any, max_bytes: int) -> Any:
    """Fit optional structured evidence while retaining deterministic chronology."""

    result = _clone_json(value)
    previous = -1
    while _json_bytes(result) > max(0, max_bytes):
        current = _json_bytes(result)
        if current == previous:
            result = _empty_json_value(result)
            break
        previous = current
        result = _shrink_json_value(result)
    return result


def _select_chat_rows_for_budget(
    rows: list[dict[str, Any]], max_bytes: int,
) -> list[dict[str, Any]]:
    """Select as many semantic chat rows as fit while retaining full-span order."""

    if not rows or max_bytes < 2:
        return []
    for count in range(len(rows), 0, -1):
        selected = _thin_full_span(rows, count)
        if _json_bytes(selected) <= max_bytes:
            return selected
    return []


def _fit_chunk_payload(
    payload: dict[str, Any],
    *,
    system_prompt: str,
    max_request_bytes: int,
    reserved_headroom_bytes: int,
    chunk_index: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Assemble one chunk request under a total system+user byte target."""

    system_bytes = len(system_prompt.encode("utf-8"))
    target_bytes = max_request_bytes - reserved_headroom_bytes
    if target_bytes <= system_bytes:
        raise ValueError(
            "chunk prompt target cannot contain its system policy: "
            f"chunk_index={chunk_index}; system_prompt_bytes={system_bytes}; "
            f"target_request_bytes={target_bytes}; raw_free=true"
        )
    original = _clone_json(payload)
    chat_rows = list(original.get("replay_chat_rows") or [])
    context_naming = original.get("stage_qualified_context_naming") or {}
    prepared = _clone_json(original)
    prepared["replay_chat_rows"] = []
    prepared["stage_qualified_context_naming"] = {}
    minimum_user_bytes = _json_bytes(prepared)
    minimum_total = system_bytes + minimum_user_bytes
    stt_bytes = len(
        str((original.get("stt_slice") or {}).get("text") or "").encode("utf-8")
    )
    anchors = (original.get("private_timetable_anchors") or {}).get("rows") or []
    anchor_bytes = _json_bytes(anchors)
    private_comment_blocks = (
        (original.get("private_timeline_source_blocks") or {}).get("blocks") or []
    )
    private_comment_bytes = _json_bytes(private_comment_blocks)
    envelope = _clone_json(prepared)
    envelope.setdefault("stt_slice", {})["text"] = ""
    envelope.setdefault("private_timetable_anchors", {})["rows"] = []
    ledger: dict[str, Any] = {
        "schema_version": "broadcast_map_prompt_budget.v1",
        "stage": "chunk",
        "chunk_index": chunk_index,
        "system_prompt_bytes": system_bytes,
        "fixed_user_envelope_bytes": _json_bytes(envelope),
        "stt_bytes": stt_bytes,
        "replay_chat_bytes_before": _json_bytes(chat_rows),
        "timeline_anchor_bytes": anchor_bytes,
        "timeline_anchor_count": len(anchors),
        "private_comment_source_bytes": private_comment_bytes,
        "private_comment_block_count": len(private_comment_blocks),
        "private_comment_body_included": bool(private_comment_blocks),
        "context_naming_bytes_before": _json_bytes(context_naming),
        "total_before_compaction": system_bytes + _json_bytes(original),
        "target_request_bytes": target_bytes,
        "max_request_bytes": max_request_bytes,
        "reserved_headroom_bytes": reserved_headroom_bytes,
        "raw_content_included": False,
    }
    if minimum_total > target_bytes:
        ledger.update(total_after_compaction=minimum_total, minimum_envelope_failed=True)
        raise ValueError(
            "chunk minimum semantic envelope exceeds reserved request budget: "
            + _serialize_prompt_payload(ledger)
        )

    prepared["stage_qualified_context_naming"] = {}
    chat_container_bytes = _json_bytes(prepared)
    chat_budget = max(0, target_bytes - system_bytes - chat_container_bytes)
    selected_chat = _select_chat_rows_for_budget(chat_rows, chat_budget)
    prepared["replay_chat_rows"] = selected_chat

    base_without_context = _clone_json(prepared)
    base_without_context["stage_qualified_context_naming"] = {}
    context_budget = max(
        0,
        target_bytes - system_bytes - _json_bytes(base_without_context),
    )
    prepared["stage_qualified_context_naming"] = _compact_json_value(
        context_naming, context_budget
    )
    total = system_bytes + _json_bytes(prepared)
    while total > target_bytes and selected_chat:
        selected_chat = (
            _thin_full_span(selected_chat, len(selected_chat) - 1)
            if len(selected_chat) > 1
            else []
        )
        prepared["replay_chat_rows"] = selected_chat
        total = system_bytes + _json_bytes(prepared)
    if total > target_bytes:
        prepared["stage_qualified_context_naming"] = {}
        total = system_bytes + _json_bytes(prepared)
    if total > target_bytes:
        ledger.update(total_after_compaction=total, minimum_envelope_failed=True)
        raise ValueError(
            "chunk prompt cannot preserve primary semantic coverage under target: "
            + _serialize_prompt_payload(ledger)
        )

    selected_ms = [int(row.get("ms") or 0) for row in selected_chat]
    source_ms = [int(row.get("ms") or 0) for row in chat_rows]
    coverage = not source_ms or (
        bool(selected_ms)
        and selected_ms[0] == source_ms[0]
        and selected_ms[-1] == source_ms[-1]
        and (len(source_ms) < 3 or len(selected_ms) >= 3)
    )
    ledger.update({
        "replay_chat_bytes_after": _json_bytes(selected_chat),
        "replay_chat_rows_before": len(chat_rows),
        "replay_chat_rows_after": len(selected_chat),
        "context_naming_bytes_after": _json_bytes(
            prepared.get("stage_qualified_context_naming") or {}
        ),
        "total_after_compaction": total,
        "actual_headroom_bytes": max_request_bytes - total,
        "chat_chronological_coverage_preserved": coverage,
        "chunk_start_end_preserved": True,
        "private_comment_source_complete": True,
        "minimum_envelope_failed": False,
    })
    return prepared, ledger


_MERGE_OPTIONAL_KEYS = (
    "bounded_support_signals",
    "stage_qualified_evidence",
    "timeline_comment_context_summary",
    "approved_content_information",
    "prior_independent_outline_hypothesis",
    "prior_hypothesis_policy",
)


_MERGE_PROTECTED_SUPPORT_PATHS = (
    ("timeline_comment_context_summary", "private_comment_semantic_context"),
    ("stage_qualified_evidence", "context_naming", "context_doc"),
)


def _set_json_path(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = target
    for key in path[:-1]:
        child = cursor.get(key)
        if not isinstance(child, dict):
            child = {}
            cursor[key] = child
        cursor = child
    cursor[path[-1]] = _clone_json(value)


def _get_json_path(target: dict[str, Any], path: tuple[str, ...]) -> tuple[bool, Any]:
    cursor: Any = target
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            return False, None
        cursor = cursor[key]
    return True, cursor


def _pop_json_path(target: dict[str, Any], path: tuple[str, ...]) -> tuple[bool, Any]:
    cursor: Any = target
    parents: list[tuple[dict[str, Any], str]] = []
    for key in path[:-1]:
        if not isinstance(cursor, dict) or key not in cursor:
            return False, None
        parents.append((cursor, key))
        cursor = cursor[key]
    if not isinstance(cursor, dict) or path[-1] not in cursor:
        return False, None
    value = cursor.pop(path[-1])
    for parent, key in reversed(parents):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            parent.pop(key, None)
        else:
            break
    return True, value


def _overlay_json_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = _clone_json(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _overlay_json_dict(result[key], value)
        else:
            result[key] = _clone_json(value)
    return result


def _split_protected_merge_support(
    optional: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate relationship-bearing raw context from compactable support.

    These values already exist in the product contract. They are not copied to
    a new authority object: the split only prevents generic size compaction from
    destroying their line order before the finalizer reads them.
    """

    compactable = _clone_json(optional)
    protected: dict[str, Any] = {}
    for path in _MERGE_PROTECTED_SUPPORT_PATHS:
        found, value = _pop_json_path(compactable, path)
        if found and value not in (None, "", {}, []):
            _set_json_path(protected, path, value)
    return protected, compactable


def _apply_merge_support(
    prepared: dict[str, Any],
    optional: dict[str, Any],
    compactable: dict[str, Any],
    protected: dict[str, Any],
) -> None:
    combined = _overlay_json_dict(compactable, protected)
    for key, value in optional.items():
        prepared[key] = combined.get(key, _empty_json_value(value))


def _prepared_support(prepared: dict[str, Any], optional: dict[str, Any]) -> dict[str, Any]:
    return {key: prepared.get(key, _empty_json_value(value)) for key, value in optional.items()}


def _protected_merge_support_preserved(
    prepared: dict[str, Any], protected: dict[str, Any]
) -> bool:
    for path in _MERGE_PROTECTED_SUPPORT_PATHS:
        protected_found, protected_value = _get_json_path(protected, path)
        if not protected_found:
            continue
        prepared_found, prepared_value = _get_json_path(prepared, path)
        if not prepared_found or prepared_value != protected_value:
            return False
    return True


def _minimum_compacted_observation(text: str) -> tuple[str, int]:
    for budget in range(160, 2_049, 16):
        try:
            return compact_chunk_observation(text, budget), budget
        except ValueError:
            continue
    raise ValueError("observation minimum semantic headings exceed bounded envelope")


def _fit_merge_payload(
    payload: dict[str, Any],
    *,
    system_prompt: str = MERGE_SYSTEM_PROMPT,
    max_request_bytes: int = MAX_REQUEST_BYTES,
    reserved_headroom_bytes: int = MIN_REQUEST_HEADROOM_BYTES,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit every observation and bounded support under the same total-byte contract."""

    system_bytes = len(system_prompt.encode("utf-8"))
    target_bytes = max_request_bytes - reserved_headroom_bytes
    original = _clone_json(payload)
    observations = [str(row) for row in original.get("chunk_observations") or []]
    prepared = _clone_json(original)
    prepared["chunk_observations"] = []
    optional = {
        key: original.get(key)
        for key in _MERGE_OPTIONAL_KEYS
        if key in original
    }
    protected_support, compactable_support = _split_protected_merge_support(optional)
    for key, value in optional.items():
        prepared[key] = _empty_json_value(value)
    _apply_merge_support(prepared, optional, {}, protected_support)

    minimum_observations: list[str] = []
    minimum_budgets: list[int] = []
    for observation in observations:
        compacted, budget = _minimum_compacted_observation(observation)
        minimum_observations.append(compacted)
        minimum_budgets.append(budget)
    prepared["chunk_observations"] = minimum_observations
    protected_support_bytes = _json_bytes(_prepared_support(prepared, optional))
    private_context = (
        (protected_support.get("timeline_comment_context_summary") or {}).get(
            "private_comment_semantic_context"
        ) or {}
    )
    private_comment_body_included = any(
        bool(row.get("body"))
        for row in private_context.get("comments") or []
        if isinstance(row, dict)
    )
    minimum_total = system_bytes + _json_bytes(prepared)
    ledger: dict[str, Any] = {
        "schema_version": "broadcast_map_prompt_budget.v1",
        "stage": "merge",
        "system_prompt_bytes": system_bytes,
        "fixed_user_envelope_bytes": _json_bytes({**prepared, "chunk_observations": []}),
        "observation_count_before": len(observations),
        "observation_bytes_before": _json_bytes(observations),
        "support_bytes_before": _json_bytes(optional),
        "support_soft_target_bytes": MERGE_SUPPORT_SOFT_TARGET_BYTES,
        "support_hard_limit_bytes": MERGE_SUPPORT_HARD_LIMIT_BYTES,
        "protected_context_bytes_before": protected_support_bytes,
        "total_before_compaction": system_bytes + _json_bytes(original),
        "target_request_bytes": target_bytes,
        "max_request_bytes": max_request_bytes,
        "reserved_headroom_bytes": reserved_headroom_bytes,
        "raw_content_included": False,
        "ledger_raw_content_included": False,
        "private_comment_body_included": private_comment_body_included,
    }
    if protected_support_bytes > MERGE_SUPPORT_HARD_LIMIT_BYTES:
        ledger.update(
            total_after_compaction=minimum_total,
            protected_context_bytes_after=protected_support_bytes,
            protected_context_preserved=False,
            minimum_envelope_failed=True,
        )
        raise ValueError(
            "merge protected semantic context exceeds internal support hard limit: "
            + _serialize_prompt_payload(ledger)
        )
    if minimum_total > target_bytes:
        ledger.update(
            total_after_compaction=minimum_total,
            protected_context_bytes_after=protected_support_bytes,
            protected_context_preserved=False,
            minimum_envelope_failed=True,
        )
        raise ValueError(
            "merge minimum semantic envelope exceeds reserved request budget: "
            + _serialize_prompt_payload(ledger)
        )

    slack = target_bytes - minimum_total
    compactable_budget = min(
        _json_bytes(compactable_support),
        max(0, MERGE_SUPPORT_HARD_LIMIT_BYTES - protected_support_bytes),
        max(0, MERGE_SUPPORT_SOFT_TARGET_BYTES - protected_support_bytes, slack // 3),
    )
    compact_optional = _compact_json_value(compactable_support, compactable_budget)
    _apply_merge_support(prepared, optional, compact_optional, protected_support)
    while (
        _json_bytes(_prepared_support(prepared, optional))
        > MERGE_SUPPORT_HARD_LIMIT_BYTES
        and compact_optional not in ({}, [])
    ):
        compact_optional = _shrink_json_value(compact_optional)
        _apply_merge_support(prepared, optional, compact_optional, protected_support)

    available_for_observations = (
        target_bytes
        - system_bytes
        - _json_bytes({**prepared, "chunk_observations": []})
    )
    if observations:
        common_budget = max(
            max(minimum_budgets), available_for_observations // len(observations)
        )
        common_budget = min(
            common_budget,
            max(len(row.encode("utf-8")) for row in observations),
        )
        compacted = minimum_observations
        while common_budget >= max(minimum_budgets):
            candidate = [
                compact_chunk_observation(row, common_budget)
                for row in observations
            ]
            prepared["chunk_observations"] = candidate
            if system_bytes + _json_bytes(prepared) <= target_bytes:
                compacted = candidate
                break
            common_budget -= 16
        else:
            prepared["chunk_observations"] = minimum_observations
            common_budget = max(minimum_budgets)
    else:
        compacted = []
        common_budget = 0
        prepared["chunk_observations"] = []

    total = system_bytes + _json_bytes(prepared)
    if total > target_bytes:
        _apply_merge_support(prepared, optional, {}, protected_support)
        prepared["chunk_observations"] = minimum_observations
        compacted = minimum_observations
        common_budget = max(minimum_budgets, default=0)
        total = system_bytes + _json_bytes(prepared)
    if total > target_bytes:
        ledger.update(total_after_compaction=total, minimum_envelope_failed=True)
        raise ValueError(
            "merge prompt cannot preserve all chronological observations under target: "
            + _serialize_prompt_payload(ledger)
        )

    ledger.update({
        "observation_count_after": len(compacted),
        "observation_bytes_after": _json_bytes(compacted),
        "support_bytes_after": _json_bytes(_prepared_support(prepared, optional)),
        "protected_context_bytes_after": protected_support_bytes,
        "protected_context_preserved": _protected_merge_support_preserved(
            prepared, protected_support
        ),
        "per_observation_budget_bytes": common_budget,
        "first_middle_last_coverage_preserved": len(compacted) == len(observations),
        "required_semantic_headings_preserved": all(
            all(heading in row for heading in (
                "[지속 활동 관찰]",
                "[전환·중단·재개 관찰]",
                "[지원 신호 후보]",
                "[불확실]",
            ))
            for row in compacted
        ),
        "total_after_compaction": total,
        "actual_headroom_bytes": max_request_bytes - total,
        "minimum_envelope_failed": False,
    })
    return prepared, ledger


def compact_chunk_observation(text: str, max_bytes: int) -> str:
    """Deterministically retain every required section within one observation budget."""

    header, _, body = text.partition("[지속 활동 관찰]")
    headings = [
        "[지속 활동 관찰]",
        "[전환·중단·재개 관찰]",
        "[지원 신호 후보]",
        "[불확실]",
    ]
    if not body or any(heading not in text for heading in headings):
        raise ValueError("cannot compact observation missing required semantic sections")
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    header = header.rstrip()
    fixed = (header + "\n" if header else "") + "\n".join(headings) + "\n"
    available = max_bytes - len(fixed.encode("utf-8")) - (len(headings) - 1)
    if available < len(headings):
        raise ValueError("per-observation budget cannot preserve required semantic sections")
    sections = []
    for index, heading in enumerate(headings):
        start = text.index(heading) + len(heading)
        end = (
            text.index(headings[index + 1], start)
            if index + 1 < len(headings)
            else len(text)
        )
        sections.append(text[start:end].strip() or "unknown")
    remaining = available
    rows = []
    for index, (heading, section) in enumerate(zip(headings, sections)):
        slots = len(headings) - index
        share = max(1, remaining // slots)
        compacted = _truncate_utf8(section, share)
        rows.extend([heading, compacted or "?"])
        remaining -= len((compacted or "?").encode("utf-8"))
    result = ((header + "\n") if header else "") + "\n".join(rows)
    if len(result.encode("utf-8")) > max_bytes:
        raise ValueError("compacted observation exceeds computed budget")
    return result


def assert_prompt_budget(system_prompt: str, user_prompt: str) -> None:
    total = len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8"))
    if total > MAX_REQUEST_BYTES:
        raise ValueError(f"system+user request exceeds 40,000 bytes: {total}")
