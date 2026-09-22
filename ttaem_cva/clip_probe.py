"""Original clip DOM provenance checks; no profile or credential handling."""
from __future__ import annotations
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlparse, parse_qs
_logger=logging.getLogger(__name__)

_logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

def parse_chzzk_datetime(value) -> datetime | None:
    """CHZZK API date text to an aware UTC datetime.

    CHZZK responses have used both ISO-8601 offsets and naive KST text.  A
    missing or malformed value is not guessed because clip-window filtering
    must fail closed instead of admitting an unrelated clip.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_KST)
    return parsed.astimezone(timezone.utc)

_CHZZK_CLIP_UNAVAILABLE_MARKERS = (
    "삭제된", "비공개", "찾을 수 없", "존재하지 않", "권한이 없", "차단된",
)

def _parse_chzzk_offset_from_href(href: str) -> Optional[int]:
    """href 의 currentTime 쿼리 파라미터를 정수로. 비-/video/ 또는 누락 시 None."""
    if not href:
        return None
    try:
        u = urlparse(href)
        if "/video/" not in (u.path or ""):
            return None
        qs = parse_qs(u.query or "")
        if "currentTime" not in qs:
            return None
        return int(qs["currentTime"][0])
    except (ValueError, KeyError, IndexError):
        return None

_ENGAGEMENT_LIKE_SELECTORS: tuple = (
    '[data-like-action="count"]',
    '[data-like-action] [class*="count"]',
    'button[aria-label*="좋아요"] [class*="count"]',
)

_ENGAGEMENT_PLAY_SELECTORS: tuple = (
    '[class*="DescriptionAreaView-module__sub"]',
    '[class*="play_count"]',
    '[class*="PlayCount"]',
)

_ENGAGEMENT_PLAY_TEXT_ANCHOR_RE = re.compile(r"재생수\s*([0-9.,KkMmBb만억천]+)")

_ENGAGEMENT_LIKE_LABEL_ZERO_RE = re.compile(r"^(좋아요|いいね|Like|👍)\s*$", re.IGNORECASE)

_ENGAGEMENT_NUMBER_UNITS_KR = (("억", 100_000_000), ("만", 10_000), ("천", 1_000))

_ENGAGEMENT_NUMBER_UNITS_EN = (
    ("B", 1_000_000_000), ("b", 1_000_000_000),
    ("M", 1_000_000),     ("m", 1_000_000),
    ("K", 1_000),         ("k", 1_000),
)

_ENGAGEMENT_DIGITS_HEAD_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)")

def _parse_engagement_korean_number(s: str) -> Optional[int]:
    """raw text → int. 지원: '1234'/'1,234'/'1.2만'/'12.3K'. 실패 → None.

    클립 페이지에 표시되는 한국어·영문 축약 숫자를 해석한다.
    """
    if not s:
        return None
    raw = s.strip()
    if not raw:
        return None
    for unit, mult in _ENGAGEMENT_NUMBER_UNITS_KR:
        if unit in raw:
            head_m = _ENGAGEMENT_DIGITS_HEAD_RE.search(raw.split(unit)[0])
            if head_m:
                head = head_m.group(1).replace(",", "")
                try:
                    return int(round(float(head) * mult))
                except ValueError:
                    return None
            return None
    raw_compact = raw.replace(" ", "")
    for unit, mult in _ENGAGEMENT_NUMBER_UNITS_EN:
        if raw_compact.endswith(unit):
            head = raw_compact[:-len(unit)].replace(",", "")
            head_m = _ENGAGEMENT_DIGITS_HEAD_RE.match(head)
            if head_m:
                try:
                    return int(round(float(head_m.group(1)) * mult))
                except ValueError:
                    return None
            return None
    cleaned = raw.replace(",", "").replace(" ", "")
    if re.match(r"^\d+(?:\.\d+)?$", cleaned):
        try:
            return int(round(float(cleaned)))
        except ValueError:
            return None
    return None

def _scan_engagement_selectors(page, selectors: tuple) -> list:
    """frames 순회 → 셀렉터 매치 raw text 수집 (text 비어있는 element 제외)."""
    out: list = []
    for frame in (page.frames or [page.main_frame]):
        for sel in selectors:
            try:
                items = frame.eval_on_selector_all(
                    sel,
                    "els => els.map(e => ({text: (e.textContent || '').trim()}))"
                    ".filter(o => o.text.length > 0)",
                ) or []
            except Exception:
                continue
            for item in items:
                out.append({"text": item.get("text") or "", "selector": sel})
    return out

def _scan_engagement_play_text_anchor(page) -> list:
    """모든 frame body innerText 에서 '재생수 N' anchor 추출."""
    out: list = []
    for frame in (page.frames or [page.main_frame]):
        try:
            body_text = frame.evaluate(
                "() => (document.body && document.body.innerText) "
                "? document.body.innerText : ''"
            ) or ""
        except Exception:
            continue
        for m in _ENGAGEMENT_PLAY_TEXT_ANCHOR_RE.finditer(body_text):
            out.append({"text": m.group(1), "selector": "<text:재생수>"})
    return out

def _pick_engagement_first_hit_int(
    hits: list,
    anchor_re: Optional[re.Pattern] = None,
    label_to_zero_re: Optional[re.Pattern] = None,
) -> Optional[int]:
    """hits[0] 만 사용 (contamination-safe — hits[1+] 추천 영역 위험).

    play의 hits[1+]는 추천 영역 숫자일 수 있으므로 fallback하지 않는다.
    like가 hits[0]='좋아요' 라벨이면 likes=0으로 매핑한다.
    """
    if not hits:
        return None
    text = (hits[0].get("text") or "").strip()
    if anchor_re is not None:
        m = anchor_re.search(text)
        if m:
            n = _parse_engagement_korean_number(m.group(1))
            if n is not None:
                return n
    n = _parse_engagement_korean_number(text)
    if n is not None:
        return n
    if label_to_zero_re is not None and label_to_zero_re.search(text):
        return 0
    return None

def _scan_frames_for_offset_anchor_candidates(page) -> list[dict]:
    """page.frames 모두 순회 → /video/ AND currentTime= anchor 후보 전체 반환.

    SourceArea 컴포넌트가 m.naver.com/shorts cross-origin iframe 안에 있으므로
    main frame 단독 셀렉터는 false negative를 낼 수 있다.

    반환: dict list — href, offset_sec, video_no, frame_index, frame_url.
    """
    candidates: list[dict] = []
    for frame_index, frame in enumerate(page.frames or [page.main_frame]):
        try:
            hrefs = frame.eval_on_selector_all(
                'a[href*="/video/"][href*="currentTime="]',
                """
                els => els.map(e => {
                  const r = e.getBoundingClientRect();
                  const style = window.getComputedStyle(e);
                  const opacity = Number.parseFloat(style.opacity || '1');
                  const isVisible =
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    opacity > 0 &&
                    r.width > 0 &&
                    r.height > 0;
                  const isInViewport =
                    r.bottom > 0 &&
                    r.right > 0 &&
                    r.top < window.innerHeight &&
                    r.left < window.innerWidth;
                  return {
                    href: e.getAttribute('href'),
                    is_visible: isVisible,
                    is_in_viewport: isInViewport,
                    rect_top: r.top,
                    rect_bottom: r.bottom
                  };
                })
                """,
            ) or []
        except Exception:
            continue
        for item in hrefs:
            if isinstance(item, dict):
                href = item.get("href")
            else:
                href = item
            if not href:
                continue
            offset = _parse_chzzk_offset_from_href(href)
            if offset is None:
                continue
            m = re.search(r"/video/(\d+)", urlparse(href).path or "")
            video_no = m.group(1) if m else None
            candidates.append({
                "href": href,
                "offset_sec": offset,
                "video_no": video_no,
                "frame_index": frame_index,
                "frame_url": getattr(frame, "url", None),
                "is_visible": item.get("is_visible") if isinstance(item, dict) else None,
                "is_in_viewport": item.get("is_in_viewport") if isinstance(item, dict) else None,
                "rect_top": item.get("rect_top") if isinstance(item, dict) else None,
                "rect_bottom": item.get("rect_bottom") if isinstance(item, dict) else None,
            })
    return candidates

def _select_offset_anchor_candidate(
    candidates: list[dict],
    target_video_no: Optional[str] = None,
) -> Optional[dict]:
    """현재 보이는 SourceArea 후보를 선택한다.

    CHZZK shorts iframe can keep adjacent recommendation cards mounted. Those
    cards may include a target VOD link that is not the current clip's source, so
    ``target_video_no`` is only used later for validation, not for selection.
    """
    if not candidates:
        return None
    visible = [
        candidate for candidate in candidates
        if candidate.get("is_visible") is True
        and candidate.get("is_in_viewport") is True
    ]
    if visible:
        return visible[0]
    return candidates[0]

def _anchor_candidate_video_nos(candidates: list[dict]) -> list[str]:
    """로그용 unique video_no 리스트. 순서 보존."""
    seen: set[str] = set()
    out: list[str] = []
    for candidate in candidates:
        video_no = candidate.get("video_no")
        if not video_no:
            continue
        key = str(video_no)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out

def _probe_chzzk_clip_offset_in_context(
    ctx,
    clip_uid: str,
    *,
    goto_timeout_ms: int,
    post_load_settle_ms: int,
    anchor_timeout_ms: int,
    target_video_no: Optional[str] = None,
    poll_interval_ms: int = 350,
    extract_engagement: bool = False,
) -> dict:
    """단일 클립 페이지 probe — caller-managed BrowserContext 재사용.

    anchor는 iframe 안에 있으므로 모든 frame을 poll하고 mount되면 즉시 반환한다.

    클립이 다른 VOD의 anchor를 가질 수 있다. target_video_no가 주어지면
    anchor의 video_no가 일치할 때만 status="ok", 다르면 "wrong_vod"로 표기한다.

    unavailable 텍스트는 frames-poll이 anchor를 회수하지 못한 경우에만 검사한다.

    extract_engagement=True이면 같은 페이지에서 likes/plays DOM을 추출한다.
    offset poll loop와 같은 deadline을 사용한다.

    반환 dict:
      offset_sec: int|None
      offset_status: str — ok/wrong_vod/no_anchor/unavailable/error
      video_no: str|None
      like_count: int|None             (extract_engagement=True 일 때만 유효)
      play_count: int|None             (extract_engagement=True 일 때만 유효)
      engagement_status: str           — ok/parse_failure/error/disabled
    """
    page = ctx.new_page()
    out: dict = {
        "offset_sec": None,
        "offset_status": "error",
        "video_no": None,
        "anchor_candidate_count": 0,
        "anchor_candidate_video_nos": [],
        "like_count": None,
        "play_count": None,
        "engagement_status": "disabled" if not extract_engagement else "pending",
    }
    try:
        try:
            page.goto(
                f"https://chzzk.naver.com/clips/{clip_uid}",
                wait_until="load",
                timeout=goto_timeout_ms,
            )
        except Exception as e:
            _logger.warning(
                f"chzzk clip probe goto 실패 ({clip_uid}, {type(e).__name__})"
            )
            out["offset_status"] = "error"
            if extract_engagement:
                out["engagement_status"] = "error"
            return out

        try:
            page.wait_for_timeout(post_load_settle_ms)
        except Exception:
            pass

        deadline = time.monotonic() + (anchor_timeout_ms / 1000.0)
        offset = None
        video_no = None
        anchor_candidates: list[dict] = []
        selected_anchor: Optional[dict] = None
        first_wrong_anchor_seen_at: Optional[float] = None
        like_hits: list = []
        play_hits: list = []
        while True:
            now = time.monotonic()
            if offset is None:
                anchor_candidates = _scan_frames_for_offset_anchor_candidates(page)
                if anchor_candidates:
                    out["anchor_candidate_count"] = len(anchor_candidates)
                    out["anchor_candidate_video_nos"] = _anchor_candidate_video_nos(
                        anchor_candidates
                    )
                    selected_anchor = _select_offset_anchor_candidate(
                        anchor_candidates,
                        target_video_no=target_video_no,
                    )
                    selected_video_no = (
                        selected_anchor.get("video_no") if selected_anchor else None
                    )
                    selected_offset = (
                        selected_anchor.get("offset_sec") if selected_anchor else None
                    )
                    target_matched = (
                        target_video_no
                        and selected_video_no
                        and str(selected_video_no) == str(target_video_no)
                    )
                    if (
                        target_video_no
                        and selected_video_no
                        and not target_matched
                    ):
                        if first_wrong_anchor_seen_at is None:
                            first_wrong_anchor_seen_at = now
                        wrong_anchor_grace_s = min(
                            2.0, max(0.0, anchor_timeout_ms / 1000.0)
                        )
                        if now - first_wrong_anchor_seen_at >= wrong_anchor_grace_s:
                            offset = selected_offset
                            video_no = selected_video_no
                    else:
                        offset = selected_offset
                        video_no = selected_video_no
            if extract_engagement and not (like_hits and play_hits):
                if not like_hits:
                    like_hits = _scan_engagement_selectors(page, _ENGAGEMENT_LIKE_SELECTORS)
                if not play_hits:
                    play_hits = _scan_engagement_selectors(page, _ENGAGEMENT_PLAY_SELECTORS)
                    if not play_hits:
                        play_hits = _scan_engagement_play_text_anchor(page)
            offset_done = offset is not None
            engagement_done = (not extract_engagement) or (like_hits and play_hits)
            if offset_done and engagement_done:
                break
            if time.monotonic() >= deadline:
                break
            try:
                page.wait_for_timeout(poll_interval_ms)
            except Exception:
                break

        if offset is not None:
            if target_video_no and video_no and str(video_no) != str(target_video_no):
                out["offset_status"] = "wrong_vod"
                out["video_no"] = video_no
                _logger.warning(
                    "chzzk clip anchor target mismatch "
                    f"(clip={clip_uid}, target={target_video_no}, selected={video_no}, "
                    f"candidates={out['anchor_candidate_video_nos']}, "
                    f"count={out['anchor_candidate_count']})"
                )
            else:
                out["offset_sec"] = offset
                out["offset_status"] = "ok"
                out["video_no"] = video_no
                if (
                    target_video_no
                    and anchor_candidates
                    and len(out["anchor_candidate_video_nos"]) > 1
                ):
                    _logger.info(
                        "chzzk clip anchor target selected among candidates "
                        f"(clip={clip_uid}, target={target_video_no}, "
                        f"candidates={out['anchor_candidate_video_nos']}, "
                        f"count={out['anchor_candidate_count']})"
                    )
        else:
            unavailable = False
            try:
                body_text = page.evaluate(
                    "() => (document.body && document.body.innerText) "
                    "? document.body.innerText.slice(0, 500) : ''"
                ) or ""
                if any(m in body_text for m in _CHZZK_CLIP_UNAVAILABLE_MARKERS):
                    unavailable = True
            except Exception:
                pass
            out["offset_status"] = "unavailable" if unavailable else "no_anchor"
            if target_video_no and anchor_candidates:
                _logger.warning(
                    "chzzk clip anchor candidates never matched target "
                    f"(clip={clip_uid}, target={target_video_no}, "
                    f"candidates={out['anchor_candidate_video_nos']}, "
                    f"count={out['anchor_candidate_count']}, "
                    f"status={out['offset_status']})"
                )

        if extract_engagement:
            if out["offset_status"] == "wrong_vod":
                out["like_count"] = None
                out["play_count"] = None
                out["engagement_status"] = "wrong_vod"
            else:
                like_n = _pick_engagement_first_hit_int(
                    like_hits, label_to_zero_re=_ENGAGEMENT_LIKE_LABEL_ZERO_RE
                )
                play_n = _pick_engagement_first_hit_int(
                    play_hits, anchor_re=_ENGAGEMENT_PLAY_TEXT_ANCHOR_RE
                )
                out["like_count"] = like_n
                out["play_count"] = play_n
                if like_n is not None and play_n is not None:
                    out["engagement_status"] = "ok"
                else:
                    out["engagement_status"] = "parse_failure"

        return out
    finally:
        try:
            page.close()
        except Exception:
            pass
