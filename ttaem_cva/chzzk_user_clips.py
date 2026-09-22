"""Chzzk 사용자 클립 수집·정규화·캐시.

API:
    fetch_chzzk_user_clips(video_no, channel_id, cookies, work_dir, ...)
        → UserClipsBundle  (실패 포함, 항상 bundle 반환 — 파이프라인 중단 안 함)
    load_chzzk_user_clips(video_no, work_dir, *, cache_ttl_hours=24)
        → UserClipsBundle | None  (TTL 만료/없음/스키마 미스매치 시 None)

설계:
  이 모듈은 정규화한 자료만 저장하며 clustering / scoring은 후속 단계가 맡는다.
  (1) channel API 호출 (2) DOM offset 회수 (3) UserClip dataclass 정규화
  (4) work/<video_no>/user_clips.json 캐시 I/O 만 한다.

cache schema (work/<video_no>/user_clips.json):
  {
    "schema_version": 4,            # v4: broadcast-time window selection guard
    "signal_version": 1,
    "video_no": "13003574",
    "channel_id": "a7e175...",
    "fetched_at": "2026-05-04T...Z",
    "engagement_fetched_at": "2026-05-04T...Z",
    "filter_type": "WITHIN_ONE_DAY",
    "status": "ok",
    "total": 23,
    "with_offset": 23,
    "with_engagement": 21,           # P1: engagement_status=="ok" 카운트
    "order_type": "POPULAR",
    "window_start_at": "2026-08-26T00:00:00+00:00",
    "window_end_at": "2026-08-26T10:00:00+00:00",
    "clips": [
      {
        ...UserClip 메타...,
        "offset_status": "ok",
        "offset_sec": 5765,
        "like_count": 42,            # P1
        "play_count": 1234,          # P1
        "engagement_status": "ok",   # P1
        "engagement_extracted_at": "2026-05-04T...Z"
      }, ...
    ]
  }
  v1 캐시는 cache miss 처리 (자동 migration X) — 로그에 schema_version 차이 노출
  후 다음 호출에서 fresh fetch.

제한:
  - pipeline_config.json 직접 수정 금지
  - output/, 운영 work/ 본체 구조 수정 금지 (사이드카 1개만 추가)
  - real clip media download 금지 (metadata + offset only)
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .clip_network import (
    NetworkManager,
    get_chzzk_clip_vod_offsets_via_browser,
    parse_chzzk_datetime,
)
from .models import UserClip, UserClipsBundle

logger = logging.getLogger(__name__)

CACHE_FILENAME = "user_clips.json"
CACHE_SCHEMA_VERSION = 4
DEFAULT_CACHE_TTL_HOURS = 24
DEFAULT_MAX_CLIPS = 30
DEFAULT_OFFSET_BUDGET = 60
DEFAULT_CLIP_SCAN_PAGES = 120
POST_LIVE_CLIP_GRACE_HOURS = 1.0
DEFAULT_SIGNAL_VERSION = 1
DEFAULT_LINK_RETRY_COOLDOWN_HOURS = 2

TRANSIENT_FETCH_STATUSES = frozenset({
    "fetch_failed",
    "partial",
    "playwright_missing",
    "playwright_profile_busy",
    "no_anchor_only",
    "wrong_vod_only",
    "window_unavailable",
})
LINK_PENDING_STATUSES = frozenset({"no_anchor_only", "wrong_vod_only"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_raw_clip(raw: dict) -> Optional[UserClip]:
    """channel API raw clip dict → UserClip. clip_uid 가 비면 None."""
    if not isinstance(raw, dict):
        return None
    clip_uid = raw.get("clipUID") or raw.get("clipId") or ""
    if not clip_uid:
        return None
    owner_channel = raw.get("ownerChannelId") or ""
    if not owner_channel and isinstance(raw.get("ownerChannel"), dict):
        owner_channel = raw["ownerChannel"].get("channelId", "") or ""
    return UserClip(
        clip_uid=str(clip_uid),
        title=str(raw.get("clipTitle") or raw.get("title") or ""),
        thumbnail_url=str(raw.get("thumbnailImageUrl") or ""),
        category=str(raw.get("clipCategory") or raw.get("category") or ""),
        duration=int(raw.get("duration") or 0),
        created_date=str(raw.get("createdDate") or ""),
        read_count=int(raw.get("readCount") or 0),
        owner_channel_id=str(owner_channel),
        rec_id=str(raw.get("recId") or ""),
        creation_window_status="unknown",
    )


def build_clip_creation_window(
    broadcast_start_at,
    vod_duration_sec,
    *,
    grace_hours: float = POST_LIVE_CLIP_GRACE_HOURS,
) -> tuple[datetime, datetime] | None:
    """Return the exact UTC clip-candidate window for one VOD."""
    start = parse_chzzk_datetime(broadcast_start_at)
    try:
        duration_sec = int(vod_duration_sec or 0)
        grace = float(grace_hours)
    except (TypeError, ValueError):
        return None
    if start is None or duration_sec <= 0 or grace < 0:
        return None
    end = start + timedelta(seconds=duration_sec, hours=grace)
    return start, end


def select_raw_clips_in_window(
    raw_clips: list,
    *,
    window_start: datetime,
    window_end: datetime,
    max_clips: int,
    order_type: str = "RECENT",
) -> tuple[list, dict]:
    """Filter by creation time and preserve POPULAR ordering before the cap."""
    accepted: list[tuple[datetime, dict]] = []
    invalid = 0
    outside = 0
    seen: set[str] = set()
    for raw in raw_clips or []:
        if not isinstance(raw, dict):
            invalid += 1
            continue
        created_at = parse_chzzk_datetime(raw.get("createdDate"))
        if created_at is None:
            invalid += 1
            continue
        if created_at < window_start or created_at > window_end:
            outside += 1
            continue
        uid = str(raw.get("clipUID") or raw.get("clipId") or "")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        accepted.append((created_at, raw))
    if str(order_type or "").upper() == "RECENT":
        accepted.sort(key=lambda item: item[0], reverse=True)
    limit = max(0, int(max_clips or 0))
    selected = [raw for _, raw in accepted[:limit]]
    return selected, {
        "raw": len(raw_clips or []),
        "in_window": len(accepted),
        "selected": len(selected),
        "outside": outside,
        "invalid": invalid,
    }


def _clip_popularity_key(clip: UserClip) -> tuple[int, int, int, str]:
    def count(value) -> int:
        return int(value) if isinstance(value, (int, float)) and value >= 0 else 0

    return (
        count(getattr(clip, "play_count", None)),
        count(getattr(clip, "like_count", None)),
        count(getattr(clip, "read_count", None)),
        str(getattr(clip, "clip_uid", "") or ""),
    )


def _select_clips_for_time_coverage(
    clips: list[UserClip],
    *,
    target_video_no: str,
    vod_duration_sec: int,
    limit: int,
) -> list[UserClip]:
    """Select same-VOD clips by adaptive time coverage, then return time order.

    The number of buckets follows the requested display capacity, not a genre or
    a fixed minute threshold. Empty buckets stay empty. Within every nonempty
    bucket popularity decides first, and later rounds may take additional clips
    from a bucket when capacity remains.
    """

    try:
        duration = int(vod_duration_sec or 0)
        cap = max(0, int(limit or 0))
    except (TypeError, ValueError):
        return []
    if duration <= 0 or cap <= 0:
        return []

    best_by_uid: dict[str, UserClip] = {}
    for clip in clips or []:
        uid = str(getattr(clip, "clip_uid", "") or "")
        try:
            offset = int(getattr(clip, "offset_sec", None))
        except (TypeError, ValueError):
            continue
        if (
            not uid
            or str(getattr(clip, "video_no", "") or "") != str(target_video_no)
            or str(getattr(clip, "offset_status", "") or "") != "ok"
            or offset < 0
            or offset > duration
        ):
            continue
        previous = best_by_uid.get(uid)
        if previous is None or _clip_popularity_key(clip) > _clip_popularity_key(previous):
            best_by_uid[uid] = clip

    anchored = list(best_by_uid.values())
    if not anchored:
        return []
    bucket_count = min(cap, len(anchored))
    buckets: list[list[UserClip]] = [[] for _ in range(bucket_count)]
    for clip in anchored:
        offset = int(clip.offset_sec or 0)
        bucket_index = min(bucket_count - 1, math.floor(offset * bucket_count / duration))
        clip.coverage_bucket_index = bucket_index
        clip.selection_reason = ""
        buckets[bucket_index].append(clip)
    for bucket in buckets:
        bucket.sort(key=_clip_popularity_key, reverse=True)

    selected: list[UserClip] = []
    rank = 0
    while len(selected) < cap:
        added = False
        for bucket in buckets:
            if rank < len(bucket):
                clip = bucket[rank]
                clip.selection_reason = (
                    "temporal_coverage" if rank == 0
                    else "popularity_within_covered_bucket"
                )
                selected.append(clip)
                added = True
                if len(selected) >= cap:
                    break
        if not added:
            break
        rank += 1
    return sorted(selected, key=lambda clip: (int(clip.offset_sec or 0), clip.clip_uid))


def _bundle_to_json(bundle: UserClipsBundle) -> dict:
    return {
        "schema_version": bundle.schema_version,
        "signal_version": bundle.signal_version,
        "video_no": bundle.video_no,
        "channel_id": bundle.channel_id,
        "fetched_at": bundle.fetched_at,
        "filter_type": bundle.filter_type,
        "status": bundle.status,
        "total": bundle.total,
        "with_offset": bundle.with_offset,
        "with_engagement": bundle.with_engagement,
        "order_type": bundle.order_type,
        "window_start_at": bundle.window_start_at,
        "window_end_at": bundle.window_end_at,
        "window_status": bundle.window_status,
        "raw_clip_count": bundle.raw_clip_count,
        "window_candidate_count": bundle.window_candidate_count,
        "excluded_out_of_window_count": bundle.excluded_out_of_window_count,
        "excluded_invalid_date_count": bundle.excluded_invalid_date_count,
        "excluded_unverified_count": bundle.excluded_unverified_count,
        "selection_policy": bundle.selection_policy,
        "coverage_bucket_count": bundle.coverage_bucket_count,
        "coverage_nonempty_bucket_count": bundle.coverage_nonempty_bucket_count,
        "coverage_hole_count": bundle.coverage_hole_count,
        "clips": [asdict(c) for c in bundle.clips],
    }


def _coerce_optional_int(value) -> Optional[int]:
    """JSON round-trip 시 None / 숫자 / 숫자형 문자열 모두 처리. 실패 → None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bundle_from_json(data: dict) -> Optional[UserClipsBundle]:
    if not isinstance(data, dict):
        return None
    try:
        schema_v = int(data.get("schema_version") or 0)
    except (TypeError, ValueError):
        return None
    if schema_v != CACHE_SCHEMA_VERSION:
        return None
    clips_raw = data.get("clips") or []
    clips: list = []
    for cd in clips_raw:
        if not isinstance(cd, dict):
            continue
        try:
            clips.append(UserClip(
                clip_uid=str(cd.get("clip_uid") or ""),
                title=str(cd.get("title") or ""),
                thumbnail_url=str(cd.get("thumbnail_url") or ""),
                category=str(cd.get("category") or ""),
                duration=int(cd.get("duration") or 0),
                created_date=str(cd.get("created_date") or ""),
                read_count=int(cd.get("read_count") or 0),
                owner_channel_id=str(cd.get("owner_channel_id") or ""),
                rec_id=str(cd.get("rec_id") or ""),
                video_no=cd.get("video_no") if cd.get("video_no") else None,
                offset_sec=_coerce_optional_int(cd.get("offset_sec")),
                offset_status=str(cd.get("offset_status") or "unknown"),
                like_count=_coerce_optional_int(cd.get("like_count")),
                play_count=_coerce_optional_int(cd.get("play_count")),
                engagement_status=str(cd.get("engagement_status") or "pending"),
                engagement_fetched_at=(
                    str(cd["engagement_fetched_at"])
                    if cd.get("engagement_fetched_at") else None
                ),
                engagement_source=str(cd.get("engagement_source") or "clip_page_dom"),
                manual_override=(
                    str(cd["manual_override"])
                    if cd.get("manual_override") else None
                ),
                creation_window_status=str(
                    cd.get("creation_window_status") or "legacy_unverified"
                ),
                source_vod_duration_sec=_coerce_optional_int(
                    cd.get("source_vod_duration_sec")
                ),
                popularity_rank=_coerce_optional_int(cd.get("popularity_rank")),
                coverage_bucket_index=_coerce_optional_int(
                    cd.get("coverage_bucket_index")
                ),
                selection_reason=str(cd.get("selection_reason") or ""),
            ))
        except (TypeError, ValueError):
            continue
    try:
        bundle_total = int(data.get("total") or len(clips))
        bundle_with_offset = int(data.get("with_offset") or 0)
        bundle_with_engagement = int(data.get("with_engagement") or 0)
        bundle_signal_version = int(data.get("signal_version") or DEFAULT_SIGNAL_VERSION)
    except (TypeError, ValueError):
        return None
    return UserClipsBundle(
        video_no=str(data.get("video_no") or ""),
        channel_id=str(data.get("channel_id") or ""),
        fetched_at=str(data.get("fetched_at") or ""),
        filter_type=str(data.get("filter_type") or ""),
        status=str(data.get("status") or "ok"),
        total=bundle_total,
        with_offset=bundle_with_offset,
        with_engagement=bundle_with_engagement,
        order_type=str(data.get("order_type") or ""),
        window_start_at=str(data.get("window_start_at") or ""),
        window_end_at=str(data.get("window_end_at") or ""),
        window_status=str(data.get("window_status") or "unknown"),
        raw_clip_count=_coerce_optional_int(data.get("raw_clip_count")) or 0,
        window_candidate_count=(
            _coerce_optional_int(data.get("window_candidate_count")) or 0
        ),
        excluded_out_of_window_count=(
            _coerce_optional_int(data.get("excluded_out_of_window_count")) or 0
        ),
        excluded_invalid_date_count=(
            _coerce_optional_int(data.get("excluded_invalid_date_count")) or 0
        ),
        excluded_unverified_count=(
            _coerce_optional_int(data.get("excluded_unverified_count")) or 0
        ),
        selection_policy=str(data.get("selection_policy") or ""),
        coverage_bucket_count=(
            _coerce_optional_int(data.get("coverage_bucket_count")) or 0
        ),
        coverage_nonempty_bucket_count=(
            _coerce_optional_int(data.get("coverage_nonempty_bucket_count")) or 0
        ),
        coverage_hole_count=(
            _coerce_optional_int(data.get("coverage_hole_count")) or 0
        ),
        clips=clips,
        schema_version=CACHE_SCHEMA_VERSION,
        signal_version=bundle_signal_version,
    )


def _cache_path(video_no: str, work_dir: str | os.PathLike) -> Path:
    """work_dir 은 PER-VOD (예: work/13003574/) — caller 가 video_no 로 이미 진입.

    work_dir은 이미 VOD별 디렉터리이므로 CACHE_FILENAME만 결합한다.
    """
    return Path(work_dir) / CACHE_FILENAME


def _is_attempted_status_only_bundle(bundle: UserClipsBundle, status: str) -> bool:
    """Legacy guard: status=ok 이지만 attempted clips 가 모두 같은 실패 상태인 캐시."""
    clips = list(getattr(bundle, "clips", None) or [])
    attempted = [c for c in clips if getattr(c, "offset_status", "") != "skipped"]
    return bool(attempted) and all(
        getattr(c, "offset_status", "") == status for c in attempted
    )


def _save_bundle(bundle: UserClipsBundle, work_dir: str | os.PathLike) -> Path:
    """원자적 rename 으로 캐시 저장. 디렉터리 자동 생성. work_dir = per-VOD."""
    out_path = _cache_path(bundle.video_no, work_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    payload = _bundle_to_json(bundle)
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, out_path)
    logger.info(
        f"chzzk user_clips 캐시 저장: {out_path} "
        f"(total={bundle.total}, with_offset={bundle.with_offset}, "
        f"with_engagement={bundle.with_engagement}, status={bundle.status})"
    )
    return out_path


def _reusable_cached_offsets(
    video_no: str,
    work_dir: str | os.PathLike,
) -> dict[str, UserClip]:
    """Reuse only immutable, previously verified clip→VOD offsets.

    An older RECENT cache is not valid selection authority for the new POPULAR
    coverage policy, but a matching clip UID whose source VOD and offset were
    already verified does not need another browser probe.  Never reuse unknown,
    cross-VOD, negative, or out-of-duration rows.
    """
    cache_path = _cache_path(video_no, work_dir)
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            bundle = _bundle_from_json(json.load(f))
    except (OSError, json.JSONDecodeError):
        return {}
    if bundle is None or str(bundle.video_no or "") != str(video_no):
        return {}
    reusable: dict[str, UserClip] = {}
    for clip in bundle.clips:
        uid = str(clip.clip_uid or "")
        offset = _coerce_optional_int(clip.offset_sec)
        duration = _coerce_optional_int(clip.source_vod_duration_sec)
        if (
            not uid
            or clip.offset_status != "ok"
            or str(clip.video_no or "") != str(video_no)
            or offset is None
            or offset < 0
            or (duration is not None and duration > 0 and offset > duration)
        ):
            continue
        reusable[uid] = clip
    return reusable


def load_chzzk_user_clips(
    video_no: str,
    work_dir: str | os.PathLike,
    *,
    cache_ttl_hours: float = DEFAULT_CACHE_TTL_HOURS,
    allow_transient_cache: bool = False,
) -> Optional[UserClipsBundle]:
    """캐시 로드. TTL 만료/없음/스키마 미스매치 → None.

    이 함수는 단순 TTL만 적용하며 추가 갱신 전략은 호출자가 정한다.
    """
    cache_path = _cache_path(video_no, work_dir)
    if not cache_path.is_file() or cache_path.stat().st_size == 0:
        return None
    age_h = (
        datetime.now(timezone.utc).timestamp() - cache_path.stat().st_mtime
    ) / 3600.0
    if cache_ttl_hours > 0 and age_h > cache_ttl_hours:
        logger.info(
            f"chzzk user_clips 캐시 만료 ({age_h:.1f}h > {cache_ttl_hours}h): {cache_path}"
        )
        return None
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"chzzk user_clips 캐시 로드 실패 → 재수집: {e}")
        return None
    bundle = _bundle_from_json(data)
    if bundle is None:
        found_schema = data.get("schema_version") if isinstance(data, dict) else "unknown"
        logger.warning(
            f"chzzk user_clips 캐시 스키마 미스매치 (found v{found_schema}, "
            f"expected v{CACHE_SCHEMA_VERSION}) → 재수집"
        )
        return None
    if allow_transient_cache:
        logger.info(
            "chzzk user_clips frozen cache 재사용: status='%s' path=%s",
            bundle.status,
            cache_path,
        )
        return bundle
    if str(bundle.order_type or "").upper() != "POPULAR":
        logger.info(
            "chzzk user_clips cache order_type='%s' has no POPULAR time-coverage proof → refetch (%s)",
            bundle.order_type,
            cache_path,
        )
        return None
    if bundle.status in LINK_PENDING_STATUSES and age_h < DEFAULT_LINK_RETRY_COOLDOWN_HOURS:
        logger.info(
            f"chzzk user_clips 캐시 status='{bundle.status}' 재사용 "
            f"(link retry cooldown, age={age_h:.1f}h < {DEFAULT_LINK_RETRY_COOLDOWN_HOURS}h): {cache_path}"
        )
        return bundle
    if bundle.status in TRANSIENT_FETCH_STATUSES:
        logger.info(
            f"chzzk user_clips 캐시 status='{bundle.status}' (transient) → 재수집 "
            f"({cache_path}, age={age_h:.1f}h)"
        )
        return None
    if (
        bundle.status == "ok"
        and _is_attempted_status_only_bundle(bundle, "wrong_vod")
        and age_h < DEFAULT_LINK_RETRY_COOLDOWN_HOURS
    ):
        logger.info(
            "chzzk user_clips 캐시 status='ok' 이지만 attempted clips 가 모두 "
            f"wrong_vod — 짧은 쿨다운 내 재사용 ({cache_path}, age={age_h:.1f}h)"
        )
        return bundle
    if bundle.status == "ok" and _is_attempted_status_only_bundle(bundle, "wrong_vod"):
        logger.info(
            "chzzk user_clips 캐시 status='ok' 이지만 attempted clips 가 모두 "
            f"wrong_vod → 재수집 ({cache_path}, age={age_h:.1f}h)"
        )
        return None
    if (
        bundle.status == "ok"
        and _is_attempted_status_only_bundle(bundle, "no_anchor")
        and age_h < DEFAULT_LINK_RETRY_COOLDOWN_HOURS
    ):
        logger.info(
            "chzzk user_clips 캐시 status='ok' 이지만 attempted clips 가 모두 "
            f"no_anchor — 짧은 쿨다운 내 재사용 ({cache_path}, age={age_h:.1f}h)"
        )
        return bundle
    if bundle.status == "ok" and _is_attempted_status_only_bundle(bundle, "no_anchor"):
        logger.info(
            "chzzk user_clips 캐시 status='ok' 이지만 attempted clips 가 모두 "
            f"no_anchor → 재수집 ({cache_path}, age={age_h:.1f}h)"
        )
        return None
    logger.info(
        f"✓ chzzk user_clips 캐시 재사용: {bundle.total}개 "
        f"(with_offset={bundle.with_offset}, age={age_h:.1f}h)"
    )
    return bundle


def fetch_chzzk_user_clips(
    video_no: str,
    channel_id: str,
    cookies: dict,
    work_dir: str | os.PathLike,
    *,
    work_root: Optional[str | os.PathLike] = None,
    max_clips: int = DEFAULT_MAX_CLIPS,
    offset_budget: int = DEFAULT_OFFSET_BUDGET,
    enable_offset_extraction: bool = True,
    enable_engagement_extraction: bool = True,
    raw_clips_override: Optional[tuple[list, str] | tuple[list, str, bool]] = None,
    broadcast_start_at: str = "",
    vod_duration_sec: int = 0,
    post_live_grace_hours: float = POST_LIVE_CLIP_GRACE_HOURS,
    max_scan_pages: int = DEFAULT_CLIP_SCAN_PAGES,
) -> UserClipsBundle:
    """채널 클립 목록 수집 + DOM 으로 VOD offset 회수 + 캐시 저장.

    실패해도 bundle을 반환해 전체 파이프라인은 계속 진행한다.

    Args:
        video_no: 본 처리 대상 VOD 번호. cross-VOD anchor (status="wrong_vod")
                  필터링 기준.
        channel_id: 클립을 만든 채널의 ID.
        cookies: pipeline_config.json 의 cookies dict. **본 함수가 진입 시 사본을
                 만들어 양 sub-call (list_channel_clips → playwright offset) 사이에서
                 같은 dict를 mutate하므로 둘 다 갱신된 쿠키를 본다.
                 caller 의 dict 는 mutate 안 됨.
        work_dir: PER-VOD 캐시 저장 디렉터리 (예: work/13003574/). caller 책임.
        work_root: GLOBAL work root (예: work/) — Playwright persistent profile 위치.
                   미지정 시 work_dir.parent로 추정한다. cross-VOD 로그인 공유에 필수다.
        max_clips: 채널 API 에서 가져올 최대 클립 개수.
        offset_budget: POPULAR 후보 중 DOM offset을 회수할 bounded pool 크기.
        enable_offset_extraction: False 면 metadata-only mode (clip 정규화만).
        enable_engagement_extraction: False 면 likes/plays DOM 추출 skip (offset 만 수집).
                                       기본값은 True다.
        raw_clips_override: (raw_clips_list, filter_type[, partial]) 튜플. 번들
                            처리 시 list_channel_clips API 중복 호출 회피용.
                            제공 시 채널 API 호출을 건너뛰고 override 를 사용.
                            이후 정규화·probe·캐시·status 로직은 동일하게 수행.
    """
    local_cookies = dict(cookies) if isinstance(cookies, dict) else cookies
    reusable_offsets = _reusable_cached_offsets(video_no, work_dir)

    if work_root is None:
        work_root = Path(work_dir).parent

    bundle = UserClipsBundle(
        video_no=str(video_no),
        channel_id=str(channel_id or ""),
        fetched_at=_utc_now_iso(),
        status="ok",
        order_type="POPULAR",
    )
    if not channel_id:
        logger.warning(
            f"chzzk user_clips: channel_id 누락 → 수집 skip (video_no={video_no})"
        )
        bundle.status = "fetch_failed"
        _save_bundle(bundle, work_dir)
        return bundle

    window = build_clip_creation_window(
        broadcast_start_at,
        vod_duration_sec,
        grace_hours=post_live_grace_hours,
    )
    if window is None:
        logger.warning(
            "chzzk user_clips: 방송 시작/길이 시간창을 계산할 수 없어 "
            "클립 근거 0개로 진행 (video_no=%s)",
            video_no,
        )
        bundle.status = "window_unavailable"
        bundle.window_status = "unavailable"
        _save_bundle(bundle, work_dir)
        return bundle
    window_start, window_end = window
    bundle.window_status = "ok"
    bundle.window_start_at = window_start.isoformat()
    bundle.window_end_at = window_end.isoformat()

    raw_clips: list = []
    used_filter = ""
    clips_partial = False
    if raw_clips_override is not None:
        if len(raw_clips_override) == 3:
            raw_clips, used_filter, clips_partial = raw_clips_override
        else:
            raw_clips, used_filter = raw_clips_override
        logger.info(
            f"chzzk channel clips: 번들 override 사용 "
            f"({len(raw_clips)}개, filter={used_filter})"
            f"{' (partial)' if clips_partial else ''}"
        )
    else:
        try:
            candidate_pool_limit = max(int(max_clips or 0), int(offset_budget or 0))
            scan_pool_limit = max(candidate_pool_limit, int(max_scan_pages or 0) * 30)
            raw_clips, used_filter, clips_partial = NetworkManager.list_channel_clips(
                channel_id=channel_id,
                cookies=local_cookies,
                filter_types=("ALL",),
                order_type="RECENT",
                max_clips=scan_pool_limit,
                max_pages=max_scan_pages,
                created_after=window_start,
                created_before=window_end,
            )
        except Exception as e:
            logger.warning(
                f"chzzk channel clips fetch 실패 ({type(e).__name__}: {e}) — 빈 결과로 진행"
            )
            bundle.status = "fetch_failed"
            _save_bundle(bundle, work_dir)
            return bundle

    bundle.filter_type = used_filter
    if clips_partial:
        bundle.status = "partial"

    selected_raw, window_stats = select_raw_clips_in_window(
        raw_clips,
        window_start=window_start,
        window_end=window_end,
        max_clips=max(int(max_clips or 0), int(offset_budget or 0)),
        order_type="POPULAR",
    )
    bundle.raw_clip_count = int(window_stats["raw"])
    bundle.window_candidate_count = int(window_stats["in_window"])
    bundle.excluded_out_of_window_count = int(window_stats["outside"])
    bundle.excluded_invalid_date_count = int(window_stats["invalid"])
    raw_clips = selected_raw
    if not raw_clips:
        logger.info(
            "chzzk channel clips: 방송 시간창 안의 후보 0개 "
            f"(channel={channel_id}, start={bundle.window_start_at}, "
            f"end={bundle.window_end_at})"
        )
        bundle.status = "partial" if clips_partial else "no_clips"
        _save_bundle(bundle, work_dir)
        return bundle

    normalized: list[UserClip] = []
    for popularity_rank, raw in enumerate(raw_clips, 1):
        c = _normalize_raw_clip(raw)
        if c is not None:
            c.creation_window_status = "in_window"
            c.source_vod_duration_sec = int(vod_duration_sec or 0)
            c.popularity_rank = popularity_rank
            normalized.append(c)
    bundle.clips = normalized
    bundle.total = len(normalized)
    logger.info(
        f"chzzk channel clips: {bundle.total}개 시간창 후보 정규화 완료 "
        f"(order=POPULAR, start={bundle.window_start_at}, end={bundle.window_end_at})"
    )

    if not enable_offset_extraction or bundle.total == 0:
        bundle.with_offset = 0
        bundle.with_engagement = 0
        for c in bundle.clips:
            if c.offset_status == "unknown":
                c.offset_status = "skipped"
            if c.engagement_status == "pending":
                c.engagement_status = "skipped"
        _save_bundle(bundle, work_dir)
        return bundle

    targets = sorted(
        bundle.clips, key=lambda c: c.read_count, reverse=True
    )[:offset_budget]
    target_uids = [c.clip_uid for c in targets]
    try:
        offsets = get_chzzk_clip_vod_offsets_via_browser(
            target_uids,
            cookies=local_cookies,
            work_root=str(work_root),
            target_video_no=str(video_no),
            on_progress=_offset_progress_logger(bundle.total),
            extract_engagement=bool(enable_engagement_extraction),
        )
    except Exception as e:
        logger.warning(
            f"chzzk offset extraction 예외 ({type(e).__name__}: {e}) — metadata-only 로 진행"
        )
        offsets = {}

    engagement_now = _utc_now_iso() if enable_engagement_extraction else None
    for c in bundle.clips:
        info = offsets.get(c.clip_uid)
        cached = reusable_offsets.get(c.clip_uid)
        if (
            cached is not None
            and (
                not isinstance(info, dict)
                or str(info.get("status") or "") != "ok"
            )
        ):
            info = {
                "offset_sec": cached.offset_sec,
                "status": "ok",
                "video_no": cached.video_no,
                "like_count": cached.like_count,
                "play_count": cached.play_count,
                "engagement_status": cached.engagement_status,
            }
            offsets[c.clip_uid] = info
        if info is None:
            c.offset_status = "skipped"
            if enable_engagement_extraction:
                c.engagement_status = "skipped"
            continue
        c.offset_sec = info.get("offset_sec")
        c.video_no = info.get("video_no")
        c.offset_status = info.get("status") or "error"
        if enable_engagement_extraction:
            c.like_count = info.get("like_count")
            c.play_count = info.get("play_count")
            c.engagement_status = info.get("engagement_status") or "error"
            if c.engagement_status == "ok":
                c.engagement_fetched_at = engagement_now
        else:
            c.engagement_status = "skipped"

    bundle.with_offset = sum(1 for c in bundle.clips if c.offset_status == "ok")
    bundle.with_engagement = sum(
        1 for c in bundle.clips if c.engagement_status == "ok"
    )

    attempted_offset_infos = {
        uid: info for uid, info in offsets.items()
        if isinstance(info, dict)
    }

    attempted = sum(1 for c in bundle.clips if c.offset_status != "skipped")
    if attempted > 0 and bundle.status != "partial":
        attempted_clips = [c for c in bundle.clips if c.offset_status != "skipped"]
        if all(c.offset_status == "playwright_profile_busy" for c in attempted_clips):
            bundle.status = "playwright_profile_busy"
        elif all(c.offset_status == "playwright_missing" for c in attempted_clips):
            bundle.status = "playwright_missing"
        elif all(c.offset_status == "wrong_vod" for c in attempted_clips):
            bundle.status = "wrong_vod_only"
        elif all(c.offset_status == "no_anchor" for c in attempted_clips):
            bundle.status = "no_anchor_only"
        elif all(c.offset_status in ("error", "playwright_missing", "playwright_profile_busy")
                 for c in attempted_clips):
            bundle.status = "fetch_failed"

    if attempted_offset_infos and bundle.with_offset == 0:
        status_counts = Counter(
            str(info.get("status") or "error")
            for info in attempted_offset_infos.values()
        )
        selected_video_counts = Counter(
            str(info.get("video_no"))
            for info in attempted_offset_infos.values()
            if info.get("video_no")
        )
        candidate_video_counts: Counter[str] = Counter()
        candidate_count_hist = Counter()
        for info in attempted_offset_infos.values():
            candidate_count_hist[int(info.get("anchor_candidate_count") or 0)] += 1
            for vno in info.get("anchor_candidate_video_nos") or []:
                candidate_video_counts[str(vno)] += 1
        logger.warning(
            "chzzk user_clips offset forensic: "
            f"target={video_no}, attempted={len(attempted_offset_infos)}, "
            f"status_counts={dict(status_counts)}, "
            f"selected_video_counts={dict(selected_video_counts)}, "
            f"candidate_count_hist={dict(candidate_count_hist)}, "
            f"candidate_video_counts={dict(candidate_video_counts)}"
        )

    coverage_selected = _select_clips_for_time_coverage(
        bundle.clips,
        target_video_no=str(video_no),
        vod_duration_sec=int(vod_duration_sec or 0),
        limit=int(max_clips or 0),
    )
    selected_uids = {str(clip.clip_uid or "") for clip in coverage_selected}
    bundle.excluded_unverified_count = sum(
        1 for clip in bundle.clips
        if str(clip.clip_uid or "") not in selected_uids
    )
    bundle.clips = coverage_selected
    bundle.total = len(bundle.clips)
    bundle.with_offset = len(bundle.clips)
    bundle.with_engagement = sum(
        1 for clip in bundle.clips if clip.engagement_status == "ok"
    )
    bundle.selection_policy = "popular_with_temporal_coverage"
    bundle.coverage_bucket_count = min(int(max_clips or 0), len(bundle.clips))
    nonempty_buckets = {
        int(clip.coverage_bucket_index)
        for clip in bundle.clips
        if clip.coverage_bucket_index is not None
    }
    bundle.coverage_nonempty_bucket_count = len(nonempty_buckets)
    bundle.coverage_hole_count = max(
        0, bundle.coverage_bucket_count - bundle.coverage_nonempty_bucket_count
    )

    logger.info(
        f"✓ chzzk user_clips 수집 완료: {bundle.total} 발견, "
        f"{bundle.with_offset} offset / {bundle.with_engagement} engagement "
        f"사용가능 (status={bundle.status})"
    )
    _save_bundle(bundle, work_dir)
    return bundle


def _offset_progress_logger(total_in_bundle: int):
    """on_progress 콜백 — 진행 상황 로깅 (1줄/clip)."""
    def cb(clip_uid: str, idx: int, total: int, offset, status: str, video_no):
        offset_str = f"@{offset}" if offset is not None else "—"
        vno = f" vod={video_no}" if video_no else ""
        logger.info(
            f"  [{idx}/{total}] {clip_uid[:12]} → {status} {offset_str}{vno}"
        )
    return cb
