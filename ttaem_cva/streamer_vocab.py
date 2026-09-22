"""Selected unchanged Loki 2.9.4.2 algorithms; local adapters own file access."""

from __future__ import annotations

import re
import hashlib
import json
from datetime import date, datetime
from typing import Any

CONTEXT_ALIAS_SCHEMA_VERSION = "streamer_vocab_context_alias.v1"

VOCAB_ACTIVATION_MODES = {"always", "context"}

_ALLOWED_CONTEXTS = {
    "exact_phrase",
    "game_role",
    "role_noun",
}

_FORBIDDEN_CONTEXTS = {
    "action_support",
    "ambiguous",
    "quoted_speech",
}

_APPLICABLE_SURFACES = {
    "title",
    "summary",
    "timeline",
    "hashtag",
    "quoted_speech",
}

_PROMPT_LIKE_RE = re.compile(
    r"(?i)(ignore\s+(all\s+)?previous|system\s+prompt|developer\s+message|"
    r"assistant\s*:|사용자\s*지시|이전\s*지시|명령을\s*따|```|<script|</script)"
)

_CODE_LIKE_VOCAB_RE = re.compile(
    r"(?ix)(?:"
    r"(?:^|\s)(?:async\s+def|def|class|function|import|from|return|lambda|await|"
    r"const|let|var)\b|"
    r"\b[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+\b|"
    r"\b[A-Za-z_][A-Za-z0-9_]{1,}(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b|"
    r"\b[A-Za-z_][A-Za-z0-9_]*\s*\([^\r\n)]*\)|"
    r"\b[A-Za-z0-9_.-]+\.(?:py|pyw|js|jsx|ts|tsx|ps1|bat|cmd|sh|java|cs|cpp|"
    r"go|rs)\b|"
    r"(?:^|\s)(?:[A-Za-z]:[\\/]|\.{1,2}[\\/])\S+|"
    r"</?[A-Za-z][^>\r\n]*>|=>|::|:=|[{};]"
    r")"
)

_ROLE_FOLLOW_RE = re.compile(
    r"^\s*(?:1\s*티어|티어|유저|포지션|챔피언|캐릭터|픽|장인|메인|역할)(?:\b|$)"
)

_ACTION_FOLLOW_RE = re.compile(
    r"^\s*(?:하(?:다|는|고|면|자|세요)|한(?:다|다고|다면)|했|해(?:\s*준|\s*주|요|라|서|도|야)?|"
    r"받|해준|해주)"
)

_ACTION_PREFIX_RE = re.compile(r"(?:팀|방송|선수|친구|동료|시청자)(?:을|를)\s*$")

_QUOTED_SPAN_RE = re.compile(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’')

_FEEDBACK_EVENT_TYPES = {
    "registered",
    "hidden",
    "demoted",
    "delete_suggested",
    "restored",
}

def _safe_channel_id(channel_id: str) -> str:
    """파일명 안전성 — channel_id 가 이상값이어도 잘못된 파일경로를 만들지 않게."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-]", "_", channel_id or "")
    return cleaned or "unknown"

def empty_vocab(channel_id: str = "", channel_name: str = "") -> dict:
    return {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "whitelist": [],
        "blacklist": [],
        "context_aliases": [],
        "feedback": {"candidates": {}, "terms": {}},
        "updated_at": "",
    }

def _empty_activation() -> dict:
    return {
        "mode": "always",
        "keywords": [],
        "active_from": "",
        "active_until": "",
    }

def _normalize(data: Any, channel_id: str) -> dict:
    """입력 dict 를 안전한 스키마로 정규화.

    - 잘못된 타입 / 빈 문자열 / 중복 alias 제거
    - term/alias 는 strip + 중복 제거 (term 자체와 동일한 alias 는 제외)
    """
    if not isinstance(data, dict):
        data = {}
    out = empty_vocab(channel_id)
    out["channel_id"] = data.get("channel_id") or channel_id
    out["channel_name"] = (data.get("channel_name") or "").strip()
    out["updated_at"] = data.get("updated_at") or ""
    out["feedback"] = _normalize_feedback(data.get("feedback") or {})

    seen_terms: set[str] = set()
    for entry in data.get("whitelist") or []:
        if not isinstance(entry, dict):
            continue
        term = _safe_vocab_phrase(entry.get("term"))
        if not term or term in seen_terms:
            continue
        seen_terms.add(term)
        aliases_raw = entry.get("aliases") or []
        aliases: list[str] = []
        seen_alias: set[str] = {term}
        for a in aliases_raw:
            if not isinstance(a, str):
                continue
            a = _safe_vocab_phrase(a)
            if a and a not in seen_alias:
                seen_alias.add(a)
                aliases.append(a)
        note = _bounded_text(entry.get("note"), 240)
        activation = _normalize_activation(entry.get("activation"))
        out["whitelist"].append({
            "term": term,
            "aliases": aliases,
            "note": note,
            "activation": activation,
        })

    seen_bl: set[str] = set()
    for b in data.get("blacklist") or []:
        b = _safe_vocab_phrase(b)
        if b and b not in seen_bl:
            seen_bl.add(b)
            out["blacklist"].append(b)

    seen_alias_rules: set[str] = set()
    for raw_rule in data.get("context_aliases") or []:
        rule = _normalize_context_alias(raw_rule, out["channel_id"] or channel_id)
        if not rule:
            continue
        rule_hash = rule["rule_hash"]
        if rule_hash in seen_alias_rules:
            continue
        seen_alias_rules.add(rule_hash)
        out["context_aliases"].append(rule)

    return out

def normalize_vocab(data: Any, channel_id: str) -> dict:
    """Public safe normalizer for admin previews and shadow-only evaluation."""
    return _normalize(data, channel_id)

def _bounded_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]

def _safe_vocab_phrase(value: Any) -> str:
    phrase = _bounded_text(value, 80)
    if not phrase or "\n" in phrase or "\r" in phrase:
        return ""
    if _PROMPT_LIKE_RE.search(phrase) or _CODE_LIKE_VOCAB_RE.search(phrase):
        return ""
    return phrase

def _normalize_iso_date(value: Any) -> str:
    raw = _bounded_text(value, 10)
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return ""

def _normalize_activation(value: Any) -> dict:
    """Normalize one term's channel/common or contextual activation rule.

    Missing activation data is deliberately treated as ``always`` so every
    existing channel vocabulary file remains behavior-compatible.  Contextual
    rules may use keywords, a date window, or both; an empty contextual rule is
    preserved but never activates, which is safer than silently making it
    channel-wide.
    """
    if not isinstance(value, dict):
        return _empty_activation()
    mode = str(value.get("mode") or "always").strip().lower()
    if mode not in VOCAB_ACTIVATION_MODES:
        mode = "always"
    if mode == "always":
        return _empty_activation()

    keywords: list[str] = []
    seen: set[str] = set()
    for raw in value.get("keywords") or []:
        keyword = _safe_vocab_phrase(raw)
        folded = keyword.casefold() if keyword else ""
        if keyword and folded not in seen:
            seen.add(folded)
            keywords.append(keyword)
        if len(keywords) >= 20:
            break
    active_from = _normalize_iso_date(value.get("active_from"))
    active_until = _normalize_iso_date(value.get("active_until"))
    if active_from and active_until and active_from > active_until:
        active_from, active_until = active_until, active_from
    return {
        "mode": "context",
        "keywords": keywords,
        "active_from": active_from,
        "active_until": active_until,
    }

def _normalize_string_list(value: Any, allowed: set[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for raw in value:
        item = str(raw or "").strip()
        if item in allowed and item not in out:
            out.append(item)
    return out

def _context_alias_hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _normalize_context_alias(value: Any, channel_id: str) -> dict | None:
    if not isinstance(value, dict):
        return None
    source = _safe_vocab_phrase(value.get("source_phrase"))
    target = _safe_vocab_phrase(value.get("target_phrase"))
    if not source or not target or source == target:
        return None
    channel_scope = _safe_channel_id(str(value.get("channel_scope") or channel_id))
    if channel_scope != _safe_channel_id(channel_id):
        return None
    approval = value.get("manager_approval") if isinstance(value.get("manager_approval"), dict) else {}
    approved = approval.get("approved") is True
    provenance = {
        "approved": approved,
        "approved_at": _bounded_text(approval.get("approved_at"), 64),
        "source": _bounded_text(approval.get("source"), 64),
    }
    allowed_contexts = _normalize_string_list(value.get("allowed_contexts"), _ALLOWED_CONTEXTS)
    forbidden_contexts = _normalize_string_list(value.get("forbidden_contexts"), _FORBIDDEN_CONTEXTS)
    surfaces = _normalize_string_list(value.get("applicable_surfaces"), _APPLICABLE_SURFACES)
    if not allowed_contexts or not surfaces:
        return None
    unsigned = {
        "schema_version": CONTEXT_ALIAS_SCHEMA_VERSION,
        "source_phrase": source,
        "target_phrase": target,
        "allowed_contexts": allowed_contexts,
        "forbidden_contexts": forbidden_contexts,
        "applicable_surfaces": surfaces,
        "channel_scope": channel_scope,
        "manager_approval": provenance,
        "version": max(1, _safe_int(value.get("version"), default=1)),
    }
    unsigned["rule_hash"] = _context_alias_hash(unsigned)
    return unsigned

def _normalize_feedback(data: Any) -> dict:
    out = {"candidates": {}, "terms": {}}
    if not isinstance(data, dict):
        return out
    for bucket in ("candidates", "terms"):
        raw_bucket = data.get(bucket) or {}
        if not isinstance(raw_bucket, dict):
            continue
        for raw_term, raw_meta in raw_bucket.items():
            term = (raw_term or "").strip() if isinstance(raw_term, str) else ""
            if not term or not isinstance(raw_meta, dict):
                continue
            state = (raw_meta.get("state") or "").strip()
            if state not in _FEEDBACK_EVENT_TYPES:
                state = ""
            events: list[dict] = []
            for ev in raw_meta.get("events") or []:
                if not isinstance(ev, dict):
                    continue
                ev_type = (ev.get("type") or "").strip()
                if ev_type not in _FEEDBACK_EVENT_TYPES:
                    continue
                events.append({
                    "type": ev_type,
                    "at": (ev.get("at") or "").strip(),
                    "source": (ev.get("source") or "").strip(),
                    "note": (ev.get("note") or "").strip(),
                })
            out[bucket][term] = {
                "state": state,
                "count": _safe_int(raw_meta.get("count"), default=len(events)),
                "updated_at": (raw_meta.get("updated_at") or "").strip(),
                "events": events[-20:],
            }
    return out

def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default or 0))

def _observed_date(value: str) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.date()

def _entry_activation_matches(entry: dict, evidence_text: str, observed_at: str) -> bool:
    activation = _normalize_activation(entry.get("activation"))
    if activation["mode"] == "always":
        return True

    keywords = activation["keywords"]
    if not keywords and not activation["active_from"] and not activation["active_until"]:
        return False
    folded_evidence = str(evidence_text or "").casefold()
    if keywords and not any(keyword.casefold() in folded_evidence for keyword in keywords):
        return False

    if activation["active_from"] or activation["active_until"]:
        observed = _observed_date(observed_at)
        if observed is None:
            return False
        if activation["active_from"] and observed < date.fromisoformat(activation["active_from"]):
            return False
        if activation["active_until"] and observed > date.fromisoformat(activation["active_until"]):
            return False
    return True

def partition_whitelist_terms(
    vocab: dict,
    *,
    evidence_text: str = "",
    observed_at: str = "",
) -> tuple[list[str], list[str]]:
    """Return active terms as ``(current-context, channel-background)``.

    The full dictionary can grow without becoming the Whisper prompt.  Terms
    whose spelling/alias is visible in current pre-STT evidence, plus active
    game/event/date terms, form the priority group.  Unmatched channel-wide
    terms remain available as a small fallback pool.
    """
    feedback_terms = ((vocab.get("feedback") or {}).get("terms") or {})
    lowered_states = {
        term
        for term, meta in feedback_terms.items()
        if isinstance(meta, dict) and meta.get("state") in {"demoted", "delete_suggested"}
    }
    folded_evidence = str(evidence_text or "").casefold()
    priority: list[tuple[int, int, str]] = []
    background: list[tuple[int, str]] = []
    for index, entry in enumerate(vocab.get("whitelist") or []):
        term = str(entry.get("term") or "").strip()
        if not term or term in lowered_states:
            continue
        if not _entry_activation_matches(entry, evidence_text, observed_at):
            continue
        activation = _normalize_activation(entry.get("activation"))
        term_hits = folded_evidence.count(term.casefold())
        alias_hits = sum(
            folded_evidence.count(str(alias).casefold())
            for alias in entry.get("aliases") or []
            if str(alias).strip()
        )
        is_contextual = activation["mode"] == "context"
        if term_hits or alias_hits or is_contextual:
            score = (term_hits * 100) + (alias_hits * 50) + (25 if is_contextual else 0)
            priority.append((-score, index, term))
        else:
            background.append((index, term))
    priority.sort()
    background.sort()
    return [term for _score, _index, term in priority], [term for _index, term in background]

def whitelist_terms(
    vocab: dict,
    *,
    evidence_text: str = "",
    observed_at: str = "",
) -> list[str]:
    """Return all active terms, current-context terms first."""
    priority, background = partition_whitelist_terms(
        vocab,
        evidence_text=evidence_text,
        observed_at=observed_at,
    )
    return [*priority, *background]

def alias_replacements(
    vocab: dict,
    *,
    evidence_text: str = "",
    observed_at: str = "",
) -> list[tuple[str, str]]:
    """recorrect_srt 가 LLM 없이 즉시 적용할 (old, new) 매핑.

    각 whitelist 엔트리의 alias → term. blacklist 와 충돌하는 것은 제외.
    체인 충돌 (한 term 이 다른 alias) 도 제외.
    """
    bl = set(vocab.get("blacklist") or [])
    pairs: list[tuple[str, str]] = []
    active_terms = set(whitelist_terms(
        vocab,
        evidence_text=evidence_text,
        observed_at=observed_at,
    ))
    all_terms = {w["term"] for w in vocab.get("whitelist", []) if w.get("term")}
    for w in vocab.get("whitelist", []):
        term = (w.get("term") or "").strip()
        if not term or term not in active_terms:
            continue
        for alias in w.get("aliases") or []:
            alias = (alias or "").strip()
            if not alias or alias == term:
                continue
            if alias in bl:
                continue
            if alias in all_terms:
                continue
            pairs.append((alias, term))
    pairs.sort(key=lambda p: (-len(p[0]), p[0]))
    return pairs

def _context_match_kind(
    text: str,
    start: int,
    end: int,
    rule: dict,
    known_terms: set[str],
) -> str:
    source = str(rule.get("source_phrase") or "")
    allowed = set(rule.get("allowed_contexts") or [])
    before = text[max(0, start - 24):start]
    after = text[end:end + 24]
    if _ACTION_FOLLOW_RE.match(after) or _ACTION_PREFIX_RE.search(before):
        return "action_support"
    if source.strip() != "서포트" and "exact_phrase" in allowed:
        return "exact_phrase"
    if _ROLE_FOLLOW_RE.match(after):
        return "role_noun"
    previous_token = re.search(r"([A-Za-z가-힣][A-Za-z0-9가-힣]{1,20})\s*$", before)
    if previous_token and previous_token.group(1) in known_terms:
        return "game_role"
    return "ambiguous"

def apply_context_aliases(
    text: str,
    vocab: dict,
    *,
    surface: str,
    channel_id: str,
    quoted: bool = False,
) -> tuple[str, dict]:
    """Apply only manager-approved, context-supported phrase aliases.

    This adapter is deterministic and side-effect free.  It intentionally
    treats quoted speech as immutable and never turns a plain vocabulary term
    into a global replacement rule.
    """
    source_text = str(text or "")
    diagnostics = {
        "schema_version": "streamer_vocab_context_alias_result.v1",
        "surface": surface,
        "applied_count": 0,
        "rejected_count": 0,
        "applied_rule_hashes": [],
        "rejection_codes": [],
        "raw_content_included": False,
    }
    if quoted or surface == "quoted_speech":
        diagnostics["rejection_codes"].append("quoted_speech_immutable")
        return source_text, diagnostics
    if surface not in _APPLICABLE_SURFACES:
        diagnostics["rejection_codes"].append("unsupported_surface")
        return source_text, diagnostics

    normalized_vocab = _normalize(vocab, channel_id)
    current = source_text
    known_terms = {
        str(row.get("term") or "").strip()
        for row in normalized_vocab.get("whitelist") or []
        if isinstance(row, dict) and str(row.get("term") or "").strip()
    }
    for rule in normalized_vocab.get("context_aliases") or []:
        if not isinstance(rule, dict):
            continue
        if rule.get("manager_approval", {}).get("approved") is not True:
            diagnostics["rejected_count"] += 1
            diagnostics["rejection_codes"].append("manager_approval_absent")
            continue
        if _safe_channel_id(str(rule.get("channel_scope") or "")) != _safe_channel_id(channel_id):
            diagnostics["rejected_count"] += 1
            diagnostics["rejection_codes"].append("channel_mismatch")
            continue
        if surface not in set(rule.get("applicable_surfaces") or []):
            diagnostics["rejected_count"] += 1
            diagnostics["rejection_codes"].append("surface_not_allowed")
            continue
        source = str(rule.get("source_phrase") or "")
        target = str(rule.get("target_phrase") or "")
        if not source or source not in current:
            continue
        allowed = set(rule.get("allowed_contexts") or [])
        forbidden = set(rule.get("forbidden_contexts") or [])
        pieces: list[str] = []
        cursor = 0
        applied_for_rule = 0
        quoted_ranges = [
            (quoted_match.start(), quoted_match.end())
            for quoted_match in _QUOTED_SPAN_RE.finditer(current)
        ]
        for match in re.finditer(re.escape(source), current):
            pieces.append(current[cursor:match.start()])
            inside_quote = any(
                quote_start <= match.start() and match.end() <= quote_end
                for quote_start, quote_end in quoted_ranges
            )
            kind = (
                "quoted_speech"
                if inside_quote
                else _context_match_kind(
                    current,
                    match.start(),
                    match.end(),
                    rule,
                    known_terms,
                )
            )
            if kind in allowed and kind not in forbidden:
                pieces.append(target)
                applied_for_rule += 1
            else:
                pieces.append(match.group(0))
                diagnostics["rejected_count"] += 1
                diagnostics["rejection_codes"].append(
                    "forbidden_context" if kind in forbidden else f"context_not_allowed:{kind}"
                )
            cursor = match.end()
        pieces.append(current[cursor:])
        current = "".join(pieces)
        if applied_for_rule:
            diagnostics["applied_count"] += applied_for_rule
            diagnostics["applied_rule_hashes"].append(str(rule.get("rule_hash") or ""))
    diagnostics["rejection_codes"] = sorted(set(diagnostics["rejection_codes"]))
    diagnostics["applied_rule_hashes"] = sorted(
        {item for item in diagnostics["applied_rule_hashes"] if item}
    )
    return current, diagnostics
