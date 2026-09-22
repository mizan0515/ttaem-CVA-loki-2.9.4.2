"""Selected unchanged Loki 2.9.4.2 algorithms; local adapters own file access."""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable
from .models import VODInfo, CommunityPost

SCHEMA_VERSION = "streamer_recent_context.v1"

POLICY_DISABLED_SOURCES = {
    "x",
    "twitter",
    "tiktok",
    "instagram",
    "threads",
    "pinterest",
    "polymarket",
    "truthsocial",
    "bluesky",
    "scrapecreators",
    "xai",
    "openrouter",
    "brave",
    "serper",
    "exa",
    "parallel",
    "browser_cookies",
}

DEFAULT_MAX_PROMPT_CHARS = 4500

MAX_SNIPPET_CHARS = 260

MAX_QUERY_CHARS = 120

_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}|\d{2,}")

_WHITESPACE_RE = re.compile(r"\s+")

_SECRET_KEY_RE = re.compile(
    r"(token|secret|cookie|password|passwd|authorization|auth|nid_aut|nid_ses|api[_-]?key)",
    re.IGNORECASE,
)

_STOPWORDS = {
    "방송",
    "다시보기",
    "스트리머",
    "오늘",
    "어제",
    "진짜",
    "완전",
    "그냥",
    "근데",
    "채팅",
    "시청자",
}

@dataclass(frozen=True)
class RecentContextItem:
    source: str
    url: str
    title: str
    snippet: str = ""
    published_at: str = ""
    engagement: dict[str, int] = field(default_factory=dict)
    query: str = ""
    matched_terms: list[str] = field(default_factory=list)
    confidence: str = "low"
    role: str = "public_context"
    privacy_class: str = "public"
    cost_class: str = "free_public"
    score: float = 0.0
    match_proof: bool = False

def normalize_aliases(vod_info: VODInfo, cfg: dict | None = None) -> list[str]:
    cfg = cfg or {}
    aliases: list[str] = []
    for value in cfg.get("fmkorea_search_keywords") or []:
        if isinstance(value, str):
            aliases.append(value)
    for key in ("streamer_name",):
        value = cfg.get(key)
        if isinstance(value, str):
            aliases.append(value)
    aliases.extend([vod_info.channel_name or "", vod_info.title or ""])

    normalized: list[str] = []
    for value in aliases:
        value = _clean_text(value, max_chars=MAX_QUERY_CHARS)
        if value and value not in normalized:
            normalized.append(value)
    return normalized

def normalize_item(payload: dict[str, Any]) -> RecentContextItem | None:
    """Return a safe item projection, dropping secret-like raw keys."""
    if not isinstance(payload, dict):
        return None
    safe = {k: v for k, v in payload.items() if not _SECRET_KEY_RE.search(str(k))}
    source = _safe_source(safe.get("source") or "")
    title = _clean_text(safe.get("title") or "", max_chars=180)
    url = _clean_url(safe.get("url") or "")
    if not source or not title:
        return None

    engagement = _normalize_engagement(safe.get("engagement") or {})
    matched_terms = _normalize_terms(safe.get("matched_terms") or [])
    role = _safe_role(safe.get("role") or "public_context")
    item = RecentContextItem(
        source=source,
        url=url,
        title=title,
        snippet=_clean_text(safe.get("snippet") or "", max_chars=MAX_SNIPPET_CHARS),
        published_at=_clean_text(safe.get("published_at") or "", max_chars=64),
        engagement=engagement,
        query=_clean_text(safe.get("query") or "", max_chars=MAX_QUERY_CHARS),
        matched_terms=matched_terms,
        confidence=_safe_confidence(safe.get("confidence") or "low"),
        role=role,
        privacy_class="public",
        cost_class="free_public",
        score=float(safe.get("score") or 0.0),
        match_proof=bool(safe.get("match_proof")) if role != "context_hint" else False,
    )
    if item.privacy_class != "public" or item.cost_class != "free_public":
        return None
    return item

def format_recent_context_for_prompt(context: dict[str, Any] | None, max_chars: int = DEFAULT_MAX_PROMPT_CHARS) -> str:
    if not isinstance(context, dict):
        return ""

    items = [item for item in context.get("items") or [] if isinstance(item, dict)]
    diagnostics = [d for d in context.get("diagnostics") or [] if isinstance(d, dict)]
    if not items and not diagnostics:
        return ""

    lines = [
        "## 최근 30일 공개 맥락 (한국 스트리머 파일럿)",
        "사용 규칙:",
        "- 이 블록은 공개 웹/커뮤니티에서 얻은 낮은 신뢰도 배경 힌트다.",
        "- 방송 장면, 발언, 사건을 증명하지 않는다. `match_proof=false`로 취급한다.",
        "- 고유명사, 별명, 최근 화제, 밈 후보를 이해하는 보조 맥락으로만 사용한다.",
        "- 자막/채팅/관리자 맥락/시청자 클립 근거 없이 새 장면을 만들지 않는다.",
        "",
        "### 수집 상태",
    ]
    if diagnostics:
        for diag in diagnostics[:8]:
            lines.append(
                f"- {diag.get('source', 'unknown')}: {diag.get('status', 'unknown')}"
            )
    else:
        lines.append("- 진단 없음")

    if items:
        lines.extend(["", "### 공개 맥락 후보"])
        for item in items[:12]:
            source = item.get("source") or "unknown"
            title = _clean_text(item.get("title") or "", max_chars=120)
            role = item.get("role") or "public_context"
            confidence = item.get("confidence") or "low"
            score = item.get("score", 0)
            url = item.get("url") or ""
            snippet = _clean_text(item.get("snippet") or "", max_chars=180)
            terms = ", ".join(item.get("matched_terms") or [])
            lines.append(
                f"- [{source}] {title} (role={role}, confidence={confidence}, score={score}, match_proof=false)"
            )
            if terms:
                lines.append(f"  - matched_terms: {terms}")
            if snippet:
                lines.append(f"  - snippet: {snippet}")
            if url:
                lines.append(f"  - url: {url}")
    else:
        lines.extend(["", "### 공개 맥락 후보", "(데이터 없음)"])

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n... (최근 공개 맥락 일부 생략)"
    return text

def _items_from_fmkorea(posts: list[CommunityPost], aliases: list[str]) -> list[RecentContextItem]:
    out: list[RecentContextItem] = []
    for post in posts:
        terms = _matched_terms(f"{post.title} {post.body_preview}", aliases)
        if not terms:
            continue
        payload = {
            "source": "fmkorea",
            "url": post.url,
            "title": post.title,
            "snippet": post.body_preview,
            "published_at": post.publish_date or post.timestamp,
            "engagement": {
                "views": int(post.views or 0),
                "comments": int(post.comments or 0),
                "likes": int(post.likes or 0),
            },
            "query": ", ".join(aliases[:3]),
            "matched_terms": terms,
            "confidence": "medium" if terms else "low",
            "role": "community_reaction",
            "match_proof": False,
        }
        item = normalize_item(payload)
        if item:
            out.append(item)
    return out

def _items_from_chzzk_public(vod_info: VODInfo, aliases: list[str]) -> list[RecentContextItem]:
    title = vod_info.title or ""
    url = f"https://chzzk.naver.com/video/{vod_info.video_no}" if vod_info.video_no else ""
    item = normalize_item(
        {
            "source": "chzzk_public",
            "url": url,
            "title": title or f"{vod_info.channel_name} VOD",
            "snippet": f"{vod_info.channel_name} 공개 VOD metadata. category={vod_info.category or 'unknown'}",
            "published_at": vod_info.publish_date,
            "engagement": {},
            "query": ", ".join(aliases[:3]),
            "matched_terms": _matched_terms(f"{vod_info.channel_name} {title}", aliases),
            "confidence": "medium",
            "role": "public_metadata",
            "match_proof": False,
        }
    )
    return [item] if item else []

def _dedupe_score_and_cap(
    items: list[RecentContextItem],
    *,
    aliases: list[str],
    max_items: int,
    max_per_source: int,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[RecentContextItem] = []
    for item in items:
        key = _dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    scored = [
        _replace_score(item, _score_item(item, aliases))
        for item in unique
    ]
    scored.sort(key=lambda item: (-item.score, item.source, item.title))

    counts: dict[str, int] = {}
    capped: list[dict[str, Any]] = []
    for item in scored:
        count = counts.get(item.source, 0)
        if count >= max_per_source:
            continue
        capped.append(asdict(item))
        counts[item.source] = count + 1
        if len(capped) >= max_items:
            break
    return capped

def _result(
    *,
    vod_info: VODInfo,
    generated_at: str,
    lookback_days: int,
    include_sources: list[str],
    aliases: list[str],
    items: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    statuses = {str(d.get("status") or "") for d in diagnostics}
    status = "present" if items else "missing_optional"
    if "not_configured" in statuses:
        status = "not_configured"
    elif items and any(s in statuses for s in {"failed_open", "no_results", "unknown_source", "unverified_source"}):
        status = "partial"
    elif not items and diagnostics:
        status = "missing_optional"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "quality_claim": "not_evaluated",
        "generated_at": generated_at,
        "video_no": str(vod_info.video_no or ""),
        "channel_id": str(vod_info.channel_id or ""),
        "channel_name": str(vod_info.channel_name or ""),
        "lookback_days": lookback_days,
        "include_sources": include_sources,
        "aliases": aliases[:8],
        "item_count": len(items),
        "items": items,
        "diagnostics": diagnostics,
        "policy": {
            "privacy": "public_only",
            "cost": "free_public_only",
            "match_proof": False,
            "excluded": sorted(POLICY_DISABLED_SOURCES),
        },
    }

def _diag(source: str, status: str, reason: str) -> dict[str, str]:
    return {"source": source, "status": status, "reason": reason}

def _replace_score(item: RecentContextItem, score: float) -> RecentContextItem:
    return RecentContextItem(**{**asdict(item), "score": round(score, 3)})

def _score_item(item: RecentContextItem, aliases: list[str]) -> float:
    engagement = item.engagement or {}
    views = int(engagement.get("views") or 0)
    comments = int(engagement.get("comments") or 0)
    likes = int(engagement.get("likes") or 0)
    engagement_score = math.log1p(views) * 0.5 + comments * 3 + likes * 8
    matched = len(item.matched_terms or [])
    source_weight = {
        "fmkorea": 8.0,
        "chzzk_public": 4.0,
        "namuwiki": 2.0,
    }.get(item.source, 1.0)
    text = f"{item.title} {item.snippet}"
    query_bonus = len(_matched_terms(text, aliases)) * 2
    role_penalty = -2.0 if item.role == "context_hint" else 0.0
    return source_weight + engagement_score + matched * 4 + query_bonus + role_penalty

def _dedupe_key(item: RecentContextItem) -> str:
    if item.url:
        return f"url:{item.url.lower().rstrip('/')}"
    title = re.sub(r"\W+", "", item.title.lower())
    date = (item.published_at or "")[:10]
    return f"title:{item.source}:{title}:{date}"

def _matched_terms(text: str, aliases: list[str]) -> list[str]:
    lowered = (text or "").lower()
    terms: list[str] = []
    for alias in aliases:
        alias = alias.strip()
        if not alias:
            continue
        if alias.lower() in lowered and alias not in terms:
            terms.append(alias)
    for token in _TOKEN_RE.findall(text or ""):
        if token not in _STOPWORDS and token not in terms and any(token in a or a in token for a in aliases):
            terms.append(token)
    return terms[:8]

def _normalize_terms(value: object) -> list[str]:
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, Iterable):
        raw = list(value)
    else:
        raw = []
    out: list[str] = []
    for term in raw:
        cleaned = _clean_text(term, max_chars=60)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out[:8]

def _normalize_engagement(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key in ("views", "comments", "likes", "recommendations", "plays"):
        try:
            number = int(value.get(key) or 0)
        except (TypeError, ValueError):
            number = 0
        if number > 0:
            out[key] = number
    return out

def _clean_text(value: object, *, max_chars: int) -> str:
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    text = text.replace("\x00", "")
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text

def _clean_url(value: object) -> str:
    url = str(value or "").strip()
    if not url.startswith(("http://", "https://")):
        return ""
    return url[:500]

def _safe_source(value: object) -> str:
    return re.sub(r"[^a-z0-9_]", "_", str(value or "").lower()).strip("_")[:48]

def _safe_role(value: object) -> str:
    role = _safe_source(value)
    return role if role in {"public_context", "community_reaction", "public_metadata", "context_hint"} else "public_context"

def _safe_confidence(value: object) -> str:
    confidence = str(value or "").lower()
    return confidence if confidence in {"low", "medium", "high"} else "low"
