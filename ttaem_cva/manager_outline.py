"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

import logging

import re

from typing import Any

from .outline.schema import CAUSAL_PROPOSAL_HEADING, CAUSAL_WINDOW_RE, POINT_RE as _POINT_RE, POINT_REVIEW_HEADING, RANGE_RE as _RANGE_RE, hms_to_seconds as _hms_to_seconds, point_review_heading as _point_review_heading, seconds_to_hms as _seconds_to_hms

from .outline.check import MAX_FINAL_CAUSAL_WINDOW_SEC, _section, validate_manager_outline

from .broadcast_map_prompt_budget import _thin_full_span

from .outline.payload import _navigation_timetable_label, _number


def build_cached_support_package(
    *, chunks: list[dict], chats: list[dict], audio_metadata: dict | None,
    timestamp_comments: list[dict], viewer_clips: list[dict], visual_rows: list[dict],
    duration_sec: int, max_rows: int = 12,
    timeline_comment_context: dict[str, Any] | None = None,
    broadcast_map_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project already-present caches into one raw-free final-merge package."""
    from .chat_analyzer import build_time_series

    chat_rows: list[dict[str, Any]] = []
    if chats:
        buckets = build_time_series(chats, 10).get("buckets") or {}
        ranked = sorted(
            buckets.items(),
            key=lambda item: (-int(item[1].get("count", 0)), float(item[0])),
        )[:max_rows]
        chat_rows = [
            {
                "start_sec": float(start), "end_sec": min(float(duration_sec), float(start) + 10),
                "count": int(row.get("count", 0)),
                "laughter_count": int(row.get("laughter_count", 0)),
                "surprise_count": int(row.get("surprise_count", 0)),
            }
            for start, row in ranked
        ]
    dialogue_rows = [
        {
            "start": str(row.get("start_hhmmss") or ""),
            "end": str(row.get("end_hhmmss") or ""),
            "char_count": len(str(row.get("text") or "")),
        }
        for row in ([chunks[round(i * (len(chunks) - 1) / (max_rows - 1))] for i in range(max_rows)] if len(chunks) > max_rows and max_rows > 1 else chunks)
        if str(row.get("text") or "").strip()
    ]
    peak_source = (audio_metadata or {}).get("audio_reaction_peaks") or (audio_metadata or {}).get("reaction_peaks") or (audio_metadata or {}).get("peaks") or []
    audio_rows = []
    for row in _thin_full_span(peak_source, max_rows) if isinstance(peak_source, list) else []:
        if not isinstance(row, dict): continue
        sec = _number(row.get("center_sec", row.get("sec", row.get("time_sec", row.get("start_sec")))))
        if sec is not None and sec <= duration_sec:
            audio_rows.append({"time_sec": sec, "score": _number(row.get("score"))})
    waveform = (audio_metadata or {}).get("audio_waveform") or {}
    waveform_samples = waveform.get("samples") if isinstance(waveform, dict) else []
    waveform_rows = [{key: row.get(key) for key in ("start_sec", "end_sec", "value") if _number(row.get(key)) is not None} for row in (_thin_full_span(waveform_samples, max_rows) if isinstance(waveform_samples, list) else []) if isinstance(row, dict)]
    from .timeline_comment_anchors import build_timeline_comment_context

    comment_context = (
        timeline_comment_context
        if isinstance(timeline_comment_context, dict)
        else build_timeline_comment_context(
            timestamp_comments,
            source_status="legacy_timestamp_only",
            source_kind="legacy",
            duration_sec=duration_sec,
        )
    )
    context_rows: list[dict[str, Any]] = []
    outline_chunks = [row for row in chunks if isinstance(row, dict)]
    for raw in comment_context.get("anchors") or []:
        if not isinstance(raw, dict):
            continue
        sec = _number(raw.get("time_sec"))
        label = str(raw.get("label") or "").strip()
        lane = str(raw.get("lane") or "chapter_timetable")
        if sec is None or sec > duration_sec or not label:
            continue
        containing = [
            chunk
            for chunk in outline_chunks
            if int(chunk.get("start_ms") or 0)
            <= int(sec * 1000)
            <= int(chunk.get("end_ms") or 0)
        ]
        candidates = containing or outline_chunks
        assigned = (
            min(
                candidates,
                key=lambda chunk: (
                    abs(
                        (
                            int(chunk.get("start_ms") or 0)
                            + int(chunk.get("end_ms") or 0)
                        )
                        / 2
                        - int(sec * 1000)
                    ),
                    int(chunk.get("index") or 0),
                ),
            )
            if candidates
            else None
        )
        context_rows.append({
            "time_sec": int(sec),
            "timecode": str(raw.get("timecode") or _seconds_to_hms(int(sec))),
            "label": label,
            "lane": (
                lane
                if lane in {"chapter_timetable", "highlight_hint"}
                else "chapter_timetable"
            ),
            "chunk_index": int(assigned.get("index") or 0) if assigned else 0,
        })
    comment_context = {
        key: value
        for key, value in comment_context.items()
        if key != "anchors"
    }
    comment_context["anchors"] = context_rows
    comment_context["used_anchor_count"] = len(context_rows)
    comment_context["chapter_anchor_count"] = sum(
        row["lane"] == "chapter_timetable" for row in context_rows
    )
    comment_context["highlight_anchor_count"] = sum(
        row["lane"] == "highlight_hint" for row in context_rows
    )
    clip_rows = []
    for row in _thin_full_span(viewer_clips, max_rows):
        if not isinstance(row, dict): continue
        sec = _number(row.get("start_sec", row.get("offset_sec")))
        if sec is not None and sec <= duration_sec:
            clip_rows.append({"start_sec": sec, "like_count": int(_number(row.get("like_count")) or 0), "play_count": int(_number(row.get("play_count")) or 0), "read_count": int(_number(row.get("read_count")) or 0)})
    visual = []
    for row in _thin_full_span(visual_rows, max_rows):
        if not isinstance(row, dict) or str(row.get("status") or "") != "ok" or row.get("public_safe") is not True or row.get("privacy_class") != "public_safe": continue
        visual.append({key: row.get(key) for key in (
            "event_id", "signal_type", "start_sec", "end_sec", "sanitized_text_class",
            "marker", "status",
        ) if row.get(key) not in (None, "")})
    signals = {
        "chat_reaction_density": chat_rows, "dialogue_density": dialogue_rows,
        "audio_points": audio_rows, "audio_waveform": waveform_rows,
        "viewer_clips": clip_rows, "visual_ocr": visual,
    }
    chat_parts = []
    if chat_rows: chat_parts.append("cached 10-second chat aggregates")
    if dialogue_rows: chat_parts.append("chunk dialogue density")
    usage = {
        "chat reaction/density": ("used: cached 10-second chat aggregates" if chat_rows else "not used: cached 10-second chat aggregates unavailable") + ("; used: chunk dialogue density" if dialogue_rows else "; not used: chunk dialogue density unavailable"),
        "audio": ("used: " + " and ".join(part for part in ("cached reaction peaks as Point support" if audio_rows else "", "bounded cached waveform samples" if waveform_rows else "") if part)) if (audio_rows or waveform_rows) else "not used: cached audio signal unavailable",
        "public timestamp comment": (
            f"used: {len(context_rows)} private timetable anchors and selected source blocks distributed across chronological chunks; source remains private"
            if context_rows
            else "not used: private rewritten timetable anchors unavailable"
        ),
        "viewer clip": "used: cached timestamp/engagement only; contents not claimed" if clip_rows else "not used: cached viewer clips unavailable",
        "visual/OCR": "used: existing raw-free sidecar candidates" if visual else "not used: cached raw-free visual/OCR sidecar unavailable",
    }
    package = {
        "usage": usage,
        "signals": signals,
        "timeline_comment_context": comment_context,
    }
    if broadcast_map_evidence is not None:
        package["broadcast_map_evidence"] = dict(broadcast_map_evidence)
    return package

from .outline.parse import _normalize_terminal_outline_ends, parse_manager_outline

from .outline.canonicalize import _canonicalize_model_outline


def _preserve_corroborated_timetable_name_pairs(

    text: str,
    support_package: dict[str, Any] | None,
) -> str:
    """Keep raw-free timetable name pairs when the model already chose the same unit.

    This does not create a D2 or move a boundary.  It only restores omitted nouns from a
    short rewritten label when an existing D2 overlaps that anchor and its title already
    contains another noun from the same label.  The model therefore remains the semantic
    owner, while compaction/brevity cannot turn ``러너 제이슨`` into anonymous ``제이슨``.
    """
    context = (support_package or {}).get("timeline_comment_context") or {}
    anchors = []
    for row in context.get("anchors") or []:
        if not isinstance(row, dict) or str(row.get("lane") or "") != "chapter_timetable":
            continue
        label = _navigation_timetable_label(row.get("label"))
        tokens = re.findall(r"[0-9A-Za-z가-힣]+", label)
        if not (2 <= len(tokens) <= 4) or len(label) > 40:
            continue
        sec = int(_number(row.get("time_sec")) or 0)
        anchors.append((sec, label, tokens))
    if not anchors or "[실제 목차]" not in text or "[Point]" not in text:
        return text

    start = text.index("[실제 목차]") + len("[실제 목차]")
    end = text.index("[Point]", start)
    rows = text[start:end].splitlines()
    for index, row in enumerate(rows):
        if not re.match(r"^\s*D2\s+", row):
            continue
        range_match = _RANGE_RE.search(row)
        if not range_match:
            continue
        d2_start, d2_end = (
            _hms_to_seconds(value.strip())
            for value in re.split(r"[-–~]", range_match.group(0), maxsplit=1)
        )
        title = row[range_match.end():].strip()
        candidates = []
        for sec, label, tokens in anchors:
            if not (d2_start - 90 <= sec < d2_end):
                continue
            present = [token for token in tokens if token in title]
            if not present or len(present) == len(tokens):
                continue
            candidates.append((-len(present), abs(sec - d2_start), label, tokens, present))
        if not candidates:
            continue
        _, _, label, tokens, present = min(candidates)
        lead, separator, tail = title.partition(" - ")
        if any(token in lead for token in present) and len(lead) <= 40:
            rightmost = max(
                ((lead.rfind(token) + len(token), token) for token in present),
                key=lambda item: item[0],
            )
            semantic_suffix = lead[rightmost[0]:]
            enriched = label + semantic_suffix + (separator + tail if separator else "")
        else:
            token = max(present, key=len)
            enriched = title.replace(token, label, 1)
        rows[index] = row[:range_match.end()] + " " + enriched
    rebuilt = "\n".join(rows)
    if rebuilt and not rebuilt.endswith("\n"):
        rebuilt += "\n"
    return text[:start] + rebuilt + text[end:]


def _drop_unowned_model_points(text: str) -> str:
    """Omit model Points that cannot belong to any accepted D2.

    This is a model-boundary recovery only. It never expands a range and it does not
    relax the validators used for manager edits or stored/public artifacts. Outlines
    without D2 rows retain their Points because D1-only structure is still valid.
    """
    toc_marker = "[실제 목차]"
    point_marker = "[Point]"
    review_heading = _point_review_heading(text)
    point_end_marker = (
        CAUSAL_PROPOSAL_HEADING
        if CAUSAL_PROPOSAL_HEADING in text
        else review_heading
        if review_heading
        else "[짧은 요약]"
    )
    if any(marker not in text for marker in (toc_marker, point_marker, point_end_marker)):
        return text

    toc_start = text.index(toc_marker) + len(toc_marker)
    toc_end = text.index(point_marker, toc_start)
    d2_ranges: list[tuple[int, int, int]] = []
    parent_index = -1
    for row in text[toc_start:toc_end].splitlines():
        level = re.match(r"^\s*(D[12])\s+", row)
        match = _RANGE_RE.search(row)
        if not level or not match:
            continue
        if level.group(1) == "D1":
            parent_index += 1
            continue
        if parent_index < 0:
            continue
        start_text, end_text = re.split(r"\s*[-–~]\s*", match.group(0))
        d2_ranges.append(
            (parent_index, _hms_to_seconds(start_text), _hms_to_seconds(end_text))
        )
    if not d2_ranges:
        return text

    point_start = text.index(point_marker, toc_end) + len(point_marker)
    point_end = text.index(point_end_marker, point_start)
    point_rows = [
        row.strip()
        for row in text[point_start:point_end].splitlines()
        if row.strip()
    ]
    if point_rows == ["없음"] or not point_rows:
        return text
    if any(not _POINT_RE.fullmatch(row) for row in point_rows):
        return text

    def has_d2_owner(sec: int) -> bool:
        if any(start <= sec < end for _parent, start, end in d2_ranges):
            return True
        for parent, _start, end in d2_ranges:
            if sec != end:
                continue
            if not any(
                sibling_parent == parent and sibling_start == sec
                for sibling_parent, sibling_start, _sibling_end in d2_ranges
            ):
                return True
        return False

    kept = [
        row
        for row in point_rows
        if has_d2_owner(_hms_to_seconds(row.split(maxsplit=1)[0]))
    ]
    if kept == point_rows:
        return text
    replacement = "\n" + "\n".join(kept or ["없음"]) + "\n"
    return text[:point_start] + replacement + text[point_end:]


def _drop_unowned_model_causal_proposals(text: str) -> str:
    """Keep proposals bound to an accepted Point context start.

    New proposals bind ``setup`` to Point.  ``event`` remains a read-compatible
    fallback for artifacts produced before the context-start contract.
    """

    if CAUSAL_PROPOSAL_HEADING not in text:
        return text
    point_start = text.index("[Point]") + len("[Point]")
    point_end = text.index(CAUSAL_PROPOSAL_HEADING, point_start)
    accepted_times = {
        row.split(maxsplit=1)[0]
        for row in text[point_start:point_end].splitlines()
        if _POINT_RE.fullmatch(row.strip())
    }
    proposal_start = point_end + len(CAUSAL_PROPOSAL_HEADING)
    proposal_end_marker = _point_review_heading(text) or "[짧은 요약]"
    proposal_end = text.index(proposal_end_marker, proposal_start)
    kept = []
    for row in text[proposal_start:proposal_end].splitlines():
        stripped = row.strip()
        match = CAUSAL_WINDOW_RE.fullmatch(stripped)
        if match and any(
            match.group(phase) in accepted_times for phase in ("setup", "event")
        ):
            kept.append(stripped)
    replacement = "\n" + "\n".join(kept or ["없음"]) + "\n"
    return text[:proposal_start] + replacement + text[proposal_end:]


def sanitize_final_causal_proposals(text: str) -> str:
    """Drop malformed optional final proposals before strict artifact validation."""

    if CAUSAL_PROPOSAL_HEADING not in text:
        return text
    point_start = text.index("[Point]") + len("[Point]")
    point_end = text.index(CAUSAL_PROPOSAL_HEADING, point_start)
    accepted_times = {
        row.split(maxsplit=1)[0]
        for row in text[point_start:point_end].splitlines()
        if _POINT_RE.fullmatch(row.strip())
    }
    proposal_start = point_end + len(CAUSAL_PROPOSAL_HEADING)
    proposal_end_marker = _point_review_heading(text) or "[짧은 요약]"
    proposal_end = text.index(proposal_end_marker, proposal_start)
    kept: list[str] = []
    seen_points: set[str] = set()
    dropped = {"shape": 0, "clock": 0, "span": 0, "event": 0, "duplicate": 0}
    for row in text[proposal_start:proposal_end].splitlines():
        stripped = row.strip()
        match = CAUSAL_WINDOW_RE.fullmatch(stripped)
        if not match:
            if stripped and stripped != "없음":
                dropped["shape"] += 1
            continue
        phases = [_hms_to_seconds(match.group(name)) for name in (
            "setup", "event", "reaction", "payoff"
        )]
        point_time = next(
            (
                match.group(phase)
                for phase in ("setup", "event")
                if match.group(phase) in accepted_times
            ),
            None,
        )
        if phases != sorted(phases):
            dropped["clock"] += 1
            continue
        if phases[-1] - phases[0] > MAX_FINAL_CAUSAL_WINDOW_SEC:
            dropped["span"] += 1
            continue
        if point_time is None:
            dropped["event"] += 1
            continue
        if point_time in seen_points:
            dropped["duplicate"] += 1
            continue
        kept.append(stripped)
        seen_points.add(point_time)
    replacement = "\n" + "\n".join(kept or ["없음"]) + "\n"
    if any(dropped.values()):
        logging.getLogger(__name__).info(
            "BroadcastMap final causal proposals: kept=%s dropped=%s",
            len(kept),
            dropped,
        )
    return text[:proposal_start] + replacement + text[proposal_end:]


def validate_model_outline(text: str, *, duration_sec: int | None = None) -> None:
    """Validate the sole BroadcastMap response before code adds clip review windows."""
    if _point_review_heading(text):
        raise ValueError("model must not create Point review windows")
    if CAUSAL_PROPOSAL_HEADING not in text:
        raise ValueError("model must provide the final Point-aligned causal evidence section")
    validate_manager_outline(
        sanitize_final_causal_proposals(_canonicalize_model_outline(text)),
        duration_sec=duration_sec,
    )


def add_point_review_windows(
    text: str,
    *,
    duration_sec: int,
    max_candidates: int = 5,
) -> str:
    """Create review-only navigation windows from accepted Points."""
    if _point_review_heading(text):
        raise ValueError("Point review window section must be product-generated")
    point_body = _section(
        text,
        "[Point]",
        CAUSAL_PROPOSAL_HEADING if CAUSAL_PROPOSAL_HEADING in text else "[짧은 요약]",
    ).strip()
    if point_body == "없음":
        marker = "[짧은 요약]"
        result = text.replace(
            marker,
            POINT_REVIEW_HEADING + "\n없음\n" + marker,
            1,
        )
        validate_manager_outline(result)
        return result
    points = []
    for order, row in enumerate(line.strip() for line in point_body.splitlines() if line.strip()):
        if not _POINT_RE.fullmatch(row):
            continue
        timestamp, label = row.split(maxsplit=1)
        sec = _hms_to_seconds(timestamp)
        points.append((order, sec, label))
    selected = points[:max_candidates]
    d1_ranges = []
    for row in _section(text, "[실제 목차]", "[Point]").splitlines():
        if not row.startswith("D1 "):
            continue
        match = _RANGE_RE.search(row)
        if match:
            start, end = re.split(r"\s*[-–~]\s*", match.group(0))
            d1_ranges.append((_hms_to_seconds(start), _hms_to_seconds(end)))
    candidate_rows = []
    for _order, sec, label in selected:
        start_sec, end_sec = max(0, sec - 20), min(max(0, duration_sec), sec + 40)
        for d1_start, d1_end in d1_ranges:
            if d1_start <= sec <= d1_end:
                start_sec, end_sec = max(start_sec, d1_start), min(end_sec, d1_end)
                break
        if end_sec > start_sec:
            candidate_rows.append(
                f"{_seconds_to_hms(start_sec)}-{_seconds_to_hms(end_sec)} {label} 전후 확인"
            )
    if not candidate_rows:
        return text
    marker = "[짧은 요약]"
    result = text.replace(marker, POINT_REVIEW_HEADING + "\n" + "\n".join(candidate_rows) + "\n" + marker, 1)
    validate_manager_outline(result)
    return result


def finalize_manager_outline(
    text: str,
    *,
    duration_sec: int,
    call_count: int,
    replay_chat_used: bool,
    content_info_cards: list[dict[str, Any]] | None = None,
    model: str = "provider-selected",
    support_package: dict[str, Any] | None = None,
) -> str:
    """Replace execution-truth sections and derive editor windows without another call."""
    text = _canonicalize_model_outline(text)
    text = sanitize_final_causal_proposals(text)
    text = _preserve_corroborated_timetable_name_pairs(text, support_package)
    text = _drop_unowned_model_points(text)
    text = _drop_unowned_model_causal_proposals(text)
    text = _normalize_terminal_outline_ends(text, duration_sec=duration_sec)
    validate_model_outline(text, duration_sec=duration_sec)
    usage = {
        "STT": "used - raw timestamped speech",
        "replay chat": "used - raw timestamped chat sentences as semantic evidence" if replay_chat_used else "not used - replay chat missing",
        "chat reaction/density": "not used - no aggregate support package supplied",
        "audio": "not used - no compatible cached audio support supplied",
        "public timestamp comment": "not used - no cached timestamp candidates supplied",
        "viewer clip": "not used - no cached clip timestamps supplied and contents not watched",
        "visual/OCR": "not used - no usable cached sidecar candidates supplied",
    }
    usage.update((support_package or {}).get("usage") or {})
    used_body = "\n".join(f"{label}: {status}" for label, status in usage.items())
    cards = content_info_cards or []
    content_body = "없음" if not cards else "\n".join(
        f"{str(card.get('canonical_name') or 'unnamed')} "
        f"(card: {str(card.get('card_id') or 'unknown')}; "
        f"matched: {str(card.get('matched_term') or 'unknown')}) - "
        f"{str(card.get('reason') or 'approved exact naming hint supplied')}"
        for card in cards
    )
    prefix = text[: text.index("[실제 사용한 정보]")]
    result = (
        prefix.rstrip()
        + "\n[실제 사용한 정보]\n"
        + used_body
        + "\n[사용한 콘텐츠 정보]\n"
        + content_body
        + "\n[모델/호출]\n"
        + f"model: {model}; call count: {call_count}; retry: 0; fallback: 0"
    )
    result = add_point_review_windows(result, duration_sec=duration_sec)
    validate_manager_outline(result, duration_sec=duration_sec)
    return result


def resolve_single_flight_route(config=None):
    return 'codex_session', 'codex-session'


def single_flight_config(config=None, *, provider='', model=''):
    result = dict(config or {})
    result['llm_default_provider_order'] = ['codex_session']
    return result


def summary_stage_route_configs(config=None, *, model):
    return [single_flight_config(config)]


def is_summary_route_fallback_failure(exc):
    return False


def assert_single_flight_meta(
    meta: dict[str, Any],
    *,
    expected_provider: str,
    expected_model: str,
) -> None:
    selected = str(meta.get("selected_provider") or meta.get("provider") or "")
    if not selected:
        raise ValueError("single-flight metadata missing selected_provider")
    attempts = int(meta.get("provider_attempt_count") or 0)
    calls = int(meta.get("call_count") or 0)
    requested_order = list(meta.get("requested_provider_order") or [])
    actual_model = str(meta.get("actual_model") or meta.get("model") or "")
    if selected != expected_provider:
        raise ValueError(f"unexpected provider: {selected}")
    if attempts != 1 or calls != 1:
        raise ValueError(
            f"retry/fallback forbidden: provider_attempt_count={attempts}, call_count={calls}"
        )
    if requested_order != [expected_provider]:
        raise ValueError(f"provider order drift: {requested_order}")
    if expected_model and actual_model != expected_model:
        raise ValueError(f"model drift: {actual_model}")
    if (
        meta.get("fallback_reason")
        or meta.get("llm_bridge_shape_retry")
        or int(meta.get("model_fallback_count") or 0) != 0
    ):
        raise ValueError("retry/fallback metadata is non-zero")
