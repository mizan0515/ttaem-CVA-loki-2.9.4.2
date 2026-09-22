"""Word-evidence-only subtitle presentation timing (approved F1 policy).

This module neither runs ASR nor changes transcript tokens. Missing, malformed,
or conflicting evidence retains the original cues. Times use one source clock.
"""
from __future__ import annotations

from copy import deepcopy
import difflib
import math
from numbers import Real


def norm(text):
    """A language-independent matching alphabet; never used as output text."""
    return "".join(char for char in text if char.isalnum()).lower()


def _finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _cue_shape(cue):
    return (isinstance(cue, dict) and isinstance(cue.get("text"), str)
            and _finite(cue.get("start")) and _finite(cue.get("end")))


def exact_word_maps(cues, words):
    """Map every meaningful cue token to consecutive, reliable source words.

    Punctuation-only tokens stay in the displayed text. A partial matching cue
    never receives interpolated or guessed timings.
    """
    if not isinstance(cues, (list, tuple)):
        return []
    empty = [{"valid": False, "tokens": c.get("text", "").split()
              if isinstance(c, dict) and isinstance(c.get("text"), str) else [],
              "token_words": {}} for c in cues]
    if not isinstance(words, (list, tuple)) or not all(_cue_shape(c) for c in cues):
        return empty
    if not all(isinstance(w, dict) and isinstance(w.get("text"), str) for w in words):
        return empty
    tokens = [token for cue in cues for token in cue["text"].split()]
    left = [(i, norm(t)) for i, t in enumerate(tokens) if norm(t)]
    right = [(i, norm(w["text"])) for i, w in enumerate(words) if norm(w["text"])]
    matcher = difflib.SequenceMatcher(a=[t for _, t in left], b=[t for _, t in right], autojunk=False)
    mapping = {}
    for block in matcher.get_matching_blocks():
        for delta in range(block.size):
            mapping[left[block.a + delta][0]] = right[block.b + delta][0]
    cursor, results = 0, []
    for cue in cues:
        ts = cue["text"].split()
        needed = [cursor + i for i, t in enumerate(ts) if norm(t)]
        indices = [mapping[i] for i in needed if i in mapping]
        valid = bool(needed) and len(indices) == len(needed)
        valid = valid and all(b == a + 1 for a, b in zip(indices, indices[1:]))
        matched = [words[i] for i in indices]
        valid = valid and all(_cue_shape(w) for w in matched)
        valid = valid and all(w["start"] >= 0 and 0 <= w["end"] - w["start"] <= 3.0 for w in matched)
        valid = valid and all(b["start"] >= a["end"] - .001 for a, b in zip(matched, matched[1:]))
        valid = valid and any(w["end"] > w["start"] for w in matched)
        token_words = {i: words[mapping[cursor + i]] for i in range(len(ts))
                       if cursor + i in mapping} if valid else {}
        results.append({"valid": bool(valid), "tokens": ts, "token_words": token_words})
        cursor += len(ts)
    return results


def _word_span(mapping, left, right):
    ws = [w for i, w in mapping["token_words"].items() if left <= i < right]
    return (ws[0]["start"], ws[-1]["end"]) if ws else None


def _parts(mapping, duration):
    count = len(mapping["tokens"])
    whole = _word_span(mapping, 0, count)
    if whole is not None and not 0 <= whole[0] <= whole[1] <= duration:
        return None
    whole_long = whole is not None and whole[1] - whole[0] + .2 > 7
    groups, first = [], 0
    for index in range(1, count):
        span = _word_span(mapping, first, index + 1)
        before, after = mapping["token_words"].get(index - 1), mapping["token_words"].get(index)
        if not span or not before or not after:
            continue
        if span[1] - span[0] + .2 > 7 or (whole_long and after["start"] - before["end"] >= .65):
            groups.append((first, index))
            first = index
    groups.append((first, count))
    out = []
    for a, b in groups:
        span = _word_span(mapping, a, b)
        if span is None:
            return None
        start, end = max(0, span[0] - .08), min(duration, span[1] + .12)
        if end - start < .3:
            end = min(duration, start + .3)
        out.append({"start": start, "end": end, "text": " ".join(mapping["tokens"][a:b]),
                    "_speech_start": span[0], "_speech_end": span[1]})
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["end"]:
            boundary = (out[i - 1]["_speech_end"] + out[i]["_speech_start"]) / 2
            out[i - 1]["end"] = boundary
            out[i]["start"] = boundary
    return out


def valid_output(before, after, duration):
    """False (not an exception) for malformed, lossy, overlapping output."""
    try:
        return (bool(_finite(duration) and duration > 0)
                and all(_cue_shape(r) for r in [*before, *after])
                and " ".join(r["text"] for r in before).split() == " ".join(r["text"] for r in after).split()
                and all(0 <= r["start"] < r["end"] <= duration for r in after)
                and all(b["start"] >= a["end"] - .001 for a, b in zip(after, after[1:])))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def retime(cues, words, locked_boundaries=(), duration=600, enabled=True):
    """Return timing candidates and an audit trail; protect accepted turn edges."""
    original = deepcopy(cues)
    if not enabled:
        return original, {"status": "disabled", "decisions": []}
    if (not valid_output(original, original, duration)
            or not isinstance(locked_boundaries, (list, tuple, set))
            or not all(_finite(t) for t in locked_boundaries)):
        return original, {"status": "fallback", "reason": "invalid_input", "decisions": []}
    maps = exact_word_maps(cues, words)
    proposals, decisions = [], []
    for i, (cue, mapping) in enumerate(zip(cues, maps)):
        parts = _parts(mapping, duration) if mapping["valid"] else None
        reason = "word_timing_proposal"
        if not parts:
            reason = "unreliable_word_mapping"
        elif max(abs(parts[0]["start"] - cue["start"]), abs(parts[-1]["end"] - cue["end"])) > 6:
            parts, reason = None, "excessive_time_shift"
        if parts:
            if any(abs(cue["start"] - t) <= .002 for t in locked_boundaries):
                parts[0]["start"] = cue["start"]
            if any(abs(cue["end"] - t) <= .002 for t in locked_boundaries):
                parts[-1]["end"] = cue["end"]
            if any(not .3 <= p["end"] - p["start"] <= 7.000001 for p in parts):
                parts, reason = None, "locked_boundary_conflict"
        proposals.append(parts or [dict(cue)])
        decisions.append({"cue_index": i, "applied": parts is not None, "reason": reason,
                          "before": dict(cue), "after": []})
    for _ in range(len(cues) + 1):
        changed = False
        for i in range(1, len(proposals)):
            left, right = proposals[i - 1][-1], proposals[i][0]
            if left["end"] <= right["start"] + .000001:
                continue
            left_on, right_on = decisions[i - 1]["applied"], decisions[i]["applied"]
            fixed = any(abs(cues[i - 1]["end"] - t) <= .002 or
                        abs(cues[i]["start"] - t) <= .002 for t in locked_boundaries)
            if fixed:
                pass
            elif left_on and right_on:
                a, b = left["_speech_end"], right["_speech_start"]
                boundary = (a + b) / 2
                if b >= a - .001 and boundary - left["start"] >= .3 and right["end"] - boundary >= .3:
                    left["end"], right["start"] = boundary, boundary
                    continue
            elif left_on and left["_speech_end"] <= right["start"] and right["start"] - left["start"] >= .3:
                left["end"] = right["start"]
                continue
            elif right_on and right["_speech_start"] >= left["end"] and right["end"] - left["end"] >= .3:
                right["start"] = left["end"]
                continue
            for index in (i - 1, i):
                if decisions[index]["applied"]:
                    decisions[index].update(applied=False, reason="neighbor_conflict_fallback")
                    proposals[index] = [dict(cues[index])]
                    changed = True
        if not changed:
            break
    output = [{k: p[k] for k in ("start", "end", "text")} for parts in proposals for p in parts]
    for d, parts in zip(decisions, proposals):
        d["after"] = [{k: p[k] for k in ("start", "end", "text")} for p in parts]
    retained = all(any(abs(c[k] - t) <= .002 for c in output for k in ("start", "end"))
                   for t in locked_boundaries
                   if any(abs(c[k] - t) <= .002 for c in original for k in ("start", "end")))
    if not retained or not valid_output(original, output, duration):
        return original, {"status": "fallback", "reason": "invariant_failed", "decisions": decisions}
    return output, {"status": "candidate", "decisions": decisions}
