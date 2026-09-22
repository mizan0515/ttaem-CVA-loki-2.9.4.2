"""Selected baseline private review lanes. Never passed to public projection."""
from __future__ import annotations
import hashlib
import re
from typing import Any
from .chat_emoticons import EMOTICON_TOKEN_RE, CHAT_MODE_REACTION_HEAVY, aggregate_emoticon_windows, classify_emoticon_windows
SCHEMA_VERSION = "signal_timeline_workbench.v1"
_LANE_LABELS = {"chat_laughter":"웃음/놀람 채팅", "chat_surprise":"채팅 급증", "emoticon_reaction":"감정표현 반응", "audio_peak":"소리 변화", "subtitle_density":"자막 맥락", "comment_replay":"댓글 시간표", "viewer_clip":"시청자 클립", "existing_segments":"요약 장면", "highlight":"Highlight"}
_ROLE_LABELS = {"candidate":"검토 후보", "supporting":"보조 신호", "confirmed":"요약에 반영됨"}
_REACTION_SIGNAL_ALIASES = {"reaction_surprise", "reaction_question", "question", "qna", "question_cluster"}
def _canonical_event_lane(lane):
    if lane not in _LANE_LABELS: raise ValueError("Unsupported private review lane")
    return lane

def _safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default

def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str) and ":" in value:
            parts = [float(part.strip()) for part in value.split(":")]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return float(value)
    except (TypeError, ValueError):
        return default

def _clip_text(value: Any, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."

def _audio_peak_editor_label(labels: list[Any]) -> str:
    normalized = {str(label).lower() for label in labels if label}
    if "chat_audio_overlap" in normalized:
        return "소리와 채팅이 함께 튄 구간"
    if "voice_reaction_candidate" in normalized:
        return "목소리 리액션이 커진 구간"
    if "clipping_or_limiter_diagnostic" in normalized:
        return "음량이 갑자기 거칠어진 구간"
    if "audio_energy_peak" in normalized:
        return "소리가 크게 튄 구간"
    return "소리 변화가 큰 구간"

def _sec_to_tc(sec: Any) -> str:
    n = _safe_int(sec, 0) or 0
    h = n // 3600
    m = (n % 3600) // 60
    s = n % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def _event_id(lane: str, start_sec: Any, end_sec: Any, label: str, segment_id: str = "") -> str:
    payload = "|".join(
        [
            SCHEMA_VERSION,
            lane,
            "" if start_sec is None else str(start_sec),
            "" if end_sec is None else str(end_sec),
            segment_id,
            label,
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"stw_{lane}_{digest}"

def _event(
    lane: str,
    *,
    start_sec: Any,
    end_sec: Any = None,
    label: str,
    role: str = "candidate",
    confidence: str = "medium",
    segment_id: str = "",
    evidence: list[dict[str, Any]] | None = None,
    risk_flags: list[str] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    lane = _canonical_event_lane(lane)
    start = _safe_float(start_sec)
    end = _safe_float(end_sec)
    if start is not None and end is not None and end < start:
        start, end = end, start
    return {
        "event_id": _event_id(lane, start, end, label, segment_id),
        "lane": lane,
        "lane_label": _LANE_LABELS.get(lane, lane),
        "start_sec": start,
        "end_sec": end,
        "timecode": _sec_to_tc(start) if start is not None else "",
        "label": _clip_text(label, 120),
        "role": role,
        "role_label": _ROLE_LABELS.get(role, role),
        "confidence": confidence,
        "segment_id": segment_id,
        "source_refs": source_refs or [],
        "evidence": evidence or [],
        "risk_flags": risk_flags or [],
    }

def _stable_source_identity_key(prefix: str, *parts: Any) -> str:
    payload = "|".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"

def _viewer_clip_source_identity(src: dict[str, Any], row: dict[str, Any], start: Any) -> dict[str, Any]:
    clip_uid = str(src.get("clip_uid") or src.get("uid") or "").strip()
    video_no = str(src.get("video_no") or src.get("source_video_no") or row.get("video_no") or "").strip()
    offset = src.get("offset_sec", start)
    title = src.get("title") or row.get("title") or ""
    if clip_uid:
        return {
            "source_identity_key": _stable_source_identity_key("viewer_clip", video_no, clip_uid),
            "source_identity_kind": "public_viewer_clip_uid",
            "source_identity_confidence": "high",
            "engagement_weighted": True,
        }
    return {
        "source_identity_key": _stable_source_identity_key("viewer_clip_anchor", video_no, offset, title),
        "source_identity_kind": "deterministic_viewer_clip_anchor",
        "source_identity_confidence": "low",
        "engagement_weighted": False,
    }

def _metadata_events(meta: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    ledger = meta.get("segment_ledger") if isinstance(meta, dict) else []
    if not isinstance(ledger, list):
        ledger = []
    has_legacy_comment_chapter = any(
        isinstance(row, dict) and str(row.get("kind") or "") == "chapter"
        for row in ledger
    )

    for row in ledger:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        if kind == "summary":
            continue
        start = row.get("start_sec")
        end = row.get("end_sec")
        title = row.get("title") or row.get("timecode") or kind or "segment"
        sources = row.get("sources") if isinstance(row.get("sources"), list) else []
        evidence = [
            {
                "type": "segment",
                "label": kind or "segment",
                "text": _clip_text(row.get("summary") or row.get("reason") or row.get("timecode")),
            }
        ]
        for src in sources[:5]:
            if isinstance(src, dict):
                evidence.append(
                    {
                        "type": str(src.get("source_type") or "source"),
                        "label": str(src.get("section") or src.get("role") or ""),
                        "text": _clip_text(src.get("text") or src.get("title") or src.get("timecode") or src),
                    }
                )
        segment_lane = {
            "timeline": "existing_segments",
            "highlight": "highlight",
        }.get(kind)
        if segment_lane:
            events.append(
                _event(
                    segment_lane,
                    start_sec=start,
                    end_sec=end,
                    label=title,
                    role="confirmed",
                    confidence="high",
                    segment_id=str(row.get("segment_id") or ""),
                    evidence=evidence,
                    risk_flags=[str(v) for v in (row.get("risk_flags") or [])],
                    source_refs=[{"type": "metadata.segment_ledger", "kind": kind}],
                )
            )
        if kind == "chapter":
            comment_evidence = [
                {
                    "type": "comment_replay",
                    "label": "치지직 댓글 타임라인",
                    "text": _clip_text(row.get("title") or row.get("timecode")),
                }
            ]
            comment_evidence.extend(
                {
                    "type": str(src.get("source_type") or "comment_replay"),
                    "label": str(src.get("section") or src.get("role") or "원문"),
                    "text": _clip_text(src.get("text") or src.get("title") or src.get("timecode") or src),
                }
                for src in sources
                if isinstance(src, dict)
            )
            events.append(
                _event(
                    "comment_replay",
                    start_sec=start,
                    end_sec=end,
                    label=title,
                    role="supporting",
                    confidence="medium",
                    segment_id=str(row.get("segment_id") or ""),
                    evidence=comment_evidence,
                    risk_flags=["comment_chapter_is_navigation_not_fact"],
                    source_refs=[{"type": "metadata.segment_ledger", "kind": "chapter"}],
                )
            )
        for src in sources:
            if not isinstance(src, dict) or src.get("source_type") != "viewer_clip_anchor":
                continue
            offset = src.get("offset_sec", start)
            identity = _viewer_clip_source_identity(src, row, start)
            event = _event(
                "viewer_clip",
                start_sec=offset,
                label=src.get("title") or title or "viewer clip",
                role="supporting",
                confidence="medium",
                segment_id=str(row.get("segment_id") or ""),
                evidence=[
                    {
                        "type": "viewer_clip",
                        "label": str(src.get("clip_uid") or "viewer_clip_anchor"),
                        "text": _clip_text(src.get("title") or src),
                    }
                ],
                risk_flags=[
                    "viewer_interest_signal_not_ground_truth",
                    *([] if identity["engagement_weighted"] else ["viewer_clip_anchor_not_engagement_weighted"]),
                ],
                source_refs=[
                    {
                        "type": "metadata.segment_ledger.sources",
                        "source_type": "viewer_clip_anchor",
                        "source_identity_key": identity["source_identity_key"],
                        "source_identity_kind": identity["source_identity_kind"],
                        "source_identity_confidence": identity["source_identity_confidence"],
                        "engagement_weighted": identity["engagement_weighted"],
                    }
                ],
            )
            event.update(identity)
            events.append(event)

    comment_context = meta.get("timeline_comment_context") if isinstance(meta, dict) else None
    if not has_legacy_comment_chapter and isinstance(comment_context, dict):
        context_hash = str(comment_context.get("anchor_sha256") or "")
        for index, row in enumerate(comment_context.get("anchors") or []):
            if not isinstance(row, dict):
                continue
            sec = _safe_float(row.get("time_sec"))
            label = str(row.get("label") or "").strip()
            lane = str(row.get("lane") or "chapter_timetable")
            if sec is None or not label:
                continue
            events.append(
                _event(
                    "comment_replay",
                    start_sec=sec,
                    label=label,
                    role="supporting",
                    confidence="medium" if lane == "chapter_timetable" else "low",
                    segment_id=f"comment-context-{index}",
                    evidence=[{
                        "type": "comment_replay",
                        "label": "정제된 댓글 시간표 후보",
                        "text": "자막·채팅과 함께 확인해야 하는 탐색 후보",
                    }],
                    risk_flags=[
                        "private_rewritten_timetable_anchor",
                        "comment_anchor_requires_stt_or_chat_corroboration",
                    ],
                    source_refs=[{
                        "type": "metadata.timeline_comment_context",
                        "schema_version": str(
                            comment_context.get("schema_version") or ""
                        ),
                        "anchor_sha256": context_hash,
                        "lane": lane,
                    }],
                )
            )

    for peak in meta.get("audio_reaction_peaks") or []:
        if not isinstance(peak, dict):
            continue
        center = peak.get("center_sec", peak.get("sec"))
        labels = peak.get("labels") if isinstance(peak.get("labels"), list) else []
        events.append(
            _event(
                "audio_peak",
                start_sec=center,
                label=_audio_peak_editor_label(labels),
                role="supporting",
                confidence="medium" if labels else "low",
                evidence=[
                    {
                        "type": "audio_peak",
                        "label": "보조 신호",
                        "text": _clip_text(peak.get("caution") or peak.get("decision_rule") or labels),
                    }
                ],
                risk_flags=["audio_peak_supporting_signal"],
                source_refs=[{"type": "metadata.audio_reaction_peaks"}],
            )
        )

    return events

def _chat_message_sec(item: Any) -> float | None:
    if not isinstance(item, dict):
        return None
    for key in ("sec", "time_sec", "offset_sec"):
        value = _safe_float(item.get(key))
        if value is not None:
            return value
    ms = _safe_float(item.get("ms"))
    if ms is not None:
        return ms / 1000.0
    return None

def _chat_message_text(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("msg", "message", "text", "content"):
        if item.get(key):
            text = EMOTICON_TOKEN_RE.sub("", str(item.get(key) or ""))
            return _clip_text(text, 80)
    return ""

def _chat_samples(chats: list[dict[str, Any]], center_sec: Any, radius_sec: int = 20) -> list[str]:
    center = _safe_float(center_sec)
    if center is None:
        return []
    samples: list[tuple[float, str]] = []
    for item in chats:
        sec = _chat_message_sec(item)
        text = _chat_message_text(item)
        if sec is None or not text:
            continue
        delta = abs(sec - center)
        if delta <= radius_sec:
            samples.append((delta, text))
    samples.sort(key=lambda row: (row[0], row[1]))
    return [text for _, text in samples[:3]]

def _chat_events(meta: dict[str, Any], chats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in meta.get("highlights") or []:
        if not isinstance(row, dict):
            continue
        sec = row.get("sec")
        labels = {str(v).lower() for v in (row.get("source_signals") or row.get("labels") or [])}
        sample_texts = _chat_samples(chats, sec)
        sample_blob = " / ".join(sample_texts)
        reaction_families = {str(v).lower() for v in (row.get("reaction_families") or [])}
        has_reaction_signal = bool(
            labels & _REACTION_SIGNAL_ALIASES
            or reaction_families & {"surprise", "question"}
            or any(label.startswith("reaction_") for label in labels)
        )
        if (
            row.get("laughter_burst")
            or any("laugh" in label or "laughter" in label for label in labels)
            or has_reaction_signal
        ):
            lane = "chat_laughter"
        else:
            lane = "chat_surprise"
        events.append(
            _event(
                lane,
                start_sec=sec,
                label=sample_blob or f"chat peak count={row.get('count', '')}",
                role="supporting",
                confidence="medium",
                evidence=[
                    {
                        "type": "chat",
                        "label": "채팅 반응 묶음",
                        "text": sample_blob,
                    }
                ],
                risk_flags=["chat_peak_supporting_signal"],
                source_refs=[{"type": "metadata.highlights"}],
            )
        )
    return events

def _chat_context_index(chats: list[dict[str, Any]], *, bucket_sec: int = 30, max_buckets: int = 2400) -> list[dict[str, Any]]:
    buckets: dict[int, dict[str, Any]] = {}
    for item in chats:
        sec = _chat_message_sec(item)
        text = _chat_message_text(item)
        if sec is None:
            continue
        bucket_start = int(sec // bucket_sec) * bucket_sec
        bucket = buckets.setdefault(
            bucket_start,
            {
                "start_sec": bucket_start,
                "end_sec": bucket_start + bucket_sec,
                "count": 0,
                "laughter_count": 0,
                "surprise_count": 0,
                "samples": [],
            },
        )
        bucket["count"] += 1
        lowered = text.lower()
        if "ㅋ" in text or "웃" in text or "개웃" in lowered:
            bucket["laughter_count"] += 1
        if any(token in text for token in ("뭐야", "헐", "와", "미친", "대박", "?")):
            bucket["surprise_count"] += 1
        if text and len(bucket["samples"]) < 4:
            bucket["samples"].append({"sec": round(sec, 3), "text": text})
    rows = sorted(buckets.values(), key=lambda row: row["start_sec"])
    return rows[:max_buckets]

def _chat_bucket_reaction_events(
    chat_buckets: list[dict[str, Any]],
    *,
    window_sec: int = 1800,
    max_windows: int = 24,
) -> list[dict[str, Any]]:
    """Promote time-balanced chat markers for long VOD admin review.

    The main chat peak list is intentionally selective. The scene finder has a
    different job: keep later reviewable moments visible even when the strongest
    global peaks all happened early.
    """

    if not chat_buckets:
        return []
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in chat_buckets:
        start = _safe_float(row.get("start_sec"))
        if start is None:
            continue
        grouped.setdefault(int(start // window_sec), []).append(row)

    events: list[dict[str, Any]] = []
    for _, rows in sorted(grouped.items())[:max_windows]:
        selected_reaction_start: float | None = None
        best_reaction = max(
            rows,
            key=lambda row: (
                int(row.get("laughter_count") or 0),
                int(row.get("surprise_count") or 0),
                int(row.get("count") or 0),
                -(_safe_float(row.get("start_sec"), 0.0) or 0.0),
            ),
        )
        laughter_count = int(best_reaction.get("laughter_count") or 0)
        surprise_count = int(best_reaction.get("surprise_count") or 0)
        if laughter_count >= 3 or surprise_count >= 2:
            selected_reaction_start = _safe_float(best_reaction.get("start_sec"))
            samples = [
                str(sample.get("text") or "")
                for sample in (best_reaction.get("samples") or [])
                if isinstance(sample, dict) and sample.get("text")
            ]
            reaction_label = []
            if laughter_count:
                reaction_label.append(f"웃음 {laughter_count}개")
            if surprise_count:
                reaction_label.append(f"놀람/질문 {surprise_count}개")
            label = " / ".join(samples[:3]) or ", ".join(reaction_label) or "웃음/놀람 채팅"
            events.append(
                _event(
                    "chat_laughter",
                    start_sec=best_reaction.get("start_sec"),
                    end_sec=best_reaction.get("end_sec"),
                    label=label,
                    role="supporting",
                    confidence="medium",
                    evidence=[
                        {
                            "type": "chat_bucket",
                            "label": "시간대 대표 웃음/놀람",
                            "text": _clip_text(label),
                        }
                    ],
                    risk_flags=["chat_bucket_supporting_signal"],
                    source_refs=[{"type": "work.chat_bucket_context", "selection": "balanced_laughter_surprise"}],
                )
            )

        density_rows = rows
        if selected_reaction_start is not None:
            density_rows = [
                row for row in rows
                if _safe_float(row.get("start_sec")) != selected_reaction_start
            ]
        if not density_rows:
            continue
        best_density = max(
            density_rows,
            key=lambda row: (
                int(row.get("count") or 0),
                int(row.get("surprise_count") or 0),
                int(row.get("laughter_count") or 0),
                -(_safe_float(row.get("start_sec"), 0.0) or 0.0),
            ),
        )
        count = int(best_density.get("count") or 0)
        surprise_count = int(best_density.get("surprise_count") or 0)
        if count >= 20:
            samples = [
                str(sample.get("text") or "")
                for sample in (best_density.get("samples") or [])
                if isinstance(sample, dict) and sample.get("text")
            ]
            label = " / ".join(samples[:3]) or f"채팅 {count}개"
            events.append(
                _event(
                    "chat_surprise",
                    start_sec=best_density.get("start_sec"),
                    end_sec=best_density.get("end_sec"),
                    label=label,
                    role="supporting",
                    confidence="medium",
                    evidence=[
                        {
                            "type": "chat_bucket",
                            "label": "시간대 대표 채팅 급증",
                            "text": _clip_text(f"{count}개" + (f", 놀람/질문 {surprise_count}개" if surprise_count else "")),
                        }
                    ],
                    risk_flags=["chat_bucket_supporting_signal"],
                    source_refs=[{"type": "work.chat_bucket_context", "selection": "balanced_density"}],
                )
            )
    return events

def _emoticon_reaction_events(
    chats: list[dict[str, Any]],
    *,
    window_sec: int = 30,
    balance_window_sec: int = 1800,
    max_windows: int = 24,
) -> list[dict[str, Any]]:
    """Expose aggregate emoticon spikes without copying raw chat text.

    Emoticon codes are opaque reaction tokens. This lane is only a default
    editor-finder signal, not a semantic label such as laugh/hype/surprise.
    """

    if not chats:
        return []
    windows = aggregate_emoticon_windows(chats, window_sec=window_sec, include_empty_windows=False)
    modes = classify_emoticon_windows(windows)
    grouped: dict[int, list[tuple[Any, Any]]] = {}
    for window, mode in zip(windows, modes):
        if window.emote_count <= 0:
            continue
        if mode.chat_mode != CHAT_MODE_REACTION_HEAVY and mode.reaction_strength < 0.35:
            continue
        grouped.setdefault(int(window.start_sec // balance_window_sec), []).append((window, mode))

    events: list[dict[str, Any]] = []
    for _, rows in sorted(grouped.items())[:max_windows]:
        window, mode = max(
            rows,
            key=lambda item: (
                float(item[1].reaction_strength),
                int(item[0].emote_count),
                int(item[0].emote_message_count),
                -int(item[0].start_sec),
            ),
        )
        label = f"감정표현 반응 {window.emote_count}개 · 채팅 {window.message_count}개"
        events.append(
            _event(
                "emoticon_reaction",
                start_sec=window.start_sec,
                end_sec=window.end_sec,
                label=label,
                role="supporting",
                confidence="medium",
                evidence=[
                    {
                        "type": "emoticon_aggregate",
                        "label": "집계 감정표현 반응",
                        "text": _clip_text(
                            f"반응 강도 {mode.reaction_strength:.2f}, "
                            f"감정표현 메시지 {window.emote_message_count}개, "
                            f"서로 다른 감정표현 {window.unique_emote_count}종"
                        ),
                    }
                ],
                risk_flags=["emoticon_aggregate_supporting_signal"],
                source_refs=[
                    {
                        "type": "work.chat_emoticon_aggregate",
                        "privacy_class": window.privacy_class,
                    }
                ],
            )
        )
    return events

def _chat_coverage_summary(chats: list[dict[str, Any]], duration_sec: float) -> dict[str, Any]:
    seconds = [
        sec
        for sec in (_chat_message_sec(item) for item in chats)
        if sec is not None
    ]
    if not seconds:
        return {"status": "missing", "message_count": 0}
    latest = max(seconds)
    first = min(seconds)
    gap = max(0.0, float(duration_sec or 0.0) - latest)
    ratio = min(1.0, latest / duration_sec) if duration_sec > 0 else 0.0
    status = "partial" if duration_sec > 0 and gap >= 600 and ratio < 0.85 else "present"
    return {
        "status": status,
        "message_count": len(seconds),
        "first_message_sec": round(first, 3),
        "latest_message_sec": round(latest, 3),
        "coverage_ratio": round(ratio, 4),
        "coverage_gap_sec": round(gap, 3),
    }

def _subtitle_context_index(segments: list[dict[str, Any]], *, max_segments: int = 5000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in segments[:max_segments]:
        if not isinstance(row, dict):
            continue
        start = _safe_float(row.get("start_sec"))
        end = _safe_float(row.get("end_sec"))
        text = _clip_text(row.get("text"), 220)
        if start is None or not text:
            continue
        rows.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end if end is not None else start, 3),
                "text": text,
                "index": _safe_int(row.get("index"), 0) or 0,
            }
        )
    return rows

def _subtitle_events_from_segments(segments: list[dict[str, Any]], *, video_no: str = "") -> list[dict[str, Any]]:
    if not segments:
        return []
    ranked = sorted(
        segments,
        key=lambda row: (
            -len(str(row.get("text") or "")),
            _safe_float(row.get("start_sec"), 0.0) or 0.0,
        ),
    )[:16]
    ranked.sort(key=lambda row: _safe_float(row.get("start_sec"), 0.0) or 0.0)
    events = [
        _event(
            "subtitle_density",
            start_sec=row.get("start_sec"),
            end_sec=row.get("end_sec"),
            label=row.get("text") or f"subtitle {row.get('index', '')}",
            role="supporting",
            confidence="medium",
            evidence=[
                {
                    "type": "subtitle",
                    "label": f"asr segment {row.get('index', '')}",
                    "text": _clip_text(row.get("text")),
                }
            ],
            risk_flags=["subtitle_context_not_final_claim"],
            source_refs=[{"type": "work.asr_segments_or_srt", "video_no": video_no}],
        )
        for row in ranked
    ]
    events.extend(_subtitle_balanced_events_from_segments(segments, video_no=video_no))
    return events

def _subtitle_balanced_events_from_segments(
    segments: list[dict[str, Any]],
    *,
    video_no: str = "",
    window_sec: int = 1800,
    max_windows: int = 24,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in segments:
        if not isinstance(row, dict):
            continue
        start = _safe_float(row.get("start_sec"))
        text = str(row.get("text") or "").strip()
        if start is None or len(text) < 24:
            continue
        grouped.setdefault(int(start // window_sec), []).append(row)

    events: list[dict[str, Any]] = []
    for _, rows in sorted(grouped.items())[:max_windows]:
        best = max(
            rows,
            key=lambda row: (
                len(str(row.get("text") or "")),
                -(_safe_float(row.get("start_sec"), 0.0) or 0.0),
            ),
        )
        events.append(
            _event(
                "subtitle_density",
                start_sec=best.get("start_sec"),
                end_sec=best.get("end_sec"),
                label=best.get("text") or f"subtitle {best.get('index', '')}",
                role="supporting",
                confidence="medium",
                evidence=[
                    {
                        "type": "subtitle",
                        "label": f"시간대 대표 자막 {best.get('index', '')}",
                        "text": _clip_text(best.get("text")),
                    }
                ],
                risk_flags=["subtitle_context_not_final_claim"],
                source_refs=[{"type": "work.asr_segments_or_srt", "video_no": video_no, "selection": "balanced_window"}],
            )
        )
    return events


def build_private_review(vod, speech, chats, chat_peaks, audio, comment_context, clips):
    """Portable file adapter around the original five private lane producers."""
    segments=[{"index":i+1,"start_sec":row["start"],"end_sec":row["end"],"text":row["text"]}
              for i,row in enumerate(speech)]
    meta={"highlights":chat_peaks,"audio_reaction_peaks":audio.get("audio_reaction_peaks",[]),
          "timeline_comment_context":comment_context}
    events = (_metadata_events(meta) + _chat_events(meta,chats)
        + _chat_bucket_reaction_events(_chat_context_index(chats))
        + _emoticon_reaction_events(chats)
        + _subtitle_events_from_segments(segments,video_no=str(vod.video_no)))
    for clip in clips:
        event=_event("viewer_clip",start_sec=clip["offset_sec"],label=clip["title"],
            role="supporting",evidence=[{"type":"viewer_clip","label":clip['clip_uid'],"text":clip["title"]}])
        event['clip_uid']=clip['clip_uid']
        events.append(event)
    events=list({row["event_id"]:row for row in events}.values())
    events.sort(key=lambda row:(row["start_sec"] or 0,row["event_id"]))
    return {"schema_version":SCHEMA_VERSION,"video_no":str(vod.video_no),
        "privacy":"local_admin_only","labels":_LANE_LABELS,
        "lanes":[{"key":key,"label":label,"event_count":sum(e["lane"]==key for e in events)}
                 for key,label in _LANE_LABELS.items() if key not in ("existing_segments","highlight")],
        "events":events,"chat_coverage":_chat_coverage_summary(chats,vod.duration)}


def render_private_review(data,workbench):
    """Original complete template; additional evidence stays on the private route."""
    from .report_template import render_report
    return render_report(data,private_workbench=workbench)
