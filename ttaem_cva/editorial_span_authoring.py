"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

import json

import math

import re

from typing import Any, Mapping


def _parse_editorial_highlight_author_response(text: str) -> dict[str, Any]:
    """Parse transient proposals and per-relation terminal outcomes."""

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
        raise ValueError("invalid_editorial_highlight_json") from exc
    if (
        not isinstance(payload, dict)
        or "proposals" not in payload
        or not set(payload).issubset({"proposals", "group_outcomes"})
    ):
        raise ValueError("invalid_editorial_highlight_response_shape")
    proposals = payload["proposals"]
    if not isinstance(proposals, list) or any(
        not isinstance(row, dict) for row in proposals
    ):
        raise ValueError("invalid_editorial_highlight_proposals")
    group_outcomes = payload.get("group_outcomes") or []
    if not isinstance(group_outcomes, list) or any(
        not isinstance(row, dict) for row in group_outcomes
    ):
        raise ValueError("invalid_editorial_highlight_group_outcomes")
    return {"proposals": proposals, "group_outcomes": group_outcomes}


def _semantic_evidence_time(
    ref: str, evidence_catalog: Mapping[str, Any]
) -> float | None:
    if ref.startswith("stt-sec:"):
        try:
            return float(ref.split(":", 1)[1])
        except ValueError:
            return None
    if ref.startswith("chat-ms:"):
        try:
            return float(ref.split(":", 1)[1]) / 1000.0
        except ValueError:
            return None
    row = evidence_catalog.get(ref)
    if not isinstance(row, Mapping):
        return None
    for key in ("time_sec", "canonical_time_sec"):
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _role_only_reason(reason: str) -> bool:
    compact = re.sub(r"[^a-z가-힣]+", " ", reason.lower()).strip()
    tokens = set(compact.split())
    role_tokens = {
        "story", "role", "transition", "beat", "setup", "development",
        "performance", "event", "reaction", "result", "conversation",
        "역할", "전환", "도입", "전개", "사건", "반응", "결말",
    }
    return not tokens or tokens.issubset(role_tokens)


def _normalize_authored_source_span_boundaries(
    raw_spans: list[dict[str, Any]],
    *,
    evidence_catalog: Mapping[str, Any],
    approved_primary_refs: set[str],
    gap_coverage_batches: list[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[int], set[str]]:
    """Merge touching footage and require grounded reasons for every real cut."""

    spans: list[dict[str, Any]] = []
    raw_to_canonical: list[int] = []
    exclusion_evidence_refs: set[str] = set()
    for raw_index, raw in enumerate(raw_spans):
        start = float(raw["start_sec"])
        prior_end = float(raw_spans[raw_index - 1]["end_sec"]) if raw_index else -1.0
        raw_exclusion = raw.pop("_raw_exclusion_before", None)
        if raw_index == 0 and raw_exclusion is not None:
            raise ValueError("first_source_span_cannot_have_exclusion_rationale")
        if raw_index and start > prior_end:
            if not isinstance(raw_exclusion, Mapping):
                raise ValueError("source_span_gap_requires_exclusion_rationale")
            reason = str(raw_exclusion.get("reason") or "").strip()
            if not reason:
                raise ValueError("source_span_exclusion_reason_required")
            if _role_only_reason(reason):
                raise ValueError("source_span_gap_cannot_be_justified_by_story_role")
            refs_value = raw_exclusion.get("evidence_refs")
            if not isinstance(refs_value, (list, tuple)):
                raise ValueError("source_span_exclusion_evidence_refs_must_be_a_list")
            refs = [str(ref or "").strip() for ref in refs_value]
            if not refs or any(not ref for ref in refs) or len(refs) != len(set(refs)):
                raise ValueError(
                    "source_span_exclusion_evidence_refs_must_have_unique_nonempty_refs"
                )
            times = [_semantic_evidence_time(ref, evidence_catalog) for ref in refs]
            if any(time is None for time in times) or not any(
                prior_end < float(time) < start for time in times if time is not None
            ):
                raise ValueError("source_span_exclusion_evidence_must_be_inside_gap")
            if not all(
                ref.startswith(("stt-sec:", "chat-ms:", "chzzk-semantic:"))
                or ref in approved_primary_refs
                for ref in refs
            ):
                raise ValueError(
                    "source_span_exclusion_requires_primary_semantic_evidence"
                )
            overlapping_batches = sorted(
                (
                    batch
                    for batch in gap_coverage_batches or []
                    if float(batch.get("end_sec") or 0) > prior_end
                    and float(batch.get("start_sec") or 0) < start
                ),
                key=lambda batch: (
                    float(batch.get("start_sec") or 0),
                    float(batch.get("end_sec") or 0),
                ),
            )
            uncovered_batches: list[Mapping[str, Any]] = []
            coverage_cursor = prior_end
            for batch in overlapping_batches:
                batch_start = max(prior_end, float(batch.get("start_sec") or 0))
                batch_end = min(start, float(batch.get("end_sec") or 0))
                if batch_end <= batch_start:
                    continue
                if batch_start > coverage_cursor:
                    raise ValueError(
                        "source_span_exclusion_bridge_coverage_unavailable"
                    )
                batch_refs = {
                    str(ref)
                    for ref in batch.get("evidence_refs") or []
                    if str(ref)
                    and (
                        (time := _semantic_evidence_time(str(ref), evidence_catalog))
                        is not None
                    )
                    and prior_end < float(time) < start
                }
                if not batch_refs:
                    raise ValueError(
                        "source_span_exclusion_bridge_coverage_unavailable"
                    )
                if not batch_refs.intersection(refs):
                    uncovered_batches.append(batch)
                coverage_cursor = max(coverage_cursor, batch_end)
            if overlapping_batches and coverage_cursor < start:
                raise ValueError(
                    "source_span_exclusion_bridge_coverage_unavailable"
                )
            if uncovered_batches:
                raise ValueError(
                    "source_span_exclusion_evidence_does_not_cover_gap"
                )
            raw["exclusion_before"] = {"reason": reason, "evidence_refs": refs}
            exclusion_evidence_refs.update(refs)

        if spans and start == float(spans[-1]["end_sec"]):
            previous = spans[-1]
            previous["end_sec"] = raw["end_sec"]
            previous["duration_sec"] = (
                float(raw["end_sec"]) - float(previous["start_sec"])
            )
            for key in ("story_roles", "evidence_refs"):
                previous[key] = list(dict.fromkeys([*previous[key], *raw[key]]))
            if raw["reason"] != previous["reason"]:
                previous["reason"] = f'{previous["reason"]} / {raw["reason"]}'
            raw_to_canonical.append(len(spans) - 1)
            continue
        raw["span_index"] = len(spans)
        spans.append(raw)
        raw_to_canonical.append(len(spans) - 1)
    return spans, raw_to_canonical, exclusion_evidence_refs
