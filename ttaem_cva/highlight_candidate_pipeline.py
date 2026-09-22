"""Baseline single-VOD chat and audio producers; cross-VOD alignment is out of scope."""
from __future__ import annotations
import hashlib
import json
from typing import Any, Mapping
from .chat_analyzer import find_edit_points

def build_chat_highlight_candidates(
    chats: list[dict],
    *,
    duration_sec: int | float | None,
    cfg: Mapping[str, Any],
    source_video_id: str,
) -> tuple[list[dict[str, Any]], str]:
    """Run the stable chat producer once and stamp its reusable run identity."""

    producer_input = {
        "source_video_id": str(source_video_id),
        "duration_sec": int(duration_sec or 0),
        "chat_count": len(chats or []),
        "first_ms": int((chats or [{}])[0].get("ms") or 0),
        "last_ms": int((chats or [{}])[-1].get("ms") or 0),
        "highlight_per_hour": int(cfg.get("highlight_per_hour", 2)),
        "highlight_min": int(cfg.get("highlight_min", 8)),
        "highlight_max": int(cfg.get("highlight_max", 20)),
    }
    producer_run_ref = "chat-edit-points:" + hashlib.sha256(
        json.dumps(
            producer_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    ranked = find_edit_points(
        chats,
        duration_sec=duration_sec,
        per_hour=cfg.get("highlight_per_hour", 2),
        min_count=cfg.get("highlight_min", 8),
        max_count=cfg.get("highlight_max", 20),
    )
    stamped = []
    for row in ranked:
        item = dict(row)
        item["producer_run_ref"] = producer_run_ref
        stamped.append(item)
    return stamped, producer_run_ref

def merge_audio_reaction_highlights(
    chat_highlights: list[dict],
    audio_reaction_metadata: dict | None,
    *,
    overlap_sec: int = 60,
) -> list[dict]:
    """Use the stable audio merge behavior for an already-built chat list."""

    merged = [dict(row) for row in (chat_highlights or [])]
    raw = (
        audio_reaction_metadata.get("audio_reaction_peaks")
        if isinstance(audio_reaction_metadata, dict)
        else None
    )
    peaks = raw if isinstance(raw, list) else []
    if not peaks:
        return merged
    for peak in peaks:
        if not isinstance(peak, dict):
            continue
        sec_raw = peak.get("center_sec", peak.get("start_sec"))
        try:
            sec = int(round(float(sec_raw)))
        except (TypeError, ValueError):
            continue
        labels = [str(value) for value in (peak.get("labels") or []) if value]
        score = float(peak.get("score") or 0.0)
        nearest = None
        nearest_dist = None
        for item in merged:
            try:
                item_sec = int(round(float(item.get("sec"))))
            except (TypeError, ValueError):
                continue
            distance = abs(item_sec - sec)
            if distance <= overlap_sec and (
                nearest_dist is None or distance < nearest_dist
            ):
                nearest = item
                nearest_dist = distance
        if nearest is not None:
            old = float(nearest.get("composite", 0.0) or 0.0)
            boost = 0.08 if "chat_audio_overlap" in labels else 0.04
            nearest["composite"] = round(min(1.0, max(old, score) + boost), 4)
            nearest["audio_reaction_peak"] = True
            nearest["audio_labels"] = labels
            nearest["audio_score"] = round(score, 4)
            nearest["source_signals"] = sorted(
                set(nearest.get("source_signals") or ["chat"]) | {"audio"}
            )
            continue
        merged.append(
            {
                "sec": sec,
                "count": 0,
                "raw_score": round(score, 4),
                "z_count": 0.0,
                "z_score": round(float(peak.get("z_score") or 0.0), 4),
                "composite": round(min(1.0, score * 0.9), 4),
                "rank": 999,
                "signal_type": "audio_reaction",
                "audio_reaction_peak": True,
                "audio_labels": labels,
                "audio_score": round(score, 4),
                "source_signals": ["audio"],
            }
        )
    merged.sort(
        key=lambda row: (
            float(row.get("composite", 0.0) or 0.0),
            bool(row.get("audio_reaction_peak")),
        ),
        reverse=True,
    )
    for rank, item in enumerate(merged, start=1):
        item["rank"] = rank
    return merged
