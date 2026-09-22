"""Original 2.9.4.2 strict/soft clip anchors with baseline balanced defaults."""
from __future__ import annotations
import logging
from typing import Optional
from datetime import datetime, timezone
from .models import UserClipsBundle, VODInfo
from .user_clips_filter import is_eligible_in_vod_clip
from .clip_probe import parse_chzzk_datetime

def _vod_age_hours(value):
    start = parse_chzzk_datetime(value)
    return max(0, (datetime.now(timezone.utc)-start).total_seconds()/3600) if start else None

logger = logging.getLogger("pipeline")

_USER_CLIPS_ANCHOR_BUCKET_SEC = 60

_ANCHORED_UPPER_ESCAPE_PLAY = 1000

_ANCHORED_CAP_PER_VOD = 5

_ANCHORED_AGE_FRESH_HOURS = 6

_ANCHORED_AGE_RECENT_HOURS = 24

_ANCHORED_AGE_FRESH_FACTOR = 0.2

_ANCHORED_AGE_RECENT_FACTOR = 0.5

def _bucketed_eligible_clips(
    bundle: Optional[UserClipsBundle],
    vod_info: VODInfo,
    *,
    bucket_sec: int = _USER_CLIPS_ANCHOR_BUCKET_SEC,
) -> list[tuple[int, int, int]]:
    """eligible in-VOD 클립 → `(offset, like, play)` + 60s bucket dedup.

    필터: `is_eligible_in_vod_clip` (single source of truth — `manual_override`
    "ignore" 도 caller hydrate 후 이 predicate 가 흡수).
    Dedup: 같은 bucket 내 (escape_eligible=play>=upper_escape desc, like desc,
    play desc, offset asc) 1개만. escape_eligible 우선 — #176 MAJOR2: 같은
    버킷에 play>=1000 (자동 strict) 클립이 lower-play 인 더 좋아요 많은 클립한테
    evicted 되어 자동 strict 후보가 사라지는 회귀 방지.

    Returns: 정렬 안 된 list — 호출자가 정책에 맞게 정렬.
    """
    selected = _bucketed_eligible_clip_objects(
        bundle, vod_info, bucket_sec=bucket_sec,
    )
    rows = []
    for c in selected:
        off = int(getattr(c, "offset_sec", 0) or 0)
        like, play = _clip_engagement_tuple(c)
        rows.append((off, like, play))
    return rows

def _clip_engagement_tuple(clip) -> tuple[int, int]:
    like = getattr(clip, "like_count", None)
    like_norm = int(like) if isinstance(like, (int, float)) and like >= 0 else 0
    play = getattr(clip, "play_count", None)
    play_norm = int(play) if isinstance(play, (int, float)) and play >= 0 else 0
    return like_norm, play_norm

def _clip_offset_int(clip) -> Optional[int]:
    try:
        offset = int(getattr(clip, "offset_sec", None))
    except (TypeError, ValueError):
        return None
    return offset if offset >= 0 else None

def _bucket_winner_is_better(cur, prev) -> bool:
    cur_off = _clip_offset_int(cur)
    prev_off = _clip_offset_int(prev)
    if cur_off is None:
        return False
    if prev_off is None:
        return True

    cur_like, cur_play = _clip_engagement_tuple(cur)
    prev_like, prev_play = _clip_engagement_tuple(prev)
    cur_escape = cur_play >= _ANCHORED_UPPER_ESCAPE_PLAY
    prev_escape = prev_play >= _ANCHORED_UPPER_ESCAPE_PLAY

    if cur_escape != prev_escape:
        return cur_escape
    if cur_like != prev_like:
        return cur_like > prev_like
    if cur_play != prev_play:
        return cur_play > prev_play
    if cur_off != prev_off:
        return cur_off < prev_off
    return (getattr(cur, "clip_uid", "") or "") < (getattr(prev, "clip_uid", "") or "")

def _bucketed_eligible_clip_objects(
    bundle: Optional[UserClipsBundle],
    vod_info: VODInfo,
    *,
    bucket_sec: int = _USER_CLIPS_ANCHOR_BUCKET_SEC,
) -> list:
    """eligible in-VOD clips deduped — within bucket_sec proximity, keep best.

    Uses proximity-based dedup: clips are sorted by offset, then each clip is
    compared against the last accepted clip. If within bucket_sec, the better
    one (by _bucket_winner_is_better) survives; otherwise both are kept.
    This avoids the floor-division boundary problem where two clips 50s apart
    could land in different buckets (e.g. 48:10→bucket48, 49:00→bucket49).
    """
    if bundle is None:
        return []
    clips = getattr(bundle, "clips", None) or []
    if not clips:
        return []
    target_vno = str(getattr(vod_info, "video_no", "") or "")

    eligible: list[tuple[int, object]] = []
    for c in clips:
        if not is_eligible_in_vod_clip(c, target_vno):
            continue
        off = _clip_offset_int(c)
        if off is None:
            continue
        eligible.append((off, c))
    if not eligible:
        return []

    bs = max(1, int(bucket_sec))
    eligible.sort(key=lambda t: t[0])

    result: list[object] = [eligible[0][1]]
    window_starts: list[int] = [eligible[0][0]]
    for off, c in eligible[1:]:
        if off - window_starts[-1] < bs:
            if _bucket_winner_is_better(c, result[-1]):
                result[-1] = c
        else:
            result.append(c)
            window_starts.append(off)
    return result

def _apply_vod_age_correction(lower_play_min: int, vod_info: VODInfo) -> int:
    """VOD 신선도(`publish_date`)에 따라 lower_play_min을 조정한다.

    age<6h: ×0.2, 6-24h: ×0.5, ≥24h or 파싱 실패: full.

    `clip.created_date` 절대 사용 X — 12940641 outlier (clip 2025년 created vs
    VOD 2026년 publish) 의 dirty data 오염 사례 (메모리:
    project_chunk_chat_injection_peak_gate_bug 와 동일 정신 — 잘못된 source 로
    floor 결정 금지). publish_date 만 ground truth.
    """
    pub = getattr(vod_info, "publish_date", "") or ""
    age: float | None = None
    try:
        from .clip_anchors import _vod_age_hours
        age = _vod_age_hours(pub)
    except Exception:
        age = None
    if age is None:
        return max(0, int(lower_play_min))
    if age < _ANCHORED_AGE_FRESH_HOURS:
        return max(1, int(round(lower_play_min * _ANCHORED_AGE_FRESH_FACTOR)))
    if age < _ANCHORED_AGE_RECENT_HOURS:
        return max(1, int(round(lower_play_min * _ANCHORED_AGE_RECENT_FACTOR)))
    return max(0, int(lower_play_min))

def _resolve_engagement_levers(*, override=None):
    override = override or {}
    return {key:max(0,int(override.get(key,value))) for key,value in {"top_k_strict":3,"top_k_soft":3,"lower_play_min":300}.items()}

def _select_promoted_anchored_clips(
    bundle: Optional[UserClipsBundle],
    vod_info: VODInfo,
    *,
    thresholds: Optional[dict] = None,
) -> dict:
    """anchored in-VOD 클립을 v4 정책으로 strict와 soft로 분리한다.

    Algorithm:
      1. eligible → 60s bucket dedup (`_bucketed_eligible_clips`)
      2. sort: play desc, like desc tie-break, offset asc 최종
         (engagement-driven: lower_play_min / upper_escape_play 가 모두 play
         단위이므로 play 가 primary signal)
      3. **escape**: play >= upper_escape_play (1000) 인 모든 클립 → 자동 strict
         (rank 무관). 사용자 의도 verbatim ("좋아요 20+ AND 조회수 2000+ = 역대급
         인기" — escape 는 floor 만 통과해도 압도적 인기 안전 가정).
      4. **strict_top_k**: rank top `top_k_strict` 중 play >= age_lower 통과한 것
         (strict + escape 합쳐 cap 까지)
      5. **soft_top_k**: rank top `top_k_soft` 중 play >= age_lower, strict 와
         중복 제외, 남은 cap budget 안에서

    `top_k_strict=0` (conservative preset) 시 escape 만으로 strict 채움 — 즉
    "강한 자동" 케이스만 통과시키고 자유재량 K 는 끄는 의미. soft 는 별도.

    Returns: {
        "strict": [(off, like, play), ...],  # T4 가 must-include force-inject
        "soft":   [(off, like, play), ...],  # anchor block hint only
        "thresholds_used": {effective levers + age_lower + escape + cap},
    }
    출력 리스트는 chronological (offset asc) — LLM 가독성.
    """
    levers = _resolve_engagement_levers(override=thresholds)
    candidates = _bucketed_eligible_clips(bundle, vod_info)
    age_lower = _apply_vod_age_correction(levers["lower_play_min"], vod_info)
    escape_floor = _ANCHORED_UPPER_ESCAPE_PLAY
    cap = _ANCHORED_CAP_PER_VOD
    debug = {**levers,
             "lower_play_min_effective": age_lower,
             "upper_escape_play": escape_floor,
             "cap": cap}

    if not candidates:
        return {"strict": [], "soft": [], "thresholds_used": debug}

    candidates.sort(key=lambda t: (-t[2], -t[1], t[0]))

    k_strict = levers["top_k_strict"]
    k_soft = levers["top_k_soft"]

    escape_set = [c for c in candidates if c[2] >= escape_floor]
    strict_top = [c for c in candidates[:k_strict] if c[2] >= age_lower]

    seen: set[tuple[int, int, int]] = set()
    strict_combined: list[tuple[int, int, int]] = []
    for c in escape_set + strict_top:
        if c in seen:
            continue
        seen.add(c)
        strict_combined.append(c)
        if len(strict_combined) >= cap:
            break

    soft_combined: list[tuple[int, int, int]] = []
    soft_budget = max(0, cap - len(strict_combined))
    for c in candidates[:k_soft]:
        if c in seen:
            continue
        if c[2] < age_lower:
            continue
        seen.add(c)
        soft_combined.append(c)
        if len(soft_combined) >= soft_budget:
            break

    strict_combined.sort(key=lambda t: t[0])
    soft_combined.sort(key=lambda t: t[0])
    return {"strict": strict_combined, "soft": soft_combined, "thresholds_used": debug}
