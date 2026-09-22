"""BroadcastMap outline parsing and terminal-time normalization.

This module owns parsing of the public outline text and editable manager
Markdown input, including only the terminal D1/D2 duration correction.
It does not generate semantics, persist data, or render reports.
"""
from __future__ import annotations

import re
from typing import Any

from .schema import (
    CAUSAL_PROPOSAL_HEADING,
    RANGE_RE,
    hms_to_seconds,
    point_review_heading,
    seconds_to_hms,
)
from .check import _section, validate_manager_outline, validate_structured_manager_outline

def _normalize_terminal_outline_ends(text: str, *, duration_sec: int | None) -> str:
    """Clamp only the final D1 and its final D2 to the exact VOD boundary."""
    if duration_sec is None:
        return text

    toc_start = text.index("[실제 목차]") + len("[실제 목차]")
    toc_end = text.index("[Point]", toc_start)
    toc = text[toc_start:toc_end]
    lines = toc.splitlines(keepends=True)
    outline_rows: list[tuple[int, str, re.Match[str]]] = []
    for index, row in enumerate(lines):
        level = re.match(r"^\s*(D[12])\s+", row)
        match = RANGE_RE.search(row)
        if level and match:
            outline_rows.append((index, level.group(1), match))
    if not outline_rows:
        return text

    terminal_indexes = {next(index for index, level, _ in reversed(outline_rows) if level == "D1")}
    if outline_rows[-1][1] == "D2":
        terminal_indexes.add(outline_rows[-1][0])
    exact_end = seconds_to_hms(duration_sec)
    for index, _level, match in reversed(outline_rows):
        if index not in terminal_indexes:
            continue
        start_text, end_text = re.split(r"\s*[-–~]\s*", match.group(0))
        start_sec = hms_to_seconds(start_text)
        end_sec = hms_to_seconds(end_text)
        if start_sec < duration_sec and (
            duration_sec < end_sec or abs(end_sec - duration_sec) <= 2
        ):
            separator = re.search(r"[-–~]", match.group(0)).group(0)
            lines[index] = (
                lines[index][:match.start()]
                + f"{start_text}{separator}{exact_end}"
                + lines[index][match.end():]
            )
    return text[:toc_start] + "".join(lines) + text[toc_end:]


def parse_manager_outline(text: str, *, duration_sec: int | None = None) -> dict[str, Any]:
    """Parse the one public manager-outline contract into editable rows."""
    text = _normalize_terminal_outline_ends(text, duration_sec=duration_sec)
    validate_manager_outline(text, duration_sec=duration_sec)

    def editable_parts(tail: str) -> tuple[str, str]:
        title, separator, content = tail.partition(" — ")
        return title, content if separator else ""

    ranges: list[dict[str, Any]] = []
    parent_id = ""
    for row in _section(text, "[실제 목차]", "[Point]").splitlines():
        if not row.strip():
            continue
        level_match = re.match(r"^\s*(D[12])\s+", row)
        range_match = RANGE_RE.search(row)
        assert level_match and range_match
        start, end = re.split(r"\s*[-–~]\s*", range_match.group(0))
        item_id = f"range-{len(ranges)}"
        if level_match.group(1) == "D1":
            parent_id = item_id
        title, content = editable_parts(row[range_match.end():].strip())
        ranges.append({
            "id": item_id,
            "level": level_match.group(1),
            "parent_id": "" if level_match.group(1) == "D1" else parent_id,
            "start": start,
            "end": end,
            "title": title,
            "content": content,
        })
    points: list[dict[str, Any]] = []
    review_heading = point_review_heading(text)
    point_end = (
        CAUSAL_PROPOSAL_HEADING
        if CAUSAL_PROPOSAL_HEADING in text
        else review_heading
        if review_heading
        else "[짧은 요약]"
    )
    for row in _section(text, "[Point]", point_end).splitlines():
        row = row.strip()
        if not row or row == "없음":
            continue
        timestamp, tail = row.split(maxsplit=1)
        title, content = editable_parts(tail)
        points.append({
            "id": f"point-{len(points)}",
            "timestamp": timestamp,
            "title": title,
            "content": content,
        })
    candidate_rows: list[str] = []
    if review_heading:
        for row in _section(text, review_heading, "[짧은 요약]").splitlines():
            row = row.strip()
            if not row:
                continue
            candidate_rows.append(row)
    if candidate_rows == ["없음"]:
        candidate_rows = []
    candidates: list[dict[str, Any]] = []
    for row in candidate_rows:
        if row == "없음":
            continue
        match = RANGE_RE.search(row)
        assert match
        start, end = re.split(r"\s*[-–~]\s*", match.group(0))
        title, content = editable_parts(row[match.end():].strip())
        candidates.append({
            "id": f"candidate-{len(candidates)}",
            "start": start,
            "end": end,
            "title": title,
            "content": content,
        })
    return {
        "ranges": ranges,
        "points": points,
        "candidates": candidates,
        "read_only": {
            "broadcast_understanding": _section(text, "[방송 전체 이해]", "[실제 목차]").strip(),
            "short_summary": _section(text, "[짧은 요약]", "[불확실하거나 빠진 부분]").strip(),
            "unknown_or_missing": _section(text, "[불확실하거나 빠진 부분]", "[실제 사용한 정보]").strip(),
            "used_signals": _section(text, "[실제 사용한 정보]", "[사용한 콘텐츠 정보]").strip(),
            "content_info_usage": _section(text, "[사용한 콘텐츠 정보]", "[모델/호출]").strip(),
            "model_calls": text[text.index("[모델/호출]") + len("[모델/호출]"):].strip(),
        },
    }




_MANAGER_MARKDOWN_RANGE_RE = re.compile(
    r"^(?P<marks>#{3,4})\s+(?P<start>\d{2,}:\d{2}:\d{2})\s*[-–~]\s*"
    r"(?P<end>\d{2,}:\d{2}:\d{2})\s+(?P<title>\S.*)$"
)
_MANAGER_MARKDOWN_POINT_RE = re.compile(
    r"^###\s+(?P<timestamp>\d{2,}:\d{2}:\d{2})\s+(?P<title>\S.*)$"
)


def parse_manager_outline_markdown(
    markdown: str,
    *,
    previous_outline: dict[str, Any],
    duration_sec: int | None = None,
) -> dict[str, Any]:
    """Apply the manager Markdown view back to the same BroadcastMap rows.

    Add/remove stays in the structure controls. Requiring the same row counts here
    keeps Markdown prose from silently creating a second structure authority.
    """
    if not isinstance(markdown, str):
        raise ValueError("Markdown 문서를 읽을 수 없습니다.")
    validate_structured_manager_outline(previous_outline, duration_sec=duration_sec)
    section_names = ["방송 흐름", "주요 장면", "검토 범위"]
    sections: dict[str, list[tuple[int, str]]] = {name: [] for name in section_names}
    current_section = ""
    seen_sections: list[str] = []
    for line_number, raw_line in enumerate(markdown.splitlines(), 1):
        line = raw_line.rstrip()
        if line.startswith("## ") and not line.startswith("### "):
            name = line[3:].strip()
            if name not in sections:
                raise ValueError(f"{line_number}번째 줄: 허용되지 않은 큰 제목입니다. 방송 흐름, 주요 장면, 검토 범위만 사용해 주세요.")
            if name in seen_sections:
                raise ValueError(f"{line_number}번째 줄: '{name}' 제목이 중복되었거나 순서가 잘못되었습니다.")
            expected = section_names[len(seen_sections)]
            if name != expected:
                raise ValueError(f"{line_number}번째 줄: '{expected}' 제목이 먼저 와야 합니다.")
            current_section = name
            seen_sections.append(name)
            sections[name].append((line_number, line))
            continue
        if not current_section:
            if line.strip():
                raise ValueError(f"{line_number}번째 줄: 문서는 '## 방송 흐름'으로 시작해야 합니다.")
            continue
        sections[current_section].append((line_number, line))
    if any(not sections[name] for name in section_names):
        missing = next(name for name in section_names if not sections[name])
        raise ValueError(f"'## {missing}' 영역이 없습니다.")

    previous_ranges = list(previous_outline.get("ranges") or [])
    previous_points = list(previous_outline.get("points") or [])
    previous_candidates = list(previous_outline.get("candidates") or [])
    parsed_ranges: list[dict[str, Any]] = []
    parsed_points: list[dict[str, Any]] = []
    parsed_candidates: list[dict[str, Any]] = []

    def blocks(name: str) -> list[tuple[int, str, list[str]]]:
        result: list[tuple[int, str, list[str]]] = []
        for line_number, line in sections[name][1:]:
            if line.startswith("###"):
                result.append((line_number, line, []))
            elif result:
                if line.startswith("- 실제 반응:") or line.startswith("- 확인 근거:"):
                    continue
                result[-1][2].append(line)
            elif line.strip():
                raise ValueError(f"{line_number}번째 줄: 항목 제목보다 앞에 설명을 둘 수 없습니다.")
        return result

    current_parent_id = ""
    for index, (line_number, heading, body) in enumerate(blocks("방송 흐름")):
        match = _MANAGER_MARKDOWN_RANGE_RE.fullmatch(heading)
        if not match:
            raise ValueError(f"{line_number}번째 줄: 방송 흐름은 '### 시작–끝 제목' 또는 '#### 시작–끝 제목' 형식이어야 합니다.")
        level = "D1" if match.group("marks") == "###" else "D2"
        if index >= len(previous_ranges) or str(previous_ranges[index].get("level")) != level:
            raise ValueError(f"{line_number}번째 줄: D1/D2 추가·삭제와 부모 변경은 방송 목차 편집에서 해 주세요.")
        previous = previous_ranges[index]
        if level == "D1":
            current_parent_id = str(previous["id"])
        elif not current_parent_id:
            raise ValueError(f"{line_number}번째 줄: D2 앞에는 부모 D1이 있어야 합니다.")
        parsed_ranges.append({
            **previous,
            "parent_id": "" if level == "D1" else current_parent_id,
            "start": match.group("start"),
            "end": match.group("end"),
            "title": match.group("title").strip(),
            "content": "\n".join(body).strip(),
        })
    if len(parsed_ranges) != len(previous_ranges):
        raise ValueError("방송 흐름 항목 수가 달라졌습니다. 추가·삭제는 방송 목차 편집에서 해 주세요.")

    for index, (line_number, heading, body) in enumerate(blocks("주요 장면")):
        match = _MANAGER_MARKDOWN_POINT_RE.fullmatch(heading)
        if not match:
            raise ValueError(f"{line_number}번째 줄: 주요 장면은 '### 시각 제목' 형식이어야 합니다.")
        if index >= len(previous_points):
            raise ValueError(f"{line_number}번째 줄: 주요 장면 추가는 방송 목차 편집에서 해 주세요.")
        parsed_points.append({
            **previous_points[index],
            "timestamp": match.group("timestamp"),
            "title": match.group("title").strip(),
            "content": "\n".join(body).strip(),
        })
    if len(parsed_points) != len(previous_points):
        raise ValueError("주요 장면 수가 달라졌습니다. 추가·삭제는 방송 목차 편집에서 해 주세요.")

    for index, (line_number, heading, body) in enumerate(blocks("검토 범위")):
        match = _MANAGER_MARKDOWN_RANGE_RE.fullmatch(heading)
        if not match or match.group("marks") != "###":
            raise ValueError(f"{line_number}번째 줄: 검토 범위는 '### 시작–끝 제목' 형식이어야 합니다.")
        if index >= len(previous_candidates):
            raise ValueError(f"{line_number}번째 줄: 검토 범위 추가는 방송 목차 편집에서 해 주세요.")
        parsed_candidates.append({
            **previous_candidates[index],
            "start": match.group("start"),
            "end": match.group("end"),
            "title": match.group("title").strip(),
            "content": "\n".join(body).strip(),
        })
    if len(parsed_candidates) != len(previous_candidates):
        raise ValueError("검토 범위 수가 달라졌습니다. 추가·삭제는 방송 목차 편집에서 해 주세요.")

    result = {
        "ranges": parsed_ranges,
        "points": parsed_points,
        "candidates": parsed_candidates,
        "read_only": dict(previous_outline.get("read_only") or {}),
    }
    try:
        validate_structured_manager_outline(result, duration_sec=duration_sec)
    except ValueError as exc:
        translations = {
            "outline range end must be after start": "끝 시각은 시작 시각보다 뒤여야 합니다.",
            "D1 ranges must be ordered and non-overlapping": "D1 방송 흐름은 시간순이어야 하며 서로 겹칠 수 없습니다.",
            "D2 range must be contained by its D1": "D2 시간은 부모 D1 범위 안에 있어야 합니다.",
            "sibling D2 ranges must be ordered and non-overlapping": "같은 D1 아래의 D2는 시간순이어야 하며 서로 겹칠 수 없습니다.",
            "outline timestamp exceeds VOD duration": "방송 흐름 시각이 VOD 길이를 넘었습니다.",
            "Point timestamp exceeds VOD duration": "주요 장면 시각이 VOD 길이를 넘었습니다.",
            "highlight candidate end must be after start": "검토 범위 끝 시각은 시작 시각보다 뒤여야 합니다.",
            "highlight candidate end exceeds VOD duration": "검토 범위가 VOD 길이를 넘었습니다.",
        }
        raise ValueError(translations.get(str(exc), str(exc))) from exc
    return result
