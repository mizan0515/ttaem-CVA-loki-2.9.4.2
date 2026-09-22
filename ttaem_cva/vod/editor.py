"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations


def apply_story_point_shadow_to_normal_consumers(
    outline: dict, candidate_shadow: dict,
) -> tuple[dict, list[dict]]:
    """Validate the Story shadow without replacing canonical Points."""

    provider_meta = dict(candidate_shadow.get("story_provider_meta") or {})
    selection = dict(candidate_shadow.get("story_point_selection") or {})
    audit = dict(candidate_shadow.get("story_primary_evidence_audit") or {})
    if provider_meta.get("actual_model") != "codex-session":
        raise ValueError("story Point consumer requires actual codex-session")
    if not selection.get("all_packets_decided"):
        raise ValueError("story Point consumer requires one decision per packet")
    if int(audit.get("silent_drop_count") or 0) != 0:
        raise ValueError("story Point consumer rejects silently dropped primary evidence")

    canonical_points = [
        dict(row) for row in outline.get("points") or [] if isinstance(row, dict)
    ]
    canonical_point_ids = [str(row.get("id") or "") for row in canonical_points]
    if (
        any(not point_id for point_id in canonical_point_ids)
        or len(canonical_point_ids) != len(set(canonical_point_ids))
    ):
        raise ValueError("canonical Points require unique non-empty IDs")
    stories = [
        dict(row)
        for row in selection.get("selected_points") or []
        if isinstance(row, dict)
    ]
    story_point_ids = [str(row.get("point_ref") or "") for row in stories]
    story_packet_ids = [str(row.get("story_packet_id") or "") for row in stories]
    if (
        story_point_ids != canonical_point_ids
        or len(story_packet_ids) != len(set(story_packet_ids))
        or any(not story_packet_id for story_packet_id in story_packet_ids)
        or selection.get("rejected_packets")
        or int(selection.get("packet_count") or 0) != len(canonical_points)
        or int(selection.get("packet_decision_count") or 0) != len(canonical_points)
    ):
        raise ValueError("every canonical Point requires exactly one Story in stable order")

    effective = dict(outline)
    effective["points"] = canonical_points
    point_rows: list[dict] = []
    for row in effective["points"]:
        timestamp = str(row.get("timestamp") or "")
        hours, minutes, seconds = (int(part) for part in timestamp.split(":"))
        point_rows.append({
            **row,
            "currentTime": hours * 3600 + minutes * 60 + seconds,
        })
    return effective, point_rows
