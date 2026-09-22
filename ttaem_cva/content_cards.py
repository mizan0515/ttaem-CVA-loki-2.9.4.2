"""Baseline approved naming-card matcher; optional user-prepared local input."""
from __future__ import annotations
import re
import unicodedata
from typing import Any,Iterable
from .local_files import plain_path,read_json

FORBIDDEN_STRUCTURE_KEYS = {
    "video_no", "vod_id", "d1", "d2", "point", "timeline", "summary",
    "highlight", "winner", "result", "sequence", "order", "start", "end",
    "correction", "manager_correction", "manager_correction_outline",
    "broadcast_map", "outline", "truth", "private", "private_info", "secret",
    "password", "token", "credential", "api_key",
}

def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]

def _terms(value: Any, *, limit: int, item_limit: int = 80) -> list[str]:
    rows = value if isinstance(value, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for raw in rows:
        item = _text(raw, item_limit)
        key = normalize_name(item)
        if item and key and key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= limit:
            break
    return out

def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"\s+", " ", text)

def normalize_content_payload(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    forbidden = sorted(_find_forbidden_keys(data))
    if forbidden:
        raise ValueError("특정 방송의 목차·결과는 콘텐츠 정보 카드에 저장할 수 없습니다.")
    canonical_name = _text(data.get("canonical_name"), 120)
    if not canonical_name:
        raise ValueError("정식 이름을 입력해 주세요.")
    aliases = _terms(data.get("aliases"), limit=30, item_limit=120)
    canonical_key = normalize_name(canonical_name)
    aliases = [row for row in aliases if normalize_name(row) != canonical_key]
    sources: list[dict[str, str]] = []
    for raw in data.get("public_sources") or []:
        if not isinstance(raw, dict):
            continue
        label = _text(raw.get("label"), 120)
        url = _text(raw.get("url"), 500)
        if url and not re.match(r"^https?://", url, flags=re.IGNORECASE):
            raise ValueError("정보 출처 링크는 http 또는 https 주소만 사용할 수 있습니다.")
        if label or url:
            sources.append({"label": label, "url": url})
        if len(sources) >= 10:
            break
    verified_at = _text(data.get("verified_at"), 10)
    if verified_at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", verified_at):
        raise ValueError("정보 확인 날짜는 YYYY-MM-DD 형식이어야 합니다.")
    return {
        "canonical_name": canonical_name,
        "aliases": aliases,
        "content_type": _text(data.get("content_type"), 80),
        "description": _text(data.get("description"), 500),
        "repeat_units": _terms(data.get("repeat_units"), limit=20),
        "common_terms": _terms(data.get("common_terms"), limit=40),
        "public_sources": sources,
        "verified_at": verified_at,
        "manager_note": _text(data.get("manager_note"), 1000),
    }

def _find_forbidden_keys(value: Any) -> set[str]:
    """Reject forbidden answer/private fields even when hidden in nested payloads."""
    found: set[str] = set()
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = normalize_name(raw_key).replace(" ", "_")
            if key in FORBIDDEN_STRUCTURE_KEYS and child not in (None, "", [], {}):
                found.add(key)
            found.update(_find_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_find_forbidden_keys(child))
    return found

def _contains_exact(normalized_corpus: str, phrase: Any) -> bool:
    key = normalize_name(phrase)
    if len(key) < 2:
        return False
    pattern = r"(?<![0-9a-z가-힣])" + re.escape(key).replace(r"\ ", r"\s+") + r"(?![0-9a-z가-힣])"
    return re.search(pattern, normalized_corpus, flags=re.IGNORECASE) is not None

def match_approved(cards, evidence_texts: Iterable[str], *, limit: int = 6) -> list[dict[str, Any]]:
    corpus = normalize_name("\n".join(str(row or "") for row in evidence_texts))
    matches: list[dict[str, Any]] = []
    if not corpus:
        return matches
    for card in cards:
        if card.get("status") != "approved" or not isinstance(card.get("approved"), dict):
            continue
        approved = card["approved"]
        matched = next(
            (name for name in [approved.get("canonical_name"), *(approved.get("aliases") or [])] if _contains_exact(corpus, name)),
            "",
        )
        if not matched:
            continue
        matches.append({
            "card_id": card["id"],
            "canonical_name": approved["canonical_name"],
            "matched_term": matched,
            "type": approved.get("content_type") or "",
            "repeat_units": list(approved.get("repeat_units") or [])[:4],
            "common_terms": list(approved.get("common_terms") or [])[:8],
            "reason": f"방송 원문에서 승인 이름 또는 별칭 `{matched}` 확인",
        })
        if len(matches) >= max(0, int(limit)):
            break
    return matches

def load_approved_content(run,evidence_texts):
    path=plain_path(run/'approved-content.json')
    if not path.is_file():return []
    data=read_json(path)
    cards=data.get('cards',[])
    if not isinstance(cards,list) or len(cards)>100:raise ValueError('Too many naming cards')
    normalized=[]
    for card in cards:
        if card.get('status')!='approved':continue
        card_id=str(card.get('id') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',card_id):raise ValueError('Invalid naming-card ID')
        normalized.append({'id':card_id,'status':'approved','approved':normalize_content_payload(card.get('approved'))})
    return match_approved(normalized,evidence_texts)
