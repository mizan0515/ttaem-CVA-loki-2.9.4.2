"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

from .fixed_prompt_assets import load_fixed_prompt

import hashlib

import json

from collections.abc import Mapping, Sequence

from pathlib import Path

from typing import Any


HARD_REQUEST_BYTES = 40_000


TARGET_REQUEST_BYTES = 35_904


MIN_REQUEST_RESERVE_BYTES = 4_096


SYSTEM_PROMPT = load_fixed_prompt("prompts/story/selector_system.md")


ROLE_KEYS = ("setup", "performance", "event", "reaction", "result")


def parse_story_point_selector_response(raw: str) -> tuple[Mapping[str, Any], int]:
    """Parse one closed response with one narrowly recognized brace repair."""

    value = str(raw or "").strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if value.startswith("json"):
            value = value[4:].lstrip()
    try:
        response = json.loads(value)
        repair_count = 0
    except json.JSONDecodeError:
        marker = '}},"story_packet_id"'
        if value.count(marker) != 1:
            raise
        response = json.loads(value.replace(marker, '},"story_packet_id"', 1))
        repair_count = 1
    if not isinstance(response, Mapping):
        raise ValueError("story selector response must be one JSON object")
    return response, repair_count


def validate_highlight_story_evidence(
    *,
    required_story_roles: Mapping[str, Sequence[str]],
    available_evidence_refs: Sequence[str],
    author_evidence_refs: Sequence[str] | None = None,
    used_evidence_refs: Sequence[str],
    evidence_catalog: Mapping[str, Mapping[str, Any]],
    source_spans: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Close the R1 role/evidence contract for one persisted Highlight.

    The semantic author chooses spans.  Code verifies that every canonical role
    ref survived and records a disposition for every supplied packet ref.
    """

    if set(required_story_roles) != set(ROLE_KEYS):
        raise ValueError("invalid_required_story_roles")
    roles: dict[str, list[str]] = {}
    for role in ROLE_KEYS:
        value = required_story_roles.get(role) or []
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"required_story_role_{role}_must_be_a_list")
        refs = [str(ref or "").strip() for ref in value]
        if any(not ref for ref in refs) or len(refs) != len(set(refs)):
            raise ValueError(f"required_story_role_{role}_must_have_unique_nonempty_refs")
        roles[role] = refs
    available = [str(ref or "").strip() for ref in available_evidence_refs]
    if any(not ref for ref in available) or len(available) != len(set(available)):
        raise ValueError("story_available_evidence_refs_must_have_unique_nonempty_refs")
    used = {str(ref or "").strip() for ref in used_evidence_refs}
    permitted = {str(ref or "").strip() for ref in (author_evidence_refs or available)}
    required = list(dict.fromkeys(ref for role in ROLE_KEYS for ref in roles[role]))
    if not set(required).issubset(available):
        raise ValueError("required_story_evidence_not_available")
    if not set(required).issubset(permitted):
        raise ValueError("required_story_evidence_not_sent_to_author")
    if not used.issubset(permitted):
        raise ValueError("highlight_uses_evidence_outside_approved_story")
    dispositions = []
    for evidence_id in available:
        if evidence_id in used:
            dispositions.append({"evidence_id": evidence_id, "disposition": "included", "reason": "referenced_by_persisted_source_span"})
            continue
        row = evidence_catalog.get(evidence_id) or {}
        time_sec = row.get("time_sec", row.get("canonical_time_sec"))
        inside = isinstance(time_sec, (int, float)) and not isinstance(time_sec, bool) and any(
            float(span["start_sec"]) <= float(time_sec) <= float(span["end_sec"])
            for span in source_spans
        )
        dispositions.append({
            "evidence_id": evidence_id,
            "disposition": "unused_with_reason",
            "reason": (
                "available_inside_span_but_not_selected_for_this_highlight"
                if inside
                else "canonical_story_evidence_preserved_outside_selected_highlight_footage"
            ),
        })
    return {"required_story_roles": roles, "required_evidence_refs": required, "evidence_dispositions": dispositions}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _hms(sec: int) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _row_time(row: Mapping[str, Any]) -> int:
    if "time_sec" in row:
        return int(row["time_sec"])
    return int(row["canonical_time_sec"])


def audit_primary_evidence_dispositions(
    *, packets: Sequence[Mapping[str, Any]], evidence_ledger: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    owned: dict[str, str] = {}
    overlap: dict[str, list[str]] = {}
    explicit: dict[str, str] = {}
    for packet in packets:
        packet_id = str(packet.get("story_packet_id") or "")
        for item in packet.get("primary_dispositions") or []:
            evidence_id = str(item.get("evidence_id") or "")
            disposition = str(item.get("disposition") or "")
            if disposition == "included_primary":
                if evidence_id in owned:
                    raise ValueError("primary evidence has more than one owning packet")
                owned[evidence_id] = packet_id
            elif disposition == "moved_overlap_context":
                overlap.setdefault(evidence_id, []).append(packet_id)
        for item in packet.get("explicit_exclusions") or []:
            explicit[str(item.get("evidence_id") or "")] = str(item.get("reason") or "")
    expected = {
        evidence_id
        for evidence_id, row in evidence_ledger.items()
        if isinstance(row, Mapping) and str(row.get("role") or "") == "primary_semantic"
    }
    silent = sorted(expected - set(owned) - set(explicit))
    if silent:
        raise ValueError(f"primary evidence silently dropped: {silent[:3]}")
    return {
        "owned_primary_count": len(owned),
        "overlap_context_count": sum(len(values) for values in overlap.values()),
        "explicit_exclusion_count": len(explicit),
        "silent_drop_count": 0,
        "owner_by_evidence_id": owned,
        "explicit_exclusions": explicit,
    }


def build_story_point_selector_request(
    *, packets: Sequence[Mapping[str, Any]], evidence_ledger: Mapping[str, Mapping[str, Any]]
) -> str:
    """Serialize one bounded batch of Point-owned Story packets."""

    point_bound = [bool(str(packet.get("point_ref") or "")) for packet in packets]
    if any(point_bound) and not all(point_bound):
        raise ValueError("canonical and legacy Story packets cannot share one batch")
    canonical_point_mode = bool(point_bound) and all(point_bound)
    packet_rows: list[dict[str, Any]] = []
    for packet in packets:
        packet_id = str(packet.get("story_packet_id") or "")
        evidence_ids = [str(value) for value in packet.get("evidence_ids") or []]
        if not packet_id or not evidence_ids:
            raise ValueError("story packet must have an ID and primary evidence")
        packet_start_sec = min(
            _row_time(evidence_ledger[evidence_id])
            for evidence_id in evidence_ids
            if evidence_id in evidence_ledger
        )
        evidence: list[list[Any]] = []
        for evidence_id in evidence_ids:
            row = evidence_ledger.get(evidence_id)
            if not isinstance(row, Mapping):
                raise ValueError(f"unknown packet evidence ID: {evidence_id}")
            evidence.append([
                evidence_id,
                str(row.get("source_type") or ""),
                bool(row.get("point_start_eligible", False)),
                _row_time(row) - packet_start_sec,
                str(
                    row.get("source_text")
                    or row.get("display_excerpt")
                    or row.get("excerpt")
                    or row.get("text")
                    or ""
                ),
            ])
        packet_row = {
            "story_packet_id": packet_id,
            "d2_ref": str(packet.get("d2_ref") or ""),
            "evidence_columns": [
                "evidence_id", "source_type", "point_start_eligible",
                "relative_sec", "source_text"
            ],
            "ordered_evidence": evidence,
        }
        point_ref = str(packet.get("point_ref") or "")
        if point_ref:
            packet_row["canonical_point"] = {
                "point_ref": point_ref,
                "title": str(packet.get("point_title") or ""),
                "content": str(packet.get("point_content") or ""),
                "d1_ref": str(packet.get("d1_ref") or ""),
                "d2_ref": str(packet.get("d2_ref") or ""),
            }
        packet_rows.append(packet_row)

    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "broadcast_map" / "story_point_selector.md"
    decision_contracts = [{
        "story_packet_id": "string",
        "decision": "select",
        "title": "string",
        "why_notable": "string",
        "used_evidence_ids": ["evidence_id"],
        "roles": {key: ["evidence_id"] for key in ROLE_KEYS},
    }]
    if not canonical_point_mode:
        decision_contracts.append({
            "story_packet_id": "string",
            "decision": "reject",
            "reject_reason": "string",
        })
    payload = {
        "instructions": prompt_path.read_text(encoding="utf-8"),
        "response_contract": {"decisions": decision_contracts},
        "story_packets": packet_rows,
    }
    result = _canonical_json(payload)
    serialized = _canonical_json({"system_prompt": SYSTEM_PROMPT, "user_prompt": result})
    size = len(serialized.encode("utf-8"))
    if size > TARGET_REQUEST_BYTES:
        raise ValueError(
            f"story_point_selector_serialized_request_exceeds_target:{size}>{TARGET_REQUEST_BYTES}"
        )
    if HARD_REQUEST_BYTES - size < MIN_REQUEST_RESERVE_BYTES:
        raise ValueError("story_point_selector_request_reserve_below_4096")
    return result


def build_story_point_selector_batches(
    *,
    packets: Sequence[Mapping[str, Any]],
    evidence_ledger: Mapping[str, Mapping[str, Any]],
    max_packets_per_batch: int = 5,
) -> list[dict[str, Any]]:
    """Pack every Story packet into stable API-budget batches without sampling."""

    batch_limit = max(1, int(max_packets_per_batch))
    batches: list[dict[str, Any]] = []
    current: list[Mapping[str, Any]] = []

    def append_batch(rows: list[Mapping[str, Any]]) -> None:
        request = build_story_point_selector_request(
            packets=rows, evidence_ledger=evidence_ledger,
        )
        batches.append({
            "batch_index": len(batches) + 1,
            "story_packet_ids": [str(row.get("story_packet_id") or "") for row in rows],
            "point_refs": [str(row.get("point_ref") or "") for row in rows],
            "request": request,
        })

    for packet in packets:
        candidate = [*current, packet]
        try:
            build_story_point_selector_request(
                packets=candidate, evidence_ledger=evidence_ledger,
            )
        except ValueError as exc:
            if not current or not str(exc).startswith(
                "story_point_selector_serialized_request_exceeds_target:"
            ):
                raise
            append_batch(current)
            current = [packet]
            build_story_point_selector_request(
                packets=current, evidence_ledger=evidence_ledger,
            )
            continue
        if len(candidate) > batch_limit:
            append_batch(current)
            current = [packet]
        else:
            current = candidate
    if current:
        append_batch(current)
    return batches


def _validated_refs(
    values: Any, *, allowed: set[str], label: str, allow_empty: bool = True
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a list")
    refs = [str(value) for value in values]
    if not allow_empty and not refs:
        raise ValueError(f"{label} must not be empty")
    if len(refs) != len(set(refs)):
        raise ValueError(f"{label} contains duplicate evidence IDs")
    if any(value not in allowed for value in refs):
        raise ValueError(f"{label} references evidence outside its packet")
    return refs


def _resolve_point_start(
    *, roles: Mapping[str, list[str]], evidence_ledger: Mapping[str, Mapping[str, Any]]
) -> tuple[str, int, str | None]:
    for role_name, fallback_reason in (
        ("setup", None),
        ("performance", "setup_missing_used_performance"),
        ("event", "setup_and_performance_missing_used_event"),
    ):
        eligible: list[tuple[int, str]] = []
        for evidence_id in roles[role_name]:
            row = evidence_ledger.get(evidence_id)
            if (
                isinstance(row, Mapping)
                and str(row.get("role") or "") == "primary_semantic"
                and bool(row.get("point_start_eligible", False))
            ):
                eligible.append((_row_time(row), evidence_id))
        if eligible:
            sec, evidence_id = min(eligible, key=lambda value: (value[0], value[1]))
            return evidence_id, sec, fallback_reason
    raise ValueError("selected story has no eligible setup, performance, or event start")


def materialize_story_point_selection(
    *,
    response: Mapping[str, Any],
    packets: Sequence[Mapping[str, Any]],
    evidence_ledger: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate one Story decision for every packet without owning Point time."""

    if set(response) != {"decisions"} or not isinstance(response.get("decisions"), list):
        raise ValueError("invalid story selector response shape")
    packet_index = {str(packet.get("story_packet_id") or ""): packet for packet in packets}
    if "" in packet_index or len(packet_index) != len(packets):
        raise ValueError("input packets must have unique non-empty IDs")
    point_refs = [
        str(packet.get("point_ref") or "") for packet in packets
        if str(packet.get("point_ref") or "")
    ]
    if len(point_refs) != len(set(point_refs)):
        raise ValueError("canonical Point must have exactly one Story packet")

    decision_by_packet: dict[str, Mapping[str, Any]] = {}
    for decision in response["decisions"]:
        if not isinstance(decision, Mapping):
            raise ValueError("each packet decision must be an object")
        packet_id = str(decision.get("story_packet_id") or "")
        if packet_id not in packet_index:
            raise ValueError("unknown story_packet_id")
        if packet_id in decision_by_packet:
            raise ValueError("duplicate story packet decision")
        decision_by_packet[packet_id] = decision
    missing = sorted(set(packet_index) - set(decision_by_packet))
    if missing:
        raise ValueError(f"story packets omitted from response: {missing[:3]}")

    selected_points: list[dict[str, Any]] = []
    rejected_packets: list[dict[str, str]] = []
    normalized_decisions: list[dict[str, Any]] = []
    discarded_unknown_evidence_refs: set[str] = set()
    discarded_cross_packet_evidence_refs: set[str] = set()
    for packet in packets:
        packet_id = str(packet["story_packet_id"])
        decision = decision_by_packet[packet_id]
        kind = str(decision.get("decision") or "")
        if kind == "reject":
            if str(packet.get("point_ref") or ""):
                raise ValueError("canonical Point requires exactly one Story")
            if set(decision) != {"story_packet_id", "decision", "reject_reason"}:
                raise ValueError("reject decision must use closed shape")
            reason = str(decision.get("reject_reason") or "").strip()
            if not reason:
                raise ValueError("reject decision requires a reason")
            row = {"story_packet_id": packet_id, "decision": "reject", "reject_reason": reason}
            rejected_packets.append({"story_packet_id": packet_id, "reject_reason": reason})
            normalized_decisions.append(row)
            continue
        if kind != "select":
            raise ValueError("packet decision must be select or reject")
        expected_keys = {
            "story_packet_id", "decision", "title", "why_notable", "used_evidence_ids", "roles"
        }
        if set(decision) != expected_keys:
            raise ValueError("select decision must use closed shape")
        title = str(decision.get("title") or "").strip()
        why_notable = str(decision.get("why_notable") or "").strip()
        if not title or not why_notable:
            raise ValueError("selected story requires title and why_notable")
        allowed = {str(value) for value in packet.get("evidence_ids") or []}
        raw_roles = decision.get("roles")
        if not isinstance(raw_roles, Mapping) or set(raw_roles) != set(ROLE_KEYS):
            raise ValueError("selector roles must use closed shape")
        raw_used = decision.get("used_evidence_ids")
        outside_refs = {
            str(value)
            for values in (raw_used, *(raw_roles[key] for key in ROLE_KEYS))
            if isinstance(values, list)
            for value in values
            if str(value) not in allowed
        }
        if outside_refs:
            if str(packet.get("point_ref") or ""):
                known_cross_packet_refs = outside_refs.intersection(evidence_ledger)
                discarded_cross_packet_evidence_refs.update(
                    known_cross_packet_refs
                )
                discarded_unknown_evidence_refs.update(
                    outside_refs - known_cross_packet_refs
                )
                raw_used = [
                    value for value in raw_used
                    if str(value) in allowed
                ] if isinstance(raw_used, list) else raw_used
                raw_roles = {
                    key: [
                        value for value in raw_roles[key]
                        if str(value) in allowed
                    ] if isinstance(raw_roles[key], list) else raw_roles[key]
                    for key in ROLE_KEYS
                }
            else:
                reason = "invalid_evidence_reference"
                row = {
                    "story_packet_id": packet_id,
                    "decision": "reject",
                    "reject_reason": reason,
                }
                rejected_packets.append({"story_packet_id": packet_id, "reject_reason": reason})
                normalized_decisions.append(row)
                continue
        used = _validated_refs(
            raw_used, allowed=allowed,
            label="used_evidence_ids", allow_empty=False,
        )
        roles = {
            key: _validated_refs(raw_roles[key], allowed=allowed, label=f"roles.{key}")
            for key in ROLE_KEYS
        }
        if len(roles["setup"]) > 1:
            raise ValueError("roles.setup must contain at most one minimum-sufficient start")
        role_refs = {value for values in roles.values() for value in values}
        if not role_refs:
            raise ValueError("selected story requires at least one role evidence reference")
        used = list(
            dict.fromkeys(
                [
                    *used,
                    *(
                        ref
                        for key in ROLE_KEYS
                        for ref in roles[key]
                    ),
                ]
            )
        )
        body = {
            "story_packet_id": packet_id,
            "d2_ref": str(packet.get("d2_ref") or ""),
            "title": title,
            "why_notable": why_notable,
            "used_evidence_ids": used,
            "roles": roles,
        }
        point_ref = str(packet.get("point_ref") or "")
        if point_ref:
            body.update({
                "point_ref": point_ref,
                "d1_ref": str(packet.get("d1_ref") or ""),
                "authority": {
                    "point_owner": "broadcast_map_finalizer",
                    "story_time_authority": "none",
                },
            })
        else:
            point_start_id, sec, fallback_reason = _resolve_point_start(
                roles=roles, evidence_ledger=evidence_ledger
            )
            body.update({
                "point_start_evidence_id": point_start_id,
                "point_start_fallback_reason": fallback_reason,
                "timestamp": _hms(sec),
                "currentTime": sec,
                "link_currentTime": sec,
                "authority": {
                    "clock_owner": "code_owned_evidence_ledger",
                    "adoption_status": "point_shadow_only",
                },
            })
            body["point_shadow_id"] = "point-shadow-" + _sha(body)[:24]
        selected_points.append(body)
        normalized_decisions.append({
            "story_packet_id": packet_id,
            "decision": "select",
            "title": title,
            "why_notable": why_notable,
            "used_evidence_ids": used,
            "roles": roles,
        })

    return {
        "decisions": normalized_decisions,
        "selected_points": selected_points,
        "stories": selected_points,
        "rejected_packets": rejected_packets,
        "packet_decision_count": len(normalized_decisions),
        "packet_count": len(packets),
        "all_packets_decided": True,
        "discarded_unknown_evidence_ref_count": len(
            discarded_unknown_evidence_refs
        ),
        "discarded_cross_packet_evidence_ref_count": len(
            discarded_cross_packet_evidence_refs
        ),
    }


def run_story_point_selector_shadow(
    *,
    shadow_receipt: Mapping[str, Any],
    config: Mapping[str, Any],
    video_no: str,
    raw_response_path: Path,
    call_llm_func: Any | None = None,
) -> dict[str, Any]:
    """Run the normal story shadow through the manager-selected summary route.

    Provider configuration is narrowed in a local copy.  The application
    defaults are never mutated.  Raw response bytes are persisted before JSON,
    schema, ID, or model-result validation.
    """

    packets = list(shadow_receipt.get("story_packets") or [])
    ledger = dict(shadow_receipt.get("story_evidence_ledger") or {})
    if not packets or not ledger:
        raise ValueError("normal story shadow is missing packets or evidence ledger")
    packet_index = {
        str(packet.get("story_packet_id") or ""): packet for packet in packets
    }
    if "" in packet_index or len(packet_index) != len(packets):
        raise ValueError("normal story shadow has invalid packet IDs")

    raw_batches = shadow_receipt.get("story_point_selector_batches")
    if raw_batches is None:
        request = str(shadow_receipt.get("story_point_selector_request") or "")
        if not request:
            raise ValueError(
                str(
                    shadow_receipt.get("story_point_selector_request_error")
                    or "story request unavailable"
                )
            )
        batches: list[dict[str, Any]] = [{
            "batch_index": 1,
            "story_packet_ids": list(packet_index),
            "request": request,
        }]
    else:
        if not isinstance(raw_batches, list) or not raw_batches:
            raise ValueError("story selector batches must be a non-empty list")
        batches = [dict(batch) for batch in raw_batches if isinstance(batch, Mapping)]
        if len(batches) != len(raw_batches):
            raise ValueError("story selector batch must be an object")

    from .manager_outline import (
        assert_single_flight_meta,
        is_summary_route_fallback_failure,
        resolve_single_flight_route,
        summary_stage_route_configs,
    )
    from .summary_quality_profile import SOL_MODEL

    route_configs = summary_stage_route_configs(dict(config), model=SOL_MODEL)
    route_index = 0

    if call_llm_func is None:
        from .llm_gateway import call_llm as call_llm_func
    raw_response_path.parent.mkdir(parents=True, exist_ok=True)
    selections: list[dict[str, Any]] = []
    metas: list[dict[str, Any]] = []
    raw_hashes: list[str] = []
    processed_packet_ids: list[str] = []
    json_syntax_repair_count = 0
    for batch_position, batch in enumerate(batches, 1):
        packet_ids = [str(value) for value in batch.get("story_packet_ids") or []]
        if (
            not packet_ids
            or len(packet_ids) != len(set(packet_ids))
            or any(packet_id not in packet_index for packet_id in packet_ids)
        ):
            raise ValueError("story selector batch references invalid packet IDs")
        if set(packet_ids).intersection(processed_packet_ids):
            raise ValueError("story selector packet appears in more than one batch")
        request = str(batch.get("request") or "")
        if not request:
            raise ValueError("story selector batch request is missing")
        batch_path = (
            raw_response_path
            if len(batches) == 1
            else raw_response_path.with_name(
                f"{raw_response_path.stem}.batch-{batch_position:03d}{raw_response_path.suffix}"
            )
        )
        while True:
            strict_config = route_configs[route_index]
            expected_provider, expected_model = resolve_single_flight_route(strict_config)
            meta: dict[str, Any] = {}
            try:
                raw = call_llm_func(
                    request,
                    SYSTEM_PROMPT,
                    task="summary",
                    timeout=int(strict_config.get("codex_job_timeout") or 300),
                    model=expected_model,
                    config=strict_config,
                    meta_out=meta,
                    context={
                        "video_no": str(video_no),
                        "phase": "sc_v2_r1_normal_story_point_shadow",
                        "story_batch_index": batch_position,
                        "story_batch_count": len(batches),
                        "answer_free": True,
                        "retry_forbidden": True,
                        "fallback_forbidden": True,
                        "llm_provenance_path": str(
                            batch_path.with_suffix(".provenance.jsonl")
                        ),
                    },
                )
                break
            except Exception as exc:
                has_fallback = route_index + 1 < len(route_configs)
                if not has_fallback or not is_summary_route_fallback_failure(exc):
                    raise
                route_index += 1
        batch_path.write_text(raw, encoding="utf-8")
        raw_hashes.append(hashlib.sha256(raw.encode("utf-8")).hexdigest())
        assert_single_flight_meta(
            meta, expected_provider=expected_provider, expected_model=expected_model,
        )
        response, repair_count = parse_story_point_selector_response(raw)
        json_syntax_repair_count += repair_count
        selections.append(materialize_story_point_selection(
            response=response,
            packets=[packet_index[packet_id] for packet_id in packet_ids],
            evidence_ledger=ledger,
        ))
        metas.append(meta)
        processed_packet_ids.extend(packet_ids)

    expected_packet_ids = [str(packet["story_packet_id"]) for packet in packets]
    if processed_packet_ids != expected_packet_ids:
        raise ValueError("story selector batches must preserve every packet in stable order")
    selected_points = [
        row for selection in selections for row in selection["selected_points"]
    ]
    selection = {
        "decisions": [row for item in selections for row in item["decisions"]],
        "selected_points": selected_points,
        "stories": selected_points,
        "rejected_packets": [
            row for item in selections for row in item["rejected_packets"]
        ],
        "packet_decision_count": sum(
            int(item["packet_decision_count"]) for item in selections
        ),
        "packet_count": len(packets),
        "all_packets_decided": True,
        "batch_count": len(batches),
        "discarded_unknown_evidence_ref_count": sum(
            int(item.get("discarded_unknown_evidence_ref_count") or 0)
            for item in selections
        ),
        "discarded_cross_packet_evidence_ref_count": sum(
            int(item.get("discarded_cross_packet_evidence_ref_count") or 0)
            for item in selections
        ),
        "json_syntax_repair_count": json_syntax_repair_count,
    }
    result = dict(shadow_receipt)
    result["story_point_selection"] = selection
    result["story_provider_meta"] = {
        "selected_provider": str(metas[-1].get("selected_provider") or ""),
        "selected_providers": list(dict.fromkeys(
            str(meta.get("selected_provider") or "")
            for meta in metas
            if str(meta.get("selected_provider") or "")
        )),
        "actual_model": str(metas[0].get("actual_model") or ""),
        "provider_attempt_count": sum(
            int(meta.get("provider_attempt_count") or 0) for meta in metas
        ),
        "call_count": sum(int(meta.get("call_count") or 0) for meta in metas),
        "model_fallback_count": sum(
            int(meta.get("model_fallback_count") or 0) for meta in metas
        ),
        "batch_count": len(batches),
        "raw_response_sha256": hashlib.sha256(
            _canonical_json(raw_hashes).encode("utf-8")
        ).hexdigest(),
        "raw_response_sha256s": raw_hashes,
        "raw_persisted_before_validation": True,
    }
    return result
