"""Anonymous transport for baseline viewer-clip collection. No login/profile reuse."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from .acquire import HEADERS
from .clip_probe import parse_chzzk_datetime, _probe_chzzk_clip_offset_in_context
from .local_files import plain_path

CHZZK_API = "https://api.chzzk.naver.com"
_logger = logging.getLogger(__name__)

class _PublicResponse:
    def __init__(self, code, data):
        self.status_code, self.data = code, data
    def json(self):
        return json.loads(self.data)

def _public_get(url, *, cookies=None, params=None, headers=None, timeout=15):
    if cookies:
        raise ValueError("Public extraction does not accept account credentials")
    if not url.startswith(CHZZK_API + "/service/v1/channels/"):
        raise ValueError("Unexpected clip endpoint")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(16000001)
            if len(data) > 16000000:
                raise ValueError("Clip response exceeds limit")
            return _PublicResponse(response.status, data)
    except urllib.error.HTTPError as error:
        return _PublicResponse(error.code, b"{}")

def list_channel_clips(
    channel_id: str,
    cookies: dict,
    *,
    filter_types: tuple = (
        "WITHIN_ONE_DAY", "WITHIN_SEVEN_DAYS", "WITHIN_THIRTY_DAYS", "ALL",
    ),
    order_type: str = "POPULAR",
    max_clips: int = 30,
    max_pages: int = 10,
    request_timeout_s: float = 15.0,
    created_after=None,
    created_before=None,
) -> "tuple[list, str, bool]":
    """채널의 사용자 클립 목록과 실제 사용한 필터를 반환.

    filter_types fallback chain 으로 시도, 첫 non-empty 응답을 반환.
    커서 기반 페이지네이션으로 max_clips 까지 수집. created_after/
    created_before 가 주어지면 생성 시각 필터 뒤에 max_clips 를 적용한다.
    RECENT 정렬에서는 created_after 보다 오래된 행을 만난 뒤 조기 종료한다.
    ``partial=True`` 이면 mid-pagination 오류로 불완전한 결과.
    """
    lower_bound = parse_chzzk_datetime(created_after)
    upper_bound = parse_chzzk_datetime(created_before)
    if created_after is not None and lower_bound is None:
        raise ValueError("created_after must be a valid CHZZK datetime")
    if created_before is not None and upper_bound is None:
        raise ValueError("created_before must be a valid CHZZK datetime")
    if lower_bound and upper_bound and lower_bound > upper_bound:
        raise ValueError("created_after must not be after created_before")

    local_cookies = dict(cookies) if isinstance(cookies, dict) else cookies
    headers = {"User-Agent": "Mozilla/5.0"}
    page_size = min(max(int(max_clips or 0), 1), 30)
    any_partial = False
    for ft in filter_types:
        url = f"{CHZZK_API}/service/v1/channels/{channel_id}/clips"
        all_clips: list = []
        seen_uids: set = set()
        cursor_params: dict = {}
        partial = False
        scanned = 0
        invalid_created = 0
        outside_window = 0
        reached_lower_bound = False
        for page in range(max_pages):
            params = {"filterType": ft, "orderType": order_type, "size": page_size}
            params.update(cursor_params)
            try:
                r = _public_get(
                    url, cookies=local_cookies, params=params,
                    headers=headers, timeout=request_timeout_s,
                )
            except (OSError, ValueError) as e:
                _logger.warning(
                    f"chzzk channel clips (filter={ft}, page={page}) "
                    f"request 실패: {type(e).__name__}"
                )
                partial = True
                break
            if r.status_code in (401, 403):
                _logger.error(
                    f"chzzk channel clips 인증 실패 (filter={ft}, http={r.status_code}, "
                    f"익명 공개 접근 불가)"
                )
                partial = True
                break
            if r.status_code != 200:
                _logger.warning(
                    f"chzzk channel clips (filter={ft}, page={page}) → http={r.status_code}"
                )
                partial = True
                break
            try:
                data = r.json()
            except ValueError:
                _logger.warning(f"chzzk channel clips (filter={ft}, page={page}) JSON 파싱 실패")
                partial = True
                break
            content = data.get("content") or {}
            clips = content.get("data") or content.get("clipList") or []
            if not clips:
                break
            for c in clips:
                scanned += 1
                if lower_bound is not None or upper_bound is not None:
                    created_at = parse_chzzk_datetime(c.get("createdDate"))
                    if created_at is None:
                        invalid_created += 1
                        continue
                    if lower_bound is not None and created_at < lower_bound:
                        outside_window += 1
                        reached_lower_bound = True
                        continue
                    if upper_bound is not None and created_at > upper_bound:
                        outside_window += 1
                        continue
                uid = c.get("clipUID") or c.get("clipId") or ""
                if uid and uid not in seen_uids:
                    seen_uids.add(uid)
                    all_clips.append(c)
                if len(all_clips) >= max_clips:
                    break
            if len(all_clips) >= max_clips:
                break
            if reached_lower_bound and str(order_type).upper() == "RECENT":
                break
            next_cursor = (content.get("page") or {}).get("next")
            if not next_cursor:
                break
            clip_uid = next_cursor.get("clipUID")
            if not clip_uid:
                break
            cursor_params = {"clipUID": clip_uid}
            for cursor_key in ("readCount", "createdDate"):
                if next_cursor.get(cursor_key) is not None:
                    cursor_params[cursor_key] = next_cursor.get(cursor_key)
        else:
            if scanned:
                partial = True
                any_partial = True
                _logger.warning(
                    f"chzzk channel clips filter={ft}: max_pages={max_pages} 도달, "
                    f"추가 클립이 있을 수 있음 ({len(all_clips)}개 수집)"
                )
        if all_clips:
            result = all_clips[:max_clips]
            _logger.info(
                f"chzzk channel clips filter={ft}: {len(result)}개 수집 "
                f"(scanned={scanned}, outside_window={outside_window}, "
                f"invalid_created={invalid_created})"
                f"{' (partial)' if partial else ''}"
            )
            return result, ft, partial
        any_partial = any_partial or partial
    return [], str(filter_types[-1] if filter_types else ""), any_partial
class NetworkManager:
    list_channel_clips = staticmethod(list_channel_clips)

def get_chzzk_clip_vod_offsets_via_browser(clip_uids, cookies=None, *, work_root=None,
        target_video_no=None, on_progress=None, extract_engagement=True):
    if cookies:
        raise ValueError("Public extraction does not accept account credentials")
    if not re.fullmatch(r"[0-9]{1,16}", str(target_video_no or "")):
        raise ValueError("Target VOD number required")
    if len(clip_uids) > 30 or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(uid)) for uid in clip_uids):
        raise ValueError("Invalid or excessive clip candidates")
    if not clip_uids:
        return {}
    root = Path(__file__).resolve().parents[1]
    browsers = plain_path(root / ".models" / "playwright")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    results = {}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as automation:
            browser = automation.chromium.launch(headless=True)
            try:
                context = browser.new_context(locale="ko-KR", viewport={"width":1280,"height":900})
                for index, uid in enumerate(clip_uids, 1):
                    try:
                        probe = _probe_chzzk_clip_offset_in_context(context, uid,
                            goto_timeout_ms=35000, post_load_settle_ms=4000,
                            anchor_timeout_ms=22000, target_video_no=str(target_video_no),
                            extract_engagement=extract_engagement)
                    except Exception as error:
                        _logger.warning("Clip probe failed: %s", type(error).__name__)
                        probe = {"offset_sec":None, "offset_status":"error", "video_no":None,
                                 "like_count":None, "play_count":None, "engagement_status":"error"}
                    results[uid] = {**probe, "status":probe.get("offset_status", "error")}
                    if on_progress:
                        on_progress(uid,index,len(clip_uids),probe.get("offset_sec"),
                            probe.get("offset_status"),probe.get("video_no"))
            finally:
                browser.close()
    except Exception as error:
        _logger.warning("Public clip browser unavailable: %s", type(error).__name__)
        for uid in clip_uids:
            results.setdefault(uid,{"offset_sec":None,"status":"error",
                "video_no":None,"like_count":None,"play_count":None,
                "engagement_status":"error"})
    return results
