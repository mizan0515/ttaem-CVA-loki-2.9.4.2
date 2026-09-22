"""채팅 기반 하이라이트 추출 (chzzk_editor.py 분석 알고리즘 재사용)"""

import logging
import math
import re
from collections import Counter, defaultdict

logger = logging.getLogger("pipeline")

WINDOW_SEC = 10
PEAK_MERGE_SEC = 30
Z_THRESHOLD = 2.0

_COMMAND_PREFIX_PATTERN = re.compile(r'^!\s*[\w가-힣]')
_LAUGHTER_PATTERNS = [
    re.compile(r'ㅋ{2,}'),
    re.compile(r'ㅎ{2,}'),
    re.compile(r'개웃|웃(?:기|겨|김|기네|긴|겼|긴다|기다|음)'),
    re.compile(r'재밌|재미있'),
    re.compile(r'(?:아니|뭐야|진짜|미치겠|미쳤|실화).{0,20}ㅋ{2,}'),
]
_LAUGHTER_REPEAT_PATTERN = re.compile(r'ㅋ{2,}|ㅎ{2,}')
_PUNCT_SPACE_PATTERN = re.compile(r'[\s\W_]+', re.UNICODE)
_REACTION_FAMILIES = ("surprise", "question")
_REACTION_SIGNAL_KEYS = {
    "surprise": "reaction_surprise",
    "question": "reaction_question",
}
_REACTION_LABELS = {
    "surprise": "놀람",
    "question": "질문",
}
_REACTION_FAMILY_PATTERNS = {
    "surprise": [
        re.compile(r'헐+|헉+|ㄷ{2,}|와{2,}|우와+'),
        re.compile(r'대박|미쳤|미친|레전드|뭐야|wow|wtf|omg', re.IGNORECASE),
    ],
    "question": [
        re.compile(r'[?？]+'),
        re.compile(r'뭐(?:야|지|임|냐)|왜|어케|어떻게'),
    ],
}
_CHAT_SIGNAL_PACK_SCHEMA_VERSION = "chat_signal_pack.v1"
_CHAT_SIGNAL_FAMILY_ORDER = (
    "laughter",
    "surprise",
    "question",
    "proper_noun_context",
    "clip_request_edit_request",
    "command_spam_drop",
)
_CLIP_REQUEST_PATTERN = re.compile(r'클립|편집|저장|캡처|스샷|하이라이트|짤|명장면')
_PROPER_NOUN_CONTEXT_PATTERN = re.compile(
    r'[A-Za-z][A-Za-z0-9_-]{1,}|'
    r'보스|캐릭터?|닉(?:네임)?|이름|아이디|스킬|맵|지역|던전|레이드|'
    r'직업|서버|카멘|일리아칸|에키드나|아브렐슈드|발탄|비아키스'
)


def _is_command_message(msg: str) -> bool:
    if not msg:
        return False
    return bool(_COMMAND_PREFIX_PATTERN.match(msg))


def _filter_command_messages(chats: list[dict]) -> list[dict]:
    """투표/명령어 채팅 제외. peak 분석 + LLM 입력에서 노이즈로 작용."""
    return [c for c in chats if not _is_command_message(c.get("msg", ""))]


def normalize_laughter_message(msg: str) -> str:
    """웃음 echo 비교용 정규화. ㅋㅋ 길이 차이와 공백/문장부호 차이를 흡수한다."""
    text = (msg or "").lower()
    text = _LAUGHTER_REPEAT_PATTERN.sub(lambda m: m.group(0)[0] * 2, text)
    return _PUNCT_SPACE_PATTERN.sub("", text)


def _normalize_chat_signal_text(msg: str) -> str:
    """대표 채팅 중복 제거용 정규화. 원문 보존은 quote row 에서만 한다."""
    text = normalize_laughter_message(msg)
    return re.sub(r'(.)\1{2,}', r'\1\1', text)


def is_laughter_message(msg: str) -> bool:
    """`ㅋㅋ+문장`을 포함한 한국어 웃음/재미 반응 여부."""
    if not msg:
        return False
    return any(pattern.search(msg) for pattern in _LAUGHTER_PATTERNS)


def _reaction_families_for_message(msg: str) -> list[str]:
    if not msg:
        return []
    return [
        family
        for family in _REACTION_FAMILIES
        if any(pattern.search(msg) for pattern in _REACTION_FAMILY_PATTERNS[family])
    ]


def is_surprise_message(msg: str) -> bool:
    """헉/헐/ㄷㄷ/대박 계열 놀람 반응 여부."""
    return "surprise" in _reaction_families_for_message(msg)


def is_question_message(msg: str) -> bool:
    """물음표/뭐야 계열 질문 반응 여부."""
    return "question" in _reaction_families_for_message(msg)


def _chat_user_key(chat: dict, index: int) -> str:
    """도배 방지용 유저 키. uid가 없으면 nick, 둘 다 없으면 행 위치로 fallback."""
    return str(chat.get("uid") or chat.get("user") or chat.get("nick") or f"row:{index}")


def _chat_signal_families_for_message(msg: str) -> list[str]:
    families: list[str] = []
    if _is_command_message(msg):
        families.append("command_spam_drop")
    if is_laughter_message(msg):
        families.append("laughter")
    families.extend(_reaction_families_for_message(msg))
    if _CLIP_REQUEST_PATTERN.search(msg or ""):
        families.append("clip_request_edit_request")
    if not families and _PROPER_NOUN_CONTEXT_PATTERN.search(msg or ""):
        families.append("proper_noun_context")
    return [family for family in _CHAT_SIGNAL_FAMILY_ORDER if family in set(families)]


def _corroboration_state(highlight: dict, quote_families: set[str]) -> dict:
    signals = set(highlight.get("source_signals") or [])
    states = []
    if highlight.get("source_alignment_candidate"):
        states.append("clip-supported")
    if highlight.get("audio_reaction_peak") or "audio" in signals:
        states.append("audio-supported")
    if highlight.get("subtitle_supported") or "subtitle" in signals:
        states.append("subtitle-supported")
    if not states:
        states.append("chat-only" if quote_families else "uncertain")
    return {
        "state": states[0],
        "corroboration": states,
    }


def _quote_sort_key(row: dict, center_sec: float) -> tuple[int, float, int]:
    family_priority = min(
        (_CHAT_SIGNAL_FAMILY_ORDER.index(f) for f in row["families"]),
        default=len(_CHAT_SIGNAL_FAMILY_ORDER),
    )
    return (family_priority, abs(row["sec"] - center_sec), row["index"])


def build_chat_signal_pack(
    highlights: list[dict],
    chats: list[dict],
    *,
    vod_id: str | None = None,
    context_sec: int = 30,
    quote_budget: int = 7,
    char_budget: int = 900,
) -> dict:
    """Build a shadow-only compact representative chat evidence packet.

    This function does not alter the current summary prompt path. It keeps raw
    representative quote text in the returned in-memory/local artifact only.
    """
    from .utils import sec_to_hms

    safe_quote_budget = max(0, quote_budget)
    pack_items = []
    total_current_nearby = 0
    total_selected_quotes = 0
    total_used_chars = 0
    dropped_totals = Counter()

    for highlight_index, highlight in enumerate(highlights or []):
        sec = float(highlight["sec"])
        start = max(0.0, sec - context_sec)
        end = sec + context_sec
        window_chats = [
            (index, chat)
            for index, chat in enumerate(chats or [])
            if start * 1000 <= chat.get("ms", -1) <= end * 1000
        ]
        command_drop = sum(
            1
            for _, chat in window_chats
            if _is_command_message(chat.get("msg", ""))
        )
        prompt_scope_chats = [
            (index, chat)
            for index, chat in window_chats
            if not _is_command_message(chat.get("msg", ""))
        ]
        candidates = []
        for index, chat in prompt_scope_chats:
            msg = chat.get("msg", "")
            families = [
                family
                for family in _chat_signal_families_for_message(msg)
                if family != "command_spam_drop"
            ]
            if not families:
                continue
            candidates.append({
                "index": index,
                "chat": chat,
                "sec": chat.get("ms", 0) / 1000.0,
                "user_key": _chat_user_key(chat, index),
                "normalized_text": _normalize_chat_signal_text(msg),
                "families": families,
            })

        seen_texts = set()
        seen_users = set()
        selected_families = set()
        speaker_refs = {}
        processed_indexes = set()
        selected = []
        dropped_counts = Counter({"command_spam_drop": command_drop})
        remaining_chars = max(0, char_budget)

        by_family = defaultdict(list)
        for row in candidates:
            for family in row["families"]:
                by_family[family].append(row)

        ordered_rows = []
        for family in _CHAT_SIGNAL_FAMILY_ORDER:
            rows = sorted(by_family.get(family, []), key=lambda r: _quote_sort_key(r, sec))
            if rows:
                ordered_rows.append(rows[0])
        ordered_rows.extend(sorted(candidates, key=lambda r: _quote_sort_key(r, sec)))

        for row in ordered_rows:
            if row["index"] in processed_indexes:
                continue
            processed_indexes.add(row["index"])
            if len(selected) >= safe_quote_budget:
                dropped_counts["over_budget"] += 1
                continue
            text = row["chat"].get("msg", "")
            normalized = row["normalized_text"]
            row_families = set(row["families"])
            adds_uncovered_family = bool(row_families - selected_families)
            if normalized and normalized in seen_texts:
                dropped_counts["duplicate_text"] += 1
                continue
            if (
                row["user_key"] in seen_users
                and len(candidates) > safe_quote_budget
                and not adds_uncovered_family
            ):
                dropped_counts["duplicate_user"] += 1
                continue
            quote_chars = len(text)
            if quote_chars > remaining_chars:
                dropped_counts["over_budget"] += 1
                continue
            if row["user_key"] not in speaker_refs:
                speaker_refs[row["user_key"]] = f"speaker:{len(speaker_refs) + 1}"
            speaker_ref = speaker_refs[row["user_key"]]
            selected.append({
                "sec": round(row["sec"], 3),
                "timestamp": sec_to_hms(row["sec"]),
                "speaker_ref": speaker_ref,
                "text": text,
                "signal_families": row["families"],
            })
            seen_texts.add(normalized)
            seen_users.add(row["user_key"])
            selected_families.update(row_families)
            remaining_chars -= quote_chars

        quote_families = {
            family
            for quote in selected
            for family in quote["signal_families"]
        }
        candidate_families = {
            family
            for row in candidates
            for family in row["families"]
        }
        dropped_signal_families = [
            family
            for family in _CHAT_SIGNAL_FAMILY_ORDER
            if family in candidate_families and family not in quote_families
        ]
        reduced_count = max(0, len(prompt_scope_chats) - len(selected))
        item_used_chars = sum(len(quote["text"]) for quote in selected)
        item = {
            "schema_version": _CHAT_SIGNAL_PACK_SCHEMA_VERSION,
            "vod_id": vod_id,
            "window_id": f"{vod_id or 'vod'}:{highlight_index:03d}:{int(sec)}",
            "center_sec": sec,
            "start_sec": start,
            "end_sec": end,
            "timestamp": sec_to_hms(sec),
            "signal_families": [
                family for family in _CHAT_SIGNAL_FAMILY_ORDER if family in quote_families
            ],
            "metrics": {
                "input_chat_count": len(prompt_scope_chats),
                "candidate_chat_count": len(candidates),
                "selected_quote_count": len(selected),
                "reduced_count": reduced_count,
            },
            "representative_quotes": selected,
            "dropped_counts": dict(dropped_counts),
            "dropped_signal_families": dropped_signal_families,
            "confidence": _corroboration_state(highlight, quote_families),
            "budget": {
                "quote_budget": safe_quote_budget,
                "char_budget": char_budget,
                "used_chars": item_used_chars,
            },
        }
        pack_items.append(item)
        total_current_nearby += len(prompt_scope_chats)
        total_selected_quotes += len(selected)
        total_used_chars += item_used_chars
        dropped_totals.update(dropped_counts)

    return {
        "schema_version": _CHAT_SIGNAL_PACK_SCHEMA_VERSION,
        "privacy_class": "internal_private_shadow",
        "default_output_changed": False,
        "vod_id": vod_id,
        "items": pack_items,
        "summary": {
            "highlight_count": len(pack_items),
            "input_chat_count": total_current_nearby,
            "selected_quote_count": total_selected_quotes,
            "reduced_count": max(0, total_current_nearby - total_selected_quotes),
            "used_chars": total_used_chars,
            "dropped_counts": dict(dropped_totals),
        },
    }


def build_chat_signal_pack_shadow_report(pack: dict, current_prompt_text: str) -> dict:
    """Compare current raw prompt text with a shadow chat signal pack."""
    current_chars = len(current_prompt_text or "")
    pack_chars = int((pack.get("summary") or {}).get("used_chars", 0))
    reduced_count = int((pack.get("summary") or {}).get("reduced_count", 0))
    selected_count = int((pack.get("summary") or {}).get("selected_quote_count", 0))
    lost_or_weakened_evidence = []
    for item in pack.get("items") or []:
        dropped_families = list(item.get("dropped_signal_families") or [])
        if dropped_families:
            lost_or_weakened_evidence.append(
                {
                    "window_id": item.get("window_id"),
                    "signal_families": dropped_families,
                }
            )
    decision = "continue_measure"
    if not selected_count:
        decision = "reject_no_representative_quotes"
    elif lost_or_weakened_evidence:
        decision = "continue_measure_weakened_evidence"
    elif pack_chars >= current_chars and current_chars:
        decision = "continue_measure_no_token_gain"
    return {
        "schema_version": "chat_signal_pack.shadow_report.v1",
        "default_output_changed": False,
        "current_prompt_chars": current_chars,
        "pack_quote_chars": pack_chars,
        "char_delta": current_chars - pack_chars,
        "noise_decreased": reduced_count > 0,
        "lost_or_weakened_evidence": lost_or_weakened_evidence,
        "manager_visible_summary_usefulness": "unchanged_shadow_only",
        "adoption_decision": decision,
    }

KEYWORD_WEIGHTS = {
    r'ㅋ{2,}':          1.0,
    r'ㅎ{2,}':          0.8,
    r'lol|lmao|ㄹㅇㅋ': 1.0,
    r'웃기|재밌|재미있': 0.8,
    r'ㄷ{2,}':          1.5,
    r'대박|미쳤|레전드|ㄹㅈㄷ': 1.5,
    r'와{2,}|우와+|오{3,}': 1.2,
    r'헐{1,}|헉{1,}':   1.2,
    r'wow|wtf|omg':     1.2,
    r'클립|편집|이거다|명장면': 2.5,
    r'저장|캡처|스샷':  2.0,
    r'하이라이트|짤':   2.0,
    r'ㅠ{2,}|ㅜ{2,}':   0.8,
    r'울었|눈물|감동':  1.0,
    r'ㅅㅂ|시발|존나|개ㅈ': 0.9,
    r'화남|열받|빡침':  0.9,
    r'후원|도네|구독|응원': 1.8,
    r'치어스|cheer':    1.8,
}

_compiled_keywords = [(re.compile(pat, re.IGNORECASE), w) for pat, w in KEYWORD_WEIGHTS.items()]


def score_message(msg: str) -> float:
    score = 0.0
    for pattern, weight in _compiled_keywords:
        if pattern.search(msg):
            score += weight
    return max(1.0, score)


def build_time_series(chats: list[dict], window_sec: int = WINDOW_SEC) -> dict:
    chats = _filter_command_messages(chats)
    if not chats:
        return {"buckets": {}, "duration_sec": 0}

    buckets = defaultdict(lambda: {
        "count": 0,
        "score": 0.0,
        "laughter_count": 0,
        "laughter_users": set(),
        "laughter_norm_counts": Counter(),
        "laughter_norm_users": defaultdict(set),
        "reaction_counts": Counter(),
        "reaction_users": defaultdict(set),
    })
    duration_ms = max(c["ms"] for c in chats)

    for index, chat in enumerate(chats):
        sec = chat["ms"] / 1000.0
        bucket = int(sec // window_sec) * window_sec
        buckets[bucket]["count"] += 1
        msg = chat.get("msg", "")
        buckets[bucket]["score"] += score_message(msg)
        user_key = _chat_user_key(chat, index)
        if is_laughter_message(msg):
            normalized = normalize_laughter_message(msg)
            buckets[bucket]["laughter_count"] += 1
            buckets[bucket]["laughter_users"].add(user_key)
            if normalized:
                buckets[bucket]["laughter_norm_counts"][normalized] += 1
                buckets[bucket]["laughter_norm_users"][normalized].add(user_key)
        for family in _reaction_families_for_message(msg):
            buckets[bucket]["reaction_counts"][family] += 1
            buckets[bucket]["reaction_users"][family].add(user_key)

    out = {}
    for bucket, data in buckets.items():
        echo_user_counts = {
            text: len(users)
            for text, users in data["laughter_norm_users"].items()
        }
        echo_text, echo_user_max = ("", 0)
        if echo_user_counts:
            echo_text, echo_user_max = max(
                echo_user_counts.items(),
                key=lambda item: (item[1], data["laughter_norm_counts"][item[0]]),
            )
        count = data["count"] or 1
        row = {
            "count": data["count"],
            "score": data["score"],
            "laughter_count": data["laughter_count"],
            "laughter_user_count": len(data["laughter_users"]),
            "laughter_ratio": data["laughter_count"] / count,
            "laughter_echo_max": echo_user_max,
            "laughter_echo_text": echo_text,
        }
        for family in _REACTION_FAMILIES:
            family_count = data["reaction_counts"][family]
            row[f"{family}_count"] = family_count
            row[f"{family}_user_count"] = len(data["reaction_users"][family])
            row[f"{family}_ratio"] = family_count / count
        out[bucket] = row

    return {"buckets": out, "duration_sec": duration_ms / 1000.0}


def z_score_peaks(buckets: dict, threshold: float = Z_THRESHOLD) -> list[dict]:
    if not buckets:
        return []

    times = sorted(buckets.keys())
    counts = [buckets[t]["count"] for t in times]
    scores = [buckets[t]["score"] for t in times]
    laughter_strengths = [
        buckets[t].get("laughter_count", 0)
        + buckets[t].get("laughter_user_count", 0) * 1.5
        + buckets[t].get("laughter_echo_max", 0) * 2.0
        + buckets[t].get("laughter_ratio", 0.0) * 5.0
        for t in times
    ]
    reaction_strengths_by_family = {
        family: [
            buckets[t].get(f"{family}_count", 0)
            + buckets[t].get(f"{family}_user_count", 0) * 1.75
            + buckets[t].get(f"{family}_ratio", 0.0) * 4.0
            for t in times
        ]
        for family in _REACTION_FAMILIES
    }

    def stats(lst):
        mean = sum(lst) / len(lst)
        var = sum((x - mean) ** 2 for x in lst) / len(lst)
        std = math.sqrt(var) if var > 0 else 1.0
        return mean, std

    count_mean, count_std = stats(counts)
    score_mean, score_std = stats(scores)
    laughter_mean, laughter_std = stats(laughter_strengths)
    reaction_stats = {
        family: stats(strengths)
        for family, strengths in reaction_strengths_by_family.items()
    }
    max_count = max(counts) or 1
    max_score = max(scores) or 1
    max_laughter = max(laughter_strengths) or 1
    max_reaction = max(
        [max(strengths) for strengths in reaction_strengths_by_family.values() if strengths] or [1]
    ) or 1

    peaks = []
    for index, (t, cnt, sc, laughter_strength) in enumerate(zip(times, counts, scores, laughter_strengths)):
        bucket = buckets[t]
        z_count = (cnt - count_mean) / count_std
        z_score_val = (sc - score_mean) / score_std
        z_laughter_val = (laughter_strength - laughter_mean) / laughter_std
        laughter_count = bucket.get("laughter_count", 0)
        laughter_user_count = bucket.get("laughter_user_count", 0)
        laughter_ratio = bucket.get("laughter_ratio", 0.0)
        laughter_echo_max = bucket.get("laughter_echo_max", 0)
        laughter_burst = (
            (laughter_count >= 5 and laughter_user_count >= 3)
            or (laughter_ratio >= 0.30 and laughter_user_count >= 3)
            or laughter_echo_max >= 3
            or (z_laughter_val >= threshold and laughter_user_count >= 2)
        )
        reaction_clusters = []
        reaction_details = {}
        strongest_reaction = 0.0
        for family in _REACTION_FAMILIES:
            family_strength = reaction_strengths_by_family[family][index]
            family_mean, family_std = reaction_stats[family]
            family_z = (family_strength - family_mean) / family_std
            family_count = bucket.get(f"{family}_count", 0)
            family_user_count = bucket.get(f"{family}_user_count", 0)
            family_ratio = bucket.get(f"{family}_ratio", 0.0)
            family_cluster = (
                (family_count >= 3 and family_user_count >= 3)
                or (family_ratio >= 0.25 and family_user_count >= 3)
                or (family_z >= threshold and family_count >= 2 and family_user_count >= 2)
            )
            if family_cluster:
                reaction_clusters.append(family)
                strongest_reaction = max(strongest_reaction, family_strength)
            reaction_details[f"{family}_cluster"] = family_cluster
            reaction_details[f"{family}_count"] = family_count
            reaction_details[f"{family}_user_count"] = family_user_count
            reaction_details[f"{family}_ratio"] = round(family_ratio, 4)
            reaction_details[f"z_{family}_reaction"] = round(family_z, 2)
        reaction_cluster = bool(reaction_clusters)
        if z_count >= threshold or z_score_val >= threshold or laughter_burst or reaction_cluster:
            base_composite = (cnt / max_count) * 0.4 + (sc / max_score) * 0.6
            laughter_component = (laughter_strength / max_laughter) * 0.25 if laughter_strength else 0.0
            reaction_component = (strongest_reaction / max_reaction) * 0.10 if strongest_reaction else 0.0
            composite = max(
                base_composite,
                base_composite * 0.85 + laughter_component,
                base_composite * 0.90 + reaction_component,
            )
            if laughter_burst:
                composite *= 1.15
            composite = min(1.0, composite)
            source_signals = ["chat"]
            if z_count >= threshold:
                source_signals.append("chat_density")
            if z_score_val >= threshold:
                source_signals.append("keyword_score")
            if laughter_burst:
                source_signals.append("laughter_burst")
            source_signals.extend(_REACTION_SIGNAL_KEYS[family] for family in reaction_clusters)
            peaks.append({
                "sec": t,
                "count": cnt,
                "raw_score": sc,
                "z_count": round(z_count, 2),
                "z_score": round(z_score_val, 2),
                "z_laughter": round(z_laughter_val, 2),
                "laughter_burst": laughter_burst,
                "laughter_count": laughter_count,
                "laughter_user_count": laughter_user_count,
                "laughter_ratio": round(laughter_ratio, 4),
                "laughter_echo_max": laughter_echo_max,
                "laughter_echo_text": bucket.get("laughter_echo_text", ""),
                "reaction_families": reaction_clusters,
                **reaction_details,
                "source_signals": source_signals,
                "composite": round(composite, 4),
            })
    return peaks


def merge_peaks(peaks: list[dict], merge_sec: int = PEAK_MERGE_SEC) -> list[dict]:
    if not peaks:
        return []

    def finalize(group: list[dict]) -> dict:
        best = max(
            group,
            key=lambda p: (
                p.get("composite", 0),
                bool(p.get("laughter_burst")),
                bool(p.get("reaction_families")),
            ),
        )
        best["cluster_count"] = len(group)
        best["peak_count_sum"] = sum(p["count"] for p in group)
        best["laughter_burst"] = any(p.get("laughter_burst") for p in group)
        best["laughter_count_sum"] = sum(p.get("laughter_count", 0) for p in group)
        best["laughter_user_peak"] = max((p.get("laughter_user_count", 0) for p in group), default=0)
        best["reaction_families"] = sorted({
            family
            for p in group
            for family in (p.get("reaction_families") or [])
        })
        for family in _REACTION_FAMILIES:
            best[f"{family}_cluster"] = any(p.get(f"{family}_cluster") for p in group)
            best[f"{family}_count_sum"] = sum(p.get(f"{family}_count", 0) for p in group)
            best[f"{family}_user_peak"] = max((p.get(f"{family}_user_count", 0) for p in group), default=0)
        best["source_signals"] = sorted({
            signal
            for p in group
            for signal in (p.get("source_signals") or [])
        })
        return best

    sorted_peaks = sorted(peaks, key=lambda p: p["sec"])
    merged = []
    group = [sorted_peaks[0]]

    for peak in sorted_peaks[1:]:
        has_reaction_cluster = peak.get("reaction_families") or any(p.get("reaction_families") for p in group)
        effective_merge_sec = 45 if (
            peak.get("laughter_burst")
            or any(p.get("laughter_burst") for p in group)
            or has_reaction_cluster
        ) else merge_sec
        if peak["sec"] - group[-1]["sec"] <= effective_merge_sec:
            group.append(peak)
        else:
            merged.append(finalize(group))
            group = [peak]

    merged.append(finalize(group))

    return merged


def compute_adaptive_top_n(
    duration_sec: int | float | None,
    *,
    per_hour: int = 2,
    min_count: int = 8,
    max_count: int = 20,
) -> int:
    """영상 길이(초) → 추출할 하이라이트 개수.

    산식: clamp(min_count, round(duration_h * per_hour), max_count).
    duration_sec 가 None / 비양수면 min_count 반환 (안전 fallback).

    예 (per_hour=6, min=8, max=40):
      0.5h → 8 (min), 1h → 8, 2h → 12, 4h → 24, 7h+ → 40 (cap).
    """
    if not duration_sec or duration_sec <= 0:
        return max(1, min_count)
    hours = duration_sec / 3600.0
    raw = round(hours * per_hour)
    return max(min_count, min(max_count, raw))


def find_edit_points(
    chats: list[dict],
    top_n: int | None = None,
    *,
    duration_sec: int | float | None = None,
    per_hour: int = 2,
    min_count: int = 8,
    max_count: int = 20,
) -> list[dict]:
    """메인 분석 파이프라인. composite 내림차순 정렬된 하이라이트 리스트 반환.

    top_n 명시 시 그 값을 그대로 사용 (legacy / 실험 스크립트 호환).
    None 이면 duration_sec 으로 적응형 개수 계산 (compute_adaptive_top_n).
    duration_sec 도 None 이면 chats 의 마지막 ms 에서 추정.
    """
    logger.info("채팅 분석 시작...")

    raw_count = len(chats)
    chats = _filter_command_messages(chats)
    if raw_count - len(chats):
        logger.info(f"  명령어/투표 필터: {raw_count - len(chats):,}개 제외 (raw {raw_count:,} → {len(chats):,})")

    top_n_source = "explicit"
    if top_n is None:
        if duration_sec is None and chats:
            duration_sec = max((c.get("ms", 0) for c in chats), default=0) / 1000.0
            top_n_source = "adaptive(chat_ms_fallback)"
        else:
            top_n_source = "adaptive(duration_sec)"
        top_n = compute_adaptive_top_n(
            duration_sec, per_hour=per_hour, min_count=min_count, max_count=max_count,
        )
        logger.info(
            f"  적응형 top_n={top_n} (source={top_n_source}, "
            f"duration_sec={duration_sec or 0:.0f}, per_hour={per_hour}, "
            f"min={min_count}, max={max_count})"
        )

    ts = build_time_series(chats, WINDOW_SEC)
    logger.info(f"  시계열 변환 완료: {len(ts['buckets'])}개 버킷")

    raw_peaks = z_score_peaks(ts["buckets"], Z_THRESHOLD)
    logger.info(f"  Z-score 피크 탐지: {len(raw_peaks)}개")

    merged = merge_peaks(raw_peaks, PEAK_MERGE_SEC)
    merged.sort(key=lambda p: p["composite"], reverse=True)

    for rank, p in enumerate(merged, 1):
        p["rank"] = rank

    logger.info(f"  최종 하이라이트: {len(merged)}개 (상위 {min(top_n, len(merged))}개 사용)")
    return merged[:top_n]


def score_highlight_window(
    highlights: list[dict],
    start_sec: int | float,
    end_sec: int | float,
) -> float:
    """Project the production highlight ranking onto one candidate window.

    ``highlights`` must be the unmodified output of :func:`find_edit_points`.
    This helper intentionally knows nothing about labels, source matching, or
    verification.  It lets shadow evaluations score every candidate through
    the same production ranking path instead of inventing a second baseline.
    """

    start = float(start_sec)
    end = float(end_sec)
    if end < start:
        start, end = end, start
    scores = [
        float(row.get("composite") or 0.0)
        for row in highlights or []
        if start <= float(row.get("sec") or 0.0) <= end
    ]
    return round(max(scores, default=0.0), 6)


def get_chats_in_range(chats: list[dict], start_sec: float, end_sec: float) -> list[dict]:
    """특정 시간 범위의 채팅 반환 (명령어/투표 자동 제외)"""
    start_ms = start_sec * 1000
    end_ms = end_sec * 1000
    return [
        c for c in chats
        if start_ms <= c["ms"] <= end_ms and not _is_command_message(c.get("msg", ""))
    ]


def _describe_intensity(h: dict) -> str:
    """하이라이트의 순위/강도를 설명적 표현으로 변환.

    내부 메트릭(composite, count)을 직접 노출하지 않고
    Claude가 맥락적으로 판단할 수 있는 상대 표현을 사용한다.
    """
    rank = h.get("rank", 99)
    if rank <= 3:
        return "폭발 🔥"
    elif rank <= 7:
        return "활발"
    elif rank <= 12:
        return "보통"
    else:
        return "소폭"


def _reaction_family_labels(h: dict) -> list[str]:
    families = set(h.get("reaction_families") or [])
    signals = set(h.get("source_signals") or [])
    for family, signal in _REACTION_SIGNAL_KEYS.items():
        if h.get(f"{family}_cluster") or signal in signals:
            families.add(family)
    return [_REACTION_LABELS[family] for family in _REACTION_FAMILIES if family in families]


def format_chat_highlights_for_prompt(
    highlights: list[dict], chats: list[dict], context_sec: int = 30
) -> str:
    """하이라이트/오디오 반응 구간의 주변 채팅을 프롬프트용 텍스트로 포맷"""
    from .utils import sec_to_hms
    chats = _filter_command_messages(chats)
    lines = []
    for h in highlights:
        sec = h["sec"]
        start = max(0, sec - context_sec)
        end = sec + context_sec
        nearby = get_chats_in_range(chats, start, end)

        intensity = _describe_intensity(h)
        reaction_labels = _reaction_family_labels(h)
        if h.get("source_alignment_candidate"):
            source_start = h.get("source_start_sec", sec)
            source_end = h.get("source_end_sec")
            if source_end is not None:
                time_text = f"{sec_to_hms(source_start)}~{sec_to_hms(source_end)}"
            else:
                time_text = sec_to_hms(sec)
            lines.append(f"### [{time_text}] 유튜브 편집 영상 학습 후보")
            lines.append("  - 판단: 공개 편집본-원본 정렬 데이터에서 나온 원본 구간 후보")
            lines.append("  - 활용: 방송 사실은 이 청크의 자막/채팅 맥락으로 확인한 경우에만 최종 채택")
        elif h.get("laughter_burst"):
            lines.append(f"### [{sec_to_hms(sec)}] 웃음 반응 폭발 😂")
            lines.append("  - 판단: ㅋㅋ/웃음 반응이 연속 집중된 강한 하이라이트 후보")
            lines.append("  - 활용: 실제 장면은 채팅 시각보다 앞쪽일 수 있으므로 주변 자막 맥락으로 원인 장면을 우선 확인")
            if reaction_labels:
                lines.append(f"  - 보조 반응: {', '.join(reaction_labels)} 반응도 같은 구간에 몰림")
        elif h.get("audio_reaction_peak") and "chat" in (h.get("source_signals") or []):
            labels = ", ".join(h.get("audio_labels") or [])
            lines.append(f"### [{sec_to_hms(sec)}] 채팅+오디오 반응 {intensity}")
            lines.append(f"  - 오디오 피크 반영: {labels or 'audio_energy_peak'}")
        elif h.get("audio_reaction_peak"):
            labels = ", ".join(h.get("audio_labels") or [])
            lines.append(f"### [{sec_to_hms(sec)}] 오디오 반응 {intensity}")
            lines.append(
                f"  - 오디오 피크 단서: {labels or 'audio_energy_peak'} "
                "(자막/주변 맥락으로 확인되면 타임라인 후보)"
            )
        elif reaction_labels:
            lines.append(f"### [{sec_to_hms(sec)}] {'/'.join(reaction_labels)} 반응 몰림 {intensity}")
            lines.append("  - 판단: 여러 채팅 이용자의 동시 반응이 모인 약한 하이라이트 후보")
            lines.append("  - 활용: 채팅만으로 방송 사실을 확정하지 말고 주변 자막/장면 맥락으로 원인 장면을 확인")
        else:
            lines.append(f"### [{sec_to_hms(sec)}] 채팅 반응 {intensity}")
        sample = nearby[:30]
        for c in sample:
            ts = sec_to_hms(c["ms"] / 1000.0)
            lines.append(f"  [{ts}] {c['nick']}: {c['msg']}")
        if len(nearby) > 30:
            lines.append(f"  ... 외 {len(nearby) - 30}개")
        lines.append("")

    return "\n".join(lines)
