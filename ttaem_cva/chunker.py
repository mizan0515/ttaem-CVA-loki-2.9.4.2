"""SRT → LLM 청크 분할 (srt-chunk.py 로직 재사용)

분할 단위:
    split_by_chars() 와 split_by_tokens() 모두 per-cue `Cue.raw_block` 를
    계량 단위로 사용한다. raw_block 은 인덱스 라인 + "HH:MM:SS,ms --> ..." 타임스탬프
    라인 + 텍스트 라인들 + 빈 줄을 포함하는 **원본 SRT 블록** 이다.
    cues_to_txt() 의 출력("[HH:MM:SS] text")은 보고용 요약이며 분할 단위가 아니다.
    두 단위는 대략 ~2x 차이가 나므로 (raw_block > cues_to_txt) 동일 임계값을 공유하지 않는다.
    이 불변식을 바꾸면 문자·토큰 분할 결과를 함께 검증한다.
"""

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logger = logging.getLogger("pipeline")

SRT_TS_RE = re.compile(r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})")
TIME_LINE_RE = re.compile(r"^\s*(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})")


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    start_ts: str
    end_ts: str
    text_lines: List[str]
    raw_block: str


def _ts_to_ms(ts: str) -> int:
    m = SRT_TS_RE.match(ts.strip())
    if not m:
        raise ValueError(f"Invalid timestamp: {ts}")
    h, mi, s, ms = int(m.group("h")), int(m.group("m")), int(m.group("s")), int(m.group("ms"))
    return (((h * 60 + mi) * 60) + s) * 1000 + ms


def _ms_to_hhmmss(ms: int) -> str:
    sec = ms // 1000
    h = sec // 3600
    sec %= 3600
    m = sec // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_srt(path: str) -> List[Cue]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    blocks = re.split(r"\n{2,}", content.strip(), flags=re.MULTILINE)
    cues: List[Cue] = []

    for b in blocks:
        lines = [ln.rstrip("\n") for ln in b.splitlines()]
        if len(lines) < 2:
            continue
        time_i = None
        for i, ln in enumerate(lines[:6]):
            if TIME_LINE_RE.match(ln):
                time_i = i
                break
        if time_i is None:
            continue
        m = TIME_LINE_RE.match(lines[time_i])
        start_ts, end_ts = m.group(1), m.group(2)
        cues.append(Cue(
            start_ms=_ts_to_ms(start_ts), end_ms=_ts_to_ms(end_ts),
            start_ts=start_ts, end_ts=end_ts,
            text_lines=lines[time_i + 1:],
            raw_block="\n".join(lines) + "\n\n",
        ))

    cues.sort(key=lambda c: (c.start_ms, c.end_ms))
    return cues


def cues_to_txt(cues: List[Cue]) -> str:
    out = []
    for c in cues:
        t = _ms_to_hhmmss(c.start_ms)
        text = " ".join(ln.strip() for ln in c.text_lines if ln.strip())
        if text:
            out.append(f"[{t}] {text}")
    return "\n".join(out).rstrip() + "\n"


def split_by_chars(cues: List[Cue], max_chars: int, overlap_sec: int) -> List[List[Cue]]:
    overlap_ms = overlap_sec * 1000
    chunks: List[List[Cue]] = []
    i, n = 0, len(cues)

    while i < n:
        start_i = i
        char_count = 0
        j = i
        while j < n:
            blk_len = len(cues[j].raw_block)
            if j > i and char_count + blk_len > max_chars:
                break
            char_count += blk_len
            j += 1

        chunks.append(cues[i:j])

        if j < n and overlap_ms > 0:
            next_start_ms = cues[j].start_ms
            rewind_ms = max(0, next_start_ms - overlap_ms)
            k = j
            while k > start_i and cues[k - 1].start_ms >= rewind_ms:
                k -= 1
            next_i = k
        else:
            next_i = j

        if next_i <= i:
            next_i = i + 1
        i = next_i

    return chunks


_TOKEN_ENCODERS: dict = {}


def _get_token_encoder(encoding_name: str):
    """tiktoken 인코더를 lazy 로드. 동일 인코딩은 프로세스 단위로 캐시."""
    cached = _TOKEN_ENCODERS.get(encoding_name)
    if cached is not None:
        return cached
    try:
        import tiktoken
    except ImportError as e:
        raise RuntimeError(
            "chunk_max_tokens 를 사용하려면 tiktoken 이 필요합니다. "
            "`pip install tiktoken` 후 재시도하세요."
        ) from e
    encoder = tiktoken.get_encoding(encoding_name)
    _TOKEN_ENCODERS[encoding_name] = encoder
    return encoder


def split_by_tokens(
    cues: List[Cue],
    max_tokens: int,
    overlap_sec: int,
    encoding_name: str = "cl100k_base",
) -> List[List[Cue]]:
    """token 기반 분할. split_by_chars() 와 동일한 overlap rewind 규칙.

    계량 단위는 per-cue raw_block 의 tiktoken 토큰 수이다 (docstring 참조).
    """
    encoder = _get_token_encoder(encoding_name)
    overlap_ms = overlap_sec * 1000
    chunks: List[List[Cue]] = []
    i, n = 0, len(cues)

    token_count_cache: List[int] = [len(encoder.encode(c.raw_block)) for c in cues]

    while i < n:
        start_i = i
        token_count = 0
        j = i
        while j < n:
            blk_tokens = token_count_cache[j]
            if j > i and token_count + blk_tokens > max_tokens:
                break
            token_count += blk_tokens
            j += 1

        chunks.append(cues[i:j])

        if j < n and overlap_ms > 0:
            next_start_ms = cues[j].start_ms
            rewind_ms = max(0, next_start_ms - overlap_ms)
            k = j
            while k > start_i and cues[k - 1].start_ms >= rewind_ms:
                k -= 1
            next_i = k
        else:
            next_i = j

        if next_i <= i:
            next_i = i + 1
        i = next_i

    return chunks


def filter_cues_by_highlights(
    cues: List[Cue],
    highlights: list[dict],
    hot_radius_sec: int = 300,
    cold_sample_sec: int = 30,
    promoted_anchors: Optional[list[tuple]] = None,
    anchor_radius_sec: int = 60,
) -> List[Cue]:
    """채팅 하이라이트 + 시청자 클립 anchor 기반 자막 필터링.

    - 하이라이트 ±hot_radius_sec 구간: 모든 cue 유지 (상세 분석 대상)
    - promoted_anchors ±anchor_radius_sec 구간: 모든 cue 강제 보존.
      cold-filter sampling 우회 — anchor 주변 setup/payoff 발화 evidence 확보용.
    - 나머지 구간: cold_sample_sec 간격으로 1개만 샘플링 (맥락 유지용)

    이렇게 하면 10시간 VOD 기준 ~60% 토큰 절감.
    highlights/anchors 모두 비어있으면 원본 그대로 반환 (필터링 불가).

    promoted_anchors: list of (offset_sec, like_count, play_count) 또는 동등 dict.
        T2 의 `_select_promoted_anchored_clips()['strict']` 형태와 호환.
    """
    if not cues:
        return cues
    if not highlights and not promoted_anchors:
        return cues

    hot_secs: set[int] = set()
    for h in highlights or []:
        center = int(h["sec"])
        for s in range(center - hot_radius_sec, center + hot_radius_sec + 1):
            hot_secs.add(s)

    anchor_intervals_ms: list[tuple[int, int]] = []
    for a in promoted_anchors or []:
        try:
            offset = int(a[0]) if isinstance(a, (tuple, list)) else int(a.get("sec", a.get("offset_sec")))
        except (TypeError, ValueError, AttributeError, IndexError):
            continue
        if offset < 0:
            continue
        lo_ms = max(0, (offset - anchor_radius_sec) * 1000)
        hi_ms = (offset + anchor_radius_sec) * 1000
        anchor_intervals_ms.append((lo_ms, hi_ms))

    def _cue_in_anchor_window(c: Cue) -> bool:
        for lo, hi in anchor_intervals_ms:
            if c.end_ms >= lo and c.start_ms <= hi:
                return True
        return False

    hot_cues: List[Cue] = []
    cold_cues: List[Cue] = []
    for c in cues:
        if (c.start_ms // 1000) in hot_secs or _cue_in_anchor_window(c):
            hot_cues.append(c)
        else:
            cold_cues.append(c)

    cold_sampled: List[Cue] = []
    last_bucket = -1
    for c in cold_cues:
        bucket = c.start_ms // (cold_sample_sec * 1000)
        if bucket != last_bucket:
            cold_sampled.append(c)
            last_bucket = bucket

    merged = sorted(hot_cues + cold_sampled, key=lambda c: c.start_ms)

    anchor_n = len(anchor_intervals_ms)
    logger.info(
        f"  자막 필터링: {len(cues)}개 → {len(merged)}개 "
        f"(핫 {len(hot_cues)} + 샘플 {len(cold_sampled)}"
        f"{f', anchor 윈도우 {anchor_n}개' if anchor_n else ''}, "
        f"절감 {(1 - len(merged)/len(cues))*100:.0f}%)"
    )
    return merged


def _normalize_promoted_anchors(
    promoted_anchors: Optional[list],
) -> list[dict]:
    """T2 의 strict list ([(off,like,play),...]) 또는 dict list 를 통일된
    dict 형태로 정규화. 불량 항목은 silently drop.

    Returns: [{"sec": int, "ms": int, "like": int, "play": int}, ...] (offset_sec asc)
    """
    if not promoted_anchors:
        return []
    out: list[dict] = []
    for a in promoted_anchors:
        try:
            if isinstance(a, dict):
                sec = int(a.get("sec", a.get("offset_sec")))
                like = int(a.get("like", a.get("like_count", 0)) or 0)
                play = int(a.get("play", a.get("play_count", 0)) or 0)
            else:
                sec = int(a[0])
                like = int(a[1]) if len(a) > 1 and a[1] is not None else 0
                play = int(a[2]) if len(a) > 2 and a[2] is not None else 0
        except (TypeError, ValueError, AttributeError, IndexError):
            continue
        if sec < 0:
            continue
        out.append({"sec": sec, "ms": sec * 1000, "like": max(0, like), "play": max(0, play)})
    out.sort(key=lambda d: d["sec"])
    return out


def chunk_srt(
    srt_path: str,
    max_chars: int = 150000,
    overlap_sec: int = 45,
    max_tokens: Optional[int] = None,
    tokenizer_encoding: str = "cl100k_base",
    highlights: Optional[list[dict]] = None,
    highlight_radius_sec: int = 300,
    cold_sample_sec: int = 30,
    promoted_anchors: Optional[list] = None,
    anchor_radius_sec: int = 60,
    anchor_silence_radius_sec: int = 5,
) -> list[dict]:
    """
    SRT 파일을 청크로 분할.
    반환: [{"index": 1, "start_ms": ..., "end_ms": ..., "text": "..."}, ...]

    highlights가 주어지면 채팅 하이라이트 기반 필터링을 먼저 적용:
      - 하이라이트 ±highlight_radius_sec: 상세 (모든 자막 유지)
      - 나머지: cold_sample_sec 간격으로 샘플링

    우선순위:
        max_tokens 가 None 이 아니면 split_by_tokens() 사용 (토큰 기준).
        None 이면 max_chars 로 split_by_chars() 사용 (글자 기준, 레거시).
    두 경로 모두 per-cue raw_block 을 계량 단위로 삼는다.
    """
    if max_tokens is not None:
        logger.info(
            f"SRT 청크 분할: {srt_path} "
            f"(max_tokens={max_tokens}, encoding={tokenizer_encoding}, overlap={overlap_sec}s)"
        )
    else:
        logger.info(f"SRT 청크 분할: {srt_path} (max_chars={max_chars}, overlap={overlap_sec}s)")

    cues = parse_srt(srt_path)
    if not cues:
        logger.warning("SRT에 자막이 없습니다.")
        return []

    anchors_norm = _normalize_promoted_anchors(promoted_anchors)
    silence_window_ms = max(0, anchor_silence_radius_sec) * 1000
    _silence_cache: dict[int, bool] = {}
    _original_cues = cues

    def _anchor_silence_flag(anchor_ms: int) -> bool:
        cached = _silence_cache.get(anchor_ms)
        if cached is not None:
            return cached
        lo = anchor_ms - silence_window_ms
        hi = anchor_ms + silence_window_ms
        flag = True
        for c in _original_cues:
            if c.end_ms >= lo and c.start_ms <= hi:
                flag = False
                break
        _silence_cache[anchor_ms] = flag
        return flag

    if highlights or anchors_norm:
        cues = filter_cues_by_highlights(
            cues, highlights or [],
            hot_radius_sec=highlight_radius_sec,
            cold_sample_sec=cold_sample_sec,
            promoted_anchors=anchors_norm,
            anchor_radius_sec=anchor_radius_sec,
        )

    if max_tokens is not None:
        chunks = split_by_tokens(cues, max_tokens, overlap_sec, tokenizer_encoding)
    else:
        chunks = split_by_chars(cues, max_chars, overlap_sec)
    result = []

    silence_anchor_count = 0
    attached_anchor_secs: set[int] = set()
    for idx, chunk_cues in enumerate(chunks, 1):
        if not chunk_cues:
            continue
        start_ms = chunk_cues[0].start_ms
        end_ms = max(c.end_ms for c in chunk_cues)
        text = cues_to_txt(chunk_cues)

        chunk_anchors: list[dict] = []
        for a in anchors_norm:
            if start_ms <= a["ms"] <= end_ms:
                silent = _anchor_silence_flag(a["ms"])
                if silent:
                    silence_anchor_count += 1
                chunk_anchors.append({**a, "silent": silent})
                attached_anchor_secs.add(a["sec"])

        result.append({
            "index": idx,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start_hhmmss": _ms_to_hhmmss(start_ms),
            "end_hhmmss": _ms_to_hhmmss(end_ms),
            "cue_count": len(chunk_cues),
            "char_count": len(text),
            "text": text,
            "promoted_anchors": chunk_anchors,
        })

    synthetic_anchor_count = 0
    if anchors_norm:
        for a in anchors_norm:
            if a["sec"] in attached_anchor_secs:
                continue
            silent = _anchor_silence_flag(a["ms"])
            if silent:
                silence_anchor_count += 1
            start_ms = max(0, a["ms"] - max(1, anchor_silence_radius_sec) * 1000)
            end_ms = a["ms"] + max(1, anchor_silence_radius_sec) * 1000
            text = (
                f"[{_ms_to_hhmmss(a['ms'])}] "
                "자막 없음 — 시청자 클립 anchor 주변 채팅만 확인\n"
            )
            result.append({
                "index": 0,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "start_hhmmss": _ms_to_hhmmss(start_ms),
                "end_hhmmss": _ms_to_hhmmss(end_ms),
                "cue_count": 0,
                "char_count": len(text),
                "text": text,
                "promoted_anchors": [{**a, "silent": silent}],
            })
            synthetic_anchor_count += 1
            logger.info(
                f"  promoted anchor [{_ms_to_hhmmss(a['ms'])}] "
                "무자막 anchor 청크 생성"
            )

    if result:
        result.sort(key=lambda ch: (ch["start_ms"], ch["end_ms"]))
        for idx, chunk in enumerate(result, 1):
            chunk["index"] = idx

    logger.info(f"  {len(result)}개 청크 생성 (총 {len(cues)}개 자막)")
    if anchors_norm:
        logger.info(
            f"  promoted anchor: {len(anchors_norm)}개 적용 "
            f"(silence {silence_anchor_count}개, synthetic {synthetic_anchor_count}개)"
        )
    return result
