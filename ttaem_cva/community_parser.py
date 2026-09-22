"""Baseline community parsing and ranking, with anonymous local transport."""
from __future__ import annotations
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from .models import CommunityPost
logger=logging.getLogger(__name__)
KST=timezone(timedelta(hours=9))
_DATE_ONLY_RE_SHORT=re.compile(r"^(\d{1,2})[./](\d{1,2})$")
_DATE_ONLY_RE_LONG=re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$")

def _build_search_url(keyword: str, page: int = 1) -> str:
    encoded = quote_plus(keyword)
    return (
        f"https://www.fmkorea.com/search.php"
        f"?mid=ib&category=&search_keyword={encoded}"
        f"&search_target=title_content&page={page}"
    )

def _select_first(element, selectors: list[str]):
    """여러 CSS 셀렉터를 순서대로 시도하여 첫 매칭 반환"""
    for sel in selectors:
        result = element.select_one(sel)
        if result:
            return result
    return None

def _parse_relative_time(text: str) -> Optional[datetime]:
    """
    fmkorea 상대 시간 문자열을 datetime으로 변환.
    예: '5분 전', '2시간 전', '어제 14:30', '2026.04.14 15:00'
    """
    now = datetime.now(KST)
    text = text.strip()

    m = re.match(r"(\d+)분\s*전", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))

    m = re.match(r"(\d+)시간\s*전", text)
    if m:
        return now - timedelta(hours=int(m.group(1)))

    m = re.match(r"(\d+)일\s*전", text)
    if m:
        return now - timedelta(days=int(m.group(1)))

    for fmt in ("%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=KST)
        except ValueError:
            continue

    m = re.match(r"(\d{1,2})[./](\d{1,2})\s+(\d{1,2}):(\d{2})", text)
    if m:
        month, day, hour, minute = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        try:
            candidate = now.replace(month=month, day=day, hour=hour, minute=minute,
                                    second=0, microsecond=0)
        except ValueError:
            return None
        if candidate > now:
            candidate = candidate.replace(year=candidate.year - 1)
        return candidate

    m = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        try:
            candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        except ValueError:
            return None
        if candidate > now:
            candidate -= timedelta(days=1)
        return candidate

    if _date_only_key(text, now) is not None:
        return None

    m = re.match(r"어제\s+(\d{1,2}):(\d{2})", text)
    if m:
        yesterday = now - timedelta(days=1)
        return yesterday.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)

    return None

def _parse_search_results(html: str) -> list[dict]:
    """검색 결과 페이지에서 게시글 목록 파싱.
    fmkorea 검색 결과 구조: table.bd_lst > tbody > tr
    컬럼: 카테고리(td.cate), 제목(td.title), 글쓴이(td.author), 시간(td.time), 조회(td.m_no), 추천
    """
    soup = BeautifulSoup(html, "lxml")
    posts = []

    table = soup.select_one("table.bd_lst")
    if table:
        rows = table.select("tbody tr")
        for row in rows:
            try:
                tds = row.select("td")
                if len(tds) < 5:
                    continue

                title_td = row.select_one("td.title")
                if not title_td:
                    continue

                title_a = title_td.select_one("a.hx, a[href*='document_srl']")
                if not title_a:
                    continue

                title = title_a.get_text(strip=True)
                if not title or len(title) < 2:
                    continue

                href = title_a.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://www.fmkorea.com" + href

                comments = 0
                comment_el = title_td.select_one("a.replyNum, span.rCount")
                if comment_el:
                    m = re.search(r"\d+", comment_el.get_text(strip=True))
                    if m:
                        comments = int(m.group())

                author_td = row.select_one("td.author")
                author = author_td.get_text(strip=True) if author_td else ""

                time_td = row.select_one("td.time")
                raw_timestamp = time_td.get_text(strip=True) if time_td else ""
                parsed_time = _parse_relative_time(raw_timestamp) if raw_timestamp else None

                views = 0
                likes = 0
                m_no_tds = row.select("td.m_no")
                if m_no_tds:
                    m = re.search(r"\d+", m_no_tds[0].get_text(strip=True).replace(",", ""))
                    if m:
                        views = int(m.group())
                voted_td = row.select_one("td.m_no_voted") or (
                    m_no_tds[1] if len(m_no_tds) > 1 else None
                )
                if voted_td is not None:
                    m = re.search(r"\d+", voted_td.get_text(strip=True).replace(",", ""))
                    if m:
                        likes = int(m.group())

                posts.append({
                    "title": title, "url": href, "body_preview": "",
                    "author": author, "timestamp": raw_timestamp,
                    "timestamp_parsed": parsed_time,
                    "views": views, "comments": comments, "likes": likes,
                })
            except Exception as e:
                logger.debug(f"게시글 파싱 오류: {e}")
                continue

        return posts

    links = soup.select("a[href*='document_srl']")
    for link in links:
        title = link.get_text(strip=True)
        href = link.get("href", "")
        if title and len(title) > 5:
            if not href.startswith("http"):
                href = "https://www.fmkorea.com" + href
            posts.append({
                "title": title, "url": href, "body_preview": "",
                "author": "", "timestamp": "", "timestamp_parsed": None,
                "views": 0, "comments": 0, "likes": 0,
            })

    return posts[:30]

def _score_post(p: dict) -> int:
    """게시글 화제도 점수 (높을수록 hot).

    - comments 는 단순 조회보다 적극적인 반응이라 가중치 10
    - likes 는 긍정적 동의 — 댓글보다 약하지만 조회보다 강해서 5
    - views 는 baseline 1
    """
    return (
        int(p.get("views") or 0)
        + int(p.get("comments") or 0) * 10
        + int(p.get("likes") or 0) * 5
    )

def _date_only_key(text: str, reference: Optional[datetime] = None):
    """Return a date for date-only FMKorea labels without inventing HH:MM."""
    raw = (text or "").strip()
    m = _DATE_ONLY_RE_LONG.match(raw)
    if m:
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=KST
            ).date()
        except ValueError:
            return None

    m = _DATE_ONLY_RE_SHORT.match(raw)
    if not m:
        return None
    ref = reference or datetime.now(KST)
    month, day = int(m.group(1)), int(m.group(2))
    try:
        candidate = ref.replace(
            month=month, day=day, hour=0, minute=0, second=0, microsecond=0
        )
    except ValueError:
        return None
    if candidate.date() > ref.date():
        candidate = candidate.replace(year=candidate.year - 1)
    return candidate.date()

def _bin_key(p: dict, broadcast_dt: Optional[datetime]) -> tuple:
    """게시글의 시간 bin 분류.

    반환 tuple 첫 요소가 bin 종류:
      - "unknown": timestamp 파싱 실패
      - "day":     date-only (HH:MM 없음). 한 날짜에 다수 글이 12:00 으로 떨어져
                   hour bin 에 인공 집중되는 아티팩트 회피용. 날짜 단위로 cap.
      - "hour":    HH:MM 까지 정확한 timestamp. broadcast_dt 기준 시간 offset bin.
    """
    pt = p.get("timestamp_parsed")
    if pt is None:
        date_key = _date_only_key(p.get("timestamp") or "", broadcast_dt)
        if date_key is not None:
            return ("day", date_key)
        return ("unknown",)
    ts_raw = (p.get("timestamp") or "").strip()
    date_key = _date_only_key(ts_raw, broadcast_dt or pt)
    if date_key is not None:
        return ("day", date_key)
    if broadcast_dt:
        return ("hour", int((pt - broadcast_dt).total_seconds() // 3600))
    return ("hour", int(pt.timestamp() // 3600))

def _select_top_diverse(
    posts: list[dict],
    max_posts: int,
    broadcast_dt: Optional[datetime],
    per_hour_cap: int = 6,
    per_day_cap: int = 24,
    unknown_cap_ratio: float = 0.25,
) -> list[dict]:
    """점수 내림차순 + 다중 bin cap 으로 분산 선별.

    Args:
        posts: 후보 게시글 dict 리스트
        max_posts: 최종 반환 개수 목표
        broadcast_dt: 방송 시작 시각 (KST). None 이면 wall-clock 시각 기준.
        per_hour_cap: 한 시간(hour bin)에서 base 패스에 채택할 최대 글 수
        per_day_cap: 한 날짜(date-only bin)에서 base 패스에 채택할 최대 글 수.
            HH:MM 모르는 글이 12:00 으로 한 hour bin 에 몰리는 아티팩트를 분리하여
            넓게 펼쳐 잡기 위한 cap. 시간단위 평균(2.5) × 약 10배.
        unknown_cap_ratio: timestamp 파싱 실패 글의 base cap = max_posts × 이 비율

    다중 패스 알고리즘:
        1) base cap 으로 시간 분산 우선 — 모든 bin 에 균등 기회
        2) cap × 2 로 부족분 보충 — pass1 에서 cap 못 채운 hot bin 추가 흡수
        3) cap 무한 (점수순) — max_posts 미달 시 잔여 후보로 마지막 fill

    같은 url 은 한 번만 채택.
    """
    if not posts:
        return []

    scored = sorted(posts, key=_score_post, reverse=True)

    base_caps: dict[str, int] = {
        "hour": per_hour_cap,
        "day": per_day_cap,
        "unknown": max(1, int(max_posts * unknown_cap_ratio)),
    }
    bin_count: dict = {}
    selected_urls: set[str] = set()
    selected: list[dict] = []

    def _try_take(cap_factor: Optional[float]) -> None:
        """cap_factor=None 이면 cap 무한 → 점수순으로만 채움."""
        for p in scored:
            if len(selected) >= max_posts:
                return
            if p["url"] in selected_urls:
                continue
            key = _bin_key(p, broadcast_dt)
            if cap_factor is None:
                cap_limit: float = float("inf")
            else:
                cap_limit = base_caps[key[0]] * cap_factor
            if bin_count.get(key, 0) >= cap_limit:
                continue
            bin_count[key] = bin_count.get(key, 0) + 1
            selected.append(p)
            selected_urls.add(p["url"])

    _try_take(1.0)
    if len(selected) < max_posts:
        _try_take(2.0)
    if len(selected) < max_posts:
        _try_take(None)
    return selected
def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt
    except (ValueError, TypeError):
        return None

def _parse_date_only(raw: str, reference: datetime) -> Optional[date]:
    """Return a date for FMKorea date-only strings without inventing a time."""
    text = (raw or "").strip()
    m = _DATE_ONLY_RE_LONG.match(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    m = _DATE_ONLY_RE_SHORT.match(text)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    try:
        candidate = date(reference.year, month, day)
    except ValueError:
        return None
    if candidate > reference.date():
        try:
            candidate = date(reference.year - 1, month, day)
        except ValueError:
            return None
    return candidate

def filter_posts_by_broadcast_time(
    posts: list[CommunityPost],
    broadcast_start: Optional[str],
    duration_sec: int = 0,
    margin_before_min: int = 30,
    margin_after_min: int = 60,
    unknown_ratio: float = 0.25,
) -> list[CommunityPost]:
    """방송 시간대 내에 작성된 글만 통과시킨다.

    broadcast_start 가 없거나 파싱 실패하면 원본 그대로 반환 (no-op).
    publish_date 가 빈 글은 unknown_ratio 비율까지만 허용한다.
    """
    if not posts:
        return posts
    bs = _parse_iso(broadcast_start) if broadcast_start else None
    if bs is None:
        if broadcast_start:
            logger.warning(
                f"시간 필터 bypass: broadcast_start 파싱 실패 ({broadcast_start!r})"
            )
        return posts

    window_start = bs - timedelta(minutes=margin_before_min)
    window_end = bs + timedelta(seconds=max(duration_sec, 0)) + timedelta(minutes=margin_after_min)

    matched: list[CommunityPost] = []
    date_known: list[CommunityPost] = []
    unknown: list[CommunityPost] = []
    window_dates = {
        window_start.date() + timedelta(days=i)
        for i in range((window_end.date() - window_start.date()).days + 1)
    }

    for p in posts:
        date_only = _parse_date_only(p.timestamp, bs)
        if date_only is not None:
            if date_only in window_dates:
                date_known.append(p)
            continue

        pt = _parse_iso(p.publish_date)
        if pt is None:
            unknown.append(p)
        elif window_start <= pt <= window_end:
            matched.append(p)

    unknown_cap = max(1, int(len(posts) * unknown_ratio))
    result = matched + date_known + unknown[:unknown_cap]

    logger.info(
        f"시간 필터: {len(posts)}개 → {len(result)}개 "
        f"(window {window_start:%H:%M}~{window_end:%H:%M}, "
        f"매칭 {len(matched)}, 날짜만 {len(date_known)}, "
        f"시간미확인 {len(unknown)}→{min(len(unknown), unknown_cap)})"
    )
    return result
def _vod_age_hours(publish_date: str) -> float | None:
    """VOD publish_date(ISO) → 현재까지 경과 시간(시간 단위). 파싱 실패 시 None.

    fmkorea 시간 필터링과 동일 KST 기준으로 비교 (scraper.KST = +09:00).
    """
    if not publish_date:
        return None
    try:
        from datetime import datetime, timedelta, timezone
        kst = timezone(timedelta(hours=9))
        dt = datetime.fromisoformat(publish_date.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=kst)
        delta = datetime.now(kst) - dt
        return delta.total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None

def _should_skip_fmkorea(publish_date: str, max_age_hours: int) -> tuple[bool, str]:
    """B11: VOD 가 max_age_hours 이전이면 fmkorea 스킵 결정.

    반환: (skip?, 이유 메시지). max_age_hours <= 0 이면 항상 (False, "")
    """
    if max_age_hours <= 0:
        return False, ""
    age = _vod_age_hours(publish_date)
    if age is None:
        return False, ""
    if age > max_age_hours:
        return True, f"VOD 가 {age:.1f}시간 전 ({max_age_hours}h 임계 초과)"
    return False, ""
