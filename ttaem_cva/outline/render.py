"""BroadcastMap renderers for manager Markdown and editable tagged outline text.

This module owns deterministic output from an already validated BroadcastMap.
It depends only on shared schema/check contracts and never imports the parser.
"""
from __future__ import annotations

import re
from typing import Any

from .check import validate_manager_outline, validate_structured_manager_outline
from .schema import (
    CAUSAL_PROPOSAL_HEADING,
    CAUSAL_WINDOW_RE,
    point_review_heading,
)


def _reconcile_causal_proposals(text: str, accepted_point_times: set[str]) -> str:
    """Keep only causal proposals that still belong to an accepted Point."""

    if CAUSAL_PROPOSAL_HEADING not in text:
        return text
    proposal_start = (
        text.index(CAUSAL_PROPOSAL_HEADING) + len(CAUSAL_PROPOSAL_HEADING)
    )
    proposal_end_marker = point_review_heading(text) or "[짧은 요약]"
    proposal_end = text.index(proposal_end_marker, proposal_start)
    kept_rows: list[str] = []
    for raw_row in text[proposal_start:proposal_end].splitlines():
        row = raw_row.strip()
        if not row:
            continue
        match = CAUSAL_WINDOW_RE.fullmatch(row)
        if match is None or any(
            match.group(phase) in accepted_point_times for phase in ("setup", "event")
        ):
            kept_rows.append(row)
    replacement = "\n" + "\n".join(kept_rows or ["없음"]) + "\n"
    return text[:proposal_start] + replacement + text[proposal_end:]


def project_manager_outline_markdown(
    outline: dict[str, Any],
    *,
    duration_sec: int | None = None,
    evidence_by_id: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Project BroadcastMap into the deterministic, manager-visible Markdown view."""
    validate_structured_manager_outline(outline, duration_sec=duration_sec)
    ranges = list(outline.get("ranges") or [])
    points = list(outline.get("points") or [])
    candidates = list(outline.get("candidates") or [])
    evidence_by_id = evidence_by_id or {}
    rows: list[str] = ["## 방송 흐름", ""]

    def append_item(heading: str, item: dict[str, Any]) -> None:
        rows.append(f"{heading} {str(item.get('title') or '').strip()}")
        content = str(item.get("content") or "").strip()
        if content:
            rows.extend([content, ""])
        else:
            rows.append("")

    for parent in (item for item in ranges if item.get("level") == "D1"):
        append_item(f"### {parent['start']}–{parent['end']}", parent)
        for child in ranges:
            if child.get("level") == "D2" and child.get("parent_id") == parent.get("id"):
                append_item(f"#### {child['start']}–{child['end']}", child)

    rows.extend(["## 주요 장면", ""])
    for item in points:
        append_item(f"### {item['timestamp']}", item)
        evidence = evidence_by_id.get(str(item.get("id") or "")) or {}
        reaction = str(evidence.get("actual_reaction") or "").strip()
        sources = evidence.get("sources") or []
        if reaction:
            rows.extend([f"- 실제 반응: {reaction}", ""])
        if sources:
            rows.extend([f"- 확인 근거: {', '.join(str(value) for value in sources)}", ""])

    rows.extend(["## 검토 범위", ""])
    for item in candidates:
        append_item(f"### {item['start']}–{item['end']}", item)
    return "\n".join(rows).rstrip() + "\n"


def serialize_manager_outline(
    original_text: str, outline: dict[str, Any], *, duration_sec: int | None = None
) -> str:
    """Serialize editable rows while preserving every non-outline section byte-for-byte."""
    validate_structured_manager_outline(outline, duration_sec=duration_sec)
    ranges = list(outline.get("ranges") or [])
    points = list(outline.get("points") or [])
    candidates = list(outline.get("candidates") or [])
    d1_rows = [item for item in ranges if str(item.get("level") or "") == "D1"]
    d2_rows = [item for item in ranges if str(item.get("level") or "") == "D2"]

    def editable_tail(item: dict[str, Any]) -> str:
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        return title + (f" — {content}" if content else "")

    range_rows = []
    for parent in d1_rows:
        range_rows.append(f"D1 {parent.get('start', '')}-{parent.get('end', '')} {editable_tail(parent)}")
        range_rows.extend(
            f"  D2 {child.get('start', '')}-{child.get('end', '')} {editable_tail(child)}"
            for child in d2_rows if str(child.get("parent_id") or "") == str(parent.get("id") or "")
        )
    point_rows = [f"{item.get('timestamp', '')} {editable_tail(item)}" for item in points] or ["없음"]
    candidate_rows = [
        f"{item.get('start', '')}-{item.get('end', '')} {editable_tail(item)}"
        for item in candidates
    ] or ["없음"]
    result = re.sub(r"(?s)(\[실제 목차\]\s*).*?(\s*\[Point\])", lambda m: m.group(1) + "\n".join(range_rows) + "\n" + m.group(2).lstrip(), original_text, count=1)
    review_heading = point_review_heading(result)
    point_end = (
        CAUSAL_PROPOSAL_HEADING
        if CAUSAL_PROPOSAL_HEADING in result
        else review_heading
        if review_heading
        else "[짧은 요약]"
    )
    result = re.sub(rf"(?s)(\[Point\]\s*).*?(\s*{re.escape(point_end)})", lambda m: m.group(1) + "\n".join(point_rows) + "\n" + m.group(2).lstrip(), result, count=1)
    result = _reconcile_causal_proposals(
        result,
        {str(item.get("timestamp") or "").strip() for item in points},
    )
    if review_heading:
        result = re.sub(
            rf"(?s)({re.escape(review_heading)}\s*).*?(\s*\[짧은 요약\])",
            lambda m: m.group(1) + "\n".join(candidate_rows) + "\n" + m.group(2).lstrip(),
            result,
            count=1,
        )
    validate_manager_outline(result, duration_sec=duration_sec)
    return result


__all__ = ["project_manager_outline_markdown", "serialize_manager_outline"]
