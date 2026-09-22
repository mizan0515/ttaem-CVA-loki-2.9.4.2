"""Deterministic BroadcastMap model-output canonicalization."""
from __future__ import annotations

import re
from typing import Any

from .check import _section, _short_summary_has_structure
from .schema import (
    RANGE_RE as _RANGE_RE,
    hms_to_seconds as _hms_to_seconds,
    seconds_to_hms as _seconds_to_hms,
)


def _canonicalize_model_short_summary(text: str) -> str:
    """Project an invalid model summary from its already-accepted D1 titles.

    The model remains the sole semantic owner of the BroadcastMap.  This repair only
    removes a redundant formatted restatement that the product can reproduce from the
    model's D1 rows without adding a fact, timestamp, or hierarchy.
    """
    summary = _section(text, "[짧은 요약]", "[불확실하거나 빠진 부분]")
    if not _short_summary_has_structure(summary):
        return text

    titles: list[str] = []
    for row in _section(text, "[실제 목차]", "[Point]").splitlines():
        if not row.startswith("D1 "):
            continue
        match = _RANGE_RE.search(row)
        title = row[match.end():].strip() if match else ""
        if title:
            titles.append(title)
    if not titles:
        return text

    group_size = max(1, (len(titles) + 4) // 5)
    summary_rows = []
    for start in range(0, len(titles), group_size):
        group = titles[start:start + group_size]
        if len(group) == 1:
            summary_rows.append(f"큰 흐름은 {group[0]}이다.")
        else:
            summary_rows.append(f"큰 흐름은 {', '.join(group)} 순서로 이어진다.")

    start_marker = "[짧은 요약]"
    end_marker = "[불확실하거나 빠진 부분]"
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + "\n" + "\n".join(summary_rows) + "\n" + text[end:]


def _canonicalize_model_d1_ranges(text: str) -> str:
    """Repair deterministic ordering/boundary noise in model-owned D1 blocks.

    The whole D1 block moves with its D2 children. An overlapping earlier D1 may end
    at the next D1 start only when no child starts in the disputed interval; a child
    that merely crosses the boundary is clamped to the same transition. Ambiguous
    same-start or duplicated child blocks remain strict validation failures.
    """
    start_marker = "[실제 목차]"
    end_marker = "[Point]"
    if start_marker not in text or end_marker not in text:
        return text
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker, start)
    body = text[start:end]
    rows = body.splitlines()
    d1_indexes = [index for index, row in enumerate(rows) if row.startswith("D1 ")]
    if len(d1_indexes) < 2:
        return text

    entries: list[dict[str, Any]] = []
    for group_index, d1_index in enumerate(d1_indexes):
        group_end = d1_indexes[group_index + 1] if group_index + 1 < len(d1_indexes) else len(rows)
        block = rows[d1_index:group_end]
        match = _RANGE_RE.search(block[0])
        if not match:
            return text
        start_text, end_text = re.split(r"\s*[-–~]\s*", match.group(0), maxsplit=1)
        d1_start = _hms_to_seconds(start_text)
        d1_end = _hms_to_seconds(end_text)
        if d1_end <= d1_start:
            return text
        children: list[dict[str, Any]] = []
        for line_index, line in enumerate(block[1:], start=1):
            if not line.lstrip().startswith("D2 "):
                continue
            child_match = _RANGE_RE.search(line)
            if not child_match:
                return text
            child_start_text, child_end_text = re.split(
                r"\s*[-–~]\s*", child_match.group(0), maxsplit=1
            )
            child_start = _hms_to_seconds(child_start_text)
            child_end = _hms_to_seconds(child_end_text)
            if child_end <= child_start or child_start < d1_start or child_end > d1_end:
                return text
            children.append({
                "line_index": line_index, "match_start": child_match.start(),
                "match_end": child_match.end(), "start_text": child_start_text,
                "start": child_start, "end": child_end,
            })
        entries.append({
            "block": block, "match_start": match.start(), "match_end": match.end(),
            "start_text": start_text, "start": d1_start, "end": d1_end,
            "children": children,
        })

    entries.sort(key=lambda item: item["start"])
    for index, entry in enumerate(entries[:-1]):
        next_start = entries[index + 1]["start"]
        if next_start <= entry["start"] or entry["end"] <= next_start:
            continue
        if any(child["start"] >= next_start for child in entry["children"]):
            return text
        for child in entry["children"]:
            if child["end"] <= next_start:
                continue
            replacement = f"{child['start_text']}-{_seconds_to_hms(next_start)}"
            line = entry["block"][child["line_index"]]
            entry["block"][child["line_index"]] = line[: child["match_start"]] + replacement + line[child["match_end"] :]
            child["end"] = next_start
        replacement = f"{entry['start_text']}-{_seconds_to_hms(next_start)}"
        line = entry["block"][0]
        entry["block"][0] = line[: entry["match_start"]] + replacement + line[entry["match_end"] :]
        entry["end"] = next_start

    rebuilt_rows = rows[: d1_indexes[0]]
    for entry in entries:
        rebuilt_rows.extend(entry["block"])
    rebuilt = "\n".join(rebuilt_rows)
    if body.endswith(("\n", "\r")):
        rebuilt += "\n"
    return text[:start] + rebuilt + text[end:]


def _canonicalize_model_sibling_d2_ranges(text: str) -> str:
    """Repair only deterministic ordering/boundary noise in model-owned D2 rows.

    Manager edits and stored artifacts still use ``validate_manager_outline`` directly.
    This projection is limited to one D1 at a time: it never changes a D1, D2 start,
    title, Point, or an invalid/out-of-parent range.
    """
    start_marker = "[실제 목차]"
    end_marker = "[Point]"
    if start_marker not in text or end_marker not in text:
        return text
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker, start)
    body = text[start:end]
    rows = body.splitlines()
    d1_indexes = [index for index, row in enumerate(rows) if row.startswith("D1 ")]

    for group_index, d1_index in enumerate(d1_indexes):
        group_end = d1_indexes[group_index + 1] if group_index + 1 < len(d1_indexes) else len(rows)
        parent_match = _RANGE_RE.search(rows[d1_index])
        if not parent_match:
            continue
        parent_start_text, parent_end_text = re.split(r"\s*[-–~]\s*", parent_match.group(0), maxsplit=1)
        parent_start = _hms_to_seconds(parent_start_text)
        parent_end = _hms_to_seconds(parent_end_text)
        child_indexes = [index for index in range(d1_index + 1, group_end) if rows[index].lstrip().startswith("D2 ")]
        entries: list[dict[str, Any]] = []
        safe_group = parent_end > parent_start
        for child_index in child_indexes:
            match = _RANGE_RE.search(rows[child_index])
            if not match:
                safe_group = False
                break
            start_text, end_text = re.split(r"\s*[-–~]\s*", match.group(0), maxsplit=1)
            child_start = _hms_to_seconds(start_text)
            child_end = _hms_to_seconds(end_text)
            if child_end <= child_start or child_start < parent_start or child_end > parent_end:
                safe_group = False
                break
            entries.append({
                "line": rows[child_index], "match_start": match.start(), "match_end": match.end(),
                "start_text": start_text, "start": child_start, "end": child_end,
            })
        if not safe_group or len(entries) < 2:
            continue

        entries.sort(key=lambda item: item["start"])
        for index, entry in enumerate(entries[:-1]):
            next_start = entries[index + 1]["start"]
            if next_start > entry["start"] and entry["end"] > next_start:
                replacement = f"{entry['start_text']}-{_seconds_to_hms(next_start)}"
                entry["line"] = entry["line"][: entry["match_start"]] + replacement + entry["line"][entry["match_end"] :]
                entry["end"] = next_start
        for child_index, entry in zip(child_indexes, entries):
            rows[child_index] = entry["line"]

    rebuilt = "\n".join(rows)
    if body.endswith(("\n", "\r")):
        rebuilt += "\n"
    return text[:start] + rebuilt + text[end:]


def _canonicalize_model_outline(text: str) -> str:
    text = _canonicalize_model_d1_ranges(text)
    text = _canonicalize_model_sibling_d2_ranges(text)
    return _canonicalize_model_short_summary(text)