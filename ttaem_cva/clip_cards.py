"""Original Loki viewer-clip card markup with validated public DTO input."""

from __future__ import annotations

from html import escape as _html_escape
from .models import VODInfo
from .utils import sec_to_hms
from .outline.schema import hms_to_seconds

def _chzzk_video_url_at(video_no, stamp):
    return f"https://chzzk.naver.com/video/{video_no}?currentTime={hms_to_seconds(stamp)}"

VIEWER_CLIPS_MARKER_START = "<!-- viewer-clips:start -->"

VIEWER_CLIPS_MARKER_END = "<!-- viewer-clips:end -->"

_SAFE_THUMB_SCHEMES = ("http://", "https://")

_BADGE_LOWER_PLAY_MIN_V1 = 300

def _is_promoted_for_badge_v1(clip) -> bool:
    """anchored in-VOD clip 이 promoted 임계를 통과했는지 (badge 전용).

    `_render_user_clip_card` 만 호출. T2 가 머지되면 T2 의 selection helper 로
    교체. 본 함수는 T1/T2 와 병행 작성을 위해 v1 spec 의 floor 만 단독 구현.

    Promotion 조건 (anchored 만 — unanchored 는 별도 channel-popularity signal):
      - engagement_status == "ok"
      - play_count >= lower_play_min (300)  ← `_BADGE_UPPER_ESCAPE_PLAY_V1=1000`
        은 lower_play_min 의 superset 이므로 OR 가 collapse 됨. T2 가 top-K cap
        을 적용해 strict-set 을 좁히면 본 predicate 도 cap 반영하도록 refactor.
    """
    if (getattr(clip, "engagement_status", "") or "") != "ok":
        return False
    play = getattr(clip, "play_count", None)
    if not isinstance(play, (int, float)) or play < 0:
        return False
    return int(play) >= _BADGE_LOWER_PLAY_MIN_V1

def _render_tc_anchor_html(video_no: str, tc_text: str, css_class: str = "tc") -> str:
    """`<a class="tc" href="chzzk.../currentTime=...">tc</a>` 또는 link 없을 때 `<span>`.

    css_class 는 시각적 스타일을 그대로 유지하기 위해 동일하게 적용.
    inline style 로 underline 제거 — 백필되는 구 리포트(.tc CSS rule 만 있고
    a.tc rule 없는 HTML)에서도 모양 깨지지 않게.
    """
    safe_text = _html_escape(tc_text or "")
    url = _chzzk_video_url_at(video_no, tc_text or "")
    if not url:
        return f'<span class="{css_class}">{safe_text}</span>'
    return (
        f'<a class="{css_class}" href="{_html_escape(url)}" '
        f'target="_blank" rel="noopener" '
        f'style="text-decoration:none" '
        f'title="치지직 다시보기에서 이 시점부터 재생">{safe_text}</a>'
    )

def _render_user_clip_card(clip, vod_info: VODInfo, source_label: str = "") -> str:
    title_html = _html_escape(clip.title or "(제목 없음)")
    source_label_html = ""
    if source_label:
        source_label_html = f'<span class="user-clip-source">{_html_escape(source_label)}</span>'
    thumb_html = ""
    safe_thumb = _safe_thumb_url(clip.thumbnail_url or "")
    if safe_thumb:
        thumb_html = (
            f'<img class="user-clip-thumb" loading="lazy" '
            f'alt="" src="{_html_escape(safe_thumb)}">'
        )
    tc_html = ""
    if clip.offset_status == "ok" and clip.video_no == vod_info.video_no and clip.offset_sec is not None:
        tc_text = sec_to_hms(int(clip.offset_sec))
        tc_html = _render_tc_anchor_html(vod_info.video_no, tc_text, css_class="tc user-clip-tc")
    elif clip.offset_status != "ok":
        tc_html = '<span class="user-clip-tc-missing">시점 매칭 실패</span>'

    engagement_parts: list[str] = []
    if clip.engagement_status == "ok":
        if clip.like_count is not None:
            engagement_parts.append(f"♥ {clip.like_count:,}")
        if clip.play_count is not None:
            engagement_parts.append(f"▶ {clip.play_count:,}")
    elif clip.read_count:
        engagement_parts.append(f"▶ {clip.read_count:,}")
    engagement_html = (
        f'<div class="user-clip-meta">{" · ".join(engagement_parts)}</div>'
        if engagement_parts else ""
    )

    badge_html = ""
    if _is_promoted_for_badge_v1(clip):
        badge_html = (
            '<span class="user-clip-badge-promoted" '
            'title="조회수가 충분해 시청자 반응이 강한 클립">🔥 핫 클립</span>'
        )

    clip_id_html = _html_escape(clip.clip_uid)
    clip_url = f"https://chzzk.naver.com/clips/{clip_id_html}"
    return (
        f'<div class="user-clip-card">\n'
        f'  <div class="user-clip-media">\n'
        f'    <button class="user-clip-play" type="button" data-clip-id="{clip_id_html}" '
        f'data-clip-title="{title_html}" aria-label="{title_html} 바로 재생">\n'
        f'      {thumb_html}\n'
        f'      <span class="user-clip-play-label" aria-hidden="true">▶ 바로 재생</span>\n'
        f'    </button>\n'
        f'  </div>\n'
        f'  <div class="user-clip-body">\n'
        f'    {source_label_html}\n'
        f'    <a class="user-clip-title-link" href="{clip_url}" target="_blank" rel="noopener">\n'
        f'      <div class="user-clip-title">{title_html}</div>\n'
        f'    </a>\n'
        f'    {badge_html}\n'
        f'    <div class="user-clip-foot">{tc_html}{engagement_html}</div>\n'
        f'  </div>\n'
        f'</div>'
    )

def _safe_thumb_url(url: str) -> str:
    """thumbnail_url scheme allowlist — javascript:/data: 등 XSS 차단.

    Codex adversarial review: sidecar JSON 이 disk 파일이라 손상/수동 편집/비정상
    응답이 가능. _html_escape 만으로는 scheme injection 못 막음.
    """
    if not url:
        return ""
    lowered = url.lower().lstrip()
    return url if lowered.startswith(_SAFE_THUMB_SCHEMES) else ""

def _wrap_user_clips_card(title: str, body: str) -> str:
    return (
        f"{VIEWER_CLIPS_MARKER_START}\n"
        f'<div class="card" data-report-block="viewer-clips">\n'
        f'  <div class="card-head"><h2>{_html_escape(title)}</h2></div>\n'
        f'  <div class="card-body">{body}</div>\n'
        f'</div>\n'
        f"{VIEWER_CLIPS_MARKER_END}"
    )
