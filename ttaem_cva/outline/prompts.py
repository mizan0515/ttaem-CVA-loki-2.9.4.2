"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations

from dataclasses import dataclass

import hashlib

import json

from pathlib import Path

from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[2]


_PROMPT_ROOT = _REPO_ROOT / "prompts" / "broadcast_map"


_REQUIRED_METADATA = {
    "name",
    "version",
    "status",
    "language",
    "required_inputs",
    "eval",
}


@dataclass(frozen=True)
class BroadcastMapPromptAsset:
    relative_path: str
    name: str
    version: str
    status: str
    language: str
    required_inputs: tuple[str, ...]
    eval_command: str
    body: str
    body_sha256: str


def _canonical_asset_path(relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or not candidate.parts:
        raise ValueError("BroadcastMap prompt asset path must be repo-relative")
    resolved = (_REPO_ROOT / candidate).resolve(strict=True)
    if not resolved.is_relative_to(_PROMPT_ROOT.resolve(strict=True)):
        raise ValueError("BroadcastMap prompt asset path escapes prompts/broadcast_map")
    current = _REPO_ROOT
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("BroadcastMap prompt asset path contains a symlink")
        attributes = getattr(current.stat(), "st_file_attributes", 0)
        if attributes & 0x400:
            raise ValueError("BroadcastMap prompt asset path contains a reparse point")
    return resolved


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("BroadcastMap prompt asset is missing frontmatter")
    delimiter = text.find("\n---\n", 4)
    if delimiter < 0:
        raise ValueError("BroadcastMap prompt asset frontmatter is not terminated")
    metadata_text = text[4:delimiter]
    body = text[delimiter + len("\n---\n"):]
    try:
        metadata = json.loads(metadata_text)
    except json.JSONDecodeError as exc:
        raise ValueError("BroadcastMap prompt asset frontmatter must be JSON") from exc
    if not isinstance(metadata, dict):
        raise ValueError("BroadcastMap prompt asset frontmatter must be an object")
    return metadata, body


def _validated_metadata(
    metadata: dict[str, Any],
) -> tuple[str, str, str, str, tuple[str, ...], str]:
    missing = sorted(_REQUIRED_METADATA - set(metadata))
    if missing:
        raise ValueError(f"BroadcastMap prompt asset metadata missing: {', '.join(missing)}")
    name = metadata.get("name")
    version = metadata.get("version")
    status = metadata.get("status")
    language = metadata.get("language")
    required_inputs = metadata.get("required_inputs")
    evaluation = metadata.get("eval")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (name, version, status, language)
    ):
        raise ValueError("BroadcastMap prompt asset string metadata is invalid")
    if status not in {"shadow", "candidate"}:
        raise ValueError("BroadcastMap prompt asset status must be shadow or candidate")
    if language not in {"en", "ko"}:
        raise ValueError("BroadcastMap prompt asset language must be en or ko")
    if not isinstance(required_inputs, list) or not required_inputs or not all(
        isinstance(value, str) and value.strip() for value in required_inputs
    ):
        raise ValueError("BroadcastMap prompt asset required_inputs is invalid")
    if (
        not isinstance(evaluation, dict)
        or not isinstance(evaluation.get("command"), str)
        or not evaluation["command"].strip()
    ):
        raise ValueError("BroadcastMap prompt asset eval.command is invalid")
    return (
        name.strip(),
        version.strip(),
        status.strip(),
        language.strip(),
        tuple(value.strip() for value in required_inputs),
        evaluation["command"].strip(),
    )


def load_broadcast_map_prompt_asset(relative_path: str) -> BroadcastMapPromptAsset:
    path = _canonical_asset_path(relative_path)
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("BroadcastMap prompt asset must be UTF-8") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    metadata, body = _split_frontmatter(text)
    if not body:
        raise ValueError("BroadcastMap prompt asset body is empty")
    name, version, status, language, required_inputs, eval_command = _validated_metadata(
        metadata
    )
    return BroadcastMapPromptAsset(
        relative_path=path.relative_to(_REPO_ROOT).as_posix(),
        name=name,
        version=version,
        status=status,
        language=language,
        required_inputs=required_inputs,
        eval_command=eval_command,
        body=body,
        body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )


CHUNK_PROMPT_ASSET = load_broadcast_map_prompt_asset(
    "prompts/broadcast_map/chunk_observer.md"
)


FINALIZER_PROMPT_ASSET = load_broadcast_map_prompt_asset(
    "prompts/broadcast_map/finalizer.md"
)


CHUNK_SYSTEM_PROMPT = CHUNK_PROMPT_ASSET.body


MERGE_SYSTEM_PROMPT = FINALIZER_PROMPT_ASSET.body
