"""Anonymous, bounded overlapping-interjection display candidates (F2).

These are timing hypotheses, not speaker separation or identities. Raw speaker
labels are consumed only by anonymous_episodes and never leave that boundary.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .timing import _finite, exact_word_maps, valid_output


def anonymous_episodes(raw):
    """Project raw diarization to only start/end/union-overlap evidence.

    The fixed .6--2-second, >=.6-second overlap policy matches the F2 experiment.
    Malformed evidence fails closed; overlapping same-speaker fragments do not
    count as another voice, and overlapping other voices are not double counted.
    """
    segments = raw.get("segments") if isinstance(raw, dict) else raw
    if not isinstance(segments, (list, tuple)):
        return []
    for s in segments:
        if (not isinstance(s, dict) or not _finite(s.get("start"))
                or not _finite(s.get("end")) or not 0 <= s["start"] < s["end"]
                or isinstance(s.get("speaker"), bool)
                or not isinstance(s.get("speaker"), (str, int))
                or s["speaker"] == ""):
            return []
    eligible = {i: [] for i, s in enumerate(segments) if .6 <= s["end"] - s["start"] <= 2.0}
    active = []
    for index, seg in sorted(enumerate(segments), key=lambda item: item[1]["start"]):
        active = [(j, other) for j, other in active if other["end"] > seg["start"]]
        for j, other in active:
            if other["speaker"] == seg["speaker"]:
                continue
            interval = (seg["start"], min(seg["end"], other["end"]))
            if index in eligible:
                eligible[index].append(interval)
            if j in eligible:
                eligible[j].append(interval)
        active.append((index, seg))
    output = []
    for index, intersections in eligible.items():
        start, end = segments[index]["start"], segments[index]["end"]
        total, previous = 0.0, float("-inf")
        for a, b in sorted(intersections):
            total += max(0.0, b - max(previous, a))
            previous = max(previous, b)
        if total >= .6:
            output.append({"start": start, "end": end, "overlap_sec": total})
    return sorted(output, key=lambda e: e["start"])


def preserve_interjections(cues, words, episodes, duration=600, enabled=True):
    """Keep short reactions in distinct SRT intervals without adding speaker IDs."""
    original = deepcopy(cues)
    if not enabled:
        return original, {"status": "disabled", "decisions": []}
    if not valid_output(cues, cues, duration) or not isinstance(episodes, (list, tuple)):
        return original, {"status": "fallback", "reason": "invalid_input", "decisions": []}
    maps = exact_word_maps(cues, words)
    selected, decisions, safe_events = {}, [], []
    for event in episodes:
        if (not isinstance(event, dict)
                or not all(_finite(event.get(k)) for k in ("start", "end", "overlap_sec"))):
            decisions.append({"applied": False, "kind": "overlap_interjection_display_only",
                              "identity_proven": False, "reason": "invalid_or_outside_episode_policy"})
        else:
            safe_events.append({k: event[k] for k in ("start", "end", "overlap_sec")})
    for event in sorted(safe_events, key=lambda e: e["start"]):
        at, end = event["start"], event["end"]
        d = {"event": event, "applied": False, "kind": "overlap_interjection_display_only", "identity_proven": False}
        if not 0 <= at < end <= duration or not .6 <= end - at <= 2 or not .6 <= event["overlap_sec"] <= end - at + .001:
            d["reason"] = "invalid_or_outside_episode_policy"
            decisions.append(d)
            continue
        index = next((i for i, c in enumerate(cues) if c["start"] < at < end < c["end"]), None)
        if index is None or index in selected or not maps[index]["valid"]:
            d["reason"] = "not_one_available_mapped_cue"
            decisions.append(d)
            continue
        m, cue = maps[index], cues[index]
        options = []
        for split in range(1, len(m["tokens"])):
            a, b = m["token_words"].get(split - 1), m["token_words"].get(split)
            if a and b and b["start"] >= a["end"] - .001:
                options.append({"index": split, "at": (a["end"] + b["start"]) / 2,
                                "terminal": bool(re.search(r"[.!?。！？]$", m["tokens"][split - 1]))})
        pairs = [(a, b) for a in options for b in options if a["index"] < b["index"] and b["terminal"]
                 and abs(a["at"] - at) <= .65 and abs(b["at"] - end) <= .65
                 and a["at"] - cue["start"] >= .3 and b["at"] - a["at"] >= .3 and cue["end"] - b["at"] >= .3]
        if not pairs:
            d["reason"] = "no_two_safe_display_boundaries"
        else:
            a, b = min(pairs, key=lambda p: abs(p[0]["at"] - at) + abs(p[1]["at"] - end))
            selected[index] = (a, b)
            d.update(applied=True, reason="raw_interjection_plus_word_and_terminal_display_hypothesis",
                     cue_index=index, boundaries=[a["at"], b["at"]])
        decisions.append(d)
    output = []
    for index, cue in enumerate(cues):
        if index not in selected:
            output.append(dict(cue))
            continue
        a, b = selected[index]
        ts = maps[index]["tokens"]
        for left, right, start, end in [(0, a["index"], cue["start"], a["at"]),
                                        (a["index"], b["index"], a["at"], b["at"]),
                                        (b["index"], len(ts), b["at"], cue["end"])]:
            output.append({"start": start, "end": end, "text": " ".join(ts[left:right])})
    if not valid_output(cues, output, duration):
        return original, {"status": "fallback", "reason": "invariant_failed", "decisions": decisions}
    return output, {"status": "candidate", "decisions": decisions}
