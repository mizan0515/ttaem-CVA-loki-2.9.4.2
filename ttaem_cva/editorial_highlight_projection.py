"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations


def _clock_seconds(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in str(value).split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _highlight_story_refs(highlight: dict) -> list[str]:
    refs = highlight.get("story_refs") or (
        [highlight.get("story_ref")] if highlight.get("story_ref") else []
    )
    return list(dict.fromkeys(str(ref).strip() for ref in refs if str(ref).strip()))
