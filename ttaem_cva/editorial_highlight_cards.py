"""Canonical user-facing renderer for accepted editorial Highlight cards.

The renderer owns presentation only.  Point, Story, and Highlight authority is
validated by the caller before it reaches this module.  The same HTML and CSS
are reused by fresh report generation and Report Admin's saved-page repair so
the iframe and final report cannot silently drift into two visual systems.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Mapping
from typing import Any

from .utils import sec_to_hms


EDITORIAL_HIGHLIGHT_CARD_DESIGN = "chzz.editorial-highlight-card.v2"


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _hms_to_seconds(value: object) -> int | None:
    parts = str(value or "").strip().split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = (int(part) for part in parts)
            if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
                return None
            return hours * 3600 + minutes * 60 + seconds
        if len(parts) == 2:
            minutes, seconds = (int(part) for part in parts)
            if minutes < 0 or not 0 <= seconds < 60:
                return None
            return minutes * 60 + seconds
    except ValueError:
        return None
    return None


def _validated_spans(raw_spans: object) -> list[dict[str, Any]] | None:
    if not isinstance(raw_spans, list) or not raw_spans:
        return None
    validated: list[dict[str, Any]] = []
    previous_end: float | None = None
    for raw in raw_spans:
        if not isinstance(raw, Mapping):
            return None
        try:
            start = float(raw.get("start_sec"))
            end = float(raw.get("end_sec"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            return None
        if previous_end is not None and start < previous_end:
            return None
        row = dict(raw)
        row["start_sec"] = start
        row["end_sec"] = end
        validated.append(row)
        previous_end = end
    return validated


def _duration_label(start: int, end: int) -> str:
    duration = max(0, end - start)
    if duration < 60:
        return f"{duration}초"
    minutes, seconds = divmod(duration, 60)
    if minutes < 60:
        return f"{minutes}분 {seconds:02d}초"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes:02d}분"


def _seconds_label(duration: float) -> str:
    return _duration_label(0, int(round(max(0, duration))))


def render_editorial_highlight_cards(view: Mapping[str, Any], *, video_no: str) -> str:
    """Render one validated editorial view as editor-ready cards."""

    points = list(view.get("points") or [])
    points_by_id = dict(view.get("points_by_id") or {})
    stories_by_ref = dict(view.get("stories_by_ref") or {})
    highlights = list(view.get("highlights") or [])
    highlight_projections: list[dict[str, Any]] = []
    for highlight_index, highlight in enumerate(highlights, start=1):
        highlight_id = str(highlight.get("highlight_id") or f"highlight-{highlight_index}")
        fragment_id = "editorial-highlight-" + re.sub(
            r"[^a-zA-Z0-9_-]+", "-", highlight_id
        ).strip("-")
        highlight_projections.append({
            "highlight_id": highlight_id,
            "fragment_id": fragment_id,
            "label": f"H{highlight_index}",
            "title": str(highlight.get("title") or "편집 Highlight"),
            "spans": _validated_spans(highlight.get("source_spans")),
        })
    referenced_points: set[str] = set()
    cards: list[str] = []

    for card_index, highlight in enumerate(highlights, start=1):
        projection = highlight_projections[card_index - 1]
        spans = projection["spans"]
        has_valid_spans = spans is not None
        spans = spans or []
        start = int(spans[0]["start_sec"]) if spans else None
        end = int(spans[-1]["end_sec"]) if spans else None
        used_duration = sum(span["end_sec"] - span["start_sec"] for span in spans)
        excluded_duration = sum(
            max(0, spans[index]["start_sec"] - spans[index - 1]["end_sec"])
            for index in range(1, len(spans))
        )
        point_refs = [str(value) for value in highlight.get("point_refs") or []]
        referenced_points.update(point_refs)
        point_stories = [
            stories_by_ref[str(points_by_id[point_ref].get("story_ref") or "")]
            for point_ref in point_refs
        ]
        why_notable = " / ".join(dict.fromkeys(
            str(story.get("why_notable") or "").strip()
            for story in point_stories
            if str(story.get("why_notable") or "").strip()
        ))
        story_summary = str(highlight.get("story_summary") or "").strip()
        if len(point_refs) == 1:
            card_reason = why_notable or story_summary or "승인된 편집 구간입니다."
        else:
            card_reason = story_summary or why_notable or "관련 Point를 잇는 승인된 편집 구간입니다."

        point_links: list[str] = []
        for point_ref in point_refs:
            point = points_by_id[point_ref]
            point_start = _hms_to_seconds(point.get("timestamp"))
            story = stories_by_ref[str(point.get("story_ref") or "")]
            point_title = str(point.get("title") or "Point")
            point_reason = str(story.get("why_notable") or "").strip()
            point_title_html = (
                f'<span class="point-link-title">{_esc(point_title)}</span>'
                if len(point_refs) > 1 else
                '<span class="point-link-title">이 시작 지점으로 이동</span>'
            )
            point_reason_html = (
                f'<p class="point-story-reason">{_esc(point_reason)}</p>'
                if len(point_refs) > 1 and point_reason else ""
            )
            point_seek = (
                '<a class="point-link" '
                f'data-start="{point_start}" '
                f'aria-label="{_esc(point.get("timestamp") or "")} {_esc(point_title)}로 이동" '
                f'href="https://chzzk.naver.com/video/'
                f'{_esc(video_no)}?currentTime={point_start}">'
                f'<span class="point-link-time">{_esc(point.get("timestamp") or "")}</span>'
                f'{point_title_html}</a>'
                if point_start is not None else
                '<span class="point-link is-disabled" aria-disabled="true">'
                '<span class="point-link-time">구간 정보 없음</span>'
                f'{point_title_html}</span>'
            )
            point_links.append(
                '<div class="highlight-point-jump">'
                f'{point_seek}{point_reason_html}</div>'
            )

        point_heading = "시작 지점" if len(point_refs) == 1 else f"주요 시작 지점 {len(point_refs)}개"
        point_nav = (
            '<nav class="highlight-point-jumps" aria-label="Highlight 안의 Point 이동">'
            f'<p class="highlight-point-jumps-title">{_esc(point_heading)}</p>'
            + "".join(point_links)
            + "</nav>"
        )
        source_span_rows: list[str] = []
        for span_index, span in enumerate(spans):
            exclusion = span.get("exclusion_before") if isinstance(span.get("exclusion_before"), Mapping) else {}
            gap = (
                max(0, span["start_sec"] - spans[span_index - 1]["end_sec"])
                if span_index else 0
            )
            exclusion_reason = str(exclusion.get("reason") or "").strip()
            exclusion_refs = [str(value) for value in exclusion.get("evidence_refs") or [] if str(value).strip()]
            gap_occupants = []
            if span_index and gap > 0:
                gap_start = float(spans[span_index - 1]["end_sec"])
                gap_end = float(span["start_sec"])
                for other_index, other in enumerate(highlight_projections):
                    if other_index == card_index - 1 or not other["spans"]:
                        continue
                    if any(
                        max(gap_start, float(other_span["start_sec"]))
                        < min(gap_end, float(other_span["end_sec"]))
                        for other_span in other["spans"]
                    ):
                        gap_occupants.append(other)
            gap_occupants_html = (
                '<span class="source-span-gap-occupants">이 구간의 다른 Highlight: '
                + "".join(
                    '<a class="source-span-gap-occupant" '
                    f'data-gap-occupant-highlight-id="{_esc(other["highlight_id"])}" '
                    f'href="#{_esc(other["fragment_id"])}">'
                    f'{_esc(other["label"])} · {_esc(other["title"])}</a>'
                    for other in gap_occupants
                )
                + '</span>'
                if gap_occupants else ''
            )
            exclusion_html = (
                '<li class="source-span-skip">'
                f'<span class="source-span-skip-label">건너뜀 · {_esc(_seconds_label(gap))}</span>'
                + (f' · {_esc(exclusion_reason)}' if exclusion_reason else '')
                + (
                    '<span class="source-span-evidence-refs">근거: '
                    + _esc(", ".join(exclusion_refs)) + '</span>'
                    if exclusion_refs else ''
                )
                + gap_occupants_html
                + '</li>'
                if span_index and gap > 0 else ''
            )
            if exclusion_html:
                source_span_rows.append(exclusion_html)
            source_span_rows.append(
                '<li class="source-span-scene" '
                f'data-start="{_esc(span.get("start_sec"))}" data-end="{_esc(span.get("end_sec"))}">'
                f'<span class="source-span-sequence-label">장면 {span_index + 1}</span>'
                '<span class="source-span-clock">'
            f'<a class="source-time-link" data-start="{int(float(span["start_sec"]))}" '
            f'aria-label="{_esc(sec_to_hms(float(span["start_sec"])))} 구간 시작으로 이동" '
            f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={int(float(span["start_sec"]))}">'
            f'{_esc(sec_to_hms(float(span["start_sec"])))}</a> → '
            f'<a class="source-time-link" data-start="{int(float(span["end_sec"]))}" '
            f'aria-label="{_esc(sec_to_hms(float(span["end_sec"])))} 구간 끝으로 이동" '
            f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={int(float(span["end_sec"]))}">'
            f'{_esc(sec_to_hms(float(span["end_sec"])))}</a></span>'
                f'<span class="source-span-reason">{_esc(span.get("reason") or "편집 구간")}</span></li>'
            )
        source_spans = "".join(source_span_rows)
        story_flow = (
            '<section class="highlight-story-flow"><h4 class="highlight-detail-heading">이야기 흐름</h4>'
            f'<p>{_esc(story_summary)}</p></section>'
            if story_summary and story_summary != card_reason else ""
        )
        source_panel = (
            '<section class="source-spans-wrap"><h4 class="highlight-detail-heading">편집 순서</h4>'
            f'<p class="source-spans-summary">장면 {len(spans)}개 · 실제 사용 {_esc(_seconds_label(used_duration))}</p>'
            f'<ol class="source-spans" aria-label="원본 편집 구간">{source_spans}</ol></section>'
            if has_valid_spans else
            '<section class="source-spans-wrap"><h4 class="highlight-detail-heading">원본 편집 구간</h4>'
            '<p class="source-spans-empty">구간 정보 없음</p></section>'
        )
        details_panel = (
            '<details class="highlight-card-details"><summary>'
            '<span>편집 정보 보기</span>'
            '</summary><div class="highlight-card-details-body">'
            f'{story_flow}{point_nav}{source_panel}</div></details>'
        )
        if not has_valid_spans:
            action_html = (
                '<div class="highlight-card-actions"><span class="highlight-link is-disabled" '
                'aria-disabled="true">구간 정보 없음</span></div>'
            )
        else:
            action_label = (
                f'{_esc(sec_to_hms(start))}부터 보기'
                if len(spans) == 1
                else f'{_esc(sec_to_hms(start))} 첫 장면부터 보기'
            )
            action_html = (
                '<div class="highlight-card-actions">'
                '<nav class="highlight-card-time-ranges" aria-label="Highlight 원본 장면">'
                + "".join(
                    '<a class="highlight-card-time-range" '
                    f'data-highlight-span-index="{span_index}" data-start="{int(float(span["start_sec"]))}" '
                    f'aria-label="장면 {span_index + 1}/{len(spans)}, '
                    f'{_esc(sec_to_hms(float(span["start_sec"])))}부터 '
                    f'{_esc(sec_to_hms(float(span["end_sec"])))}까지, 시작으로 이동" '
                    f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={int(float(span["start_sec"]))}">'
                    f'[{_esc(sec_to_hms(float(span["start_sec"])))}–{_esc(sec_to_hms(float(span["end_sec"])))}]</a>'
                    for span_index, span in enumerate(spans)
                )
                + '</nav><a class="highlight-link" '
                f'data-start="{start}" '
                f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={start}">'
                f'{action_label}</a></div>'
            )
        cards.append(
            f'<article id="{_esc(projection["fragment_id"])}" class="highlight-card" '
            f'data-editorial-card-design="{EDITORIAL_HIGHLIGHT_CARD_DESIGN}" '
            f'data-point-count="{len(point_refs)}" data-span-count="{len(spans)}">'
            '<header class="highlight-card-header">'
            f'<h3>{_esc(highlight.get("title") or "편집 Highlight")}</h3>'
            '<p class="highlight-card-reason"><span class="highlight-card-reason-label">볼 이유</span>'
            f'{_esc(card_reason)}</p></header>'
            + action_html + details_panel + "</article>"
        )

    standalone_links: list[str] = []
    for point in points:
        if str(point.get("id") or "") in referenced_points:
            continue
        point_start = _hms_to_seconds(point.get("timestamp"))
        story = stories_by_ref[str(point.get("story_ref") or "")]
        point_title = str(point.get("title") or "주요 장면")
        point_seek = (
            '<a class="standalone-point standalone-point-time-link" '
            f'data-start="{point_start}" aria-label="{_esc(point.get("timestamp") or "")} 원본으로 이동" '
            f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={point_start}">'
            f'<span class="standalone-point-time">{_esc(point.get("timestamp") or "")}</span></a>'
            if point_start is not None else
            '<span class="standalone-point standalone-point-time-link is-disabled" aria-disabled="true">'
            '<span class="standalone-point-time">구간 정보 없음</span></span>'
        )
        standalone_links.append(
            '<li class="standalone-point-card">'
            f'{point_seek}'
            '<details class="point-evidence-panel"><summary '
            f'aria-label="{_esc(point_title)} Story 펼치기">'
            f'<span class="standalone-point-disclosure-title">{_esc(point_title)}</span>'
            '<span class="standalone-point-chevron" aria-hidden="true">›</span></summary>'
            '<div class="point-evidence-body">'
            f'<p>{_esc(story.get("why_notable") or "-")}</p></div></details></li>'
        )
    standalone_html = (
        f'<section class="standalone-points"><h3>나머지 주요 장면 {len(standalone_links)}개</h3>'
        '<ol class="standalone-point-list">' + "".join(standalone_links) + "</ol></section>"
        if standalone_links else ""
    )
    return (
        '<section class="editorial-highlights" aria-label="승인된 편집 Highlight">'
        '<div class="editorial-highlight-list">' + "".join(cards) + "</div>"
        + standalone_html + "</section>"
    )


__all__ = [
    "EDITORIAL_HIGHLIGHT_CARD_DESIGN",
    "render_editorial_highlight_cards",
]
