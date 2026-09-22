"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations
from typing import Any

import hashlib

import json

from pathlib import Path

from .editorial_highlight_projection import _highlight_story_refs

from .editorial_highlight_candidate_discovery import _build_approved_story_discovery_request, _build_context_candidate_cards, _build_context_candidate_gate_request, _build_whole_broadcast_approved_story_packets, _parse_approved_story_discovery_response, _parse_context_candidate_gate_response, _reconcile_context_candidates

from .highlight_story_window import project_editorial_highlights_markdown


_AUTHOR_TERMINAL_STATES = {
    "semantic_closure_found",
    "relation_broken",
    "source_unavailable",
    "budget_exhausted",
    "no_editorial_value",
    "unvisited",
}


_VALID_ZERO_TERMINAL_STATES = {"relation_broken", "no_editorial_value"}


_INCOMPLETE_TERMINAL_STATES = {
    "source_unavailable",
    "budget_exhausted",
    "unvisited",
}


def _author_batch_outcome_rejection(
    decision: dict,
    *,
    allowed_groups: list[set[str]],
    allowed_evidence_refs: set[str],
    allowed_contexts: list[dict] | None = None,
) -> str:
    """Fail closed when an eligible relation has no explained author outcome."""

    proposals = list(decision.get("proposals") or [])
    group_outcomes = list(decision.get("group_outcomes") or [])
    if not proposals and not group_outcomes:
        return "untyped_empty_editorial_highlight_proposals"

    context_index = {
        str(row.get("context_identity") or ""): row
        for row in allowed_contexts or []
        if str(row.get("context_identity") or "")
    }
    normalized_allowed = {frozenset(group) for group in allowed_groups if group}
    materialized_groups: set[frozenset[str]] = set()
    materialized_contexts: set[str] = set()
    for proposal in proposals:
        proposal_refs = frozenset(_highlight_story_refs(proposal))
        if context_index:
            context_identity = str(proposal.get("context_identity") or "")
            context = context_index.get(context_identity)
            if context is None:
                return "highlight_requires_known_reconciled_context_identity"
            if context_identity in materialized_contexts:
                return "reconciled_context_must_materialize_exactly_once"
            if list(proposal.get("story_refs") or []) != list(
                context.get("required_story_refs_exact") or context.get("story_refs") or []
            ):
                return "highlight_must_match_exact_reconciled_story_union"
            if list(proposal.get("required_card_refs") or []) != list(
                context.get("required_card_refs") or []
            ):
                return "highlight_must_match_exact_reconciled_card_union"
            materialized_contexts.add(context_identity)
            continue
        if proposal_refs not in normalized_allowed:
            return "highlight_must_match_exact_reconciled_story_union"
        if proposal_refs in materialized_groups:
            return "reconciled_context_must_materialize_exactly_once"
        materialized_groups.add(proposal_refs)
    decided_groups: set[frozenset[str]] = set()
    decided_contexts: set[str] = set()
    for outcome in group_outcomes:
        expected_fields = {
            "story_refs",
            "terminal_state",
            "reason",
            "evidence_refs",
        }
        if context_index:
            expected_fields.update({"context_identity", "required_card_refs"})
        if set(outcome) != expected_fields:
            return "invalid_editorial_highlight_group_outcome"
        story_refs = outcome.get("story_refs")
        if (
            not isinstance(story_refs, list)
            or not story_refs
            or any(not isinstance(ref, str) or not ref.strip() for ref in story_refs)
        ):
            return "invalid_editorial_highlight_group_outcome_story_refs"
        group = frozenset(ref.strip() for ref in story_refs)
        if context_index:
            context_identity = str(outcome.get("context_identity") or "")
            context = context_index.get(context_identity)
            if context is None:
                return "editorial_highlight_group_outcome_escapes_discovery"
            if list(story_refs) != list(
                context.get("required_story_refs_exact") or context.get("story_refs") or []
            ) or list(outcome.get("required_card_refs") or []) != list(
                context.get("required_card_refs") or []
            ):
                return "invalid_editorial_highlight_group_outcome_exact_refs"
            if context_identity in decided_contexts:
                return "duplicate_editorial_highlight_group_outcome"
            if context_identity in materialized_contexts:
                return "editorial_highlight_group_has_proposal_and_terminal_outcome"
            decided_contexts.add(context_identity)
        elif group not in normalized_allowed:
            return "editorial_highlight_group_outcome_escapes_discovery"
        if not context_index and group in decided_groups:
            return "duplicate_editorial_highlight_group_outcome"
        if not context_index and group in materialized_groups:
            return "editorial_highlight_group_has_proposal_and_terminal_outcome"
        if not context_index:
            decided_groups.add(group)

        terminal_state = str(outcome.get("terminal_state") or "").strip()
        if terminal_state not in _AUTHOR_TERMINAL_STATES:
            return "invalid_editorial_highlight_terminal_state"
        reason = str(outcome.get("reason") or "").strip()
        evidence_refs = outcome.get("evidence_refs")
        if not reason:
            return "editorial_highlight_terminal_reason_required"
        if (
            not isinstance(evidence_refs, list)
            or not evidence_refs
            or any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs)
        ):
            return "editorial_highlight_terminal_evidence_required"
        normalized_evidence_refs = [ref.strip() for ref in evidence_refs]
        if len(normalized_evidence_refs) != len(set(normalized_evidence_refs)):
            return "editorial_highlight_terminal_evidence_must_be_unique"
        if not set(normalized_evidence_refs).issubset(allowed_evidence_refs):
            return "editorial_highlight_terminal_evidence_outside_author_packet"
        if terminal_state in _INCOMPLETE_TERMINAL_STATES:
            return f"editorial_highlight_authoring_incomplete:{terminal_state}"
        if terminal_state == "semantic_closure_found" and not proposals:
            return "semantic_closure_found_without_highlight_proposal"

    if context_index and materialized_contexts | decided_contexts != set(context_index):
        return "editorial_highlight_authoring_incomplete:unvisited"
    if not context_index and materialized_groups | decided_groups != normalized_allowed:
        return "editorial_highlight_authoring_incomplete:unvisited"
    if not proposals:
        terminal_states = {
            str(outcome.get("terminal_state") or "").strip()
            for outcome in group_outcomes
        }
        if not terminal_states.issubset(_VALID_ZERO_TERMINAL_STATES):
            return "invalid_zero_highlight_terminal_state"
    return ""


def _exact_chat_transport_aliases(
    *,
    video_no: str,
    chats: list[dict],
    evidence_catalog: list[dict] | tuple[dict, ...] | Any,
) -> tuple[dict[str, str], list[dict]]:
    """Recover exact request-visible chat refs from canonical source lineage.

    Canonical replay-chat evidence keeps a stable ``exact_source_ref`` that
    includes the original row identity, while the author transport deliberately
    exposes only ``chat-ms:*`` refs.  When several chat rows have identical text
    in one second, time/text alone is ambiguous.  Rebuild the stable identity
    from the frozen raw row and retain exactly the matching millisecond ref.
    The returned alias map is transient and is removed from the model request.
    """

    if not str(video_no or "").strip():
        return {}, []
    from .timed_evidence import build_timeline_source_evidence

    aliases: dict[str, str] = {}
    transport_rows: dict[str, dict] = {}
    for canonical in evidence_catalog or []:
        if not isinstance(canonical, dict):
            continue
        canonical_ref = str(
            canonical.get("evidence_id") or canonical.get("ref") or ""
        )
        source_type = str(canonical.get("source_type") or "")
        exact_source_ref = str(canonical.get("exact_source_ref") or "")
        source_text = str(canonical.get("source_text") or "").strip()
        if (
            not canonical_ref
            or source_type not in {"chat", "replay_chat"}
            or not exact_source_ref.startswith("replay_chat:")
            or not source_text
        ):
            continue
        try:
            canonical_time = int(float(canonical["time_sec"]))
        except (KeyError, TypeError, ValueError):
            continue
        exact_matches: list[dict] = []
        for raw in chats:
            if not isinstance(raw, dict):
                continue
            try:
                ms = max(0, int(raw.get("ms") or 0))
            except (TypeError, ValueError):
                continue
            if ms // 1000 != canonical_time:
                continue
            text = str(raw.get("msg") or "").strip()
            if text != source_text:
                continue
            manifest = build_timeline_source_evidence(
                video_id=str(video_no),
                duration_sec=None,
                subtitle_cues=[],
                chat_records=[raw],
            )
            item = next(iter(manifest.get("items") or []), None)
            source_identity = str((item or {}).get("source_identity") or "")
            if source_identity and exact_source_ref.endswith(f":{source_identity}"):
                exact_matches.append(
                    {
                        "ref": f"chat-ms:{ms}",
                        "time_sec": canonical_time,
                        "text": text,
                    }
                )
        if len(exact_matches) > 1:
            raise ValueError("author_evidence_alias_ambiguous")
        if not exact_matches:
            continue
        transport = exact_matches[0]
        transport_ref = str(transport["ref"])
        if transport_ref in aliases or canonical_ref in aliases.values():
            raise ValueError("author_evidence_alias_ambiguous")
        aliases[transport_ref] = canonical_ref
        transport_rows[transport_ref] = transport
    return aliases, list(transport_rows.values())


def _project_gap_coverage_requirements(packet: dict) -> list[dict]:
    """Expose the validator's bridge batches with request-visible refs only."""

    ref_times: dict[str, float] = {}
    for rows in (packet.get("stt_rows") or [], packet.get("chat_rows") or []):
        for row in rows:
            if not isinstance(row, dict):
                continue
            ref = str(row.get("ref") or "")
            try:
                time_sec = float(row.get("time_sec"))
            except (TypeError, ValueError):
                continue
            if ref:
                ref_times[ref] = time_sec

    projected: list[dict] = []
    for index, batch in enumerate(packet.get("temporal_scan_batches") or [], start=1):
        if not isinstance(batch, dict):
            continue
        start_sec = float(batch.get("start_sec") or 0)
        end_sec = float(batch.get("end_sec") or 0)
        eligible_refs = [
            ref
            for ref in dict.fromkeys(
                str(ref)
                for ref in [
                    *(batch.get("stt_refs") or []),
                    *(batch.get("chat_refs") or []),
                ]
                if str(ref)
            )
            if ref in ref_times and start_sec < ref_times[ref] < end_sec
        ]
        projected.append(
            {
                "batch_id": f"bridge-batch-{index:03d}",
                "start_sec": int(start_sec) if start_sec.is_integer() else start_sec,
                "end_sec": int(end_sec) if end_sec.is_integer() else end_sec,
                "eligible_evidence_refs": eligible_refs,
                "uncuttable_bridge": not eligible_refs,
            }
        )
    return projected


def _gap_coverage_repair_instruction(
    *,
    evidence_packets: list[dict],
    proposals: list[dict],
    rejection_reason: str,
) -> str:
    """Name only the failed bridge obligations from the previous answer."""

    if rejection_reason not in {
        "source_span_exclusion_evidence_does_not_cover_gap",
        "source_span_exclusion_bridge_coverage_unavailable",
    }:
        return ""
    packet_by_context = {
        str(packet.get("context_identity") or ""): packet
        for packet in evidence_packets
        if str(packet.get("context_identity") or "")
    }
    failures: list[dict] = []
    for proposal in proposals:
        context_identity = str(proposal.get("context_identity") or "")
        packet = packet_by_context.get(context_identity)
        if not packet:
            continue
        ref_times = {
            str(row.get("ref") or ""): float(row.get("time_sec"))
            for rows in (packet.get("stt_rows") or [], packet.get("chat_rows") or [])
            for row in rows
            if isinstance(row, dict)
            and str(row.get("ref") or "")
            and isinstance(row.get("time_sec"), (int, float))
            and not isinstance(row.get("time_sec"), bool)
        }
        spans = [
            row
            for row in proposal.get("source_spans") or []
            if isinstance(row, dict)
        ]
        for prior, current in zip(spans, spans[1:]):
            try:
                gap_start = float(prior.get("end_sec"))
                gap_end = float(current.get("start_sec"))
            except (TypeError, ValueError):
                continue
            if gap_end <= gap_start:
                continue
            exclusion = current.get("exclusion_before") or {}
            cited_refs = {
                str(ref)
                for ref in exclusion.get("evidence_refs") or []
                if str(ref)
            }
            required_batches: list[dict] = []
            missing_batches: list[dict] = []
            uncuttable_ids: list[str] = []
            for requirement in packet.get("gap_coverage_requirements") or []:
                if not isinstance(requirement, dict):
                    continue
                if (
                    float(requirement.get("end_sec") or 0) <= gap_start
                    or float(requirement.get("start_sec") or 0) >= gap_end
                ):
                    continue
                batch_id = str(requirement.get("batch_id") or "")
                eligible_refs = [
                    str(ref)
                    for ref in requirement.get("eligible_evidence_refs") or []
                    if str(ref) in ref_times
                    and gap_start < ref_times[str(ref)] < gap_end
                ]
                if bool(requirement.get("uncuttable_bridge")) or not eligible_refs:
                    uncuttable_ids.append(batch_id)
                else:
                    projected_batch = {
                        "batch_id": batch_id,
                        "eligible_evidence_refs": eligible_refs,
                    }
                    required_batches.append(projected_batch)
                    if cited_refs.isdisjoint(eligible_refs):
                        missing_batches.append(projected_batch)
            if missing_batches or uncuttable_ids:
                failures.append(
                    {
                        "context_identity": context_identity,
                        "gap": {"start_sec": gap_start, "end_sec": gap_end},
                        "missing_batch_ids": [
                            row["batch_id"] for row in missing_batches
                        ],
                        "missing_batches": missing_batches,
                        "required_batches": required_batches,
                        "uncuttable_bridge_ids": uncuttable_ids,
                        "required_action": (
                            "keep_bridge_footage_continuous"
                            if uncuttable_ids
                            else "cite_one_eligible_ref_per_required_batch"
                        ),
                    }
                )
    if not failures:
        return ""
    return (
        "\n\nGAP COVERAGE REPAIR: Apply these exact request-visible bridge obligations "
        "to the previous answer. The answer is regenerated in full, so for every "
        "gap cite at least one eligible ref from every required_batches row, including "
        "rows that the previous answer already covered. Do not invent refs or internal IDs. "
        + json.dumps(failures, ensure_ascii=False, sort_keys=True)
    )


def _semantic_obligation_repair_instruction(
    *,
    evidence_packets: list[dict],
    proposals: list[dict],
    rejection_reason: str,
) -> str:
    """Project only missing request-visible semantic refs into attempt two."""

    if rejection_reason != "highlight_does_not_cover_required_semantic_evidence_interval":
        return ""
    packet_by_context = {
        str(packet.get("context_identity") or ""): packet
        for packet in evidence_packets
        if str(packet.get("context_identity") or "")
    }
    missing: list[dict] = []
    for proposal in proposals:
        context_identity = str(proposal.get("context_identity") or "")
        packet = packet_by_context.get(context_identity)
        if not packet:
            continue
        catalog = {
            str(row.get("evidence_id") or row.get("ref") or ""): row
            for row in packet.get("evidence_catalog") or []
            if isinstance(row, dict)
            and str(row.get("evidence_id") or row.get("ref") or "")
        }
        spans = [
            span
            for span in proposal.get("source_spans") or []
            if isinstance(span, dict)
        ]
        for obligation in packet.get("coverage_obligations") or []:
            if not isinstance(obligation, dict):
                continue
            try:
                start_sec = float(obligation["start_sec"])
                end_sec = float(obligation["end_sec"])
            except (KeyError, TypeError, ValueError):
                continue
            obligation_packet = dict(packet)
            obligation_packet["coverage_obligations"] = [obligation]
            try:
                aliases = _author_coverage_evidence_aliases(obligation_packet)
            except ValueError as exc:
                if str(exc) not in {
                    "author_evidence_alias_missing",
                    "author_evidence_alias_ambiguous",
                }:
                    raise
                aliases = {}
            allowed_transport_refs = sorted(
                transport_ref
                for transport_ref, lineage_ref in aliases.items()
                if isinstance(catalog.get(lineage_ref), dict)
                and start_sec
                <= float(catalog[lineage_ref].get("time_sec", -1))
                <= end_sec
            )
            covering = any(
                float(span.get("start_sec", -1)) <= start_sec
                and end_sec <= float(span.get("end_sec", -1))
                and not set(span.get("evidence_refs") or []).isdisjoint(
                    allowed_transport_refs
                )
                for span in spans
            )
            if covering:
                continue
            missing.append(
                {
                    "context_identity": context_identity,
                    "diagnostic_key": str(obligation.get("obligation_id") or ""),
                    "phase": str(obligation.get("phase") or ""),
                    "start_sec": (
                        int(start_sec) if start_sec.is_integer() else start_sec
                    ),
                    "end_sec": int(end_sec) if end_sec.is_integer() else end_sec,
                    "allowed_transport_refs": allowed_transport_refs,
                    "required_action": (
                        "cite_one_allowed_transport_ref_in_covering_span"
                        if allowed_transport_refs
                        else "report_source_unavailable_or_fail_closed"
                    ),
                }
            )
    if not missing:
        return ""
    return (
        "\n\nSEMANTIC OBLIGATION REPAIR: Apply only these request-local diagnostics "
        "to the previous answer. Each covering source_span cites one supplied "
        "allowed_transport_ref. An empty list means report source_unavailable or "
        "fail closed; never invent a ref or copy an internal ID. "
        + json.dumps(
            {"missing_semantic_obligations": missing},
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _build_approved_story_author_packets(
    *,
    discovery_groups: list[dict],
    discovery_packet: dict,
    requirements: dict[str, dict],
    chunks: list[dict],
    chats: list[dict],
    broadcast_map_evidence: dict,
    video_no: str = "",
    evidence_radius_sec: int = 120,
    max_stt_rows: int = 96,
    max_chat_rows: int = 64,
) -> tuple[list[dict], dict[str, dict]]:
    """Re-read bounded source windows for discovered groups before authoring."""

    from .broadcast_map_evidence import build_highlight_local_evidence_packet

    approved_index = {
        str(row.get("story_ref") or ""): row
        for row in discovery_packet.get("approved_stories") or []
        if str(row.get("story_ref") or "")
    }
    evidence_index = {
        str(row.get("evidence_id") or row.get("ref") or ""): row
        for row in [
            *(discovery_packet.get("evidence_catalog") or []),
            *(discovery_packet.get("stt_rows") or []),
            *(discovery_packet.get("chat_rows") or []),
        ]
        if isinstance(row, dict)
        and str(row.get("evidence_id") or row.get("ref") or "")
    }
    coverage_range_index = {
        str(row.get("ref") or ""): row
        for row in discovery_packet.get("story_seed", {}).get("coverage_ranges") or []
        if isinstance(row, dict) and str(row.get("ref") or "")
    }
    source_bounds = list(
        discovery_packet.get("story_seed", {}).get("source_ranges") or []
    )
    lower = min((int(row.get("start_sec") or 0) for row in source_bounds), default=0)
    upper = max((int(row.get("end_sec") or 0) for row in source_bounds), default=1)
    radius = max(1, min(450, int(evidence_radius_sec)))
    author_packets: list[dict] = []
    updated_requirements = {
        story_ref: {
            **dict(requirement),
            "author_evidence_refs": list(
                dict.fromkeys(
                    ref
                    for refs in (requirement.get("required_story_roles") or {}).values()
                    for ref in refs
                )
            ),
        }
        for story_ref, requirement in requirements.items()
    }

    for group_index, group in enumerate(discovery_groups, start=1):
        story_refs = list(group["story_refs"])
        approved_stories = [approved_index[ref] for ref in story_refs]
        point_refs = list(
            dict.fromkeys(
                point_ref
                for row in approved_stories
                for point_ref in row.get("story_seed", {}).get("point_refs") or []
            )
        )
        d1_refs = list(
            dict.fromkeys(
                ref
                for row in approved_stories
                for ref in row.get("story_seed", {}).get("d1_refs") or []
            )
        )
        d2_refs = list(
            dict.fromkeys(
                ref
                for row in approved_stories
                for ref in row.get("story_seed", {}).get("d2_refs") or []
            )
        )
        anchor_refs = list(group["anchor_evidence_refs"])
        anchor_refs.extend(
            ref
            for story_ref in story_refs
            for refs in (
                updated_requirements[story_ref].get("required_story_roles") or {}
            ).values()
            for ref in refs
        )
        anchor_times = sorted(
            {
                int(
                    evidence_index.get(ref, {}).get("time_sec")
                    or evidence_index.get(ref, {}).get("canonical_time_sec")
                    or 0
                )
                for ref in anchor_refs
                if ref in evidence_index
            }
        )
        selected_ranges = [
            {
                "start_sec": max(
                    lower,
                    int(coverage_range_index[ref].get("start_sec") or 0),
                ),
                "end_sec": min(
                    upper,
                    int(coverage_range_index[ref].get("end_sec") or 0),
                ),
            }
            for ref in group.get("source_range_refs") or []
            if ref in coverage_range_index
        ]
        anchor_windows = [
            {
                "start_sec": max(lower, sec - radius),
                "end_sec": min(upper, sec + radius),
            }
            for sec in anchor_times
            if not any(
                int(row["start_sec"]) <= sec <= int(row["end_sec"])
                for row in selected_ranges
            )
        ]
        windows = sorted(
            [*selected_ranges, *anchor_windows],
            key=lambda row: (int(row["start_sec"]), int(row["end_sec"])),
        )
        if windows:
            windows = [{
                "start_sec": min(int(row["start_sec"]) for row in windows),
                "end_sec": max(int(row["end_sec"]) for row in windows),
            }]
        merged_windows: list[dict] = []
        for window in windows:
            if window["end_sec"] <= window["start_sec"]:
                continue
            if (
                merged_windows
                and window["start_sec"] <= merged_windows[-1]["end_sec"] + 1
            ):
                merged_windows[-1]["end_sec"] = max(
                    merged_windows[-1]["end_sec"], window["end_sec"]
                )
            else:
                merged_windows.append(dict(window))
        if not merged_windows:
            raise ValueError("highlight_discovery_group_has_no_materializable_anchor")
        seed = {
            "kind": "discovered_approved_story_group",
            "story_seed_id": f"approved-story-group-{group_index}",
            "story_refs": story_refs,
            "d1_refs": d1_refs,
            "d2_refs": d2_refs,
            "point_refs": point_refs,
            "point_anchors": [
                anchor
                for row in approved_stories
                for anchor in row.get("story_seed", {}).get("point_anchors") or []
            ],
            "semantic_anchors": [
                {
                    "ref": ref,
                    "time_sec": int(
                        evidence_index.get(ref, {}).get("time_sec")
                        or evidence_index.get(ref, {}).get("canonical_time_sec")
                        or 0
                    ),
                }
                for ref in dict.fromkeys(anchor_refs)
                if ref in evidence_index
            ],
            "source_ranges": merged_windows,
            "coverage_ranges": [
                dict(row)
                for row in coverage_range_index.values()
                if any(
                    int(row.get("end_sec") or 0) > int(window["start_sec"])
                    and int(row.get("start_sec") or 0) < int(window["end_sec"])
                    for window in merged_windows
                )
            ],
            "discovery_reason": str(group.get("reason") or ""),
        }
        packet = build_highlight_local_evidence_packet(
            story_seed=seed,
            chunks=chunks,
            chats=chats,
            evidence_bundle=broadcast_map_evidence,
            max_stt_rows=max_stt_rows,
            max_chat_rows=max_chat_rows,
        )
        candidate_signal_count = len(packet.pop("support_proposals", []) or [])
        packet["candidate_signal_lane"] = {
            "status": "excluded_from_canonical_highlight_authoring",
            "candidate_count": candidate_signal_count,
        }
        retrieved_catalog: dict[str, dict] = {}
        for source_type, rows in (
            ("stt", packet.get("stt_rows") or []),
            ("replay_chat", packet.get("chat_rows") or []),
        ):
            for row in rows:
                evidence_id = str(row.get("ref") or "")
                retrieved_catalog[evidence_id] = {
                    "evidence_id": evidence_id,
                    "role": "primary_semantic",
                    "point_start_eligible": False,
                    "source_type": source_type,
                    "time_sec": int(row.get("time_sec") or 0),
                    "source_text": str(row.get("text") or ""),
                    "exact_source_ref": evidence_id,
                }
        retrieved_refs = list(retrieved_catalog)
        gap_coverage_batches = [
            {
                "start_sec": int(row.get("start_sec") or 0),
                "end_sec": int(row.get("end_sec") or 0),
                "evidence_refs": list(
                    dict.fromkeys([
                        *(row.get("stt_refs") or []),
                        *(row.get("chat_refs") or []),
                    ])
                ),
            }
            for row in packet.get("temporal_scan_batches") or []
        ]
        author_catalog: dict[str, dict] = {}
        required_refs: list[str] = []
        for story_ref in story_refs:
            requirement = updated_requirements[story_ref]
            story_required_refs: list[str] = []
            for refs in (requirement.get("required_story_roles") or {}).values():
                for ref in refs:
                    if ref not in required_refs:
                        required_refs.append(ref)
                    if ref not in story_required_refs:
                        story_required_refs.append(ref)
                    row = (requirement.get("evidence_catalog") or {}).get(ref)
                    if isinstance(row, dict):
                        author_catalog[str(ref)] = dict(row)
            requirement["author_evidence_refs"] = list(
                dict.fromkeys([*story_required_refs, *retrieved_refs])
            )
            requirement["evidence_catalog"] = {
                **dict(requirement.get("evidence_catalog") or {}),
                **retrieved_catalog,
            }
            requirement["gap_coverage_batches"] = list(gap_coverage_batches)
        transport_aliases, required_chat_rows = _exact_chat_transport_aliases(
            video_no=video_no,
            chats=chats,
            evidence_catalog=list(author_catalog.values()),
        )
        existing_chat_rows = {
            str(row.get("ref") or ""): dict(row)
            for row in packet.get("chat_rows") or []
            if isinstance(row, dict) and str(row.get("ref") or "")
        }
        for row in required_chat_rows:
            ref = str(row.get("ref") or "")
            existing = existing_chat_rows.get(ref)
            if existing and existing != row:
                raise ValueError("author_evidence_alias_ambiguous")
            existing_chat_rows[ref] = dict(row)
            retrieved_catalog[ref] = {
                "evidence_id": ref,
                "role": "primary_semantic",
                "point_start_eligible": False,
                "source_type": "replay_chat",
                "time_sec": int(row.get("time_sec") or 0),
                "source_text": str(row.get("text") or ""),
                "exact_source_ref": ref,
            }
        packet["chat_rows"] = sorted(
            existing_chat_rows.values(),
            key=lambda row: (int(row.get("time_sec") or 0), str(row.get("ref") or "")),
        )
        retrieved_refs = list(retrieved_catalog)
        for story_ref in story_refs:
            requirement = updated_requirements[story_ref]
            requirement["author_evidence_refs"] = list(
                dict.fromkeys(
                    [*(requirement.get("author_evidence_refs") or []), *retrieved_refs]
                )
            )
            requirement["evidence_catalog"] = {
                **dict(requirement.get("evidence_catalog") or {}),
                **retrieved_catalog,
            }
        packet.update(
            {
                "story_refs": story_refs,
                "context_identity": str(group.get("context_identity") or ""),
                "reconciliation_action": str(
                    group.get("reconciliation_action") or ""
                ),
                "required_card_refs": list(group.get("required_card_refs") or []),
                "required_story_refs_exact": list(
                    group.get("required_story_refs_exact") or story_refs
                ),
                "coverage_obligations": [],
                "required_sibling_span_groups": list(
                    group.get("required_sibling_span_groups") or []
                ),
                "approved_stories": approved_stories,
                "required_evidence_refs": required_refs,
                "allowed_evidence_refs": list(
                    dict.fromkeys([*required_refs, *retrieved_refs])
                ),
                "evidence_catalog": list(author_catalog.values()),
                "_coverage_evidence_aliases": transport_aliases,
            }
        )
        packet["gap_coverage_requirements"] = _project_gap_coverage_requirements(
            packet
        )
        author_packets.append(packet)
    return author_packets, updated_requirements


def _compact_author_packet_to_request_budget(
    *,
    authority_revision_id: str,
    packet: dict,
    system_prompt: str,
    max_request_bytes: int = 35_904,
) -> tuple[dict, str]:
    """Fit one relation packet without changing its semantic source range.

    Row limits are transport constraints, not editorial windows.  Keep every
    required Story ref and at least one available row from every chronological
    scan batch, then spend the remaining byte budget on rows round-robin across
    those batches.  The deterministic validator retains the full internal
    evidence catalog; only the provider request is compacted.
    """

    from .highlight_story_window import build_editorial_highlight_request

    def request_for(candidate: dict) -> tuple[str | None, int]:
        try:
            request = build_editorial_highlight_request(
                authority_revision_id=authority_revision_id,
                evidence_packets=[_author_packet_model_projection(candidate)],
            )
        except ValueError as exc:
            if str(exc) != "editorial_highlight_request_exceeds_40000_bytes":
                raise
            return None, 40_001
        return request, len((system_prompt + request).encode("utf-8"))

    original = dict(packet)
    request, request_bytes = request_for(original)
    if request is not None and request_bytes <= max_request_bytes:
        return original, request

    row_index = {
        str(row.get("ref") or ""): (modality, dict(row))
        for modality, rows in (
            ("stt", original.get("stt_rows") or []),
            ("chat", original.get("chat_rows") or []),
        )
        for row in rows
        if isinstance(row, dict) and str(row.get("ref") or "")
    }
    required_refs = {
        str(ref)
        for ref in original.get("required_evidence_refs") or []
        if str(ref)
    }
    required_refs.update(
        str(row.get("ref") or "")
        for row in original.get("story_seed", {}).get("semantic_anchors") or []
        if isinstance(row, dict) and str(row.get("ref") or "")
    )
    required_refs.update(_author_coverage_evidence_aliases(original))
    for story in original.get("approved_stories") or []:
        if not isinstance(story, dict):
            continue
        required_refs.update(
            str(row.get("ref") or "")
            for row in story.get("evidence_anchors") or []
            if isinstance(row, dict) and str(row.get("ref") or "")
        )

    scan_batches = [
        dict(row)
        for row in original.get("temporal_scan_batches") or []
        if isinstance(row, dict)
    ]
    batch_ref_lists: list[list[str]] = []
    gap_requirements = _project_gap_coverage_requirements(original)
    for batch, gap_requirement in zip(scan_batches, gap_requirements):
        refs = [
            str(ref)
            for ref in [
                *(batch.get("stt_refs") or []),
                *(batch.get("chat_refs") or []),
            ]
            if str(ref) in row_index
        ]
        batch_ref_lists.append(refs)
        eligible_refs = [
            str(ref)
            for ref in gap_requirement.get("eligible_evidence_refs") or []
            if str(ref) in row_index
        ]
        if eligible_refs:
            required_refs.add(eligible_refs[len(eligible_refs) // 2])
        elif refs:
            required_refs.add(refs[0])

    selected_refs = {ref for ref in required_refs if ref in row_index}
    optional_refs: list[str] = []
    depth = 0
    while True:
        added = False
        for refs in batch_ref_lists:
            if depth < len(refs):
                ref = refs[depth]
                if ref not in required_refs and ref not in optional_refs:
                    optional_refs.append(ref)
                added = True
        if not added:
            break
        depth += 1
    optional_refs.extend(
        ref
        for ref, (_modality, row) in sorted(
            row_index.items(),
            key=lambda item: (
                int(item[1][1].get("time_sec") or 0),
                item[0],
            ),
        )
        if ref not in required_refs and ref not in optional_refs
    )

    input_stt_count = len(original.get("stt_rows") or [])
    input_chat_count = len(original.get("chat_rows") or [])

    def project(refs: set[str]) -> dict:
        candidate = dict(original)
        candidate["stt_rows"] = [
            row
            for ref, (modality, row) in row_index.items()
            if modality == "stt" and ref in refs
        ]
        candidate["chat_rows"] = [
            row
            for ref, (modality, row) in row_index.items()
            if modality == "chat" and ref in refs
        ]
        for key in ("stt_rows", "chat_rows"):
            candidate[key].sort(
                key=lambda row: (int(row.get("time_sec") or 0), str(row.get("ref") or ""))
            )
        candidate["temporal_scan_batches"] = [
            {
                **batch,
                "stt_refs": [
                    str(ref)
                    for ref in batch.get("stt_refs") or []
                    if str(ref) in refs
                ],
                "chat_refs": [
                    str(ref)
                    for ref in batch.get("chat_refs") or []
                    if str(ref) in refs
                ],
            }
            for batch in scan_batches
        ]
        candidate["gap_coverage_requirements"] = (
            _project_gap_coverage_requirements(candidate)
        )
        allowed_refs = {
            str(ref)
            for ref in original.get("allowed_evidence_refs") or []
            if str(ref)
        }
        candidate["allowed_evidence_refs"] = sorted(
            (allowed_refs & refs) | required_refs
        )
        candidate["evidence_catalog"] = [
            dict(row)
            for row in original.get("evidence_catalog") or []
            if isinstance(row, dict)
            and str(row.get("evidence_id") or row.get("ref") or "") not in refs
        ]
        candidate["request_transport"] = {
            "contract": "semantic_range_preserving_byte_budget.v1",
            "max_request_bytes": max_request_bytes,
            "input_stt_count": input_stt_count,
            "input_chat_count": input_chat_count,
            "selected_stt_count": len(candidate["stt_rows"]),
            "selected_chat_count": len(candidate["chat_rows"]),
            "scan_batch_count": len(scan_batches),
            "source_ranges_preserved": True,
        }
        candidate.pop("packet_sha256", None)
        candidate["packet_sha256"] = hashlib.sha256(
            json.dumps(
                candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return candidate

    compacted = project(selected_refs)
    request, request_bytes = request_for(compacted)
    if request is None or request_bytes > max_request_bytes:
        raise ValueError("approved_story_packet_required_evidence_exceeds_byte_target")

    for ref in optional_refs:
        candidate_refs = {*selected_refs, ref}
        candidate = project(candidate_refs)
        candidate_request, candidate_bytes = request_for(candidate)
        if candidate_request is None or candidate_bytes > max_request_bytes:
            continue
        selected_refs = candidate_refs
        compacted = candidate
        request = candidate_request

    return compacted, request


def _author_packet_model_projection(packet: dict) -> dict:
    """Remove deterministic receipts that repeat provider-visible evidence."""

    projected = {
        key: packet[key]
        for key in (
            "schema_version",
            "context_identity",
            "required_card_refs",
            "stt_rows",
            "chat_rows",
            "context_naming",
            "provenance_refs",
            "forbidden_answer_guard",
        )
        if key in packet
    }
    projected["evidence_catalog"] = [
        {
            key: row[key]
            for key in (
                "evidence_id",
                "ref",
                "source_type",
                "time_sec",
                "exact_source_ref",
                "role",
            )
            if key in row
        }
        for row in packet.get("evidence_catalog") or []
        if isinstance(row, dict)
    ]
    story_seed = dict(packet.get("story_seed") or {})
    projected["story_seed"] = {
        key: story_seed[key]
        for key in (
            "story_refs",
            "point_refs",
            "semantic_anchors",
            "source_ranges",
            "discovery_reason",
        )
        if key in story_seed
    }
    return projected


def _build_approved_story_author_request(
    *, authority_revision_id: str, evidence_packets: list[dict]
) -> str:
    from .highlight_story_window import build_editorial_highlight_request

    return build_editorial_highlight_request(
        authority_revision_id=authority_revision_id,
        evidence_packets=[
            _author_packet_model_projection(packet) for packet in evidence_packets
        ],
    )


def _author_coverage_evidence_aliases(packet: dict) -> dict[str, str]:
    """Resolve request-local transport refs to canonical evidence IDs.

    The author sees compact ``stt-sec:*``/``chat-ms:*`` rows while selector
    coverage obligations retain canonical ``evidence-*`` lineage.  Prefer exact
    modality/time/text; when request normalization changed text, accept only a
    sole canonical and sole transport row at that modality/time.  A missing or
    non-unique match is a deterministic preflight failure.
    """

    def time_value(row: dict) -> float:
        value = row.get("time_sec")
        return float(value) if value is not None else -1.0

    catalog = {
        str(row.get("evidence_id") or row.get("ref") or ""): row
        for row in packet.get("evidence_catalog") or []
        if isinstance(row, dict)
        and str(row.get("evidence_id") or row.get("ref") or "")
    }
    stt_rows = [
        row
        for row in packet.get("stt_rows") or []
        if isinstance(row, dict) and str(row.get("ref") or "").startswith("stt-sec:")
    ]
    chat_rows = [
        row
        for row in packet.get("chat_rows") or []
        if isinstance(row, dict) and str(row.get("ref") or "").startswith("chat-ms:")
    ]
    canonical_rows = [
        (canonical_ref, row)
        for canonical_ref, row in catalog.items()
        if isinstance(row, dict)
        and str(row.get("source_type") or "") in {"stt", "chat", "replay_chat"}
    ]
    aliases: dict[str, str] = {}
    transport_refs = {
        str(row.get("ref") or "")
        for row in [*stt_rows, *chat_rows]
        if str(row.get("ref") or "")
    }
    transport_times = {
        str(row.get("ref") or ""): time_value(row)
        for row in [*stt_rows, *chat_rows]
        if str(row.get("ref") or "")
    }
    for transport_ref, canonical_ref in dict(
        packet.get("_coverage_evidence_aliases") or {}
    ).items():
        transport_ref = str(transport_ref or "")
        canonical_ref = str(canonical_ref or "")
        if (
            not transport_ref
            or not canonical_ref
            or transport_ref not in transport_refs
            or canonical_ref not in catalog
        ):
            raise ValueError("author_evidence_alias_missing")
        if transport_ref in aliases or canonical_ref in aliases.values():
            raise ValueError("author_evidence_alias_ambiguous")
        aliases[transport_ref] = canonical_ref
    ambiguous_times: set[float] = set()
    for canonical_ref, canonical in canonical_rows:
        if canonical_ref in aliases.values():
            continue
        canonical_type = str(canonical.get("source_type") or "")
        candidates = stt_rows if canonical_type == "stt" else chat_rows
        candidates = [
            row for row in candidates if time_value(row) == time_value(canonical)
        ]
        same_time_canonical = [
            row
            for _ref, row in canonical_rows
            if time_value(row) == time_value(canonical)
            and (
                str(row.get("source_type") or "") == "stt"
                if canonical_type == "stt"
                else str(row.get("source_type") or "") in {"chat", "replay_chat"}
            )
        ]
        exact = [
            row
            for row in candidates
            if bool(str(canonical.get("source_text") or "").strip())
            and str(row.get("text") or "").strip()
            == str(canonical.get("source_text") or "").strip()
        ]
        if len(exact) > 1:
            ambiguous_times.add(time_value(canonical))
            continue
        if len(exact) == 1:
            transport = exact[0]
        elif len(candidates) == 1 and len(same_time_canonical) == 1:
            transport = candidates[0]
        else:
            if candidates:
                ambiguous_times.add(time_value(canonical))
            continue
        transport_ref = str(transport.get("ref") or "")
        if not transport_ref:
            continue
        if transport_ref in aliases or canonical_ref in aliases.values():
            raise ValueError("author_evidence_alias_ambiguous")
        aliases[transport_ref] = canonical_ref

    for obligation in packet.get("coverage_obligations") or []:
        if not isinstance(obligation, dict):
            raise ValueError("author_evidence_alias_missing")
        try:
            start_sec = float(obligation["start_sec"])
            end_sec = float(obligation["end_sec"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("author_evidence_alias_missing") from exc
        direct_transport_refs = {
            str(ref)
            for ref in obligation.get("evidence_refs") or []
            if str(ref) in transport_times
            and start_sec <= transport_times[str(ref)] <= end_sec
        }
        if any(start_sec <= value <= end_sec for value in ambiguous_times) and not any(
            canonical_ref in catalog
            and start_sec <= time_value(catalog[canonical_ref]) <= end_sec
            for canonical_ref in aliases.values()
        ) and not direct_transport_refs:
            raise ValueError("author_evidence_alias_ambiguous")
        if not any(
            canonical_ref in catalog
            and start_sec <= time_value(catalog[canonical_ref]) <= end_sec
            for canonical_ref in aliases.values()
        ) and not direct_transport_refs:
            raise ValueError("author_evidence_alias_missing")
    return aliases


def _author_context_requirements(packets: list[dict]) -> dict[str, dict]:
    """Project transient selector identity into deterministic author validation."""

    requirements: dict[str, dict] = {}
    for packet in packets:
        context_identity = str(packet.get("context_identity") or "").strip()
        if not context_identity:
            continue
        if context_identity in requirements:
            raise ValueError("invalid_or_duplicate_author_context_identity")
        required_story_refs = list(
            packet.get("required_story_refs_exact") or packet.get("story_refs") or []
        )
        required_card_refs = list(packet.get("required_card_refs") or [])
        if not required_story_refs or not required_card_refs:
            raise ValueError("author_context_exact_ref_union_required")
        requirements[context_identity] = {
            "reconciliation_action": str(
                packet.get("reconciliation_action") or ""
            ),
            "required_story_refs_exact": required_story_refs,
            "required_card_refs": required_card_refs,
            "coverage_obligations": list(
                packet.get("coverage_obligations") or []
            ),
            "coverage_evidence_aliases": _author_coverage_evidence_aliases(packet),
            "required_sibling_span_groups": list(
                packet.get("required_sibling_span_groups") or []
            ),
        }
    return requirements


def _run_approved_story_highlights(
    *, cfg: dict, video_no: str, outline_text: str, outline: dict,
    duration_sec: int, chunks: list[dict], chats: list[dict],
    broadcast_map_evidence: dict, point_candidate_shadow: dict, output_path: Path,
    markdown_path: Path, quality_path: Path, logger,
) -> dict:
    """Explore relations, gate each context absolutely, reconcile, then author."""

    from .highlight_story_window import (
        EDITORIAL_HIGHLIGHT_SYSTEM_PROMPT, build_editorial_highlight_request,
        build_editorial_highlight_set,
    )
    from .editorial_span_authoring import _parse_editorial_highlight_author_response
    from .highlight_prompt_assets import (
        EDITORIAL_HIGHLIGHT_DISCOVERY_SYSTEM_PROMPT,
        EDITORIAL_HIGHLIGHT_SELECTION_SYSTEM_PROMPT,
    )
    from .llm_gateway import call_llm
    from .manager_outline import (
        assert_single_flight_meta,
        is_summary_route_fallback_failure,
        resolve_single_flight_route,
        summary_stage_route_configs,
    )
    from .summary_quality_profile import SOL_MODEL

    revision = hashlib.sha256(outline_text.encode("utf-8")).hexdigest()
    packets, requirements = _build_whole_broadcast_approved_story_packets(
        outline=outline,
        point_candidate_shadow=point_candidate_shadow,
        chunks=chunks,
        chats=chats,
        broadcast_map_evidence=broadcast_map_evidence,
        max_stt_rows=max(
            8, int(cfg.get("editorial_highlight_discovery_max_stt_rows", 80) or 80)
        ),
        max_chat_rows=max(
            8, int(cfg.get("editorial_highlight_discovery_max_chat_rows", 96) or 96)
        ),
    )
    if not packets:
        raise ValueError("approved_story_packets_required")

    sol_route_configs = summary_stage_route_configs(cfg, model=SOL_MODEL)

    def call_sol_stage(
        request: str,
        system_prompt: str,
        *,
        context: dict,
        result_validator=None,
        required_provider: str = "",
    ) -> tuple[str, dict, str]:
        """Run one Sol decision through the manager-selected transport order."""

        eligible_routes = [
            route
            for route in sol_route_configs
            if not required_provider
            or resolve_single_flight_route(route)[0] == required_provider
        ]
        if not eligible_routes:
            raise ValueError("approved_story_highlight_required_provider_missing")
        for route_index, route_config in enumerate(eligible_routes):
            expected_provider, expected_model = resolve_single_flight_route(
                route_config
            )
            meta: dict = {}
            try:
                response = call_llm(
                    request,
                    system_prompt,
                    task="summary",
                    timeout=int(cfg.get("codex_job_timeout", 300) or 300),
                    model=SOL_MODEL,
                    config=route_config,
                    meta_out=meta,
                    context=context,
                    result_validator=result_validator,
                )
            except Exception as exc:
                has_fallback = route_index + 1 < len(eligible_routes)
                if not has_fallback or not is_summary_route_fallback_failure(exc):
                    raise
                continue
            assert_single_flight_meta(
                meta,
                expected_provider=expected_provider,
                expected_model=expected_model,
            )
            return response, meta, expected_provider
        raise ValueError("approved_story_highlight_supported_provider_required")
    receipts: list[dict] = []
    discovery_status = "skipped_no_whole_broadcast_coverage"
    discovery_group_count = 0
    selection_status = "skipped_no_discovered_contexts"
    selection_group_outcomes: list[dict] = []
    selected_group_count = 0
    discovery_packet = packets[0]
    if discovery_packet.get("stt_rows") or discovery_packet.get("chat_rows"):
        discovery_request = _build_approved_story_discovery_request(
            authority_revision_id=revision,
            packet=discovery_packet,
        )
        def validate_discovery(text: str, _meta: dict | None) -> None:
            _parse_approved_story_discovery_response(
                text,
                packet=discovery_packet,
            )

        discovery_response, discovery_meta, discovery_provider = call_sol_stage(
            discovery_request,
            EDITORIAL_HIGHLIGHT_DISCOVERY_SYSTEM_PROMPT,
            context={
                "video_no": video_no,
                "phase": "whole_broadcast_highlight_discovery",
                "answer_free": True,
                "broadcast_map_revision_id": revision,
            },
            result_validator=validate_discovery,
        )
        if str(discovery_meta.get("actual_model") or "") != SOL_MODEL:
            raise ValueError("highlight_discovery_actual_model_must_be_sol")
        discovery_groups = _parse_approved_story_discovery_response(
            discovery_response,
            packet=discovery_packet,
        )
        output_path.with_name(
            f"{video_no}_editorial_highlight_discovery.raw.txt"
        ).write_text(discovery_response, encoding="utf-8")
        discovery_status = "success"
        discovery_group_count = len(discovery_groups)
        receipts.append(
            {
                "phase": "whole_broadcast_highlight_discovery",
                "provider": str(
                    discovery_meta.get("selected_provider") or discovery_provider
                ),
                "model": str(discovery_meta.get("actual_model") or SOL_MODEL),
                "status": "success",
                "call_count": int(discovery_meta.get("call_count") or 1),
                "provider_attempt_count": int(
                    discovery_meta.get("provider_attempt_count") or 1
                ),
                "serialized_request_bytes": len(
                    (
                        EDITORIAL_HIGHLIGHT_DISCOVERY_SYSTEM_PROMPT
                        + discovery_request
                    ).encode("utf-8")
                ),
                "approved_story_count": len(requirements),
                "discovered_group_count": discovery_group_count,
            }
        )
        if discovery_groups:
            candidate_cards = _build_context_candidate_cards(
                authority_revision_id=revision,
                discovery_groups=discovery_groups,
                packet=discovery_packet,
            )
            selection_request = _build_context_candidate_gate_request(
                authority_revision_id=revision,
                candidate_cards=candidate_cards,
            )
            selection_system_prompt = EDITORIAL_HIGHLIGHT_SELECTION_SYSTEM_PROMPT
            def validate_selection(text: str, _meta: dict | None) -> None:
                _parse_context_candidate_gate_response(
                    text,
                    candidate_cards=candidate_cards,
                )

            selection_response, selection_meta, selection_provider = call_sol_stage(
                selection_request,
                selection_system_prompt,
                context={
                    "video_no": video_no,
                    "phase": "context_candidate_editorial_gate",
                    "answer_free": True,
                    "broadcast_map_revision_id": revision,
                },
                result_validator=validate_selection,
            )
            if str(selection_meta.get("actual_model") or "") != SOL_MODEL:
                raise ValueError("highlight_selection_actual_model_must_be_sol")
            output_path.with_name(
                f"{video_no}_editorial_highlight_selection.raw.txt"
            ).write_text(selection_response, encoding="utf-8")
            selection_decision = _parse_context_candidate_gate_response(
                selection_response,
                candidate_cards=candidate_cards,
            )
            selection_group_outcomes = list(
                selection_decision["candidate_outcomes"]
            )
            discovery_groups, terminal_selection_outcomes = (
                _reconcile_context_candidates(
                    candidate_cards=candidate_cards,
                    decision=selection_decision,
                )
            )
            selection_status = "success"
            selected_group_count = len(discovery_groups)
            receipts.append(
                {
                    "phase": "context_candidate_editorial_gate",
                    "provider": str(
                        selection_meta.get("selected_provider") or selection_provider
                    ),
                    "model": str(selection_meta.get("actual_model") or SOL_MODEL),
                    "status": "success",
                    "call_count": int(selection_meta.get("call_count") or 1),
                    "provider_attempt_count": int(
                        selection_meta.get("provider_attempt_count") or 1
                    ),
                    "serialized_request_bytes": len(
                        (selection_system_prompt + selection_request).encode("utf-8")
                    ),
                    "investigated_group_count": discovery_group_count,
                    "independently_selected_card_count": sum(
                        row.get("terminal_state") == "selected_for_highlight"
                        for row in selection_group_outcomes
                    ),
                    "selected_group_count": selected_group_count,
                    "reconciliation_action_count": len(
                        selection_decision["reconciliation_actions"]
                    ),
                }
            )

        if discovery_groups:
            packets, requirements = _build_approved_story_author_packets(
                discovery_groups=discovery_groups,
                discovery_packet=discovery_packet,
                requirements=requirements,
                chunks=chunks,
                chats=chats,
                broadcast_map_evidence=broadcast_map_evidence,
                video_no=video_no,
                evidence_radius_sec=max(
                    30,
                    int(
                        cfg.get(
                            "editorial_highlight_author_evidence_radius_sec", 120
                        )
                        or 120
                    ),
                ),
                max_stt_rows=max(
                    8,
                    int(
                        cfg.get("editorial_highlight_author_max_stt_rows", 96)
                        or 96
                    ),
                ),
                max_chat_rows=max(
                    8,
                    int(
                        cfg.get("editorial_highlight_author_max_chat_rows", 64)
                        or 64
                    ),
                ),
            )
        else:
            result = build_editorial_highlight_set(
                broadcast_map=outline,
                authority_revision_id=revision,
                proposals=[],
                highlight_producer_run_ref="highlight-approved-r1:no-discovered-groups",
                source_duration_sec=duration_sec,
                story_requirements=requirements,
            )
            result["adoption_status"] = "shadow_only"
            result["stable_saved_public_changed"] = False
            output_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            markdown_path.write_text(
                project_editorial_highlights_markdown(result), encoding="utf-8"
            )
            quality = {
                "schema_version": "chzz.editorial_highlight_quality.v1",
                "authority_revision_id": revision,
                "selected_model": SOL_MODEL,
                "model_calls": receipts,
                "successful_call_count": sum(
                    row["status"] == "success" for row in receipts
                ),
                "failed_call_count": 0,
                "cumulative_call_count": sum(
                    int(row["call_count"]) for row in receipts
                ),
                "whole_broadcast_story_discovery_input": True,
                "whole_broadcast_story_discovery_status": discovery_status,
                "approved_story_count": len(requirements),
                "discovery_packet_count": 1,
                "discovered_group_count": discovery_group_count,
                "global_editorial_selection_status": selection_status,
                "selection_group_count": len(selection_group_outcomes),
                "selected_group_count": selected_group_count,
                "group_outcomes": selection_group_outcomes,
                "stable_saved_public_changed": False,
            }
            quality_path.write_text(
                json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return result

    packets = [
        _compact_author_packet_to_request_budget(
            authority_revision_id=revision,
            packet=packet,
            system_prompt=EDITORIAL_HIGHLIGHT_SYSTEM_PROMPT,
        )[0]
        for packet in packets
    ]

    batches: list[tuple[list[dict], str]] = []
    current: list[dict] = []
    for packet in packets:
        candidate = [*current, packet]
        try:
            request = _build_approved_story_author_request(
                authority_revision_id=revision, evidence_packets=candidate,
            )
        except ValueError as exc:
            if str(exc) != "editorial_highlight_request_exceeds_40000_bytes" or not current:
                raise
            batches.append((current, _build_approved_story_author_request(
                authority_revision_id=revision, evidence_packets=current,
            )))
            current = [packet]
            continue
        if len((EDITORIAL_HIGHLIGHT_SYSTEM_PROMPT + request).encode("utf-8")) > 35_904 and current:
            batches.append((current, _build_approved_story_author_request(
                authority_revision_id=revision, evidence_packets=current,
            )))
            current = [packet]
        else:
            current = candidate
    if current:
        request = _build_approved_story_author_request(
            authority_revision_id=revision, evidence_packets=current,
        )
        if len((EDITORIAL_HIGHLIGHT_SYSTEM_PROMPT + request).encode("utf-8")) > 35_904:
            raise ValueError("approved_story_packet_exceeds_35904_byte_target")
        batches.append((current, request))

    proposals: list[dict] = []
    terminal_outcome_instruction = (
        "\n\nS3S SELECTED-CONTEXT TERMINAL OUTCOME REQUIREMENT: The JSON object may contain proposals and "
        "group_outcomes. A non-empty proposals list already proves semantic closure for its "
        "materialized relation. If a supplied relation group produces no proposal, include exactly "
        "one group_outcomes row with that group's exact story_refs, terminal_state, concrete reason, "
        "and one or more semantic evidence_refs from the request. Only no_editorial_value or "
        "relation_broken is a completed zero decision. source_unavailable, budget_exhausted, or "
        "unvisited reports incomplete work. Never return an unexplained empty proposals list."
    )
    for index, (batch, request) in enumerate(batches, start=1):
        batch_story_refs = list(
            dict.fromkeys(
                story_ref
                for row in batch
                for story_ref in (
                    row.get("story_refs")
                    or ([row.get("story_ref")] if row.get("story_ref") else [])
                )
            )
        )
        batch_requirements = {
            story_ref: requirements[story_ref] for story_ref in batch_story_refs
        }
        accepted_batch: list[dict] | None = None
        previous_proposals: list[dict] = []
        previous_rejection_reason = ""
        repair_provider = ""
        for repair_attempt in (1, 2):
            repair_instruction = ""
            if repair_attempt == 2:
                repair_instruction = (
                    "\n\nREPAIR REQUIREMENT: The previous answer failed the bounded Highlight "
                    "story contract. Regenerate the same bounded proposals. Every role must "
                    "be exactly one of introduction, development, core_event, reaction, ending. "
                    "Each exact value may appear at most once per Highlight; merge every repeated "
                    "role's span_indexes and evidence_refs into one role object. Do not emit "
                    "'ending value', 'ending_value', or any other alias. Every Highlight must "
                    "contain introduction, reaction, ending, and at least one of development or "
                    "core_event. If a required beat was omitted, select its source_span only from "
                    "the provided Story evidence and include the matching evidence_refs; never "
                    "invent a span, event, reaction, result, or evidence reference. Keep all "
                        "source, authority, and evidence constraints."
                        " Within each source_span, evidence_refs must be non-empty and unique; "
                        "remove duplicate refs without dropping any distinct supplied evidence."
                        " A source_span is continuous footage, not a story-role fragment. Merge "
                        "touching spans. For every real gap, add exclusion_before with a concrete "
                        "reason the intervening footage is discarded and supplied semantic evidence "
                    "timestamped inside that gap; a role transition alone is never a cut reason."
                    " Each supplied seed is an already reconciled immutable context identity. "
                    "Return exactly one proposal for it with the same context_identity, exact "
                    "required_card_refs, and exact required_story_refs union. Do not split or "
                    "recombine that identity. Cover every supplied semantic evidence interval; "
                    "distant required sibling groups stay separate source_spans under the one "
                    "Highlight identity. Never duplicate overlapping footage."
                )
                repair_instruction += _gap_coverage_repair_instruction(
                    evidence_packets=batch,
                    proposals=previous_proposals,
                    rejection_reason=previous_rejection_reason,
                )
                repair_instruction += _semantic_obligation_repair_instruction(
                    evidence_packets=batch,
                    proposals=previous_proposals,
                    rejection_reason=previous_rejection_reason,
                )
            effective_system_prompt = (
                EDITORIAL_HIGHLIGHT_SYSTEM_PROMPT
                + terminal_outcome_instruction
                + repair_instruction
            )
            response, meta, selected_provider = call_sol_stage(
                request,
                effective_system_prompt,
                context={"video_no": video_no, "phase": "approved_story_highlight",
                         "answer_free": True, "broadcast_map_revision_id": revision,
                         "batch_index": index,
                         "contract_repair_attempt": repair_attempt},
                required_provider=repair_provider,
            )
            if repair_attempt == 1:
                repair_provider = selected_provider
            attempt_raw_path = output_path.with_name(
                f"{video_no}_editorial_highlight_bundle_{index}.attempt-{repair_attempt}.raw.txt"
            )
            attempt_raw_path.write_text(response, encoding="utf-8")
            if str(meta.get("actual_model") or "") != SOL_MODEL:
                raise ValueError("approved_story_highlight_actual_model_must_be_sol")

            author_decision = _parse_editorial_highlight_author_response(response)
            parsed = list(author_decision["proposals"])
            allowed_groups = [
                set(row.get("story_refs") or []) for row in batch
            ]
            preflight_rejection_reason = _author_batch_outcome_rejection(
                author_decision,
                allowed_groups=allowed_groups,
                allowed_contexts=batch,
                allowed_evidence_refs={
                    str(ref)
                    for row in batch
                    for ref in row.get("allowed_evidence_refs") or []
                    if str(ref)
                },
            )
            if preflight_rejection_reason:
                rejection_reason = preflight_rejection_reason
            else:
                checked = build_editorial_highlight_set(
                    broadcast_map=outline, authority_revision_id=revision, proposals=parsed,
                    highlight_producer_run_ref=f"highlight-approved-r1:{index}",
                    source_duration_sec=duration_sec, story_requirements=batch_requirements,
                    context_requirements=(
                        _author_context_requirements(batch) if parsed else {}
                    ),
                )
                rejected = checked.get("rejected_proposals") or []
                rejection_reason = str(
                    (rejected[0] if rejected else {}).get("reason") or ""
                )
            status = "failed" if rejection_reason else "success"
            receipts.append({
                "phase": "approved_story_highlight", "batch_index": index,
                "contract_repair_attempt": repair_attempt,
                "provider": str(meta.get("selected_provider") or selected_provider),
                "model": str(meta.get("actual_model") or SOL_MODEL), "status": status,
                "call_count": int(meta.get("call_count") or 1),
                "provider_attempt_count": int(meta.get("provider_attempt_count") or 1),
                "serialized_request_bytes": len((effective_system_prompt + request).encode("utf-8")),
                **({"failure_reason": rejection_reason} if rejection_reason else {}),
            })
            if not rejection_reason:
                output_path.with_name(
                    f"{video_no}_editorial_highlight_bundle_{index}.raw.txt"
                ).write_text(response, encoding="utf-8")
                accepted_batch = parsed
                break
            if (
                repair_attempt == 1
                    and rejection_reason in {
                        "invalid_or_duplicate_story_beat_role",
                        "highlight_story_is_not_complete",
                        "source_span_gap_requires_exclusion_rationale",
                        "source_span_gap_cannot_be_justified_by_story_role",
                        "source_span_exclusion_evidence_must_be_inside_gap",
                        "source_span_exclusion_requires_primary_semantic_evidence",
                        "source_span_exclusion_evidence_does_not_cover_gap",
                        "source_span_exclusion_bridge_coverage_unavailable",
                        "span_requires_primary_semantic_evidence",
                        "highlight_uses_evidence_outside_approved_story",
                        "highlight_escapes_discovered_story_group",
                        "highlight_reuses_story_with_overlapping_source_footage",
                        "highlight_requires_known_reconciled_context_identity",
                        "highlight_must_match_exact_reconciled_story_union",
                        "highlight_must_match_exact_reconciled_card_union",
                        "highlight_does_not_cover_required_semantic_evidence_interval",
                        "distant_reconciled_contexts_require_sibling_source_spans",
                        "source_span_evidence_refs_must_have_unique_nonempty_refs",
                    }
            ):
                previous_proposals = parsed
                previous_rejection_reason = rejection_reason
                logger.warning(
                    "Approved Story Highlight 구조 오류를 Sol에 1회 한정 재작성 요청: "
                    "batch=%s reason=%s",
                    index,
                    rejection_reason,
                )
                continue
            raise ValueError(f"approved_story_highlight_rejected:{rejection_reason}")
        if accepted_batch is None:
            raise ValueError("approved_story_highlight_contract_repair_exhausted")
        proposals.extend(accepted_batch)

    result = build_editorial_highlight_set(
        broadcast_map=outline, authority_revision_id=revision, proposals=proposals,
        highlight_producer_run_ref="highlight-approved-r1:complete",
        source_duration_sec=duration_sec, story_requirements=requirements,
        context_requirements={
            context_identity: requirement
            for context_identity, requirement in (
                _author_context_requirements(packets) if proposals else {}
            ).items()
            if context_identity
            in {
                str(proposal.get("context_identity") or "")
                for proposal in proposals
            }
        },
    )
    if result.get("rejected_proposals"):
        raise ValueError(f"approved_story_highlight_rejected:{result['rejected_proposals'][0]['reason']}")
    result["adoption_status"] = "shadow_only"
    result["stable_saved_public_changed"] = False
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(project_editorial_highlights_markdown(result), encoding="utf-8")
    quality = {
        "schema_version": "chzz.editorial_highlight_quality.v1",
        "authority_revision_id": revision, "selected_model": SOL_MODEL,
        "model_calls": receipts,
        "successful_call_count": sum(row["status"] == "success" for row in receipts),
        "failed_call_count": sum(row["status"] == "failed" for row in receipts),
        "cumulative_call_count": sum(int(row["call_count"]) for row in receipts),
        "whole_broadcast_story_discovery_input": True,
        "whole_broadcast_story_discovery_status": discovery_status,
        "approved_story_count": len(requirements),
        "discovery_packet_count": len(packets),
        "discovered_group_count": discovery_group_count,
        "global_editorial_selection_status": selection_status,
        "selection_group_count": len(selection_group_outcomes),
        "selected_group_count": selected_group_count,
        "group_outcomes": selection_group_outcomes,
        "stable_saved_public_changed": False,
    }
    quality_path.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("✓ Approved Story Highlight shadow: highlights=%s calls=%s", len(result["highlights"]), quality["cumulative_call_count"])
    return result
