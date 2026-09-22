"""Shared, answer-free BroadcastMap outline syntax primitives.

This module owns textual headings, timecode parsing, and regular expressions used
by both the normal report facade and BroadcastMap evidence readers.  It contains
no generation, persistence, or manager-facing projection logic.
"""
from __future__ import annotations

import re

CAUSAL_WINDOW_RE = re.compile(
    r"^\s*(?:-\s*)?CAUSAL_WINDOW\s+"
    r"setup=(?P<setup>\d{2,}:\d{2}:\d{2})\s+"
    r"event=(?P<event>\d{2,}:\d{2}:\d{2})\s+"
    r"reaction=(?P<reaction>\d{2,}:\d{2}:\d{2})\s+"
    r"payoff=(?P<payoff>\d{2,}:\d{2}:\d{2})"
    r"(?:\s+\|\s+(?P<summary>\S.*))?\s*$"
)
CAUSAL_PROPOSAL_HEADING = "[Candidate 맥락 근거 제안]"
POINT_REVIEW_HEADING = "[Point 검토창]"
LEGACY_CANDIDATE_HEADING = "[하이라이트 후보 구간]"
FINAL_HEADINGS = (
    "[방송 전체 이해]", "[실제 목차]", "[Point]", "[짧은 요약]",
    "[불확실하거나 빠진 부분]", "[실제 사용한 정보]",
    "[사용한 콘텐츠 정보]", "[모델/호출]",
)
RANGE_RE = re.compile(r"\b\d{2}:\d{2}:\d{2}\s*[-–~]\s*\d{2}:\d{2}:\d{2}\b")
POINT_RE = re.compile(r"(?m)^\d{2}:\d{2}:\d{2}(?!\s*[-–~])\s+.+$")


def point_review_heading(text: str) -> str | None:
    """Return the currently supported review section heading in ``text``."""
    if POINT_REVIEW_HEADING in text:
        return POINT_REVIEW_HEADING
    if LEGACY_CANDIDATE_HEADING in text:
        return LEGACY_CANDIDATE_HEADING
    return None


def hms_to_seconds(value: str) -> int:
    """Parse a validated ``HH:MM:SS`` timecode without accepting invalid clocks."""
    hours, minutes, seconds = (int(part) for part in value.strip().split(":"))
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"invalid HH:MM:SS timestamp: {value}")
    return hours * 3600 + minutes * 60 + seconds


def seconds_to_hms(value: int) -> str:
    """Format seconds as a non-negative ``HH:MM:SS`` timecode."""
    value = max(0, int(value))
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


__all__ = [
    "CAUSAL_WINDOW_RE", "CAUSAL_PROPOSAL_HEADING", "POINT_REVIEW_HEADING",
    "LEGACY_CANDIDATE_HEADING", "FINAL_HEADINGS", "RANGE_RE", "POINT_RE",
    "point_review_heading", "hms_to_seconds", "seconds_to_hms",
]
