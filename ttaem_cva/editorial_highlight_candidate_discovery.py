"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

import hashlib

import json

from .editorial_highlight_projection import _clock_seconds

from .story_packet_selector import ROLE_KEYS


def _discovery_group_adds_editorial_context(
    group: dict,
    *,
    approved_stories: list[dict],
) -> bool:
    """Allow a grounded singleton as well as a related multi-Story group.

    A singleton is not automatically a Point-shaped Highlight: the author can
    still return zero. It must not, however, be structurally discarded merely
    because discovery did not invent a second nonlocal anchor.
    """

    del approved_stories
    return bool({str(ref) for ref in group.get("story_refs") or [] if str(ref)})


_EDITORIAL_WORTH_TERMINAL_STATES = {
    "selected_for_highlight",
    "no_editorial_value",
    "relation_broken",
    "source_unavailable",
    "budget_exhausted",
}


_CONTEXT_RECONCILIATION_ACTIONS = {
    "keep",
    "merge_same_story_flow",
    "suppress_duplicate",
    "keep_separate_independent_payoff",
}


def _context_card_evidence_refs(card: dict) -> set[str]:
    refs = {
        str(row.get("ref") or "")
        for row in card.get("evidence_anchors") or []
        if isinstance(row, dict) and str(row.get("ref") or "")
    }
    refs.update(
        str(ref)
        for phase in (card.get("story_flow") or {}).values()
        if isinstance(phase, dict)
        for ref in phase.get("evidence_refs") or []
        if str(ref)
    )
    return refs


def _parse_context_candidate_gate_response(
    text: str,
    *,
    candidate_cards: list[dict],
) -> dict:
    """Validate absolute per-card gates followed by compact reconciliation."""

    value = str(text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_context_candidate_gate_json") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "candidate_outcomes",
        "reconciliation_actions",
    }:
        raise ValueError("invalid_context_candidate_gate_shape")

    card_index = {
        str(card.get("card_id") or ""): card
        for card in candidate_cards
        if str(card.get("card_id") or "")
    }
    if len(card_index) != len(candidate_cards):
        raise ValueError("context_candidate_card_ids_must_be_unique_nonempty")
    raw_outcomes = payload.get("candidate_outcomes")
    if not isinstance(raw_outcomes, list):
        raise ValueError("invalid_context_candidate_outcomes")
    outcome_index: dict[str, dict] = {}
    for raw in raw_outcomes:
        if not isinstance(raw, dict) or set(raw) != {
            "card_id",
            "story_refs",
            "terminal_state",
            "reason",
            "evidence_refs",
            "uncertainties",
        }:
            raise ValueError("invalid_context_candidate_outcome")
        card_id = str(raw.get("card_id") or "").strip()
        if card_id not in card_index:
            raise ValueError("context_candidate_outcome_escapes_request")
        if card_id in outcome_index:
            raise ValueError("duplicate_context_candidate_outcome")
        card = card_index[card_id]
        story_refs = [
            str(ref).strip()
            for ref in raw.get("story_refs") or []
            if str(ref).strip()
        ]
        if story_refs != list(card.get("story_refs") or []):
            raise ValueError("context_candidate_story_refs_must_be_exact")
        terminal_state = str(raw.get("terminal_state") or "").strip()
        if terminal_state not in _EDITORIAL_WORTH_TERMINAL_STATES:
            raise ValueError("invalid_context_candidate_terminal_state")
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            raise ValueError("context_candidate_reason_required")
        allowed_evidence_refs = _context_card_evidence_refs(card)
        evidence_refs = [
            str(ref).strip()
            for ref in raw.get("evidence_refs") or []
            if str(ref).strip()
        ]
        if (
            not evidence_refs
            or len(evidence_refs) != len(raw.get("evidence_refs") or [])
            or len(evidence_refs) != len(set(evidence_refs))
            or not set(evidence_refs).issubset(allowed_evidence_refs)
        ):
            raise ValueError("invalid_context_candidate_evidence_refs")
        raw_uncertainties = raw.get("uncertainties")
        if not isinstance(raw_uncertainties, list):
            raise ValueError("invalid_context_candidate_uncertainties")
        uncertainties = [
            str(item).strip() for item in raw_uncertainties if str(item).strip()
        ]
        if len(uncertainties) != len(raw_uncertainties) or len(uncertainties) != len(
            set(uncertainties)
        ):
            raise ValueError("context_candidate_uncertainties_must_be_unique_text")

        outcome_index[card_id] = {
            "card_id": card_id,
            "story_refs": story_refs,
            "terminal_state": terminal_state,
            "reason": reason,
            "evidence_refs": evidence_refs,
            "uncertainties": uncertainties,
        }
    if set(outcome_index) != set(card_index):
        raise ValueError("context_candidate_gate_incomplete:unvisited")

    selected_ids = {
        card_id
        for card_id, row in outcome_index.items()
        if row["terminal_state"] == "selected_for_highlight"
    }
    raw_actions = payload.get("reconciliation_actions")
    if not isinstance(raw_actions, list):
        raise ValueError("invalid_context_reconciliation_actions")
    seen_action_ids: set[str] = set()
    actions: list[dict] = []
    for raw in raw_actions:
        if not isinstance(raw, dict) or set(raw) != {
            "action",
            "card_ids",
            "keep_card_ids",
            "reason",
        }:
            raise ValueError("invalid_context_reconciliation_action")
        action = str(raw.get("action") or "").strip()
        if action not in _CONTEXT_RECONCILIATION_ACTIONS:
            raise ValueError("unknown_context_reconciliation_action")
        card_ids = [
            str(item).strip()
            for item in raw.get("card_ids") or []
            if str(item).strip()
        ]
        if (
            not card_ids
            or len(card_ids) != len(raw.get("card_ids") or [])
            or len(card_ids) != len(set(card_ids))
            or not set(card_ids).issubset(selected_ids)
            or seen_action_ids.intersection(card_ids)
        ):
            raise ValueError("invalid_context_reconciliation_card_coverage")
        if action == "keep" and len(card_ids) != 1:
            raise ValueError("context_reconciliation_keep_requires_one_card")
        if action == "keep_separate_independent_payoff" and len(card_ids) < 2:
            raise ValueError("independent_payoff_must_keep_every_card")
        if action == "merge_same_story_flow" and len(card_ids) < 2:
            raise ValueError("same_story_flow_merge_requires_multiple_cards")
        if action in {"keep", "keep_separate_independent_payoff"}:
            keep_card_ids = list(card_ids)
        elif action == "merge_same_story_flow":
            keep_card_ids = []
        else:
            raw_keep_card_ids = raw.get("keep_card_ids")
            keep_card_ids = [
                str(item).strip()
                for item in raw_keep_card_ids or []
                if str(item).strip()
            ]
            if (
                not isinstance(raw_keep_card_ids, list)
                or len(keep_card_ids) != len(raw_keep_card_ids)
                or len(keep_card_ids) != len(set(keep_card_ids))
                or not set(keep_card_ids).issubset(card_ids)
            ):
                raise ValueError("invalid_context_reconciliation_keep_cards")
            if len(card_ids) < 2 or len(keep_card_ids) != 1:
                raise ValueError("duplicate_suppression_requires_one_keeper")
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            raise ValueError("context_reconciliation_reason_required")
        covered_ids = set(card_ids)
        evidence_refs = list(
            dict.fromkeys(
                ref
                for card in candidate_cards
                if str(card["card_id"]) in covered_ids
                for ref in outcome_index[str(card["card_id"])]["evidence_refs"]
            )
        )
        seen_action_ids.update(card_ids)
        actions.append(
            {
                "action": action,
                "card_ids": card_ids,
                "keep_card_ids": keep_card_ids,
                "reason": reason,
                "evidence_refs": evidence_refs,
            }
        )
    if seen_action_ids != selected_ids:
        raise ValueError("context_reconciliation_incomplete:unvisited")
    return {
        "candidate_outcomes": [outcome_index[str(card["card_id"])] for card in candidate_cards],
        "reconciliation_actions": actions,
    }


def _reconcile_context_candidates(
    *, candidate_cards: list[dict], decision: dict
) -> tuple[list[dict], list[dict]]:
    """Apply duplicate/story-flow actions without ranking or allocating slots."""

    card_index = {str(card["card_id"]): card for card in candidate_cards}
    outcomes = list(decision.get("candidate_outcomes") or [])
    terminal = [
        {
            "card_id": str(row["card_id"]),
            "story_refs": list(row["story_refs"]),
            "terminal_state": str(row["terminal_state"]),
            "reason": str(row["reason"]),
            "evidence_refs": list(row["evidence_refs"]),
            "uncertainties": list(row["uncertainties"]),
        }
        for row in outcomes
        if row.get("terminal_state") != "selected_for_highlight"
    ]

    def materialize(card_ids: list[str], *, action: dict) -> dict:
        cards = [card_index[card_id] for card_id in card_ids]
        story_refs = list(
            dict.fromkeys(
                str(ref)
                for card in cards
                for ref in card.get("story_refs") or []
                if str(ref)
            )
        )
        context_identity = "context-" + hashlib.sha256(
            json.dumps(
                {
                    "reconciliation_action": str(action["action"]),
                    "required_card_refs": list(card_ids),
                    "required_story_refs_exact": story_refs,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        source_ref_sets = [
            {str(ref) for ref in card.get("source_range_refs") or [] if str(ref)}
            for card in cards
        ]
        required_sibling_span_groups = []
        if (
            str(action["action"]) == "merge_same_story_flow"
            and len(cards) > 1
            and all(
                not source_ref_sets[left].intersection(source_ref_sets[right])
                for left in range(len(source_ref_sets))
                for right in range(left + 1, len(source_ref_sets))
            )
        ):
            required_sibling_span_groups = [str(card["card_id"]) for card in cards]
        return {
            "card_ids": list(card_ids),
            "story_refs": story_refs,
            "point_refs": list(
                dict.fromkeys(
                    str(ref)
                    for card in cards
                    for ref in card.get("point_refs") or []
                    if str(ref)
                )
            ),
            "anchor_evidence_refs": list(
                dict.fromkeys(
                    str(row.get("ref") or "")
                    for card in cards
                    for row in card.get("evidence_anchors") or []
                    if isinstance(row, dict) and str(row.get("ref") or "")
                )
            ),
            "source_range_refs": list(
                dict.fromkeys(
                    str(ref)
                    for card in cards
                    for ref in card.get("source_range_refs") or []
                    if str(ref)
                )
            ),
            "reason": str(action["reason"]),
            "selection_reason": str(action["reason"]),
            "selection_evidence_refs": list(action["evidence_refs"]),
            "reconciliation_action": str(action["action"]),
            "context_identity": context_identity,
            "required_card_refs": list(card_ids),
            "required_story_refs_exact": story_refs,
            "coverage_obligations": [],
            "required_sibling_span_groups": required_sibling_span_groups,
            "materialization_eligible": True,
        }

    selected: list[dict] = []
    for action in decision.get("reconciliation_actions") or []:
        action_name = str(action["action"])
        if action_name == "merge_same_story_flow":
            selected.append(materialize(list(action["card_ids"]), action=action))
            continue
        for card_id in action["keep_card_ids"]:
            selected.append(materialize([str(card_id)], action=action))
    return selected, terminal


def _approved_story_highlight_inputs(*, outline: dict, point_candidate_shadow: dict) -> tuple[list[dict], dict[str, dict]]:
    """Project frozen R1 decisions into H-KO packets without reselecting them."""

    selection = point_candidate_shadow.get("story_point_selection") or {}
    decisions = [row for row in selection.get("decisions") or [] if row.get("decision") == "select"]
    if not decisions:
        return [], {}
    if not selection.get("all_packets_decided"):
        raise ValueError("approved_story_selection_is_incomplete")
    packet_index = {str(row.get("story_packet_id") or ""): row for row in point_candidate_shadow.get("story_packets") or []}
    ledger = point_candidate_shadow.get("story_evidence_ledger") or {}
    selected_points = {str(row.get("story_packet_id") or ""): row for row in selection.get("selected_points") or []}
    ranges = {str(row.get("id") or ""): row for row in outline.get("ranges") or []}
    packets: list[dict] = []
    requirements: dict[str, dict] = {}
    for decision in decisions:
        story_ref = str(decision.get("story_packet_id") or "")
        packet = packet_index.get(story_ref)
        selected_point = selected_points.get(story_ref)
        if not story_ref or not packet or not selected_point:
            raise ValueError("approved_story_packet_contract_incomplete")
        d2_ref = str(packet.get("d2_ref") or selected_point.get("d2_ref") or "")
        d2 = ranges.get(d2_ref) or {}
        d1_ref = str(d2.get("parent_id") or "")
        if not d1_ref or not d2:
            raise ValueError("approved_story_references_unknown_d2")
        point_ref = str(
            selected_point.get("point_ref")
            or selected_point.get("point_shadow_id")
            or ""
        )
        evidence_ids = [str(ref) for ref in packet.get("evidence_ids") or []]
        evidence_rows = []
        evidence_catalog = {}
        for evidence_id in evidence_ids:
            row = ledger.get(evidence_id)
            if not isinstance(row, dict):
                raise ValueError("approved_story_evidence_missing_from_ledger")
            public_row = {
                key: row.get(key)
                for key in ("evidence_id", "role", "point_start_eligible", "source_type", "time_sec", "source_text", "exact_source_ref")
                if key in row
            }
            evidence_catalog[evidence_id] = public_row
        roles = {key: list((decision.get("roles") or {}).get(key) or []) for key in ROLE_KEYS}
        required_refs = list(dict.fromkeys(ref for refs in roles.values() for ref in refs))
        required_times = [float(evidence_catalog[ref]["time_sec"]) for ref in required_refs]
        author_evidence_ids = [
            evidence_id for evidence_id in evidence_ids
            if evidence_id in required_refs
            or min(required_times) - 30 <= float(evidence_catalog[evidence_id].get("time_sec") or -1) <= max(required_times) + 30
        ]
        evidence_rows = [evidence_catalog[evidence_id] for evidence_id in author_evidence_ids]
        source_ranges = [{"start_sec": _clock_seconds(d2["start"]), "end_sec": _clock_seconds(d2["end"])}]
        seed = {"story_seed_id": story_ref, "story_ref": story_ref, "d1_refs": [d1_ref], "d2_refs": [d2_ref],
                "point_refs": [point_ref] if point_ref else [], "source_ranges": source_ranges}
        packets.append({
            "story_seed": seed, "story_ref": story_ref,
            "approved_story": {"title": decision.get("title"), "why_notable": decision.get("why_notable"), "roles": roles},
            "required_story_roles": roles, "required_evidence_refs": required_refs,
            "allowed_evidence_refs": author_evidence_ids, "evidence_catalog": evidence_rows,
            "source_ranges": source_ranges,
        })
        requirements[story_ref] = {
            "point_ref": point_ref,
            "required_story_roles": roles, "available_evidence_refs": evidence_ids,
            "author_evidence_refs": author_evidence_ids, "evidence_catalog": evidence_catalog,
        }
    return packets, requirements


def _build_whole_broadcast_approved_story_packets(
    *,
    outline: dict,
    point_candidate_shadow: dict,
    chunks: list[dict],
    chats: list[dict],
    broadcast_map_evidence: dict,
    max_stt_rows: int = 80,
    max_chat_rows: int = 96,
) -> tuple[list[dict], dict[str, dict]]:
    """Compose approved Stories with bounded evidence from the whole broadcast.

    Point remains an immutable navigation anchor.  It is deliberately not used
    as a retrieval-window boundary here: one transient author packet compares
    every approved Story and stratified source evidence across the frozen D1
    envelopes.  The packet is model input only and never becomes a second
    BroadcastMap, Story, Highlight, or persisted authority object.
    """

    from .broadcast_map_evidence import build_highlight_local_evidence_packet

    story_packets, requirements = _approved_story_highlight_inputs(
        outline=outline,
        point_candidate_shadow=point_candidate_shadow,
    )
    if not story_packets:
        return [], requirements

    ranges = [
        dict(row)
        for row in outline.get("ranges") or []
        if isinstance(row, dict)
    ]
    d1_rows = [row for row in ranges if row.get("level") == "D1"]
    source_ranges = sorted(
        (
            {
                "start_sec": _clock_seconds(str(row.get("start") or "00:00:00")),
                "end_sec": _clock_seconds(str(row.get("end") or "00:00:00")),
            }
            for row in d1_rows
        ),
        key=lambda row: (row["start_sec"], row["end_sec"]),
    )
    merged_ranges: list[dict] = []
    for row in source_ranges:
        if row["end_sec"] <= row["start_sec"]:
            continue
        if merged_ranges and row["start_sec"] <= merged_ranges[-1]["end_sec"] + 1:
            merged_ranges[-1]["end_sec"] = max(
                merged_ranges[-1]["end_sec"], row["end_sec"]
            )
        else:
            merged_ranges.append(dict(row))
    if not merged_ranges:
        point_times = [
            _clock_seconds(str(row.get("timestamp") or "00:00:00"))
            for row in outline.get("points") or []
            if isinstance(row, dict)
        ]
        merged_ranges = [{"start_sec": 0, "end_sec": max([1, *point_times])}]

    coverage_ranges = []
    for index, row in enumerate(ranges):
        if row.get("level") not in {"D1", "D2"}:
            continue
        start_sec = _clock_seconds(str(row.get("start") or "00:00:00"))
        end_sec = _clock_seconds(str(row.get("end") or "00:00:00"))
        if end_sec <= start_sec:
            continue
        coverage_ranges.append(
            {
                "ref": str(row.get("id") or f"coverage-range-{index}"),
                "level": str(row.get("level") or ""),
                "start_sec": start_sec,
                "end_sec": end_sec,
                "title": str(row.get("title") or ""),
                "content": str(row.get("content") or ""),
            }
        )
    if not coverage_ranges:
        coverage_ranges = [
            {
                "ref": "broadcast-full",
                "level": "broadcast",
                "start_sec": int(merged_ranges[0]["start_sec"]),
                "end_sec": int(merged_ranges[-1]["end_sec"]),
                "title": "전체 방송",
                "content": "BroadcastMap 구간이 없는 입력의 전체 원본 범위",
            }
        ]

    point_index = {
        str(row.get("id") or ""): row
        for row in outline.get("points") or []
        if isinstance(row, dict) and str(row.get("id") or "")
    }
    story_refs = [str(row["story_ref"]) for row in story_packets]
    point_refs = [
        str(row["story_seed"]["point_refs"][0])
        for row in story_packets
        if row.get("story_seed", {}).get("point_refs")
    ]
    point_anchors = [
        {
            "point_id": point_ref,
            "time_sec": _clock_seconds(
                str(point_index.get(point_ref, {}).get("timestamp") or "00:00:00")
            ),
        }
        for point_ref in point_refs
    ]
    seed = {
        "kind": "whole_broadcast_story_scope",
        "story_seed_id": "whole-broadcast-approved-stories",
        "story_refs": story_refs,
        "d1_refs": [str(row.get("id") or "") for row in d1_rows],
        "d2_refs": [
            str(row.get("id") or "")
            for row in ranges
            if row.get("level") == "D2"
        ],
        "point_refs": point_refs,
        "point_anchors": point_anchors,
        "source_ranges": merged_ranges,
        "coverage_ranges": coverage_ranges,
        "navigation_titles": [
            str(row.get("title") or "") for row in ranges if row.get("title")
        ],
    }
    packet = build_highlight_local_evidence_packet(
        story_seed=seed,
        chunks=chunks,
        chats=chats,
        evidence_bundle=broadcast_map_evidence,
        max_stt_rows=max_stt_rows,
        max_chat_rows=max_chat_rows,
    )

    coverage_catalog: dict[str, dict] = {}
    for source_type, rows in (
        ("stt", packet.get("stt_rows") or []),
        ("replay_chat", packet.get("chat_rows") or []),
    ):
        for row in rows:
            evidence_id = str(row.get("ref") or "")
            if not evidence_id:
                continue
            coverage_catalog[evidence_id] = {
                "evidence_id": evidence_id,
                "role": "primary_semantic",
                "point_start_eligible": False,
                "source_type": source_type,
                "time_sec": int(row.get("time_sec") or 0),
                "source_text": str(row.get("text") or ""),
                "exact_source_ref": evidence_id,
            }
    coverage_refs = list(coverage_catalog)
    author_catalog = dict(coverage_catalog)
    approved_stories = []
    supplied_refs: list[str] = list(coverage_refs)
    for row in story_packets:
        story_ref = str(row["story_ref"])
        requirement = requirements[story_ref]
        story_anchor_refs = list(
            dict.fromkeys(
                ref
                for refs in (requirement.get("required_story_roles") or {}).values()
                for ref in refs
            )
        )
        approved_stories.append(
            {
                "story_ref": story_ref,
                "story_seed": dict(row["story_seed"]),
                "approved_story": dict(row["approved_story"]),
                "required_story_roles": dict(row["required_story_roles"]),
                "evidence_anchors": [
                    {
                        "ref": ref,
                        "time_sec": int(
                            (requirement.get("evidence_catalog") or {})
                            .get(ref, {})
                            .get("time_sec")
                            or 0
                        ),
                    }
                    for ref in story_anchor_refs[:2]
                    if isinstance(
                        (requirement.get("evidence_catalog") or {}).get(ref), dict
                    )
                ],
            }
        )
        for evidence_id in requirement.get("author_evidence_refs") or []:
            evidence_row = (requirement.get("evidence_catalog") or {}).get(evidence_id)
            if isinstance(evidence_row, dict):
                author_catalog[str(evidence_id)] = dict(evidence_row)
        requirement["point_refs"] = list(row["story_seed"].get("point_refs") or [])
        requirement["author_evidence_refs"] = list(
            dict.fromkeys(
                [*(requirement.get("author_evidence_refs") or []), *coverage_refs]
            )
        )
        requirement["available_evidence_refs"] = list(
            dict.fromkeys(
                [*(requirement.get("available_evidence_refs") or []), *coverage_refs]
            )
        )
        requirement["evidence_catalog"] = {
            **dict(requirement.get("evidence_catalog") or {}),
            **coverage_catalog,
        }
        supplied_refs.extend(row.get("allowed_evidence_refs") or [])

    packet.update(
        {
            "story_refs": story_refs,
            "approved_stories": approved_stories,
            "required_evidence_refs": list(
                dict.fromkeys(
                    ref
                    for row in story_packets
                    for ref in row.get("required_evidence_refs") or []
                )
            ),
            "allowed_evidence_refs": list(dict.fromkeys(supplied_refs)),
            "evidence_catalog": list(author_catalog.values()),
        }
    )
    return [packet], requirements


def _build_approved_story_discovery_request(
    *, authority_revision_id: str, packet: dict
) -> str:
    """Build the transient whole-broadcast relationship-discovery request."""

    def utf8_prefix(value: object, max_bytes: int) -> str:
        raw = str(value or "").encode("utf-8")
        if len(raw) <= max_bytes:
            return raw.decode("utf-8")
        return raw[:max_bytes].decode("utf-8", errors="ignore")

    payload = {
        "authority_revision_id": str(authority_revision_id),
        "task": (
            "Discover zero or more contextually complete Highlight story groups across the "
            "whole broadcast. Return relationships and evidence anchors only; do not author "
            "source spans and do not create, delete, move, or retime Points."
        ),
        "approved_stories": [
            {
                "story_ref": str(row.get("story_ref") or ""),
                "point_refs": list(row.get("story_seed", {}).get("point_refs") or []),
                "d1_refs": list(row.get("story_seed", {}).get("d1_refs") or []),
                "d2_refs": list(row.get("story_seed", {}).get("d2_refs") or []),
                "title": utf8_prefix(
                    row.get("approved_story", {}).get("title"), 96
                ),
                "why_notable": utf8_prefix(
                    row.get("approved_story", {}).get("why_notable"), 160
                ),
                "evidence_anchors": [
                    {
                        "ref": str(anchor.get("ref") or ""),
                        "time_sec": int(anchor.get("time_sec") or 0),
                    }
                    for anchor in row.get("evidence_anchors") or []
                    if str(anchor.get("ref") or "")
                ],
            }
            for row in packet.get("approved_stories") or []
        ],
        "coverage_ranges": [
            {
                "ref": str(row.get("ref") or ""),
                "level": str(row.get("level") or ""),
                "start_sec": int(row.get("start_sec") or 0),
                "end_sec": int(row.get("end_sec") or 0),
                "title": utf8_prefix(row.get("title"), 96),
                "content": utf8_prefix(row.get("content"), 160),
            }
            for row in packet.get("story_seed", {}).get("coverage_ranges") or []
            if str(row.get("ref") or "")
        ],
        "whole_broadcast_coverage": {
            "stt_rows": [
                {
                    "ref": str(row.get("ref") or ""),
                    "time_sec": int(row.get("time_sec") or 0),
                    "text": utf8_prefix(row.get("text"), 80),
                }
                for row in packet.get("stt_rows") or []
            ],
            "chat_rows": [
                {
                    "ref": str(row.get("ref") or ""),
                    "time_sec": int(row.get("time_sec") or 0),
                    "text": utf8_prefix(row.get("text"), 80),
                }
                for row in packet.get("chat_rows") or []
            ],
        },
        "response_schema": {
            "groups": [
                {
                    "story_refs": ["one or more exact supplied story refs"],
                    "anchor_evidence_refs": [
                        "one or more exact supplied STT/chat refs"
                    ],
                    "source_range_refs": [
                        "one or more exact supplied coverage range refs to re-read"
                    ],
                    "reason": "why these Stories form one complete edit story",
                }
            ]
        },
    }
    request = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    from .highlight_prompt_assets import EDITORIAL_HIGHLIGHT_DISCOVERY_SYSTEM_PROMPT

    if len(
        (EDITORIAL_HIGHLIGHT_DISCOVERY_SYSTEM_PROMPT + request).encode("utf-8")
    ) > 40_000:
        raise ValueError("editorial_highlight_discovery_request_exceeds_40000_bytes")
    return request


def _parse_approved_story_discovery_response(
    text: str, *, packet: dict
) -> list[dict]:
    """Validate transient groups without granting them product authority."""

    value = str(text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_editorial_highlight_discovery_json") from exc
    if not isinstance(payload, dict) or set(payload) != {"groups"}:
        raise ValueError("invalid_editorial_highlight_discovery_shape")
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list):
        raise ValueError("invalid_editorial_highlight_discovery_groups")

    known_story_refs = set(str(ref) for ref in packet.get("story_refs") or [])
    known_anchor_refs = {
        str(row.get("ref") or "")
        for key in ("stt_rows", "chat_rows")
        for row in packet.get(key) or []
        if str(row.get("ref") or "")
    }
    known_anchor_refs.update(
        str(anchor.get("ref") or "")
        for story in packet.get("approved_stories") or []
        for anchor in story.get("evidence_anchors") or []
        if str(anchor.get("ref") or "")
    )
    seen_story_refs: set[str] = set()
    groups: list[dict] = []
    for raw in raw_groups:
        if not isinstance(raw, dict) or set(raw) != {
            "story_refs",
            "anchor_evidence_refs",
            "source_range_refs",
            "reason",
        }:
            raise ValueError("invalid_editorial_highlight_discovery_group")
        story_refs = list(
            dict.fromkeys(str(ref).strip() for ref in raw["story_refs"] if str(ref).strip())
        ) if isinstance(raw["story_refs"], list) else []
        anchor_refs = list(
            dict.fromkeys(
                str(ref).strip()
                for ref in raw["anchor_evidence_refs"]
                if str(ref).strip()
            )
        ) if isinstance(raw["anchor_evidence_refs"], list) else []
        reason = str(raw.get("reason") or "").strip()
        source_range_refs = list(
            dict.fromkeys(
                str(ref).strip()
                for ref in raw["source_range_refs"]
                if str(ref).strip()
            )
        ) if isinstance(raw["source_range_refs"], list) else []
        known_source_range_refs = {
            str(row.get("ref") or "")
            for row in packet.get("story_seed", {}).get("coverage_ranges") or []
            if str(row.get("ref") or "")
        }
        if not story_refs or not set(story_refs).issubset(known_story_refs):
            raise ValueError("highlight_discovery_references_unknown_story")
        if set(story_refs) & seen_story_refs:
            raise ValueError("highlight_discovery_story_group_overlap")
        if not anchor_refs or not set(anchor_refs).issubset(known_anchor_refs):
            raise ValueError("highlight_discovery_references_unknown_anchor")
        if (
            not source_range_refs
            or not set(source_range_refs).issubset(known_source_range_refs)
        ):
            raise ValueError("highlight_discovery_references_unknown_source_range")
        if not reason:
            raise ValueError("highlight_discovery_reason_required")
        seen_story_refs.update(story_refs)
        groups.append(
            {
                "story_refs": story_refs,
                "anchor_evidence_refs": anchor_refs,
                "source_range_refs": source_range_refs,
                "reason": reason,
                "materialization_eligible": _discovery_group_adds_editorial_context(
                    {
                        "story_refs": story_refs,
                        "anchor_evidence_refs": anchor_refs,
                    },
                    approved_stories=list(packet.get("approved_stories") or []),
                ),
            }
        )
    return groups


def _build_context_candidate_cards(
    *,
    authority_revision_id: str,
    discovery_groups: list[dict],
    packet: dict,
) -> list[dict]:
    """Normalize discovery relations into bounded transient selector cards."""

    approved_index = {
        str(row.get("story_ref") or ""): row
        for row in packet.get("approved_stories") or []
        if str(row.get("story_ref") or "")
    }
    evidence_index = {
        str(row.get("ref") or row.get("evidence_id") or ""): row
        for row in [
            *(packet.get("stt_rows") or []),
            *(packet.get("chat_rows") or []),
            *(packet.get("evidence_catalog") or []),
        ]
        if isinstance(row, dict)
        and str(row.get("ref") or row.get("evidence_id") or "")
    }
    stt_refs = {
        str(row.get("ref") or row.get("evidence_id") or "")
        for row in packet.get("stt_rows") or []
        if isinstance(row, dict)
        and str(row.get("ref") or row.get("evidence_id") or "")
    }
    chat_refs = {
        str(row.get("ref") or row.get("evidence_id") or "")
        for row in packet.get("chat_rows") or []
        if isinstance(row, dict)
        and str(row.get("ref") or row.get("evidence_id") or "")
    }
    role_groups = {
        "setup": ("setup",),
        "turn": ("performance", "event"),
        "reaction": ("reaction",),
        "payoff": ("result",),
    }
    cards: list[dict] = []
    for group_index, group in enumerate(discovery_groups):
        story_refs = [str(ref) for ref in group.get("story_refs") or [] if str(ref)]
        if not story_refs or not set(story_refs).issubset(approved_index):
            raise ValueError("context_candidate_references_unknown_story")
        source_range_refs = [
            str(ref) for ref in group.get("source_range_refs") or [] if str(ref)
        ]
        anchor_refs = list(
            dict.fromkeys(
                str(ref)
                for ref in group.get("anchor_evidence_refs") or []
                if str(ref)
            )
        )
        story_flow: dict[str, dict] = {}
        for phase, roles in role_groups.items():
            phase_refs = list(
                dict.fromkeys(
                    str(ref)
                    for story_ref in story_refs
                    for role in roles
                    for ref in approved_index[story_ref]
                    .get("required_story_roles", {})
                    .get(role, [])
                    if str(ref) and str(ref) in evidence_index
                )
            )[:3]
            story_flow[phase] = {
                "summary": " ".join(
                    str(
                        evidence_index[ref].get("text")
                        or evidence_index[ref].get("source_text")
                        or ""
                    )[:120]
                    for ref in phase_refs
                )[:280],
                "evidence_refs": phase_refs,
            }
            anchor_refs.extend(phase_refs)
        anchor_refs = list(dict.fromkeys(anchor_refs))[:12]
        card_seed = json.dumps(
            {
                "authority_revision_id": str(authority_revision_id),
                "group_index": group_index,
                "story_refs": story_refs,
                "source_range_refs": source_range_refs,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        cards.append(
            {
                "card_id": "context-card-"
                + hashlib.sha256(card_seed.encode("utf-8")).hexdigest()[:20],
                "story_refs": story_refs,
                "point_refs": list(
                    dict.fromkeys(
                        str(point_ref)
                        for story_ref in story_refs
                        for point_ref in approved_index[story_ref]
                        .get("story_seed", {})
                        .get("point_refs", [])
                        if str(point_ref)
                    )
                ),
                "source_range_refs": source_range_refs,
                "relation_reason": str(group.get("reason") or "")[:320],
                "story_outline": [
                    {
                        "story_ref": story_ref,
                        "title": str(
                            approved_index[story_ref]
                            .get("approved_story", {})
                            .get("title")
                            or ""
                        )[:96],
                        "why_notable": str(
                            approved_index[story_ref]
                            .get("approved_story", {})
                            .get("why_notable")
                            or ""
                        )[:180],
                    }
                    for story_ref in story_refs
                ],
                "story_flow": story_flow,
                "evidence_anchors": [
                    {
                        "ref": ref,
                        "time_sec": int(
                            evidence_index.get(ref, {}).get("time_sec")
                            or evidence_index.get(ref, {}).get("canonical_time_sec")
                            or 0
                        ),
                        "text": str(
                            evidence_index.get(ref, {}).get("text")
                            or evidence_index.get(ref, {}).get("source_text")
                            or ""
                        )[:140],
                        "source_type": str(
                            evidence_index.get(ref, {}).get("source_type")
                            or ("stt" if ref in stt_refs else "chat" if ref in chat_refs else "evidence")
                        ),
                    }
                    for ref in anchor_refs
                ],
            }
        )
    return cards


def _build_context_candidate_gate_request(
    *, authority_revision_id: str, candidate_cards: list[dict]
) -> str:
    """Project each already-bounded card once, without ranking or re-compression."""

    def semantic_core_card(card: dict) -> dict:
        story_flow = card.get("story_flow") or {}
        return {
            "card_id": str(card.get("card_id") or ""),
            "story_refs": list(card.get("story_refs") or []),
            "source_range_refs": list(card.get("source_range_refs") or []),
            "relation_reason": str(card.get("relation_reason") or ""),
            "story_titles": [
                {
                    "story_ref": str(row.get("story_ref") or ""),
                    "title": str(row.get("title") or ""),
                }
                for row in card.get("story_outline") or []
                if isinstance(row, dict) and str(row.get("story_ref") or "")
            ],
            "story_flow": {
                phase: {
                    "summary": str(
                        (story_flow.get(phase) or {}).get("summary") or ""
                    ),
                    "evidence_refs": list(
                        (story_flow.get(phase) or {}).get("evidence_refs") or []
                    ),
                }
                for phase in ("setup", "turn", "reaction", "payoff")
            },
        }

    payload = {
        "authority_revision_id": str(authority_revision_id),
        "context_candidate_cards": [
            semantic_core_card(card) for card in candidate_cards
        ],
        "reconciliation_contract": {
            "actions": [
                "keep",
                "merge_same_story_flow",
                "suppress_duplicate",
                "keep_separate_independent_payoff",
            ],
            "same_topic_is_not_merge_evidence": True,
            "every_selected_card_covered_exactly_once": True,
        },
        "response_schema": {
            "candidate_outcomes": [
                {
                    "card_id": "exact supplied card id",
                    "story_refs": ["exact supplied story refs"],
                    "terminal_state": (
                        "selected_for_highlight|no_editorial_value|relation_broken|"
                        "source_unavailable|budget_exhausted"
                    ),
                    "reason": "absolute terminal reason",
                    "evidence_refs": ["exact refs from this card"],
                    "uncertainties": [
                        "source uncertainty or non-blocking caveat, or empty"
                    ],
                }
            ],
            "reconciliation_actions": [
                {
                    "action": "one reconciliation action",
                    "card_ids": ["selected cards covered by this action"],
                    "keep_card_ids": ["cards retained separately, or empty for merge"],
                    "reason": "duplicate, subsumption, natural flow, or independent payoff reason",
                }
            ],
        },
    }
    request = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    from .highlight_prompt_assets import EDITORIAL_HIGHLIGHT_SELECTION_SYSTEM_PROMPT

    if len(
        (EDITORIAL_HIGHLIGHT_SELECTION_SYSTEM_PROMPT + request).encode("utf-8")
    ) > 40_000:
        raise ValueError("context_candidate_gate_request_exceeds_40000_bytes")
    return request
