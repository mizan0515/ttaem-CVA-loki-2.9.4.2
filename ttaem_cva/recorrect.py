"""Approved vocabulary corrections from Loki 2.9.4.2; no model calls."""
def _apply_replacements(entries: list[dict], reps: list[tuple[str, str]]) -> int:
    """텍스트 필드에만 substring 치환. timestamp/idx 무손상.

    적용 순서: old 길이 내림차순 — 긴 매치를 먼저 소비해
    "탬탬버린" 이 "탬탬"+"버린" 으로 부분 치환되는 사고 방지.
    """
    ordered = sorted(reps, key=lambda r: -len(r[0]))
    n_changed = 0
    for e in entries:
        new_text = e["text"]
        for old, new in ordered:
            new_text = new_text.replace(old, new)
        if new_text != e["text"]:
            e["text"] = new_text
            n_changed += 1
    return n_changed
