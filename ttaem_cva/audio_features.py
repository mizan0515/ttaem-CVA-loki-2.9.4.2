"""Main-pipeline audio reaction evidence for VOD reports.

The signal is intentionally relative within one VOD. Mixed-track loudness can
come from BGM, game effects, opening/ending music, or another speaker, so
audio peaks are supporting evidence only. Stronger labels require overlap with
chat peaks or SRT/speech timing.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import wave
from array import array
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

DEFAULT_WINDOW_SEC = 5
DEFAULT_MIN_PERCENTILE = 0.95
DEFAULT_PEAK_GAP_SEC = 30
DEFAULT_CHAT_OVERLAP_SEC = 60
DEFAULT_MAX_PEAKS = 20
DEFAULT_WAVEFORM_MAX_POINTS = 720
DEFAULT_LEARNING_MAX_WINDOWS = 1440
DEFAULT_LEARNING_PEAK_CONTEXT_SEC = 30
SILENCE_DB = -120.0

TIME_VALUE_KEYS = ("sec", "time_sec", "start_sec", "t")
LOUDNESS_VALUE_KEYS = ("loudness_db", "rms_db", "lufs", "value", "db")
LUFS_VALUE_KEYS = ("lufs", "lufs_m", "momentary_lufs", "r128_m")
SRT_TIME_RE = re.compile(
    r"(?P<h1>\d{2}):(?P<m1>\d{2}):(?P<s1>\d{2}),(?P<ms1>\d{3})\s+-->\s+"
    r"(?P<h2>\d{2}):(?P<m2>\d{2}):(?P<s2>\d{2}),(?P<ms2>\d{3})"
)


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _sample_sec_and_db(sample: object) -> tuple[float, float] | None:
    if isinstance(sample, dict):
        sec = next((_finite_float(sample.get(k)) for k in TIME_VALUE_KEYS if k in sample), None)
        db = next((_finite_float(sample.get(k)) for k in LOUDNESS_VALUE_KEYS if k in sample), None)
    elif isinstance(sample, (list, tuple)) and len(sample) >= 2:
        sec = _finite_float(sample[0])
        db = _finite_float(sample[1])
    else:
        return None
    if sec is None or db is None or sec < 0:
        return None
    return sec, db


def _sample_peak_db(sample: object) -> float | None:
    if not isinstance(sample, dict):
        return None
    for key in ("peak_db", "Peak_level", "peak_level"):
        if key in sample:
            return _finite_float(sample.get(key))
    return None


def _sample_lufs(sample: object) -> float | None:
    if not isinstance(sample, dict):
        return None
    for key in LUFS_VALUE_KEYS:
        if key in sample:
            return _finite_float(sample.get(key))
    return None


def _pcm_level_to_db(value: float, max_value: float) -> float:
    if value <= 0 or max_value <= 0:
        return SILENCE_DB
    return max(SILENCE_DB, 20.0 * math.log10(value / max_value))


def _samples_with_loudness(samples: Iterable[object], key: str) -> list[dict[str, float]]:
    prepared: list[dict[str, float]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        sec = next((_finite_float(sample.get(k)) for k in TIME_VALUE_KEYS if k in sample), None)
        if sec is None or sec < 0:
            continue
        if key == "lufs":
            db = _sample_lufs(sample)
        else:
            db = _finite_float(sample.get("rms_db", sample.get("loudness_db")))
        if db is None:
            continue
        row = {"sec": sec, "loudness_db": db}
        peak_db = _sample_peak_db(sample)
        if peak_db is not None:
            row["peak_db"] = peak_db
        prepared.append(row)
    return prepared


def _peak_overlap_count(primary: Sequence[dict], secondary: Sequence[dict], tolerance_sec: int) -> int:
    centers = []
    for item in secondary:
        center = _finite_float(item.get("center_sec"))
        if center is not None:
            centers.append(center)
    count = 0
    for item in primary:
        center = _finite_float(item.get("center_sec"))
        if center is not None and any(abs(center - other) <= tolerance_sec for other in centers):
            count += 1
    return count


def build_lufs_diagnostics(
    samples: Iterable[object],
    *,
    duration_sec: float | None = None,
    window_sec: int = DEFAULT_WINDOW_SEC,
    min_percentile: float = DEFAULT_MIN_PERCENTILE,
    min_gap_sec: int = DEFAULT_PEAK_GAP_SEC,
    max_peaks: int = DEFAULT_MAX_PEAKS,
) -> dict:
    """Compare opt-in LUFS peaks with the default relative RMS/peak signal."""

    sample_list = list(samples)
    rms_samples = _samples_with_loudness(sample_list, "rms")
    lufs_samples = _samples_with_loudness(sample_list, "lufs")

    rms_windows = build_loudness_windows(rms_samples, window_sec=window_sec, duration_sec=duration_sec)
    lufs_windows = build_loudness_windows(lufs_samples, window_sec=window_sec, duration_sec=duration_sec)
    rms_peaks = extract_reaction_peaks(
        rms_windows,
        min_percentile=min_percentile,
        min_gap_sec=min_gap_sec,
        max_peaks=max_peaks,
    )
    lufs_peaks = extract_reaction_peaks(
        lufs_windows,
        min_percentile=min_percentile,
        min_gap_sec=min_gap_sec,
        max_peaks=max_peaks,
    )

    paired_deltas = []
    rms_by_sec = {int(float(row["sec"])): row["loudness_db"] for row in rms_samples}
    for row in lufs_samples:
        sec = int(float(row["sec"]))
        if sec in rms_by_sec:
            paired_deltas.append(float(row["loudness_db"]) - float(rms_by_sec[sec]))

    overlap_count = _peak_overlap_count(lufs_peaks, rms_peaks, min_gap_sec)
    overlap_ratio = overlap_count / len(lufs_peaks) if lufs_peaks else 0.0
    if len(paired_deltas) < 30 or not lufs_peaks:
        recommendation = "keep_lufs_off_insufficient_evidence"
    elif overlap_ratio >= 0.7:
        recommendation = "diagnostic_only_matches_relative_peaks"
    elif overlap_ratio >= 0.4:
        recommendation = "diagnostic_only_review_needed"
    else:
        recommendation = "keep_lufs_off_mismatch"

    return {
        "enabled_by_default": False,
        "diagnostic_only": True,
        "sample_count": len(sample_list),
        "paired_sample_count": len(paired_deltas),
        "rms_peak_count": len(rms_peaks),
        "lufs_peak_count": len(lufs_peaks),
        "peak_overlap_within_sec": min_gap_sec,
        "peak_overlap_count": overlap_count,
        "peak_overlap_ratio": round(overlap_ratio, 4),
        "mean_lufs_minus_rms_db": round(sum(paired_deltas) / len(paired_deltas), 3) if paired_deltas else None,
        "recommendation": recommendation,
        "decision_rule": "LUFS is diagnostic evidence only; relative RMS/peak remains the default highlight signal.",
    }


def normalize_loudness_windows(windows: Sequence[dict]) -> list[dict]:
    """Add per-VOD z-score, percentile, and 0..1 normalized values."""

    prepared: list[dict[str, Any]] = []
    for item in windows:
        db = _finite_float(item.get("loudness_db"))
        if db is None:
            continue
        start = max(0.0, float(item.get("start_sec", 0.0)))
        end = max(start, float(item.get("end_sec", start)))
        prepared.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "loudness_db": db,
                "peak_db": _finite_float(item.get("peak_db")),
            }
        )

    if not prepared:
        return []

    values = [w["loudness_db"] for w in prepared]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    sd = math.sqrt(variance)
    sorted_values = sorted(values)

    for item in prepared:
        db = item["loudness_db"]
        if sd < 1e-9:
            z_score = 0.0
            normalized = 0.5
        else:
            z_score = (db - mean) / sd
            normalized = (max(-3.0, min(3.0, z_score)) + 3.0) / 6.0
        less = sum(1 for v in sorted_values if v < db)
        equal = sum(1 for v in sorted_values if v == db)
        percentile = (less + (equal * 0.5)) / len(sorted_values)
        item["z_score"] = round(z_score, 4)
        item["normalized"] = round(normalized, 4)
        item["percentile"] = round(percentile, 4)
        item["loudness_db"] = round(db, 3)
        if item["peak_db"] is not None:
            item["peak_db"] = round(float(item["peak_db"]), 3)
        else:
            item.pop("peak_db", None)
    return prepared


def build_loudness_windows(
    samples: Iterable[object],
    *,
    window_sec: int = DEFAULT_WINDOW_SEC,
    duration_sec: float | None = None,
) -> list[dict]:
    """Roll raw 1-second samples into fixed windows and normalize them."""

    if window_sec <= 0:
        raise ValueError("window_sec must be positive")

    buckets: dict[int, list[tuple[float, float | None]]] = defaultdict(list)
    for sample in samples:
        parsed = _sample_sec_and_db(sample)
        if parsed is None:
            continue
        sec, db = parsed
        idx = int(sec // window_sec)
        buckets[idx].append((db, _sample_peak_db(sample)))

    windows = []
    max_idx = max(buckets, default=-1)
    if duration_sec is not None and duration_sec > 0:
        max_idx = max(max_idx, int(math.ceil(duration_sec / window_sec)) - 1)

    for idx in range(max_idx + 1):
        values = buckets.get(idx)
        if not values:
            continue
        loudness_values = [v[0] for v in values]
        peak_values = [v[1] for v in values if v[1] is not None]
        start = idx * window_sec
        end = start + window_sec
        if duration_sec is not None and duration_sec > 0:
            end = min(end, float(duration_sec))
        item: dict[str, Any] = {
            "start_sec": float(start),
            "end_sec": float(end),
            "loudness_db": sum(loudness_values) / len(loudness_values),
        }
        if peak_values:
            item["peak_db"] = max(peak_values)
        windows.append(item)
    return normalize_loudness_windows(windows)


def _overlaps_range(
    start_sec: float,
    end_sec: float,
    ranges: Sequence[tuple[float, float]],
    tolerance_sec: float = 0.0,
) -> bool:
    lo = start_sec - tolerance_sec
    hi = end_sec + tolerance_sec
    return any(max(lo, float(a)) <= min(hi, float(b)) for a, b in ranges)


def _chat_overlap(center_sec: float, chat_peaks: Sequence[dict], tolerance_sec: float) -> bool:
    for peak in chat_peaks:
        sec = _finite_float(peak.get("sec")) if isinstance(peak, dict) else None
        if sec is not None and abs(sec - center_sec) <= tolerance_sec:
            return True
    return False


def extract_reaction_peaks(
    windows: Sequence[dict],
    *,
    chat_peaks: Sequence[dict] | None = None,
    speech_ranges: Sequence[tuple[float, float]] | None = None,
    min_percentile: float = DEFAULT_MIN_PERCENTILE,
    min_gap_sec: int = DEFAULT_PEAK_GAP_SEC,
    chat_overlap_sec: int = DEFAULT_CHAT_OVERLAP_SEC,
    max_peaks: int = DEFAULT_MAX_PEAKS,
) -> list[dict]:
    """Return conservative audio reaction candidates from normalized windows."""

    if not 0.0 <= min_percentile <= 1.0:
        raise ValueError("min_percentile must be between 0 and 1")
    if min_gap_sec < 0:
        raise ValueError("min_gap_sec must be non-negative")
    if max_peaks <= 0:
        return []

    ordered = sorted(
        (w for w in windows if _finite_float(w.get("percentile")) is not None),
        key=lambda w: float(w.get("start_sec", 0)),
    )
    if len(ordered) < 3:
        return []

    chat_peaks = chat_peaks or []
    speech_ranges = speech_ranges or []
    raw_peaks: list[dict[str, Any]] = []
    for idx, item in enumerate(ordered):
        percentile = float(item.get("percentile", 0.0))
        if percentile < min_percentile:
            continue
        z = float(item.get("z_score", 0.0))
        prev_db = float(ordered[idx - 1].get("loudness_db", item.get("loudness_db", 0))) if idx else None
        next_db = (
            float(ordered[idx + 1].get("loudness_db", item.get("loudness_db", 0)))
            if idx + 1 < len(ordered)
            else None
        )
        db = float(item.get("loudness_db", 0.0))
        if prev_db is not None and db < prev_db:
            continue
        if next_db is not None and db < next_db:
            continue

        start = float(item.get("start_sec", 0.0))
        end = float(item.get("end_sec", start))
        center = (start + end) / 2.0
        labels = ["audio_energy_peak"]
        if _overlaps_range(start, end, speech_ranges, tolerance_sec=1.0):
            labels.append("voice_reaction_candidate")
        if _chat_overlap(center, chat_peaks, chat_overlap_sec):
            labels.append("chat_audio_overlap")
        peak_db = _finite_float(item.get("peak_db"))
        if peak_db is not None and peak_db >= -0.5:
            labels.append("clipping_or_limiter_diagnostic")

        score = float(item.get("normalized", 0.5))
        if "voice_reaction_candidate" in labels:
            score += 0.08
        if "chat_audio_overlap" in labels:
            score += 0.08
        raw_peaks.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "center_sec": round(center, 3),
                "loudness_db": item.get("loudness_db"),
                "peak_db": peak_db,
                "percentile": round(percentile, 4),
                "z_score": round(z, 4),
                "normalized": item.get("normalized"),
                "score": round(min(1.0, score), 4),
                "labels": labels,
                "primary_label": "chat_audio_overlap" if "chat_audio_overlap" in labels else labels[-1],
                "caution": (
                    "supporting_signal_only"
                    if ("chat_audio_overlap" in labels or "voice_reaction_candidate" in labels)
                    else "mixed_track_loudness_not_voice_proof"
                ),
            }
        )

    selected: list[dict] = []
    for peak in sorted(raw_peaks, key=lambda p: (float(p["score"]), float(p["percentile"])), reverse=True):
        center = float(peak["center_sec"])
        if any(abs(center - float(existing["center_sec"])) < min_gap_sec for existing in selected):
            continue
        selected.append(peak)
        if len(selected) >= max_peaks:
            break
    return sorted(selected, key=lambda p: float(p["center_sec"]))


def build_audio_loudness_per_min(windows: Sequence[dict], *, duration_sec: float | None = None) -> list[float]:
    """Compatibility field for tools that consume minute-level loudness."""

    if not windows:
        return []
    max_sec = max(float(w.get("end_sec", 0.0)) for w in windows)
    if duration_sec is not None and duration_sec > 0:
        max_sec = max(max_sec, float(duration_sec))
    minute_count = max(1, int(math.ceil(max_sec / 60.0)))
    buckets: list[list[float]] = [[] for _ in range(minute_count)]
    for item in windows:
        db = _finite_float(item.get("loudness_db"))
        if db is None:
            continue
        idx = min(minute_count - 1, max(0, int(float(item.get("start_sec", 0.0)) // 60)))
        buckets[idx].append(db)
    fallback = sum(_finite_float(w.get("loudness_db")) or 0.0 for w in windows) / len(windows)
    return [round(sum(values) / len(values), 3) if values else round(fallback, 3) for values in buckets]


def build_audio_waveform(
    windows: Sequence[dict],
    *,
    duration_sec: float | None = None,
    max_points: int = DEFAULT_WAVEFORM_MAX_POINTS,
) -> dict | None:
    """Build a bounded public-safe waveform strip from real loudness windows.

    This intentionally does not synthesize a waveform from sparse audio peaks.
    The public report may render it only when normalized loudness windows were
    collected from actual audio samples.
    """

    if max_points <= 0:
        raise ValueError("max_points must be positive")

    prepared: list[dict[str, float]] = []
    for item in windows:
        if not isinstance(item, dict):
            continue
        start = _finite_float(item.get("start_sec"))
        end = _finite_float(item.get("end_sec"))
        value = _finite_float(item.get("normalized"))
        db = _finite_float(item.get("loudness_db"))
        if start is None or end is None or value is None or db is None:
            continue
        if start < 0 or end <= start:
            continue
        prepared.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "value": round(max(0.0, min(1.0, value)), 4),
            }
        )

    if not prepared:
        return None

    prepared.sort(key=lambda row: (row["start_sec"], row["end_sec"]))
    if len(prepared) > max_points:
        ratio = len(prepared) / max_points
        downsampled: list[dict[str, float]] = []
        for idx in range(max_points):
            start_idx = int(idx * ratio)
            end_idx = int((idx + 1) * ratio)
            bucket = prepared[start_idx:max(end_idx, start_idx + 1)]
            if not bucket:
                continue
            downsampled.append(
                {
                    "start_sec": bucket[0]["start_sec"],
                    "end_sec": bucket[-1]["end_sec"],
                    "value": round(max(row["value"] for row in bucket), 4),
                }
            )
        prepared = downsampled

    max_sec = max(row["end_sec"] for row in prepared)
    if duration_sec is not None and duration_sec > 0:
        max_sec = max(max_sec, float(duration_sec))

    return {
        "schema_version": "audio_waveform.v1",
        "source": "audio_loudness_windows",
        "sample_count": len(prepared),
        "max_points": max_points,
        "duration_sec": round(max_sec, 3),
        "channels": "mono_mixed",
        "unit": "relative_loudness_normalized_0_1",
        "privacy_class": "public_safe",
        "decision_rule": "waveform is timeline context only; it does not prove highlight importance",
        "samples": prepared,
    }


def _nearest_peak_context(
    start_sec: float,
    end_sec: float,
    peaks: Sequence[dict],
    context_sec: int,
) -> tuple[dict | None, float | None]:
    if not peaks:
        return None, None
    center = (start_sec + end_sec) / 2.0
    nearest_peak: dict | None = None
    nearest_delta: float | None = None
    for peak in peaks:
        peak_sec = _finite_float(peak.get("center_sec"))
        if peak_sec is None:
            continue
        delta = center - peak_sec
        if nearest_delta is None or abs(delta) < abs(nearest_delta):
            nearest_peak = peak
            nearest_delta = delta
    if nearest_delta is None or abs(nearest_delta) > context_sec:
        return None, nearest_delta
    return nearest_peak, nearest_delta


def _window_learning_rank(row: dict) -> tuple[float, float, float]:
    near_peak = 1.0 if row.get("near_audio_peak") else 0.0
    percentile = _finite_float(row.get("percentile")) or 0.0
    normalized = _finite_float(row.get("normalized")) or 0.0
    return near_peak, percentile, normalized


def _bound_learning_windows(rows: list[dict], max_windows: int) -> list[dict]:
    if len(rows) <= max_windows:
        return rows
    ratio = len(rows) / max_windows
    selected: list[dict] = []
    seen: set[tuple[float, float]] = set()
    for idx in range(max_windows):
        start_idx = int(idx * ratio)
        end_idx = int((idx + 1) * ratio)
        bucket = rows[start_idx:max(end_idx, start_idx + 1)]
        if not bucket:
            continue
        best = max(bucket, key=_window_learning_rank)
        key = (float(best["start_sec"]), float(best["end_sec"]))
        if key in seen:
            continue
        selected.append(best)
        seen.add(key)
    return sorted(selected, key=lambda item: (float(item["start_sec"]), float(item["end_sec"])))


def build_audio_learning_features(
    windows: Sequence[dict],
    peaks: Sequence[dict],
    *,
    duration_sec: float | None = None,
    window_sec: int = DEFAULT_WINDOW_SEC,
    max_windows: int = DEFAULT_LEARNING_MAX_WINDOWS,
    peak_context_sec: int = DEFAULT_LEARNING_PEAK_CONTEXT_SEC,
) -> dict | None:
    """Build bounded derived audio features for later highlight learning.

    This stores no raw audio, transcript text, chat text, or waveform PCM data.
    Rows are compact loudness/context features that can later be joined with
    manager edits or approved training rows.
    """

    if max_windows <= 0:
        raise ValueError("max_windows must be positive")
    if peak_context_sec < 0:
        raise ValueError("peak_context_sec must be non-negative")

    rows: list[dict[str, Any]] = []
    for item in windows:
        if not isinstance(item, dict):
            continue
        start = _finite_float(item.get("start_sec"))
        end = _finite_float(item.get("end_sec"))
        loudness = _finite_float(item.get("loudness_db"))
        normalized = _finite_float(item.get("normalized"))
        percentile = _finite_float(item.get("percentile"))
        z_score = _finite_float(item.get("z_score"))
        if start is None or end is None or loudness is None or normalized is None or percentile is None:
            continue
        if start < 0 or end <= start:
            continue
        peak, delta = _nearest_peak_context(start, end, peaks, peak_context_sec)
        row: dict[str, Any] = {
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "loudness_db": round(loudness, 3),
            "normalized": round(max(0.0, min(1.0, normalized)), 4),
            "percentile": round(max(0.0, min(1.0, percentile)), 4),
            "near_audio_peak": peak is not None,
        }
        if z_score is not None:
            row["z_score"] = round(z_score, 4)
        peak_db = _sample_peak_db(item)
        if peak_db is not None:
            row["peak_db"] = round(peak_db, 3)
        if peak is not None:
            row["nearest_peak_delta_sec"] = round(delta or 0.0, 3)
            labels = [str(label) for label in (peak.get("labels") or []) if str(label).strip()]
            if labels:
                row["peak_labels"] = labels[:6]
            primary = str(peak.get("primary_label") or "").strip()
            if primary:
                row["peak_primary_label"] = primary
        rows.append(row)

    if not rows:
        return None

    rows.sort(key=lambda item: (float(item["start_sec"]), float(item["end_sec"])))
    bounded = _bound_learning_windows(rows, max_windows=max_windows)
    max_sec = max(float(row["end_sec"]) for row in rows)
    if duration_sec is not None and duration_sec > 0:
        max_sec = max(max_sec, float(duration_sec))

    return {
        "schema_version": "audio_learning_features.v1",
        "source": "audio_loudness_windows",
        "privacy_class": "local_private_derived",
        "raw_media_included": False,
        "raw_transcript_included": False,
        "raw_chat_included": False,
        "manager_truth": False,
        "training_ready": False,
        "duration_sec": round(max_sec, 3),
        "window_sec": window_sec,
        "window_count_before_cap": len(rows),
        "window_count": len(bounded),
        "window_cap_applied": len(bounded) < len(rows),
        "max_windows": max_windows,
        "peak_context_sec": peak_context_sec,
        "peak_count": len([p for p in peaks if isinstance(p, dict)]),
        "selection_rule": "all_windows_or_bucketed_peak_preserving",
        "decision_rule": (
            "derived audio features are weak supervision candidates only; "
            "join with manager edits or approved labels before training"
        ),
        "windows": bounded,
    }


def parse_srt_speech_ranges(srt_text: str) -> list[tuple[float, float]]:
    """Use SRT caption timing as a lightweight speech-overlap proxy."""

    ranges = []
    for match in SRT_TIME_RE.finditer(srt_text):
        start = _srt_time_to_sec(match, "1")
        end = _srt_time_to_sec(match, "2")
        if end > start:
            ranges.append((start, end))
    return ranges


def _srt_time_to_sec(match: re.Match[str], suffix: str) -> float:
    return (
        int(match.group(f"h{suffix}")) * 3600
        + int(match.group(f"m{suffix}")) * 60
        + int(match.group(f"s{suffix}"))
        + int(match.group(f"ms{suffix}")) / 1000.0
    )


def build_audio_metadata(
    samples: Iterable[object],
    *,
    duration_sec: float | None = None,
    chat_peaks: Sequence[dict] | None = None,
    speech_ranges: Sequence[tuple[float, float]] | None = None,
    window_sec: int = DEFAULT_WINDOW_SEC,
    min_percentile: float = DEFAULT_MIN_PERCENTILE,
    min_gap_sec: int = DEFAULT_PEAK_GAP_SEC,
    chat_overlap_sec: int = DEFAULT_CHAT_OVERLAP_SEC,
    max_peaks: int = DEFAULT_MAX_PEAKS,
    include_windows: bool = True,
    include_lufs_diagnostics: bool = False,
    include_waveform: bool = True,
    waveform_max_points: int = DEFAULT_WAVEFORM_MAX_POINTS,
    include_learning_features: bool = True,
    learning_max_windows: int = DEFAULT_LEARNING_MAX_WINDOWS,
    learning_peak_context_sec: int = DEFAULT_LEARNING_PEAK_CONTEXT_SEC,
) -> dict:
    """Build compact audio evidence metadata for main VOD reports."""

    sample_list = list(samples)
    windows = build_loudness_windows(sample_list, window_sec=window_sec, duration_sec=duration_sec)
    peaks = extract_reaction_peaks(
        windows,
        chat_peaks=chat_peaks,
        speech_ranges=speech_ranges,
        min_percentile=min_percentile,
        min_gap_sec=min_gap_sec,
        chat_overlap_sec=chat_overlap_sec,
        max_peaks=max_peaks,
    )
    metadata = {
        "audio_reaction_peaks": peaks,
        "audio_loudness_per_min": build_audio_loudness_per_min(windows, duration_sec=duration_sec),
        "audio_feature_source": {
            "kind": "relative_loudness_main_pipeline",
            "enabled_by_default": True,
            "window_sec": window_sec,
            "min_percentile": min_percentile,
            "min_gap_sec": min_gap_sec,
            "chat_overlap_sec": chat_overlap_sec,
            "max_peaks": max_peaks,
            "waveform_max_points": waveform_max_points,
            "normalization": "per_vod_relative_percentile_and_z_score",
            "decision_rule": "audio is supporting evidence; audio-only peaks do not prove highlight importance",
        },
    }
    if include_waveform:
        waveform = build_audio_waveform(
            windows,
            duration_sec=duration_sec,
            max_points=waveform_max_points,
        )
        if waveform:
            metadata["audio_waveform"] = waveform
    if include_windows:
        metadata["audio_loudness_windows"] = windows
    if include_lufs_diagnostics:
        metadata["audio_lufs_diagnostics"] = build_lufs_diagnostics(
            sample_list,
            duration_sec=duration_sec,
            window_sec=window_sec,
            min_percentile=min_percentile,
            min_gap_sec=min_gap_sec,
            max_peaks=max_peaks,
        )
    if include_learning_features:
        learning = build_audio_learning_features(
            windows,
            peaks,
            duration_sec=duration_sec,
            window_sec=window_sec,
            max_windows=learning_max_windows,
            peak_context_sec=learning_peak_context_sec,
        )
        if learning:
            metadata["audio_learning_features"] = learning
    return metadata


def _parse_metadata_file(path: Path) -> dict[int, dict[str, float]]:
    frames: dict[int, dict[str, float]] = {}
    current_sec: int | None = None
    if not path.is_file():
        return frames
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if "pts_time:" in line:
            match = re.search(r"pts_time:(-?\d+(?:\.\d+)?)", line)
            current_sec = int(float(match.group(1))) if match else None
            if current_sec is not None:
                frames.setdefault(current_sec, {})
            continue
        if current_sec is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        number = _finite_float(value)
        if number is None:
            continue
        if key.endswith(".Overall.RMS_level"):
            frames.setdefault(current_sec, {})["rms_db"] = number
        elif key.endswith(".Overall.RMS_peak"):
            frames.setdefault(current_sec, {})["rms_peak_db"] = number
        elif key.endswith(".Overall.Peak_level"):
            frames.setdefault(current_sec, {})["peak_db"] = number
        elif key.endswith(".r128.M"):
            frames.setdefault(current_sec, {})["lufs"] = number
    return frames


def extract_audio_samples_ffmpeg(
    source: str | Path,
    *,
    out_dir: str | Path,
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: int = 900,
    include_lufs: bool = False,
) -> list[dict]:
    """Extract one-second astats samples, optionally with ebur128 LUFS."""

    source_path = Path(source).resolve()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    astats_file = out / "audio_astats.txt"
    lufs_file = out / "audio_lufs.txt"
    cmd = [ffmpeg_bin, "-hide_banner", "-nostdin", "-i", str(source_path), "-vn"]
    if include_lufs:
        filter_complex = (
            "[0:a]aresample=48000,asplit=2[a1][a2];"
            "[a1]asetnsamples=n=48000:p=0,astats=metadata=1:reset=1,"
            "ametadata=mode=print:file=audio_astats.txt:direct=1[aout1];"
            "[a2]ebur128=metadata=1,ametadata=mode=print:file=audio_lufs.txt:direct=1[aout2]"
        )
        cmd.extend(["-filter_complex", filter_complex, "-map", "[aout1]", "-map", "[aout2]"])
    else:
        filter_audio = (
            "aresample=48000,asetnsamples=n=48000:p=0,astats=metadata=1:reset=1,"
            "ametadata=mode=print:file=audio_astats.txt:direct=1"
        )
        cmd.extend(["-filter:a", filter_audio])
    cmd.extend(["-f", "null", "-"])
    proc = subprocess.run(
        cmd,
        cwd=out,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.splitlines()[-8:])
        raise RuntimeError(f"ffmpeg audio feature extraction failed rc={proc.returncode}: {tail}")

    astats = _parse_metadata_file(astats_file)
    lufs = _parse_metadata_file(lufs_file)
    seconds = sorted(set(astats) | set(lufs))
    samples = []
    for sec in seconds:
        row: dict[str, Any] = {"sec": sec}
        row.update(astats.get(sec, {}))
        row.update(lufs.get(sec, {}))
        if row.get("rms_db") is not None:
            row["loudness_db"] = row["rms_db"]
        elif row.get("lufs") is not None:
            row["loudness_db"] = row["lufs"]
        samples.append(row)
    return samples


def extract_audio_samples_wav(
    sources: str | Path | Sequence[str | Path],
    *,
    offsets_sec: Sequence[float] | None = None,
) -> list[dict]:
    """Extract one-second RMS/peak samples from PCM WAV files without ffmpeg.

    Whisper's extraction path writes 16 kHz mono PCM WAV sidecars. Streaming
    those files one second at a time reuses that work and avoids a second
    full-media decode pass for main-report audio reaction peaks.
    """
    if isinstance(sources, (str, Path)):
        paths = [Path(sources)]
    else:
        paths = [Path(source) for source in sources]
    if offsets_sec is None:
        offsets = [0.0] * len(paths)
    else:
        offsets = [float(offset) for offset in offsets_sec]
    if len(offsets) != len(paths):
        raise ValueError("offsets_sec length must match sources length")

    samples: list[dict[str, Any]] = []
    for path, offset in zip(paths, offsets):
        if not path.is_file():
            raise FileNotFoundError(f"WAV file not found: {path}")
        with wave.open(str(path), "rb") as wf:
            sample_width = wf.getsampwidth()
            channels = wf.getnchannels()
            sample_rate = wf.getframerate()
            if sample_width <= 0 or sample_rate <= 0:
                continue
            if sample_width != 2:
                raise ValueError(f"unsupported WAV sample width for fast path: {sample_width}")
            max_pcm = float((1 << (8 * sample_width - 1)) - 1)
            local_sec = 0
            while True:
                chunk = wf.readframes(sample_rate)
                if not chunk:
                    break
                audio = array("h")
                audio.frombytes(chunk)
                if sys.byteorder != "little":
                    audio.byteswap()
                if channels > 1:
                    usable = len(audio) - (len(audio) % channels)
                    values = [
                        sum(audio[index : index + channels]) / channels
                        for index in range(0, usable, channels)
                    ]
                else:
                    values = audio
                if not values:
                    continue
                rms = math.sqrt(sum(float(value) ** 2 for value in values) / len(values))
                peak = max(abs(float(value)) for value in values)
                rms_db = round(_pcm_level_to_db(rms, max_pcm), 3)
                peak_db = round(_pcm_level_to_db(peak, max_pcm), 3)
                samples.append(
                    {
                        "sec": round(offset + local_sec, 3),
                        "rms_db": rms_db,
                        "peak_db": peak_db,
                        "loudness_db": rms_db,
                    }
                )
                local_sec += 1
    return samples


def build_audio_metadata_from_wav_files(
    wav_paths: str | Path | Sequence[str | Path],
    *,
    offsets_sec: Sequence[float] | None = None,
    duration_sec: float | None = None,
    chat_peaks: Sequence[dict] | None = None,
    srt_path: str | Path | None = None,
    window_sec: int = DEFAULT_WINDOW_SEC,
    min_percentile: float = DEFAULT_MIN_PERCENTILE,
    min_gap_sec: int = DEFAULT_PEAK_GAP_SEC,
    chat_overlap_sec: int = DEFAULT_CHAT_OVERLAP_SEC,
    max_peaks: int = DEFAULT_MAX_PEAKS,
    include_windows: bool = False,
) -> dict:
    """Build main-report audio metadata from existing Whisper WAV caches."""

    if isinstance(wav_paths, (str, Path)):
        paths = [Path(wav_paths)]
    else:
        paths = [Path(path) for path in wav_paths]
    speech_ranges: list[tuple[float, float]] = []
    if srt_path and Path(srt_path).is_file():
        speech_ranges = parse_srt_speech_ranges(Path(srt_path).read_text(encoding="utf-8", errors="replace"))
    samples = extract_audio_samples_wav(paths, offsets_sec=offsets_sec)
    metadata = build_audio_metadata(
        samples,
        duration_sec=duration_sec,
        chat_peaks=chat_peaks,
        speech_ranges=speech_ranges,
        window_sec=window_sec,
        min_percentile=min_percentile,
        min_gap_sec=min_gap_sec,
        chat_overlap_sec=chat_overlap_sec,
        max_peaks=max_peaks,
        include_windows=include_windows,
    )
    metadata["audio_feature_source"]["sample_count"] = len(samples)
    metadata["audio_feature_source"]["source_kind"] = "whisper_wav_cache"
    metadata["audio_feature_source"]["sample_extraction"] = "wav_pcm_python"
    metadata["audio_feature_source"]["source_count"] = len(paths)
    metadata["audio_feature_source"]["include_lufs"] = False
    return metadata


def build_audio_metadata_from_media(
    media_path: str | Path,
    *,
    duration_sec: float | None = None,
    chat_peaks: Sequence[dict] | None = None,
    srt_path: str | Path | None = None,
    window_sec: int = DEFAULT_WINDOW_SEC,
    min_percentile: float = DEFAULT_MIN_PERCENTILE,
    min_gap_sec: int = DEFAULT_PEAK_GAP_SEC,
    chat_overlap_sec: int = DEFAULT_CHAT_OVERLAP_SEC,
    max_peaks: int = DEFAULT_MAX_PEAKS,
    timeout_sec: int = 900,
    include_lufs: bool = False,
    include_lufs_diagnostics: bool = False,
    work_dir: str | Path | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> dict:
    """Extract and build main-report audio metadata from local media."""

    media = Path(media_path)
    if not media.is_file():
        raise FileNotFoundError(f"media file not found: {media}")
    media = media.resolve()
    speech_ranges: list[tuple[float, float]] = []
    if srt_path and Path(srt_path).is_file():
        speech_ranges = parse_srt_speech_ranges(Path(srt_path).read_text(encoding="utf-8", errors="replace"))
    out_dir = Path(work_dir) if work_dir else media.parent / ".audio_features"
    if media.suffix.lower() == ".wav" and not include_lufs:
        samples = extract_audio_samples_wav(media)
        source_kind = "wav_pcm_fast_path"
        sample_extraction = "wav_pcm_python"
    else:
        samples = extract_audio_samples_ffmpeg(
            media,
            out_dir=out_dir,
            ffmpeg_bin=ffmpeg_bin,
            timeout_sec=timeout_sec,
            include_lufs=include_lufs,
        )
        source_kind = "ffmpeg_media_astats"
        sample_extraction = "ffmpeg_astats"
    metadata = build_audio_metadata(
        samples,
        duration_sec=duration_sec,
        chat_peaks=chat_peaks,
        speech_ranges=speech_ranges,
        window_sec=window_sec,
        min_percentile=min_percentile,
        min_gap_sec=min_gap_sec,
        chat_overlap_sec=chat_overlap_sec,
        max_peaks=max_peaks,
        include_lufs_diagnostics=include_lufs_diagnostics,
    )
    metadata["audio_feature_source"]["sample_count"] = len(samples)
    metadata["audio_feature_source"]["include_lufs"] = bool(include_lufs)
    metadata["audio_feature_source"]["source_kind"] = source_kind
    metadata["audio_feature_source"]["sample_extraction"] = sample_extraction
    return metadata
