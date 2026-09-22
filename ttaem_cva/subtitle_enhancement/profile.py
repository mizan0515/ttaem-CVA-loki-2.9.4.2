"""Exact selected-channel configuration; resolving never loads an encoder."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import zipfile
from ..local_files import plain_path
from types import MappingProxyType
from typing import Mapping

ENCODER_ID = "eres2netv2_192_v1"
VOICE_SCHEMA = "chzz-streamer-voice-reference-v1"
_CHANNEL = re.compile(r"[0-9a-f]{32}\Z")
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def sha256_file(path: Path) -> str:
    plain_path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def contained_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Expected a contained relative artifact path")
    base = plain_path(root).resolve()
    result = plain_path(base / relative).resolve()
    if not result.is_relative_to(base) or result == base:
        raise ValueError("Artifact path escapes its root")
    return result


def valid_identity(platform: str, channel_id: str) -> bool:
    return platform == "chzzk" and isinstance(channel_id, str) and bool(_CHANNEL.fullmatch(channel_id))


@dataclass(frozen=True)
class ResolvedProfile:
    platform: str
    channel_id: str
    enabled: bool = False
    reason: str = "not_selected"
    timing_enabled: bool = False
    turn_display_enabled: bool = False
    audio_events_enabled: bool = False
    music_enabled: bool = False
    laughter_enabled: bool = False
    reference_enabled: bool = False
    reference_mode: str = "observe"
    reference_reason: str = "not_selected"
    profile_fingerprint: str = ""
    voice_manifest_path: Path | None = None
    voice_manifest_sha256: str = ""
    embedding_path: Path | None = None
    embedding_sha256: str = ""
    encoder_path: Path | None = None
    encoder_sha256: str = ""
    policies: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    configured_modules: frozenset[str] = frozenset()


def _read_json(path: Path) -> dict:
    plain_path(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Profile JSON exceeds 1 MiB")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Profile JSON must be an object")
    return value


def validate_voice_manifest(manifest_path: Path, references_root: Path, *, platform: str,
                            channel_id: str, revision: str) -> tuple[dict, Path, Path]:
    """Identity, containment, hashes and vector validation without torch."""
    import numpy as np

    if not manifest_path.resolve().is_relative_to(references_root.resolve()):
        raise ValueError("Manifest escapes reference root")
    manifest = _read_json(manifest_path)
    if (manifest.get("schema"), manifest.get("platform"), manifest.get("channel_id"), manifest.get("revision")) != (
            VOICE_SCHEMA, platform, channel_id, revision):
        raise ValueError("Voice manifest identity/version mismatch")
    encoder, vectors = manifest.get("encoder", {}), manifest.get("embeddings", {})
    if not isinstance(encoder, dict) or not isinstance(vectors, dict):
        raise ValueError("Invalid encoder/vector specification")
    if encoder.get("id") != ENCODER_ID or encoder.get("dimension") != 192:
        raise ValueError("Unsupported encoder contract")
    encoder_path = contained_path(references_root, encoder.get("path"))
    embedding_path = contained_path(manifest_path.parent, vectors.get("path"))
    for spec, path in ((encoder, encoder_path), (vectors, embedding_path)):
        digest = spec.get("sha256")
        if not isinstance(digest, str) or not _SHA.fullmatch(digest) or sha256_file(path) != digest:
            raise ValueError("Reference artifact checksum mismatch")
    if embedding_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Reference vectors exceed bounded size")
    with zipfile.ZipFile(embedding_path) as archive:
        if len(archive.namelist()) != 2 or set(archive.namelist()) != {"mean_profile.npy", "normalized.npy"} or sum(x.file_size for x in archive.infolist()) > 4 * 1024 * 1024:
            raise ValueError("Unpacked reference vectors exceed bounded schema")
        for name in archive.namelist():
            with archive.open(name) as member:
                version = np.lib.format.read_magic(member)
                readers = {(1, 0): np.lib.format.read_array_header_1_0, (2, 0): np.lib.format.read_array_header_2_0}
                if version not in readers:
                    raise ValueError("Unsupported reference array format")
                shape, _, dtype = readers[version](member, max_header_size=4096)
                valid_shape = shape == (192,) if name == "mean_profile.npy" else len(shape) == 2 and 1 <= shape[0] <= 64 and shape[1] == 192
                if not valid_shape or dtype.kind != "f" or dtype.itemsize not in (4, 8):
                    raise ValueError("Invalid bounded reference array header")
    with np.load(embedding_path, allow_pickle=False) as archive:
        mean, normalized = archive["mean_profile"], archive["normalized"]
        if mean.shape != (192,) or normalized.ndim != 2 or normalized.shape[1] != 192 or not 1 <= len(normalized) <= 64:
            raise ValueError("Invalid reference embedding shape")
        if not np.isfinite(mean).all() or not np.isfinite(normalized).all():
            raise ValueError("Nonfinite reference embedding")
        if not np.isclose(np.linalg.norm(mean), 1, atol=1e-4) or not np.allclose(np.linalg.norm(normalized, axis=1), 1, atol=1e-4):
            raise ValueError("Reference vectors must be normalized")
    return manifest, embedding_path, encoder_path


def resolve_profile(platform: str, channel_id: str, *, selected: bool,
                    profiles_root: Path, references_root: Path, reference_allowed: bool = True,
                    require_active: bool = False) -> ResolvedProfile:
    """Fail closed: never guess names or fall back to a different person's voice."""
    base = dict(platform=platform, channel_id=channel_id)
    if selected is not True:
        return ResolvedProfile(**base)
    if not valid_identity(platform, channel_id):
        return ResolvedProfile(**base, reason="invalid_channel_identity", reference_reason="invalid_channel_identity")
    registry_path = profiles_root / platform / channel_id / "profile.json"
    selection_path = references_root / platform / channel_id / "active.json"
    if not registry_path.exists() and not selection_path.exists():
        return ResolvedProfile(**base, reason="profile_missing", reference_reason="profile_missing")
    try:
        if not registry_path.resolve().is_relative_to(profiles_root.resolve()):
            raise ValueError("Registry escapes profile root")
        registry = _read_json(registry_path) if registry_path.exists() else {
            "schema_version": 1, "platform": platform, "channel_id": channel_id,
            "modules": {"reference": {"enabled": True, "mode": "observe"}},
        }
        if registry.get("schema_version") != 1 or registry.get("platform") != platform or registry.get("channel_id") != channel_id:
            raise ValueError("Registry identity/version mismatch")
        modules = registry["modules"]
        if not isinstance(modules, dict):
            raise ValueError("Invalid module configuration")
        for name in ("timing", "turn_display", "audio_events", "reference"):
            module = modules.get(name, {})
            if not isinstance(module, dict) or not isinstance(module.get("enabled", False), bool):
                raise ValueError("Module enabled flag must be a boolean")
        fingerprint = hashlib.sha256(json.dumps(registry, sort_keys=True).encode("utf-8")).hexdigest()
        timing = modules.get("timing", {}).get("enabled", False)
        turn = modules.get("turn_display", {}).get("enabled", False)
        event = modules.get("audio_events", {})
        events = event.get("enabled", False)
        for field_name in ("music_enabled", "laughter_enabled"):
            if not isinstance(event.get(field_name, True), bool):
                raise ValueError("Event flags must be booleans")
        values = dict(**base, enabled=bool(timing or turn or events or modules.get("reference", {}).get("enabled", False)),
                      reason="ready", timing_enabled=timing, turn_display_enabled=turn,
                      audio_events_enabled=events, music_enabled=events and event.get("music_enabled", True),
                      laughter_enabled=events and event.get("laughter_enabled", True),
                      profile_fingerprint=fingerprint,
                      configured_modules=frozenset(modules),
                      policies=MappingProxyType({"timing": "F2-v1", "turn_display": "F2-v1", "audio_events": "E2-v1"}))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError):
        return ResolvedProfile(**base, reason="profile_invalid", reference_reason="profile_invalid")
    reference = modules.get("reference", {})
    if reference_allowed is not True:
        return ResolvedProfile(**values, reference_reason="globally_disabled")
    if not reference.get("enabled", False):
        return ResolvedProfile(**values, reference_reason="reference_disabled")
    if require_active and not selection_path.exists():
        return ResolvedProfile(**values, reference_reason="voice_profile_not_selected")
    if selection_path.exists():
        try:
            if not selection_path.resolve().is_relative_to(references_root.resolve()):
                raise ValueError("Selection escapes reference root")
            selection = _read_json(selection_path)
            if (selection.get("schema"), selection.get("platform"), selection.get("channel_id"),
                    selection.get("mode")) != ("chzz.voice-profile-selection.v1", platform, channel_id, "observe"):
                raise ValueError("Selection identity/version mismatch")
            reference = {"enabled": True, "mode": "observe", "revision": selection.get("revision")}
            fingerprint = hashlib.sha256((fingerprint + sha256_file(selection_path)).encode("ascii")).hexdigest()
            values["profile_fingerprint"] = fingerprint
        except (OSError, ValueError, TypeError):
            return ResolvedProfile(**values, reference_reason="voice_selection_invalid")
    if reference.get("mode", "observe") != "observe":
        return ResolvedProfile(**values, reference_reason="unsupported_reference_mode")
    revision = reference.get("revision")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision) or revision in (".", ".."):
        return ResolvedProfile(**values, reference_reason="invalid_reference_revision")
    manifest_path = references_root / platform / channel_id / revision / "manifest.json"
    if not manifest_path.exists():
        return ResolvedProfile(**values, reference_reason="voice_profile_missing")
    try:
        manifest, embeddings, encoder = validate_voice_manifest(
            manifest_path, references_root, platform=platform, channel_id=channel_id, revision=revision)
        values["profile_fingerprint"] = hashlib.sha256(
            (fingerprint + sha256_file(manifest_path)).encode("ascii")).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError):
        return ResolvedProfile(**values, reference_reason="voice_profile_invalid")
    return ResolvedProfile(**values, reference_enabled=True, reference_reason="ready_observe_only",
                           voice_manifest_path=manifest_path, voice_manifest_sha256=sha256_file(manifest_path), embedding_path=embeddings,
                           embedding_sha256=manifest["embeddings"]["sha256"],
                           encoder_path=encoder, encoder_sha256=manifest["encoder"]["sha256"])
