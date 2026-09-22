"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]


_PROMPT_ROOT = _REPO_ROOT / "prompts"


def load_fixed_prompt(relative_path: str) -> str:
    candidate = Path(relative_path)
    if candidate.is_absolute() or not candidate.parts or candidate.parts[0] != "prompts":
        raise ValueError("fixed prompt asset path must be repo-relative under prompts/")
    resolved = (_REPO_ROOT / candidate).resolve(strict=True)
    if not resolved.is_relative_to(_PROMPT_ROOT.resolve(strict=True)):
        raise ValueError("fixed prompt asset path escapes prompts/")
    current = _REPO_ROOT
    for part in candidate.parts:
        current /= part
        if current.is_symlink() or getattr(current.stat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("fixed prompt asset path contains a link or reparse point")
    try:
        text = resolved.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("fixed prompt asset must be UTF-8") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")
