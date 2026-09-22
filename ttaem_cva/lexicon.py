"""Selected unchanged Loki 2.9.4.2 algorithms; local adapters own file access."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

_TOKEN_RE = re.compile(r"[A-Za-z가-힣][A-Za-z0-9가-힣]{1,}")

_FENCED_CODE_RE = re.compile(r"(?:```|~~~).*?(?:```|~~~)", re.DOTALL)

_INLINE_CODE_RE = re.compile(r"`[^`\r\n]+`")

_CODE_LINE_RE = re.compile(
    r"(?ix)^\s*(?:"
    r"async\s+def\b|def\b|class\b|function\b|import\b|from\s+\S+\s+import\b|"
    r"return\b|(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=|"
    r"[A-Za-z_$][\w$]*(?:\[[^\]]+\]|\.[A-Za-z_$][\w$]*)*\s*(?:=|\+=|-=|\*=|/=)|"
    r"[\[{]?\s*[\"'][A-Za-z_$][\w$]*[\"']\s*:|"
    r"</?[A-Za-z][^>]*>"
    r")"
)

_CODE_ARTIFACT_RE = re.compile(
    r"(?ix)(?:"
    r"\b[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+\b|"
    r"\b[A-Za-z_][A-Za-z0-9_]{1,}(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b|"
    r"\b[A-Za-z_][A-Za-z0-9_]*\s*\([^\r\n)]*\)|"
    r"\b(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\.(?:py|pyw|js|jsx|ts|tsx|"
    r"ps1|bat|cmd|sh|java|cs|cpp|go|rs)\b|"
    r"\b[A-Za-z0-9_.-]+\.(?:py|pyw|js|jsx|ts|tsx|ps1|bat|cmd|sh|java|cs|cpp|"
    r"go|rs)\b|"
    r"(?:[A-Za-z]:[\\/]|\.{1,2}[\\/])\S+|"
    r"</?[A-Za-z][^>\r\n]*>"
    r")"
)

_STOPWORDS = {
    "진짜","이거","그거","저거","이건","근데","그냥","정말","아니","없다","있다",
    "하다","되다","나서","으로","에서","까지","부터","이랑","라고","라는","하면",
    "해서","뭐야","뭐지","뭐냐","ㅋㅋ","ㅋㅋㅋ","ㅠㅠ","ㄷㄷ","ㅇㅇ","ㅊㅋ","ㅎㅎ",
    "스트리머","스트리밍","방송","채팅","채팅창","시청자","구독자","오늘","어제",
    "지금","이제","여기","저기","거기","그럼","근데","아니면","그리고","그래서",
    "아하","이야","오호","와우","야야","그래","맞아","응","네","예","아니","아뇨",
    "감사","고마워","부탁","안녕","환영","수고","잘자","굿밤","굿모닝",
    "으악","으아","아이고","아이구","으음","어라","어쩔","어머","오마이",
    "너무","이게","이걸","많이","그래도","역시","어우","어어","우리","일단",
    "아님","아오","아는데","아무래도","상대","좋은데","좋다","그건","좀","많은",
    "정말","진짜로","약간","완전","대박","레알","헉","헐","에잉","에이","음",
    "어떻게","뭐","왜","어디","언제","누구","이런","저런","그런","어떤","모든",
    "좋아","싫어","같아","같은","같이","같네","같다","보니","보면","보여","보자",
    "이제","저는","저도","나도","내가","내거","제가","저희","우리는","너도","너는",
    "근데요","아니요","네네","으응","응응","나만","나는","나만의",
    "그게","이미","바로","제발","원래","가자","나이스","역대급","이러면","같은데",
    "처음","마지막","다음","오늘은","오늘도","내일","어제는","계속","점점","갑자기",
    "잠깐","잠시","빨리","천천히","조금","많아","많네","적어","아예","전혀",
    "어차피","결국","드디어","마침내","이번","저번","다른","같다고","아무도","모두",
    "안돼","안됨","됐어","됐다","했어","했다","왔어","왔다","갔어","갔다",
    "보고","보면서","듣고","말고","해주","해줘","해야","하지","하나","둘이",
    "아직","없어","있어","맞음","틀림","무조건","요즘","평소","항상","가끔",
    "있는","없는","있음","없음","있다고","없다고","아닌","맞는","아닌데","맞는데",
    "gg","GG","lol","LOL","ok","OK","vs","VS","the","and","you","for","that","this",
    "async","await","class","const","def","false","from","function","import","lambda",
    "let","none","null","return","self","true","var",
}

_JOSA_SUFFIXES = (
    "에서는", "에게서", "께서는", "에서도", "에서의",
    "에게", "에서", "께서", "라고", "이라고", "이라는", "라는",
    "으로", "이라", "이며",
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "로",
    "와", "과", "랑", "이랑",
    "부터", "까지", "마저", "조차", "보다", "처럼",
)

_VERB_ENDING_RE = re.compile(
    r"("
    r"습니다|입니다|니다|"
    r"했다|한다|했어|해요|해서|하여|하면|하고|하지|하니|하는|할|함|함은|했어요|"
    r"있다|있어|있는|있고|있으|있습|"
    r"없다|없어|없는|없고|"
    r"된다|됐다|됐어|되어|되는|"
    r"이다|였다|였어|"
    r"같다|같아|같은|같이|같네|"
    r"였|겠|"
    r"었다|었어|었어요|았다|았어|았어요|"
    r"드렸|드려|드린|"
    r"보면|보니|보자|보여|"
    r"오는|오지|오면|간다|갔다|"
    r"싶다|싶어|싶은"
    r")$"
)

def _normalize_korean(tok: str) -> str | None:
    """한국어 토큰을 명사 root 로 정규화. 동사 활용형이면 None."""
    if not tok or tok.isascii():
        return tok or None
    if _VERB_ENDING_RE.search(tok):
        return None
    for josa in sorted(_JOSA_SUFFIXES, key=len, reverse=True):
        if len(tok) > len(josa) + 1 and tok.endswith(josa):
            stripped = tok[: -len(josa)]
            if len(stripped) >= 2:
                return stripped
            break
    return tok

def _strip_code_artifacts(text: str) -> str:
    """후보 추출용 복사본에서 명백한 코드 조각만 제거한다."""
    without_blocks = _FENCED_CODE_RE.sub(" ", text or "")
    without_inline = _INLINE_CODE_RE.sub(" ", without_blocks)
    kept_lines: list[str] = []
    for line in without_inline.splitlines():
        if _CODE_LINE_RE.search(line):
            continue
        cleaned = _CODE_ARTIFACT_RE.sub(" ", line)
        if re.search(r"(?:\[[^\]]*\]|\{[^}]*\}).*(?:=>|\+=|-=|\*=|/=)", cleaned):
            continue
        kept_lines.append(cleaned)
    return "\n".join(kept_lines)

def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for raw in _TOKEN_RE.findall(_strip_code_artifacts(text)):
        norm = _normalize_korean(raw)
        if norm:
            out.append(norm)
    return out

def _is_likely_proper(token: str) -> bool:
    """짧거나 일반어/stopword 면 False."""
    if len(token) < 2 or len(token) > 20:
        return False
    if token in _STOPWORDS:
        return False
    if token.lower() in _STOPWORDS:
        return False
    if token.isdigit():
        return False
    return True

def _rank_terms(counter: Counter, limit: int) -> list[str]:
    """빈도순 정렬 후 상위 N개. 동률은 길이 긴 쪽 우선 (보통 더 특이함).

    대소문자만 다른 영문 토큰(`lck` vs `LCK`)은 합산하고 가장 빈도 높은 표기 채택.
    한글은 그대로 유지 (대소문자 개념 없음).
    """
    folded: Counter = Counter()
    canonical: dict[str, str] = {}
    canonical_count: dict[str, int] = {}
    for t, c in counter.items():
        if not _is_likely_proper(t):
            continue
        is_ascii = t.isascii()
        key = t.lower() if is_ascii else t
        folded[key] += c
        if c > canonical_count.get(key, -1):
            canonical[key] = t
            canonical_count[key] = c
    items = [(canonical[k], folded[k]) for k in folded]
    items.sort(key=lambda tc: (-tc[1], -len(tc[0]), tc[0]))
    return [t for t, _ in items[:limit]]

def _from_titles(titles: Iterable[str]) -> Counter:
    counter: Counter = Counter()
    for t in titles:
        for tok in _tokenize(t or ""):
            counter[tok] += 3
    return counter

def _from_context_doc(text: str) -> Counter:
    """사용자 수기 작성 맥락 문서 — 가장 신뢰 가능한 표기 소스. 가중치 ×5."""
    counter: Counter = Counter()
    if not text:
        return counter
    for tok in _tokenize(text):
        counter[tok] += 5
    return counter

def format_for_whisper(terms: list[str], prefix: str = "") -> str:
    """Whisper initial_prompt 로 주입할 문자열.

    Whisper prompt 는 ~224 토큰 제약. 한국어 토큰 1~2자당 1 토큰 가량이니
    30개 * 평균 4자 ≈ 120자 → 60~120 토큰 정도로 예산 내.
    """
    if not terms:
        return prefix or ""
    head = prefix or "안녕하세요, 환영합니다. 오늘도 재밌게 해봅시다!"
    joined = ", ".join(terms)
    return f"{head} 자주 등장하는 표기: {joined}."
