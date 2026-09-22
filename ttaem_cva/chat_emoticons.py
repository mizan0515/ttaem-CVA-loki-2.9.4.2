"""Internal CHZZK chat emoticon feature extraction.

Emoticon codes are opaque reaction tokens. This module does not assign meanings
such as laugh, hype, sad, or surprise, and it does not change summary/report
ranking by itself.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable


EMOTICON_TOKEN_RE = re.compile(r"\{:([A-Za-z0-9_][A-Za-z0-9_-]{0,80}):\}")
CHAT_TEXT_FIELDS = ("msg", "message", "text", "content")
STRUCTURED_EMOTE_FIELDS = ("emoticons", "emotes", "emojis", "stickers")

CHAT_MODE_NORMAL = "normal_chat"
CHAT_MODE_REACTION_HEAVY = "reaction_heavy_chat"
CHAT_MODE_EMOTE_ONLY = "emote_only_likely"
CHAT_MODE_SPAM_OR_COPYPASTA = "spam_or_copypasta_likely"
CHAT_MODE_LOW_ACTIVITY = "low_chat_activity"


@dataclass(frozen=True)
class ChatEmoticonFeatures:
    sec: int
    text_without_emotes: str
    emote_codes: tuple[str, ...]
    emote_count: int
    unique_emote_count: int
    is_emote_only: bool
    has_text: bool
    extraction_source: str
    malformed_candidate_marker: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EmoticonWindowFeatures:
    start_sec: int
    end_sec: int
    message_count: int
    emote_message_count: int
    emote_count: int
    unique_emote_count: int
    emote_only_message_count: int
    mixed_text_emote_message_count: int
    emote_only_ratio: float
    mixed_text_emote_ratio: float
    repeated_code_concentration: float
    top_code_counts: tuple[tuple[str, int], ...]
    emote_count_z: float
    extraction_sources: dict[str, int]
    privacy_class: str = "internal_aggregate_no_raw_chat"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EmoticonWindowMode:
    start_sec: int
    end_sec: int
    chat_mode: str
    reaction_strength: float
    semantic_confidence: float
    semantic_downweight: float
    reasons: tuple[str, ...]
    privacy_class: str = "internal_aggregate_no_raw_chat"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _chat_text(row: dict[str, Any]) -> str:
    for key in CHAT_TEXT_FIELDS:
        value = row.get(key)
        if isinstance(value, str):
            return value
    return ""


def _chat_sec(row: dict[str, Any]) -> int:
    for key in ("sec", "time", "offset_sec"):
        try:
            value = row.get(key)
            if value is not None:
                return max(0, int(float(value)))
        except (TypeError, ValueError):
            pass
    try:
        return max(0, int(float(row.get("ms", 0)) / 1000))
    except (TypeError, ValueError):
        return 0


def _codes_from_structured_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if _valid_code(value) else []
    if isinstance(value, dict):
        for key in ("code", "id", "name", "emoteCode", "emojiCode", "stickerCode"):
            code = value.get(key)
            if isinstance(code, str) and _valid_code(code):
                return [code]
        return []
    if isinstance(value, list):
        codes: list[str] = []
        for item in value:
            codes.extend(_codes_from_structured_value(item))
        return codes
    return []


def _structured_emote_codes(row: dict[str, Any]) -> list[str]:
    for key in STRUCTURED_EMOTE_FIELDS:
        codes = _codes_from_structured_value(row.get(key))
        if codes:
            return codes
    for nested_key in ("extras", "metadata", "meta"):
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            for key in STRUCTURED_EMOTE_FIELDS:
                codes = _codes_from_structured_value(nested.get(key))
                if codes:
                    return codes
    return []


def _valid_code(code: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,80}", code))


def _compact_text_without_emotes(text: str) -> str:
    stripped = EMOTICON_TOKEN_RE.sub("", text)
    return re.sub(r"\s+", " ", stripped).strip()


def extract_chat_emoticon_features(row: dict[str, Any]) -> ChatEmoticonFeatures:
    """Extract opaque emoticon token features from one chat row.

    Structured source metadata wins when present. Regex extraction is fallback
    for stored local logs that only expose message text.
    """
    text = _chat_text(row)
    structured_codes = _structured_emote_codes(row)
    if structured_codes:
        codes = structured_codes
        source = "structured"
    else:
        codes = EMOTICON_TOKEN_RE.findall(text)
        source = "regex" if codes else "none"
    text_without_emotes = _compact_text_without_emotes(text)
    has_text = bool(re.sub(r"\s+", "", text_without_emotes))
    without_valid_tokens = EMOTICON_TOKEN_RE.sub("", text)
    malformed_marker = "{:" in without_valid_tokens or ":}" in without_valid_tokens
    return ChatEmoticonFeatures(
        sec=_chat_sec(row),
        text_without_emotes=text_without_emotes,
        emote_codes=tuple(codes),
        emote_count=len(codes),
        unique_emote_count=len(set(codes)),
        is_emote_only=bool(codes) and not has_text,
        has_text=has_text,
        extraction_source=source,
        malformed_candidate_marker=malformed_marker,
    )


def build_chat_emoticon_features(rows: Iterable[dict[str, Any]]) -> list[ChatEmoticonFeatures]:
    return [extract_chat_emoticon_features(row) for row in rows if isinstance(row, dict)]


def aggregate_emoticon_windows(
    rows: Iterable[dict[str, Any]],
    *,
    window_sec: int = 30,
    include_empty_windows: bool = False,
) -> list[EmoticonWindowFeatures]:
    window = max(1, int(window_sec))
    features = build_chat_emoticon_features(rows)
    if not features:
        return []
    buckets: dict[int, list[ChatEmoticonFeatures]] = {}
    for feature in features:
        start = (feature.sec // window) * window
        buckets.setdefault(start, []).append(feature)
    if include_empty_windows:
        for start in range(0, max(buckets) + window, window):
            buckets.setdefault(start, [])

    base_rows: list[dict[str, Any]] = []
    for start in sorted(buckets):
        bucket_features = buckets[start]
        code_counts: Counter[str] = Counter()
        source_counts: Counter[str] = Counter()
        message_count = len(bucket_features)
        emote_message_count = 0
        emote_only_count = 0
        mixed_count = 0
        max_code_count = 0
        total_emotes = 0
        for feature in bucket_features:
            source_counts[feature.extraction_source] += 1
            if feature.emote_count:
                emote_message_count += 1
                total_emotes += feature.emote_count
                code_counts.update(feature.emote_codes)
                max_code_count = max(max_code_count, max(Counter(feature.emote_codes).values()))
            if feature.is_emote_only:
                emote_only_count += 1
            elif feature.emote_count and feature.has_text:
                mixed_count += 1
        base_rows.append(
            {
                "start_sec": start,
                "end_sec": start + window,
                "message_count": message_count,
                "emote_message_count": emote_message_count,
                "emote_count": total_emotes,
                "unique_emote_count": len(code_counts),
                "emote_only_message_count": emote_only_count,
                "mixed_text_emote_message_count": mixed_count,
                "emote_only_ratio": _ratio(emote_only_count, message_count),
                "mixed_text_emote_ratio": _ratio(mixed_count, message_count),
                "repeated_code_concentration": _ratio(max_code_count, total_emotes),
                "top_code_counts": tuple(code_counts.most_common(5)),
                "extraction_sources": dict(source_counts),
            }
        )

    counts = [float(row["emote_count"]) for row in base_rows]
    mean = sum(counts) / len(counts)
    variance = sum((count - mean) ** 2 for count in counts) / len(counts)
    stddev = math.sqrt(variance)
    return [
        EmoticonWindowFeatures(
            start_sec=int(row["start_sec"]),
            end_sec=int(row["end_sec"]),
            message_count=int(row["message_count"]),
            emote_message_count=int(row["emote_message_count"]),
            emote_count=int(row["emote_count"]),
            unique_emote_count=int(row["unique_emote_count"]),
            emote_only_message_count=int(row["emote_only_message_count"]),
            mixed_text_emote_message_count=int(row["mixed_text_emote_message_count"]),
            emote_only_ratio=float(row["emote_only_ratio"]),
            mixed_text_emote_ratio=float(row["mixed_text_emote_ratio"]),
            repeated_code_concentration=float(row["repeated_code_concentration"]),
            top_code_counts=row["top_code_counts"],
            emote_count_z=round((float(row["emote_count"]) - mean) / stddev, 6) if stddev else 0.0,
            extraction_sources=row["extraction_sources"],
        )
        for row in base_rows
    ]


def classify_emoticon_window(
    window: EmoticonWindowFeatures,
    *,
    low_activity_messages: int = 3,
    reaction_heavy_emote_ratio: float = 0.35,
    emote_only_ratio: float = 0.80,
    spam_concentration: float = 0.85,
    spike_z: float = 1.0,
) -> EmoticonWindowMode:
    """Classify a chat window without assigning semantic meaning to codes."""
    emote_message_ratio = _ratio(window.emote_message_count, window.message_count)
    reaction_strength = _reaction_strength(window, emote_message_ratio)
    reasons: list[str] = []

    if window.message_count < low_activity_messages and window.emote_count == 0:
        chat_mode = CHAT_MODE_LOW_ACTIVITY
        semantic_confidence = 0.0
        reasons.append("low_chat_activity")
    elif (
        window.repeated_code_concentration >= spam_concentration
        and window.emote_only_ratio >= reaction_heavy_emote_ratio
        and window.unique_emote_count <= 2
        and window.emote_count >= max(6, low_activity_messages)
    ):
        chat_mode = CHAT_MODE_SPAM_OR_COPYPASTA
        semantic_confidence = 0.1
        reasons.extend(["high_repeated_code_concentration", "few_unique_codes"])
    elif window.emote_only_ratio >= emote_only_ratio and window.emote_message_count > 0:
        chat_mode = CHAT_MODE_EMOTE_ONLY
        semantic_confidence = 0.2
        reasons.append("emote_only_ratio_high")
    elif emote_message_ratio >= reaction_heavy_emote_ratio or window.emote_count_z >= spike_z:
        chat_mode = CHAT_MODE_REACTION_HEAVY
        semantic_confidence = 0.55
        reasons.append("reaction_heavy_signal")
    else:
        chat_mode = CHAT_MODE_NORMAL
        semantic_confidence = 1.0 if window.message_count else 0.0
        reasons.append("normal_chat")

    return EmoticonWindowMode(
        start_sec=window.start_sec,
        end_sec=window.end_sec,
        chat_mode=chat_mode,
        reaction_strength=reaction_strength,
        semantic_confidence=semantic_confidence,
        semantic_downweight=round(1.0 - semantic_confidence, 6),
        reasons=tuple(reasons),
    )


def classify_emoticon_windows(
    windows: Iterable[EmoticonWindowFeatures],
    **kwargs: Any,
) -> list[EmoticonWindowMode]:
    return [classify_emoticon_window(window, **kwargs) for window in windows]


def build_emoticon_chat_modes(
    rows: Iterable[dict[str, Any]],
    *,
    window_sec: int = 30,
    include_empty_windows: bool = False,
    **kwargs: Any,
) -> list[EmoticonWindowMode]:
    windows = aggregate_emoticon_windows(rows, window_sec=window_sec, include_empty_windows=include_empty_windows)
    return classify_emoticon_windows(windows, **kwargs)


def _reaction_strength(window: EmoticonWindowFeatures, emote_message_ratio: float) -> float:
    density_score = min(1.0, window.emote_count / 12.0)
    spike_score = min(1.0, max(0.0, (window.emote_count_z + 1.0) / 3.0)) if window.emote_count else 0.0
    return round(max(emote_message_ratio, density_score, spike_score), 6)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)
