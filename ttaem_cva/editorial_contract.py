"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

from typing import Any, Mapping


def _clock_seconds(value: Any) -> float:
    parts = str(value or "").split(":")
    if len(parts) != 3:
        raise ValueError("invalid_broadcast_map_clock")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("invalid_broadcast_map_clock") from exc
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError("invalid_broadcast_map_clock")
    return float(hours * 3600 + minutes * 60 + seconds)


def _derive_source_span_structure_refs(
    *,
    start: float,
    end: float,
    d1_index: Mapping[str, Mapping[str, Any]],
    d2_index: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    """Resolve span membership from frozen ranges without using D2 as a clip."""

    def _intersecting_ranges(
        index: Mapping[str, Mapping[str, Any]],
    ) -> list[tuple[float, float, str]]:
        values = []
        for ref, row in index.items():
            range_start = _clock_seconds(row.get("start"))
            range_end = _clock_seconds(row.get("end"))
            if range_start < end and start < range_end:
                values.append((range_start, range_end, ref))
        return sorted(values, key=lambda item: (item[0], item[1], item[2]))

    intersecting_d1 = _intersecting_ranges(d1_index)
    if not intersecting_d1:
        raise ValueError("source_span_clock_outside_broadcast_map_d1")
    covered_until = start
    for range_start, range_end, _ref in intersecting_d1:
        if range_start > covered_until:
            raise ValueError("source_span_crosses_unmapped_d1_gap")
        covered_until = max(covered_until, range_end)
        if covered_until >= end:
            break
    if covered_until < end:
        raise ValueError("source_span_clock_outside_broadcast_map_d1")

    intersecting_d2 = _intersecting_ranges(d2_index)
    return (
        [item[2] for item in intersecting_d1],
        [item[2] for item in intersecting_d2],
    )
