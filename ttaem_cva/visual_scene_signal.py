"""Raw-free visual scene signal sidecar helpers.

The sidecar is an editor-supporting signal for screen transitions, OCR classes,
and game/menu/loading markers. It deliberately strips raw frame and OCR text so
the admin workbench can show candidates without committing private media.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse


SCHEMA_VERSION = "visual_scene_signal.v1"
ALLOWED_SIGNAL_TYPES = {
    "scene_boundary",
    "visual_similarity_change",
    "ocr_text_class",
    "game_state_marker",
    "menu_loading_marker",
}
ALLOWED_TEXT_CLASSES = {
    "",
    "generic_text",
    "korean_text",
    "loading_text",
    "menu_text",
    "numeric_text",
}
_TEXT_HASH_RE = re.compile(r"^sha1:[0-9a-f]{16,40}$")
_SAFE_GENERATED_EVENT_ID_RE = re.compile(r"^visual_scene(?:_ext)?_[0-9a-f]{12}$")
ALLOWED_MARKERS = {
    "game_start",
    "game_end",
    "match_start",
    "match_end",
    "round_start",
    "round_end",
    "loading",
    "menu",
    "settings_menu",
    "lobby",
    "scoreboard",
}
ALLOWED_EVIDENCE_REF_TYPES = {
    "frame",
    "frame_hash",
    "metadata",
    "signal",
    "visual_scene_ref",
}
ALLOWED_STATUSES = {
    "ok",
    "time_unmapped",
    "outside_vod_duration",
    "sidecar_unreadable",
    "sidecar_diagnostic_redacted",
}
ALLOWED_RISK_FLAGS = {
    "visual_scene_candidate_not_truth",
    "visual_scene_malformed_sidecar_rows",
    "visual_scene_outside_vod_duration",
    "visual_scene_time_unmapped",
    "visual_scene_unavailable_visible",
}


def load_visual_scene_signals(path: str | Path) -> list[dict[str, Any]]:
    sidecar_path = Path(path)
    try:
        if sidecar_path.suffix.lower() == ".jsonl":
            rows = []
            malformed_count = 0
            for line_no, line in enumerate(sidecar_path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    malformed_count += 1
                    continue
                if isinstance(row, dict):
                    rows.append(row)
            if malformed_count:
                rows.append(
                    {
                        "event_id": _stable_event_id("sidecar_malformed_rows", str(sidecar_path), malformed_count),
                        "signal_type": "visual_similarity_change",
                        "status": "sidecar_unreadable",
                        "label": f"화면 신호 일부 행을 읽을 수 없음 ({malformed_count})",
                        "source": str(sidecar_path),
                        "risk_flags": ["visual_scene_malformed_sidecar_rows"],
                    }
                )
        else:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
            rows = _rows_from_payload(data)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [
            {
                "schema_version": SCHEMA_VERSION,
                "event_id": _stable_event_id("sidecar_unreadable", str(sidecar_path), None),
                "signal_type": "sidecar_unreadable",
                "status": "sidecar_unreadable",
                "label": "화면 신호 파일을 읽을 수 없음",
                "source": _safe_ref_text(sidecar_path),
                "privacy_class": "public_safe",
                "public_safe": True,
                "raw_frame_included": False,
                "raw_ocr_text_included": False,
                "risk_flags": ["visual_scene_unavailable_visible"],
            }
        ]
    return normalize_visual_scene_signals(rows)


def normalize_visual_scene_signals(
    rows: Iterable[dict[str, Any]] | dict[str, Any] | None,
    *,
    video_no: str = "",
    duration_sec: float | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(_rows_from_payload(rows)):
        if not isinstance(row, dict):
            continue
        signal_type = _signal_type(row)
        start = _number_or_none(_first_present(row, "start_sec", "sec", "time_sec", "timestamp_sec", "elapsed_sec"))
        end = _number_or_none(_first_present(row, "end_sec", "duration_end_sec"))
        if end is None and start is not None:
            end = start
        if start is not None and end is not None and end < start:
            start, end = end, start

        raw_risk_flags = row.get("risk_flags")
        if isinstance(raw_risk_flags, list):
            risk_flag_values = raw_risk_flags
        elif raw_risk_flags:
            risk_flag_values = [raw_risk_flags]
        else:
            risk_flag_values = []
        risk_flags = {_safe_risk_flag(flag) for flag in risk_flag_values if flag}
        risk_flags.discard("")
        status = _safe_status(row.get("status"))
        if start is None and status == "ok":
            status = "time_unmapped"
            risk_flags.add("visual_scene_time_unmapped")
        if (
            start is not None
            and (
                start < 0
                or (end is not None and end < 0)
                or (duration_sec is not None and duration_sec > 0 and (start > duration_sec or (end is not None and end > duration_sec)))
            )
        ):
            status = "outside_vod_duration"
            risk_flags.add("visual_scene_outside_vod_duration")

        raw_ocr_text = _first_present(row, "ocr_text", "raw_ocr_text", "detected_text", "text")
        sanitized_class = _safe_text_class(
            _first_present(row, "sanitized_text_class", "text_class", "ocr_text_class"),
            raw_ocr_text=raw_ocr_text,
        )
        text_hash = _safe_text_hash(_first_present(row, "text_hash", "ocr_text_hash"))
        if not text_hash and raw_ocr_text:
            text_hash = _hash_text(raw_ocr_text)

        marker = _safe_marker(_first_present(row, "game_state", "state_marker", "marker", "screen_marker"))
        label = _safe_label(signal_type, sanitized_class=sanitized_class, marker=marker)
        confidence = _confidence(row.get("confidence"))
        if status != "ok":
            confidence = "low"

        normalized.append(
            {
                "schema_version": SCHEMA_VERSION,
                "event_id": _safe_event_id(row.get("event_id"), fallback=_stable_event_id(signal_type, video_no, start, index)),
                "video_no": str(row.get("video_no") or video_no or ""),
                "start_sec": start,
                "end_sec": end,
                "signal_type": signal_type,
                "status": status,
                "label": label,
                "confidence": confidence,
                "source": _safe_ref_text(row.get("source")),
                "privacy_class": "public_safe",
                "public_safe": True,
                "raw_frame_included": False,
                "raw_ocr_text_included": False,
                "sanitized_text_class": sanitized_class,
                "text_hash": text_hash,
                "marker": marker,
                "evidence_refs": _safe_evidence_refs(row.get("evidence_refs")),
                "risk_flags": sorted({*risk_flags, "visual_scene_candidate_not_truth"}),
            }
        )
    return normalized


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("signals", "items", "rows", "events"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return [payload]
    return []


def _signal_type(row: dict[str, Any]) -> str:
    value = str(_first_present(row, "signal_type", "type", "kind") or "").strip()
    return value if value in ALLOWED_SIGNAL_TYPES else "visual_similarity_change"


def _safe_label(signal_type: str, *, sanitized_class: str, marker: str) -> str:
    if signal_type == "scene_boundary":
        return "화면 전환 후보"
    if signal_type == "visual_similarity_change":
        return "화면 변화 후보"
    if signal_type == "ocr_text_class":
        return f"OCR 글자 유형 후보: {sanitized_class or 'unknown'}"
    if signal_type == "game_state_marker":
        return f"게임 상태 후보: {marker or sanitized_class or 'unknown'}"
    if signal_type == "menu_loading_marker":
        return f"메뉴/로딩 화면 후보: {marker or sanitized_class or 'unknown'}"
    return "화면 신호 후보"


def _safe_marker(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "").strip().lower())
    text = text[:48].strip("_")
    return text if text in ALLOWED_MARKERS else ""


def _classify_ocr_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if any(token in lower for token in ("loading", "load", "로딩")):
        return "loading_text"
    if any(token in lower for token in ("menu", "메뉴", "설정")):
        return "menu_text"
    if re.search(r"\d", text):
        return "numeric_text"
    if re.search(r"[가-힣]", text):
        return "korean_text"
    return "generic_text"


def _safe_text_class(value: Any, *, raw_ocr_text: Any) -> str:
    if value is None or value == "":
        return _classify_ocr_text(raw_ocr_text)
    text = str(value or "").strip().lower()
    if text in ALLOWED_TEXT_CLASSES:
        return text
    return _classify_ocr_text(raw_ocr_text) if raw_ocr_text else ""


def _safe_text_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if _TEXT_HASH_RE.match(text) else ""


def _hash_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    if not text:
        return ""
    return "sha1:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _stable_event_id(signal_type: str, video_no: str, start: Any, index: int | None = None) -> str:
    payload = "|".join([SCHEMA_VERSION, signal_type, str(video_no or ""), str(start or ""), str(index or "")])
    return "visual_scene_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _safe_event_id(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    if _SAFE_GENERATED_EVENT_ID_RE.match(text):
        return text
    return "visual_scene_ext_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _safe_evidence_refs(value: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return refs
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        refs.append(
            {
                "type": _safe_evidence_ref_type(item.get("type")),
                "ref": _safe_evidence_ref_value(item.get("ref") or item.get("id") or ""),
                "raw_frame_included": False,
                "raw_ocr_text_included": False,
            }
        )
    return refs


def _safe_evidence_ref_type(value: Any) -> str:
    text = re.sub(r"[^a-z0-9_.:-]+", "_", str(value or "").strip().lower())
    text = text[:48].strip("_")
    return text if text in ALLOWED_EVIDENCE_REF_TYPES else "visual_scene_ref"


def _safe_evidence_ref_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower().startswith("local_ref:"):
        return "ref_hash:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    safe_ref = _safe_ref_text(text)
    if safe_ref.startswith("local_ref:"):
        return safe_ref
    if _TEXT_HASH_RE.match(text.lower()) or _SAFE_GENERATED_EVENT_ID_RE.match(text):
        return text
    return "ref_hash:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _confidence(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"low", "medium", "high"}:
            return text
    number = _number_or_none(value)
    if number is None:
        return "medium"
    if number >= 0.75:
        return "high"
    if number >= 0.4:
        return "medium"
    return "low"


def _safe_ref_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _TEXT_HASH_RE.match(text.lower()) or _SAFE_GENERATED_EVENT_ID_RE.match(text):
        return text
    parsed = urlparse(text)
    if parsed.scheme.lower() == "file":
        local_path = unquote(parsed.path or parsed.netloc or text)
        return "ref_hash:" + hashlib.sha1(local_path.encode("utf-8")).hexdigest()[:16]
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\") or text.startswith("/"):
        return "ref_hash:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return "source_hash:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _safe_status(value: Any) -> str:
    text = re.sub(r"[^a-z0-9_.:-]+", "_", str(value or "ok").strip().lower())
    text = text[:48].strip("_")
    return text if text in ALLOWED_STATUSES else "sidecar_diagnostic_redacted"


def _safe_risk_flag(value: Any) -> str:
    text = re.sub(r"[^a-z0-9_.:-]+", "_", str(value or "").strip().lower())
    text = text[:64].strip("_")
    return text if text in ALLOWED_RISK_FLAGS else "visual_scene_external_risk_redacted"


def _path_basename_any_platform(value: str) -> str:
    text = str(value or "").rstrip("\\/")
    name = re.split(r"[\\/]+", text)[-1]
    return name or "local-path"


def _clip_ref(value: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None and row[key] != "":
            return row[key]
    return None


def _number_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, 3)


__all__ = [
    "ALLOWED_SIGNAL_TYPES",
    "ALLOWED_TEXT_CLASSES",
    "SCHEMA_VERSION",
    "load_visual_scene_signals",
    "normalize_visual_scene_signals",
]
