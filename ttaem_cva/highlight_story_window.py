"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

import hashlib

import json

import math

from typing import Any, Mapping, Sequence

from ttaem_cva.context_structure.contracts import ContextStructureContractError

from ttaem_cva.highlight_prompt_assets import EDITORIAL_HIGHLIGHT_SYSTEM_PROMPT

from ttaem_cva.editorial_contract import _derive_source_span_structure_refs

from ttaem_cva.editorial_span_authoring import _normalize_authored_source_span_boundaries

from ttaem_cva.story_packet_selector import ROLE_KEYS, validate_highlight_story_evidence


def _fail(code: str) -> None:
    raise ContextStructureContractError(code, code)


def _canonical(value: Any) -> bytes:
    try:
        text = json.dumps(value, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":"), sort_keys=True)
        return text.encode("utf-8")
    except (TypeError, ValueError):
        _fail("invalid_candidate_proposal")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail(code)
    number = float(value)
    return 0.0 if number == 0 else number


EDITORIAL_SCHEMA_VERSION = "chzz.editorial_highlight.v1"


EDITORIAL_SET_SCHEMA_VERSION = "chzz.editorial_highlight_set.v1"


_EDITORIAL_STORY_ROLES = frozenset(ROLE_KEYS) | {"development", "conversation"}


_EDITORIAL_BEAT_ROLES = {
    "introduction",
    "development",
    "core_event",
    "reaction",
    "ending",
}


def _editorial_error(code: str) -> ValueError:
    return ValueError(code)


def _editorial_text(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise _editorial_error(f"{field}_required")
    return text


def _editorial_refs(value: Any, *, field: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise _editorial_error(f"{field}_must_be_a_list")
    refs = [str(item or "").strip() for item in value]
    if any(not item for item in refs) or len(refs) != len(set(refs)):
        raise _editorial_error(f"{field}_must_have_unique_nonempty_refs")
    if not allow_empty and not refs:
        raise _editorial_error(f"{field}_required")
    return refs


def _editorial_seconds(value: str) -> float:
    parts = str(value or "").split(":")
    if len(parts) != 3:
        raise _editorial_error("invalid_broadcast_map_clock")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as exc:
        raise _editorial_error("invalid_broadcast_map_clock") from exc
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise _editorial_error("invalid_broadcast_map_clock")
    return float(hours * 3600 + minutes * 60 + seconds)


def _is_primary_semantic_ref(ref: str, *, approved_primary_refs: set[str] | None = None) -> bool:
    return ref.startswith(("stt-sec:", "chat-ms:", "chzzk-semantic:")) or ref in (approved_primary_refs or set())


def _broadcast_map_indexes(broadcast_map: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    ranges = [row for row in broadcast_map.get("ranges") or [] if isinstance(row, dict)]
    points = [row for row in broadcast_map.get("points") or [] if isinstance(row, dict)]
    d1 = {str(row.get("id") or ""): row for row in ranges if row.get("level") == "D1"}
    d2 = {str(row.get("id") or ""): row for row in ranges if row.get("level") == "D2"}
    point = {str(row.get("id") or ""): row for row in points}
    if not d1 or any(not key for key in [*d1, *d2, *point]):
        raise _editorial_error("invalid_frozen_broadcast_map")
    if any(str(row.get("parent_id") or "") not in d1 for row in d2.values()):
        raise _editorial_error("invalid_frozen_broadcast_map_hierarchy")
    return d1, d2, point


def _normalize_editorial_highlight(
    proposal: Mapping[str, Any],
    *,
    d1_index: Mapping[str, dict[str, Any]],
    d2_index: Mapping[str, dict[str, Any]],
    point_index: Mapping[str, dict[str, Any]],
    authority_revision_id: str,
    highlight_producer_run_ref: str,
    source_duration_sec: float,
    story_requirement: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(proposal, Mapping):
        raise _editorial_error("highlight_proposal_must_be_an_object")
    story_refs = _editorial_refs(
        proposal.get("story_refs")
        or ([proposal.get("story_ref")] if proposal.get("story_ref") else []),
        field="highlight_story_refs",
        allow_empty=story_requirement is None,
    )
    story_ref = story_refs[0] if story_refs else ""
    if story_requirement is not None and not story_refs:
        raise _editorial_error("highlight_story_ref_required")
    approved_primary_refs = {str(evidence_id) for evidence_id, row in (story_requirement or {}).get("evidence_catalog", {}).items()
        if isinstance(row, Mapping)
        and str(row.get("source_type") or "") in {"stt", "replay_chat", "chat"}}
    coverage_evidence_aliases = dict(
        (story_requirement or {}).get("coverage_evidence_aliases") or {}
    )
    coverage_alias_mode = bool(coverage_evidence_aliases)
    if len(set(coverage_evidence_aliases.values())) != len(
        coverage_evidence_aliases
    ):
        raise _editorial_error("semantic_evidence_alias_must_be_one_to_one")
    title = _editorial_text(proposal.get("title"), field="highlight_title")
    story_summary = _editorial_text(
        proposal.get("story_summary"), field="highlight_story_summary"
    )
    d1_refs = _editorial_refs(proposal.get("d1_refs") or [], field="highlight_d1_refs", allow_empty=True)
    d2_refs = _editorial_refs(proposal.get("d2_refs") or [],
                              field="highlight_d2_refs", allow_empty=True)
    point_refs = _editorial_refs(
        proposal.get("point_refs") or [],
        field="highlight_point_refs",
    )
    if any(ref not in d1_index for ref in d1_refs):
        raise _editorial_error("highlight_references_unknown_d1")
    if any(ref not in d2_index for ref in d2_refs):
        raise _editorial_error("highlight_references_unknown_d2")
    if any(ref not in point_index for ref in point_refs):
        raise _editorial_error("highlight_references_unknown_point")
    required_point_refs = _editorial_refs(
        (story_requirement or {}).get("point_refs") or [],
        field="story_required_point_refs",
        allow_empty=True,
    )
    if required_point_refs and set(point_refs) != set(required_point_refs):
        raise _editorial_error("highlight_point_refs_must_match_composed_stories")
    if any(str(d2_index[ref].get("parent_id") or "") not in d1_refs for ref in d2_refs):
        raise _editorial_error("highlight_d2_parent_not_referenced")

    raw_spans = proposal.get("source_spans")
    if not isinstance(raw_spans, (list, tuple)) or not raw_spans:
        raise _editorial_error("highlight_requires_source_spans")
    validated_raw_spans: list[dict[str, Any]] = []
    previous_end = -1.0
    for index, raw in enumerate(raw_spans):
        if not isinstance(raw, Mapping):
            raise _editorial_error("source_span_must_be_an_object")
        start = _finite(raw.get("start_sec"), "invalid_source_span_clock")
        end = _finite(raw.get("end_sec"), "invalid_source_span_clock")
        if start < 0 or end <= start or end > source_duration_sec:
            raise _editorial_error("invalid_source_span_clock")
        if start < previous_end:
            raise _editorial_error("source_spans_must_be_ordered_and_nonoverlapping")
        roles = _editorial_refs(raw.get("story_roles"), field="source_span_story_roles")
        if any(role not in _EDITORIAL_STORY_ROLES for role in roles):
            raise _editorial_error("invalid_source_span_story_role")
        span_d1_refs = _editorial_refs(raw.get("d1_refs") or [], field="source_span_d1_refs", allow_empty=True)
        span_d2_refs = _editorial_refs(raw.get("d2_refs") or [],
                                       field="source_span_d2_refs", allow_empty=True)
        span_point_refs = _editorial_refs(
            raw.get("point_refs") or [],
            field="source_span_point_refs",
            allow_empty=True,
        )
        if any(ref not in d1_index for ref in span_d1_refs):
            raise _editorial_error("source_span_references_unknown_d1")
        if any(ref not in d2_index for ref in span_d2_refs):
            raise _editorial_error("source_span_references_unknown_d2")
        if not set(span_point_refs).issubset(point_refs):
            raise _editorial_error("source_span_point_refs_escape_highlight")
        try:
            derived_d1_refs, derived_d2_refs = _derive_source_span_structure_refs(
                start=start, end=end, d1_index=d1_index, d2_index=d2_index)
        except ValueError as exc:
            raise _editorial_error(str(exc)) from exc
        if (not set(span_d1_refs).issubset(derived_d1_refs)
                or not set(span_d2_refs).issubset(derived_d2_refs)):
            raise _editorial_error("source_span_refs_escape_canonical_clock")
        evidence_refs = _editorial_refs(
            raw.get("evidence_refs"), field="source_span_evidence_refs"
        )
        has_primary_evidence = any(
            ref in coverage_evidence_aliases
            or (
                (
                    not coverage_alias_mode
                    and _is_primary_semantic_ref(
                        ref, approved_primary_refs=approved_primary_refs
                    )
                )
                or (
                    coverage_alias_mode
                    and ref in approved_primary_refs
                    and ref.startswith(
                        ("stt-sec:", "chat-ms:", "chzzk-semantic:")
                    )
                )
            )
            for ref in evidence_refs
        )
        if not has_primary_evidence:
            raise _editorial_error("span_requires_primary_semantic_evidence")
        validated_raw_spans.append(
            {
                "span_index": index,
                "start_sec": start,
                "end_sec": end,
                "duration_sec": end - start,
                "story_roles": roles,
                "d1_refs": derived_d1_refs,
                "d2_refs": derived_d2_refs,
                "point_refs": [],
                "evidence_refs": evidence_refs,
                "reason": _editorial_text(raw.get("reason"), field="source_span_reason"),
                "_raw_exclusion_before": raw.get("exclusion_before"),
            }
        )
        previous_end = end

    try:
        spans, raw_to_canonical_span, exclusion_evidence_refs = (
            _normalize_authored_source_span_boundaries(
                validated_raw_spans,
                evidence_catalog=(story_requirement or {}).get("evidence_catalog") or {},
                approved_primary_refs=approved_primary_refs,
                gap_coverage_batches=(story_requirement or {}).get("gap_coverage_batches") or [],
            )
        )
    except ValueError as exc:
        raise _editorial_error(str(exc)) from exc

    coverage_obligations = list(
        (story_requirement or {}).get("coverage_obligations") or []
    )
    covering_span_indexes_by_card: dict[str, set[int]] = {}
    for obligation in coverage_obligations:
        if not isinstance(obligation, Mapping):
            raise _editorial_error("invalid_semantic_coverage_obligation")
        card_ref = _editorial_text(
            obligation.get("card_ref"), field="semantic_coverage_card_ref"
        )
        start_sec = _finite(
            obligation.get("start_sec"), "invalid_semantic_coverage_interval"
        )
        end_sec = _finite(
            obligation.get("end_sec"), "invalid_semantic_coverage_interval"
        )
        if start_sec < 0 or end_sec < start_sec or end_sec > source_duration_sec:
            raise _editorial_error("invalid_semantic_coverage_interval")
        obligation_refs = set(
            _editorial_refs(
                obligation.get("evidence_refs"),
                field="semantic_coverage_evidence_refs",
            )
        )
        if coverage_alias_mode:
            canonical_catalog = (story_requirement or {}).get("evidence_catalog") or {}
            approved_refs_by_interval = {
                transport_ref
                for transport_ref, canonical_ref in coverage_evidence_aliases.items()
                if isinstance(canonical_catalog.get(canonical_ref), Mapping)
                and start_sec
                <= float(
                    canonical_catalog[canonical_ref].get("time_sec")
                    if canonical_catalog[canonical_ref].get("time_sec") is not None
                    else -1
                )
                <= end_sec
            }
            if not approved_refs_by_interval:
                raise _editorial_error("semantic_evidence_alias_missing")
        else:
            approved_refs_by_interval = obligation_refs
        covering_indexes = {
            index
            for index, span in enumerate(spans)
            if float(span["start_sec"]) <= start_sec
            and end_sec <= float(span["end_sec"])
            and (
                bool(
                    approved_refs_by_interval.intersection(
                        set(span.get("evidence_refs") or [])
                    )
                )
                if coverage_alias_mode
                else approved_refs_by_interval.issubset(
                    set(span.get("evidence_refs") or [])
                )
            )
        }
        if not covering_indexes:
            raise _editorial_error(
                "highlight_does_not_cover_required_semantic_evidence_interval"
            )
        if card_ref in covering_span_indexes_by_card:
            covering_span_indexes_by_card[card_ref].intersection_update(
                covering_indexes
            )
        else:
            covering_span_indexes_by_card[card_ref] = set(covering_indexes)

    sibling_groups = list(
        (story_requirement or {}).get("required_sibling_span_groups") or []
    )
    if sibling_groups:
        sibling_groups = _editorial_refs(
            sibling_groups,
            field="required_sibling_span_groups",
        )
        if any(
            not covering_span_indexes_by_card.get(card_ref)
            for card_ref in sibling_groups
        ):
            raise _editorial_error(
                "required_sibling_context_has_no_complete_source_span"
            )
        if any(
            covering_span_indexes_by_card[left].intersection(
                covering_span_indexes_by_card[right]
            )
            for left_index, left in enumerate(sibling_groups)
            for right in sibling_groups[left_index + 1 :]
        ):
            raise _editorial_error(
                "distant_reconciled_contexts_require_sibling_source_spans"
            )

    canonical_d1_refs: list[str] = []
    canonical_d2_refs: list[str] = []
    for span_index, span in enumerate(spans):
        try:
            derived_d1_refs, derived_d2_refs = _derive_source_span_structure_refs(
                start=float(span["start_sec"]),
                end=float(span["end_sec"]),
                d1_index=d1_index,
                d2_index=d2_index,
            )
        except ValueError as exc:
            raise _editorial_error(str(exc)) from exc
        span["span_index"] = span_index
        span["d1_refs"] = derived_d1_refs
        span["d2_refs"] = derived_d2_refs
        span["point_refs"] = [
            ref
            for ref in point_refs
            if float(span["start_sec"])
            <= _editorial_seconds(str(point_index[ref].get("timestamp") or ""))
            <= float(span["end_sec"])
        ]
        for ref in derived_d1_refs:
            if ref not in canonical_d1_refs:
                canonical_d1_refs.append(ref)
        for ref in derived_d2_refs:
            if ref not in canonical_d2_refs:
                canonical_d2_refs.append(ref)

    raw_beats = proposal.get("story_beats")
    if not isinstance(raw_beats, (list, tuple)) or not raw_beats:
        raise _editorial_error("highlight_requires_story_beats")
    span_evidence = {ref for span in spans for ref in span["evidence_refs"]}
    story_evidence_contract: dict[str, Any] = {}
    if story_requirement is not None:
        available = _editorial_refs(story_requirement.get("available_evidence_refs") or [],
                                    field="story_available_evidence_refs", allow_empty=True)
        catalog = story_requirement.get("evidence_catalog") or {}
        if not isinstance(catalog, Mapping):
            raise _editorial_error("invalid_story_evidence_catalog")
        try:
            story_evidence_contract = validate_highlight_story_evidence(
                required_story_roles=story_requirement.get("required_story_roles") or {},
                available_evidence_refs=available, author_evidence_refs=story_requirement.get("author_evidence_refs") or available,
                used_evidence_refs=sorted(span_evidence | exclusion_evidence_refs),
                evidence_catalog=catalog, source_spans=spans)
        except ValueError as exc:
            raise _editorial_error(str(exc)) from exc
    beats: list[dict[str, Any]] = []
    beat_roles: set[str] = set()
    for raw in raw_beats:
        if not isinstance(raw, Mapping):
            raise _editorial_error("story_beat_must_be_an_object")
        role = str(raw.get("role") or "")
        if role not in _EDITORIAL_BEAT_ROLES or role in beat_roles:
            raise _editorial_error("invalid_or_duplicate_story_beat_role")
        indexes = raw.get("span_indexes")
        if (
            not isinstance(indexes, (list, tuple))
            or not indexes
            or any(isinstance(value, bool) or not isinstance(value, int) for value in indexes)
            or len(indexes) != len(set(indexes))
            or any(value < 0 or value >= len(raw_to_canonical_span) for value in indexes)
        ):
            raise _editorial_error("invalid_story_beat_span_indexes")
        canonical_indexes = list(
            dict.fromkeys(raw_to_canonical_span[value] for value in indexes)
        )
        evidence_refs = _editorial_refs(
            raw.get("evidence_refs"), field="story_beat_evidence_refs"
        )
        if not set(evidence_refs).issubset(span_evidence):
            raise _editorial_error("story_beat_evidence_not_in_source_spans")
        beats.append(
            {
                "role": role,
                "span_indexes": canonical_indexes,
                "evidence_refs": evidence_refs,
                "reason": _editorial_text(raw.get("reason"), field="story_beat_reason"),
            }
        )
        beat_roles.add(role)
    derived_role_sources = {
        "introduction": {"setup"},
        "development": {"development", "performance"},
        "core_event": {"event"},
        "reaction": {"reaction"},
        "ending": {"result"},
    }
    for beat_role, span_roles in derived_role_sources.items():
        if beat_role in beat_roles:
            continue
        indexes = [
            index
            for index, span in enumerate(spans)
            if set(span["story_roles"]) & span_roles
        ]
        if not indexes:
            continue
        evidence_refs = sorted({
            ref for index in indexes for ref in spans[index]["evidence_refs"]
        })
        beats.append({
            "role": beat_role,
            "span_indexes": indexes,
            "evidence_refs": evidence_refs,
            "reason": "source_spans의 정본 story role에서 계산된 표시 역할",
        })
        beat_roles.add(beat_role)
    if not {"introduction", "reaction", "ending"}.issubset(beat_roles) or not (
        {"development", "core_event"} & beat_roles
    ):
        raise _editorial_error("highlight_story_is_not_complete")

    estimate = proposal.get("estimated_edited_duration")
    if not isinstance(estimate, Mapping):
        raise _editorial_error("estimated_edited_duration_required")
    minimum = _finite(estimate.get("min_sec"), "invalid_estimated_edited_duration")
    maximum = _finite(estimate.get("max_sec"), "invalid_estimated_edited_duration")
    total_source_duration = sum(float(span["duration_sec"]) for span in spans)
    if minimum <= 0 or maximum < minimum:
        raise _editorial_error("invalid_estimated_edited_duration")
    estimated = {
        "min_sec": total_source_duration,
        "max_sec": total_source_duration,
        "category": _editorial_text(
            estimate.get("category"), field="estimated_edited_duration_category"
        ),
    }
    first_start = float(spans[0]["start_sec"])
    last_end = float(spans[-1]["end_sec"])
    edit_mode = "continuous" if len(spans) == 1 else "multi_span"
    used_evidence = sorted(span_evidence | exclusion_evidence_refs | {
        ref for beat in beats for ref in beat["evidence_refs"]
    })
    body = {
        "schema_version": EDITORIAL_SCHEMA_VERSION,
        "authority_revision_id": authority_revision_id,
        "title": title,
        "story_summary": story_summary,
        "d1_refs": canonical_d1_refs,
        "d2_refs": canonical_d2_refs,
        "point_refs": point_refs,
        **({"story_refs": story_refs} if story_refs else {}),
        **({"story_ref": story_ref} if story_ref else {}),
        "source_spans": spans,
        "edit_mode": edit_mode,
        "story_beats": beats,
        "story_completeness": {"state": "complete", "present_roles": sorted(beat_roles)},
        "total_source_duration_sec": total_source_duration,
        "estimated_edited_duration": estimated,
        "excluded_gap_duration_sec": (last_end - first_start) - total_source_duration,
        "used_evidence_refs": used_evidence,
        **(story_evidence_contract if story_requirement is not None else {}),
        "missing_information": [str(item) for item in proposal.get("missing_information") or []],
        "uncertainties": [str(item) for item in proposal.get("uncertainties") or []],
        "highlight_producer_run_ref": highlight_producer_run_ref,
        "classification": "editorial_highlight",
        "counts_as_highlight": True,
    }
    body["highlight_id"] = "highlight-" + _sha(body)
    return body


def build_editorial_highlight_set(
    *,
    broadcast_map: Mapping[str, Any],
    authority_revision_id: str,
    proposals: Sequence[Mapping[str, Any]],
    highlight_producer_run_ref: str,
    source_duration_sec: float,
    point_review_windows: Sequence[Mapping[str, Any]] = (),
    story_requirements: Mapping[str, Mapping[str, Any]] | None = None,
    context_requirements: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate Highlights; legacy review windows are never persisted."""

    revision = _editorial_text(authority_revision_id, field="authority_revision_id")
    producer = _editorial_text(
        highlight_producer_run_ref, field="highlight_producer_run_ref"
    )
    duration = _finite(source_duration_sec, "invalid_source_duration_sec")
    if duration <= 0:
        raise _editorial_error("invalid_source_duration_sec")
    d1_index, d2_index, point_index = _broadcast_map_indexes(broadcast_map)
    highlights: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    requirements = dict(story_requirements or {})
    contexts = dict(context_requirements or {})
    seen_contexts: set[str] = set()
    seen_story_refs: set[str] = set()
    for index, proposal in enumerate(proposals):
        try:
            story_refs = _editorial_refs(
                proposal.get("story_refs")
                or ([proposal.get("story_ref")] if proposal.get("story_ref") else []),
                field="highlight_story_refs",
                allow_empty=not requirements,
            )
            if requirements:
                if any(story_ref not in requirements for story_ref in story_refs):
                    raise _editorial_error("highlight_references_unknown_story")
            context_requirement: Mapping[str, Any] | None = None
            context_identity = str(proposal.get("context_identity") or "").strip()
            if contexts:
                if not context_identity or context_identity not in contexts:
                    raise _editorial_error(
                        "highlight_requires_known_reconciled_context_identity"
                    )
                if context_identity in seen_contexts:
                    raise _editorial_error(
                        "reconciled_context_must_materialize_exactly_once"
                    )
                context_requirement = contexts[context_identity]
                exact_story_refs = list(
                    context_requirement.get("required_story_refs_exact") or []
                )
                if story_refs != exact_story_refs:
                    raise _editorial_error(
                        "highlight_must_match_exact_reconciled_story_union"
                    )
                required_card_refs = list(
                    context_requirement.get("required_card_refs") or []
                )
                if list(proposal.get("required_card_refs") or []) != required_card_refs:
                    raise _editorial_error(
                        "highlight_must_match_exact_reconciled_card_union"
                    )
            combined_requirement: dict[str, Any] | None = None
            if requirements:
                combined_requirement = {
                    "point_refs": [],
                    "required_story_roles": {},
                    "available_evidence_refs": [],
                    "author_evidence_refs": [],
                    "evidence_catalog": {},
                    "gap_coverage_batches": [],
                    "coverage_obligations": [],
                    "required_sibling_span_groups": [],
                }
                for story_ref in story_refs:
                    requirement = requirements[story_ref]
                    point_ref = str(requirement.get("point_ref") or "").strip()
                    point_refs = requirement.get("point_refs") or ([point_ref] if point_ref else [])
                    for ref in point_refs:
                        if ref not in combined_requirement["point_refs"]:
                            combined_requirement["point_refs"].append(ref)
                    for role, refs in (requirement.get("required_story_roles") or {}).items():
                        combined_requirement["required_story_roles"].setdefault(role, [])
                        for ref in refs:
                            if ref not in combined_requirement["required_story_roles"][role]:
                                combined_requirement["required_story_roles"][role].append(ref)
                    for field in ("available_evidence_refs", "author_evidence_refs"):
                        for ref in requirement.get(field) or []:
                            if ref not in combined_requirement[field]:
                                combined_requirement[field].append(ref)
                    for evidence_id, row in (requirement.get("evidence_catalog") or {}).items():
                        existing = combined_requirement["evidence_catalog"].get(evidence_id)
                        if existing is not None and existing != row:
                            raise _editorial_error("conflicting_story_evidence_catalog")
                        combined_requirement["evidence_catalog"][evidence_id] = row
                    for batch in requirement.get("gap_coverage_batches") or []:
                        if batch not in combined_requirement["gap_coverage_batches"]:
                            combined_requirement["gap_coverage_batches"].append(batch)
                    for obligation in requirement.get("coverage_obligations") or []:
                        if obligation not in combined_requirement["coverage_obligations"]:
                            combined_requirement["coverage_obligations"].append(
                                obligation
                            )
                    for card_ref in requirement.get(
                        "required_sibling_span_groups"
                    ) or []:
                        if card_ref not in combined_requirement[
                            "required_sibling_span_groups"
                        ]:
                            combined_requirement[
                                "required_sibling_span_groups"
                            ].append(card_ref)
                if context_requirement is not None:
                    combined_requirement["coverage_obligations"] = list(
                        context_requirement.get("coverage_obligations") or []
                    )
                    if "coverage_evidence_aliases" in context_requirement:
                        combined_requirement["coverage_evidence_aliases"] = dict(
                            context_requirement.get("coverage_evidence_aliases") or {}
                        )
                    combined_requirement["required_sibling_span_groups"] = list(
                        context_requirement.get("required_sibling_span_groups") or []
                    )
            normalized = _normalize_editorial_highlight(
                proposal,
                d1_index=d1_index,
                d2_index=d2_index,
                point_index=point_index,
                authority_revision_id=revision,
                highlight_producer_run_ref=producer,
                source_duration_sec=duration,
                story_requirement=combined_requirement,
            )
            normalized_story_refs = set(normalized.get("story_refs") or [])
            for existing in highlights:
                if not normalized_story_refs.intersection(existing.get("story_refs") or []):
                    continue
                if any(
                    max(float(left["start_sec"]), float(right["start_sec"]))
                    < min(float(left["end_sec"]), float(right["end_sec"]))
                    for left in existing.get("source_spans") or []
                    for right in normalized.get("source_spans") or []
                ):
                    raise _editorial_error(
                        "highlight_reuses_story_with_overlapping_source_footage"
                    )
            highlights.append(normalized)
            seen_story_refs.update(story_refs)
            if context_identity:
                seen_contexts.add(context_identity)
        except ValueError as exc:
            rejected.append({"proposal_index": index,
                             **({"context_identity": str(proposal.get("context_identity") or "")} if isinstance(proposal, Mapping) and proposal.get("context_identity") else {}),
                             **({"story_refs": list(proposal.get("story_refs") or [])} if isinstance(proposal, Mapping) and proposal.get("story_refs") else {}),
                             **({"story_ref": str(proposal.get("story_ref") or "")} if isinstance(proposal, Mapping) and proposal.get("story_ref") else {}),
                             "reason": str(exc)})
    for context_identity in contexts:
        if context_identity not in seen_contexts and not any(
            row.get("context_identity") == context_identity for row in rejected
        ):
            rejected.append(
                {
                    "context_identity": context_identity,
                    "reason": "reconciled_context_not_materialized",
                }
            )
    result = {
        "schema_version": EDITORIAL_SET_SCHEMA_VERSION,
        "authority_revision_id": revision,
        "highlight_producer_run_ref": producer,
        "authority": {
            "broadcast_map_owner": "pipeline.manager_outline.finalize_manager_outline",
            "highlight_owner": "pipeline.highlight_story_window.build_editorial_highlight_set",
            "source_spans_are_canonical": True,
            "display_clock_is_derived_from_source_spans": True,
        },
        "highlights": highlights,
        "rejected_proposals": rejected,
        "story_dispositions": [
            {
                "story_ref": story_ref,
                "disposition": (
                    "materialized"
                    if story_ref in seen_story_refs
                    else "not_selected_as_highlight"
                ),
            }
            for story_ref in requirements
        ],
        "quality_summary": {
            "highlight_pass_count": len(highlights),
            "rejected_proposal_count": len(rejected),
            "fixed_window_highlight_pass_count": 0,
        },
    }
    result["set_sha256"] = _sha(result)
    return result


def build_editorial_highlight_request(
    *,
    authority_revision_id: str,
    evidence_packets: Sequence[Mapping[str, Any]],
) -> str:
    """Build a bounded story-local request; never include a whole BroadcastMap answer."""

    packets = [dict(packet) for packet in evidence_packets]
    shared_context_naming = dict(packets[0].get("context_naming") or {}) if packets else {}
    shared_provenance_refs = sorted({
        str(ref)
        for packet in packets
        for ref in packet.get("provenance_refs") or []
    })
    forbidden_answer_guard = list(packets[0].get("forbidden_answer_guard") or []) if packets else []
    for packet in packets:
        packet.pop("context_naming", None)
        packet.pop("provenance_refs", None)
        packet.pop("forbidden_answer_guard", None)
        packet.pop("_coverage_evidence_aliases", None)
        if packet.get("gap_coverage_requirements"): packet.pop("temporal_scan_batches", None)
    payload = {
        "authority_revision_id": _editorial_text(
            authority_revision_id, field="authority_revision_id"
        ),
        "task": (
            "Author zero or more complete editorial Highlights from the supplied canonical Stories. "
            "One Highlight may compose one or more related Stories and their exact Point refs; "
            "Stories that are not selected as Highlights remain valid canonical Stories. Related "
            "distant scenes must be separate source_spans. Use only evidence_refs and original times "
            "present in the referenced seed packets. Every span start/end must stay inside one of "
            "those seeds' source_ranges; never pad beyond a source range. "
            "A source_span is one continuous piece of source footage an editor can use, not one "
            "story-role fragment. Put setup, event, reaction, and result in the same span whenever "
            "the footage is continuous. Touching spans are always normalized into one span. Every "
            "positive gap must be justified by exclusion_before with a semantic evidence_ref whose "
            "time is inside the discarded footage. Cite at least one eligible ref from every overlapping gap_coverage_requirements batch; an uncuttable_bridge stays continuous. A story-role transition is never a cut reason. "
            "Canonical D1/D2 boundaries are not edit-duration limits: a supported span may cross "
            "them, and code derives its exact structural membership from the BroadcastMap clock. "
            "Each story_beats role may appear at most once per Highlight. When one role covers "
            "multiple spans, combine all span_indexes and evidence_refs into that one role object. "
            "An empty proposals list is never a complete answer by itself. For every supplied "
            "relation group that produces no Highlight, emit one group_outcomes row with the exact "
            "story_refs, a terminal_state, a concrete reason, and evidence_refs from this request. "
            "Only no_editorial_value or relation_broken can justify a completed zero result. "
            "source_unavailable, budget_exhausted, or unvisited report incomplete authoring rather "
            "than a zero-Highlight decision."
        ),
        "shared_context_naming": shared_context_naming,
        "shared_provenance_refs": shared_provenance_refs,
        "forbidden_answer_guard": forbidden_answer_guard,
        "proposal_schema": {
            "context_identity": "exact supplied transient context_identity",
            "required_card_refs": [
                "exact supplied required_card_refs in supplied order"
            ],
            "story_refs": ["one or more exact supplied approved story_ref values"],
            "title": "string",
            "story_summary": "one-line complete story",
            "d1_refs": ["existing D1 hints; exact membership is code-derived"],
            "d2_refs": ["zero or more existing D2 hints; exact membership is code-derived"],
            "point_refs": ["one or more existing Point ids"],
            "source_spans": [{
                "start_sec": "number",
                "end_sec": "number",
                "story_roles": ["setup|performance|event|reaction|result|development|conversation"],
                "d1_refs": ["existing D1 hints; exact membership is code-derived"],
                "d2_refs": ["zero or more existing D2 hints; exact membership is code-derived"],
                "point_refs": ["zero or more existing Point ids"],
                "evidence_refs": ["supplied stt-sec/chat-ms refs"],
                "reason": "why this exact source span is needed",
                "exclusion_before": {
                    "reason": "required only after the first span: why the intervening source footage must be discarded; never a story-role change",
                    "evidence_refs": ["semantic refs timestamped strictly inside that excluded gap"],
                },
            }],
            "story_beats": [{
                "role": "exactly one of introduction|development|core_event|reaction|ending; each value at most once",
                "span_indexes": ["zero-based source span indexes"],
                "evidence_refs": ["refs already used by those spans"],
                "reason": "why the beat is necessary",
            }],
            "estimated_edited_duration": {
                "min_sec": "number",
                "max_sec": "number no greater than used source duration",
                "category": "descriptive non-gating category",
            },
            "used_evidence_refs": ["supplied refs only"],
            "missing_information": ["strings"],
            "uncertainties": ["strings"],
        },
        "group_outcome_schema": {
            "context_identity": "the exact supplied transient context_identity",
            "required_card_refs": ["the exact supplied required_card_refs"],
            "story_refs": ["the exact supplied relation group's story_refs"],
            "terminal_state": (
                "semantic_closure_found|relation_broken|source_unavailable|"
                "budget_exhausted|no_editorial_value|unvisited"
            ),
            "reason": "evidence-based reason for this terminal state",
            "evidence_refs": ["one or more supplied semantic evidence refs"],
        },
        "evidence_packets": packets,
    }
    request = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len((EDITORIAL_HIGHLIGHT_SYSTEM_PROMPT + request).encode("utf-8")) > 40_000:
        raise _editorial_error("editorial_highlight_request_exceeds_40000_bytes")
    return request


def project_editorial_highlights_markdown(result: Mapping[str, Any]) -> str:
    """Project validated edit stories from persisted ``source_spans`` only."""

    rows = ["## 실제 편집 하이라이트", ""]
    highlights = list(result.get("highlights") or [])
    if not highlights:
        rows.extend(["입증된 편집용 하이라이트 없음", ""])
    for highlight in highlights:
        rows.extend([
            f"### {str(highlight.get('title') or '').strip()}",
            str(highlight.get("story_summary") or "").strip(),
            "",
            f"- Highlight ID: {highlight.get('highlight_id')}",
            f"- BroadcastMap revision: {highlight.get('authority_revision_id')}",
            f"- D1: {', '.join(highlight.get('d1_refs') or [])}",
            f"- D2: {', '.join(highlight.get('d2_refs') or [])}",
            f"- Point: {', '.join(highlight.get('point_refs') or []) or '없음'}",
            f"- 편집 방식: {'여러 원본 구간 연결' if highlight.get('edit_mode') == 'multi_span' else '연속 원본 구간'}",
            f"- 원본 사용 합계: {highlight.get('total_source_duration_sec')}초",
            f"- 예상 편집 길이: {highlight.get('estimated_edited_duration', {}).get('min_sec')}–{highlight.get('estimated_edited_duration', {}).get('max_sec')}초",
            "",
        ])
        for span in highlight.get("source_spans") or []:
            rows.extend([
                f"#### span {int(span.get('span_index') or 0) + 1}: {span.get('start_sec')}–{span.get('end_sec')}초",
                f"- 역할: {', '.join(span.get('story_roles') or [])}",
                f"- 필요한 이유: {span.get('reason')}",
                f"- 근거: {', '.join(span.get('evidence_refs') or [])}",
                "",
            ])
    return "\n".join(rows).rstrip() + "\n"
