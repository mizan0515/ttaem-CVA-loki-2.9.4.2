"""Folder-local adapters for baseline spelling and background evidence.

No operator dictionaries, edit history, account state or external proxy is loaded.
Local context is untrusted background, never proof of a scene in this VOD.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone

from .acquire import save_json
from .local_files import plain_path, read_json
from .lexicon import _tokenize, _rank_terms, _from_titles, _from_context_doc
from .streamer_vocab import normalize_vocab, partition_whitelist_terms


def load_local_context(run, vod):
    path = plain_path(run / "context.md")
    context = path.read_text(encoding="utf-8").strip()[:8000] if path.is_file() else ""
    vocab_path = plain_path(run / "approved-vocabulary.json")
    raw = read_json(vocab_path) if vocab_path.is_file() else {}
    if raw and (not vod.channel_id or raw.get("channel_id") != vod.channel_id):
        raise ValueError("Approved vocabulary must name this VOD's exact channel_id")
    return context, normalize_vocab(raw, vod.channel_id)


def spelling_terms(vod, chats, *, context="", vocabulary=None, clips=(), community=(), wiki=(), limit=30):
    """Baseline tokenization, weights and approved-term ordering; new file layout."""
    counter = Counter()
    for row in chats:
        counter.update(_tokenize(row.get("msg") or ""))
    for row in community:
        for key in ("title", "body_preview"):
            for token in _tokenize(getattr(row, key, "")):
                counter[token] += 2
    titles = [str(row.get("title") or "") for row in clips]
    counter.update(_from_titles([vod.title, vod.category, *titles]))
    counter.update(_from_context_doc(context))
    terms = _rank_terms(counter, limit * 2)
    terms = [t for t in terms if t in wiki] + [t for t in terms if t not in wiki]
    priority, background = partition_whitelist_terms(vocabulary or {},
        evidence_text="\n".join([vod.title, vod.category, *titles, context, " ".join(counter)]),
        observed_at=vod.broadcast_start_at or vod.publish_date)
    reserve = min(len(background), max(1, limit // 6))
    ordered = [*priority, *background[:reserve], vod.channel_name.strip(), *terms, *background[reserve:]]
    return list(dict.fromkeys(term for term in ordered if term))[:limit]


def background_evidence(run, vod, chats, clips, comment_bundle):
    from .streamer_recent_context import (
        normalize_aliases, _items_from_chzzk_public, _items_from_fmkorea, _dedupe_score_and_cap, _result, _diag,
    )
    context, vocabulary = load_local_context(run, vod)
    from .community import community_evidence
    community, community_status = community_evidence(run, vod)
    comment_text = "\n".join(str(row.text) for row in comment_bundle.comments)
    terms = spelling_terms(vod, chats, context="\n\n".join([context, comment_text]),
                           vocabulary=vocabulary, clips=clips, community=community,
                           wiki=wiki_terms(run, vod, fetch_external=False))
    aliases = normalize_aliases(vod)
    recent_path = plain_path(run / "streamer_recent_context.json")
    prior = read_json(recent_path) if recent_path.is_file() else {}
    generated_at = prior.get("generated_at") or datetime.now(timezone.utc).isoformat()
    recent = _result(vod_info=vod, generated_at=generated_at, lookback_days=30,
        include_sources=["chzzk_public", "fmkorea"], aliases=aliases,
        items=_dedupe_score_and_cap(_items_from_chzzk_public(vod, aliases)+_items_from_fmkorea(community, aliases),
            aliases=aliases, max_items=18, max_per_source=6),
        diagnostics=[_diag("chzzk_public", "ok", "VOD public metadata projected without network"),
                     _diag("fmkorea", community_status["status"], "Public source; broadcast window and original age policy"),
                     _diag("markdown_proxy", "disabled_by_policy", "No separate API/proxy route")])
    save_json(recent_path, recent)
    save_json(run / "context-evidence.json", {"lexicon_terms":terms,
        "context_document_present":bool(context), "approved_vocabulary_present":bool(vocabulary.get("whitelist")),
        "community_status":community_status["status"], "recent_context_status":recent["status"]})
    return context, terms, recent, community


def wiki_terms(run, vod, *, fetch_external=False):
    """Original 168h identity-scoped ranking cache; bounded anonymous transport."""
    import re
    import time
    from urllib.request import Request, urlopen
    from urllib.parse import quote
    path = plain_path(run / 'wiki-spelling.json')
    old = read_json(path) if path.is_file() else {}
    if (old.get('channel_id') == vod.channel_id and old.get('channel_name') == vod.channel_name
            and 0 <= time.time()-old.get('fetched_at', 0) < 168*3600):
        return old.get('terms', [])
    if not fetch_external or not vod.channel_id or not vod.channel_name:
        return []
    terms, status = [], 'unavailable'
    try:
        request = Request('https://namu.wiki/w/'+quote(vod.channel_name),
                          headers={'User-Agent':'Mozilla/5.0'})
        with urlopen(request, timeout=5) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Spelling page exceeds size limit')
        text = raw.decode('utf-8', errors='replace')
        if re.search(r'captcha|verify you are human|access denied', text, re.I):
            raise ValueError('Spelling page is unavailable')
        text = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', text, flags=re.I)
        text = re.sub(r'<[^>]+>', ' ', text)
        terms = [term for term, _ in Counter(_tokenize(text)).most_common(1000)]
        status = 'complete'
    except (OSError, ValueError):
        pass
    save_json(path, {'channel_id':vod.channel_id, 'channel_name':vod.channel_name,
                    'fetched_at':time.time(), 'status':status, 'terms':terms})
    return terms


def correct_transcript(run, vod, segments, chats):
    """Always derive from raw transcript; preserve clocks and private original."""
    from copy import deepcopy
    from .streamer_vocab import alias_replacements
    from .recorrect import _apply_replacements
    context, vocabulary = load_local_context(run, vod)
    terms = spelling_terms(vod, chats, context=context, vocabulary=vocabulary)
    pairs = alias_replacements(vocabulary,
        evidence_text="\n".join([vod.title, vod.category, context, *terms]),
        observed_at=vod.broadcast_start_at or vod.publish_date)
    rows = deepcopy(segments)
    count = _apply_replacements(rows, pairs)
    save_json(run / "recorrection.json", {"mode":"approved_aliases_only", "llm_called":False,
        "entries_changed":count,"replacements":pairs,"original":"transcript.json"})
    return rows
