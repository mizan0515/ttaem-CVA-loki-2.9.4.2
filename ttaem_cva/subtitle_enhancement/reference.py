"""Optional portable voice-reference observation. Never edits subtitles/identity.

ERes2NetV2 architecture below is adapted from 3D-Speaker (Copyright 3D-Speaker,
https://github.com/alibaba-damo-academy/3D-Speaker), Apache License 2.0:
https://www.apache.org/licenses/LICENSE-2.0 . Only the 192-dim speaker forward
is retained; no TTS features, training or synthesis implementation is included.
Dependencies/model loading are lazy and occur only after the exact profile gate.
"""
from __future__ import annotations

import math
from pathlib import Path
import time
import wave

from .profile import ResolvedProfile, sha256_file

RATE = 16000


def _build_eres2_model():
    import torch
    from torch import nn
    from torch.nn import functional as F

    class AFF(nn.Module):
        def __init__(self, channels):
            super().__init__()
            self.local_att = nn.Sequential(
                nn.Conv2d(channels * 2, channels // 4, 1), nn.BatchNorm2d(channels // 4),
                nn.SiLU(inplace=True), nn.Conv2d(channels // 4, channels, 1), nn.BatchNorm2d(channels))

        def forward(self, x, other):
            attention = 1.0 + torch.tanh(self.local_att(torch.cat((x, other), dim=1)))
            return x * attention + other * (2.0 - attention)

    class Block(nn.Module):
        def __init__(self, in_planes, planes, stride=1, fused=False):
            super().__init__()
            self.width = planes * 24 // 64
            width = self.width
            self.conv1 = nn.Conv2d(in_planes, width * 4, 1, stride=stride, bias=False)
            self.bn1 = nn.BatchNorm2d(width * 4)
            self.convs = nn.ModuleList([nn.Conv2d(width, width, 3, padding=1, bias=False) for _ in range(4)])
            self.bns = nn.ModuleList([nn.BatchNorm2d(width) for _ in range(4)])
            self.fuse_models = nn.ModuleList([AFF(width) for _ in range(3)]) if fused else None
            self.relu = nn.Hardtanh(0, 20, inplace=True)
            self.conv3 = nn.Conv2d(width * 4, planes * 4, 1, bias=False)
            self.bn3 = nn.BatchNorm2d(planes * 4)
            self.shortcut = nn.Sequential()
            if stride != 1 or in_planes != planes * 4:
                self.shortcut = nn.Sequential(nn.Conv2d(in_planes, planes * 4, 1, stride=stride, bias=False),
                                              nn.BatchNorm2d(planes * 4))

        def forward(self, x):
            parts = torch.split(self.relu(self.bn1(self.conv1(x))), self.width, 1)
            outputs = []
            for i, part in enumerate(parts):
                if i == 0:
                    current = part
                elif self.fuse_models is None:
                    current = current + part
                else:
                    current = self.fuse_models[i - 1](current, part)
                current = self.relu(self.bns[i](self.convs[i](current)))
                outputs.append(current)
            return self.relu(self.bn3(self.conv3(torch.cat(outputs, 1))) + self.shortcut(x))

    class ERes2NetV2(nn.Module):
        def __init__(self):
            super().__init__()
            self.conv1 = nn.Conv2d(1, 64, 3, padding=1, bias=False)
            self.bn1 = nn.BatchNorm2d(64)
            current = 64
            for number, (planes, count, stride, fused) in enumerate(
                    ((64, 3, 1, False), (128, 4, 2, False), (256, 6, 2, True), (512, 3, 2, True)), 1):
                blocks = []
                for index in range(count):
                    blocks.append(Block(current, planes, stride if index == 0 else 1, fused))
                    current = planes * 4
                setattr(self, f"layer{number}", nn.Sequential(*blocks))
            self.layer3_ds = nn.Conv2d(1024, 2048, 3, padding=1, stride=2, bias=False)
            self.fuse34 = AFF(2048)
            self.seg_1 = nn.Linear(40960, 192)

        def forward(self, x):
            out = F.relu(self.bn1(self.conv1(x.permute(0, 2, 1).unsqueeze(1))))
            out3 = self.layer3(self.layer2(self.layer1(out)))
            fused = self.fuse34(self.layer4(out3), self.layer3_ds(out3))
            stats = torch.cat((fused.mean(dim=-1).flatten(start_dim=1),
                               torch.sqrt(torch.var(fused, dim=-1) + 1e-8).flatten(start_dim=1)), 1)
            return self.seg_1(stats)

    return ERes2NetV2()


class ERes2ReferenceEncoder:
    """Cached local checkpoint -> canonical 16k FBank -> unit speaker vector192."""

    def __init__(self, encoder_path: Path, expected_sha256: str, *, threads: int = 2):
        import torch
        if not 1 <= threads <= 8:
            raise ValueError("Reference CPU threads must be 1-8")
        if sha256_file(encoder_path) != expected_sha256:
            raise ValueError("Encoder checksum mismatch")
        self.threads = threads
        self.model = _build_eres2_model()
        self.model.load_state_dict(torch.load(str(encoder_path), map_location="cpu", weights_only=True), strict=True)
        self.model.eval().cpu()

    def embed(self, audio):
        import numpy as np
        import torch
        from torchaudio.compliance.kaldi import fbank

        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim != 1 or not RATE <= len(audio) <= 30 * RATE or not np.isfinite(audio).all():
            raise ValueError("Embedding requires finite 1-30 second mono 16k audio")
        previous_threads = torch.get_num_threads()
        try:
            torch.set_num_threads(self.threads)
            with torch.inference_mode():
                features = fbank(torch.from_numpy(audio.copy()).unsqueeze(0), num_mel_bins=80,
                                 sample_frequency=RATE, dither=0)
                features = features - features.mean(0, keepdim=True)
                raw = self.model(features.unsqueeze(0)).squeeze(0).cpu().numpy().copy()
        finally:
            torch.set_num_threads(previous_threads)
        if raw.shape != (192,) or not np.isfinite(raw).all() or np.linalg.norm(raw) <= 1e-10:
            raise ValueError("Invalid speaker embedding")
        return raw / np.linalg.norm(raw)


def read_pcm16(path: Path, *, start: float = 0, end: float | None = None):
    import numpy as np
    with wave.open(str(path), "rb") as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (RATE, 1, 2):
            raise ValueError("Expected 16k mono PCM16 WAV")
        duration = stream.getnframes() / RATE
        end = duration if end is None else end
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
            raise ValueError("Window is outside source audio")
        if end - start > 30:
            raise ValueError("Read a bounded reference window of at most 30 seconds")
        stream.setpos(round(start * RATE))
        return np.frombuffer(stream.readframes(round((end - start) * RATE)), dtype="<i2").astype(np.float32) / 32768.0


def observe_reference(audio_path: Path, profile: ResolvedProfile, *, windows=None,
                      max_windows: int = 30, max_seconds: float = 60,
                      encoder_factory=ERes2ReferenceEncoder) -> dict:
    """Bounded observation only. Cosine is not probability, identity or a veto.

Caller-provided windows use the WAV-relative clock. Otherwise an evenly spaced,
content-independent sample is used; this is not continuous speaker tracking.
"""
    out = {"schema": "chzz-reference-observation-v1", "mode": "observe", "identity_proven": False,
           "changes_subtitles": False, "profile_fingerprint": profile.profile_fingerprint, "windows": []}
    if not profile.reference_enabled:
        return {**out, "status": "disabled", "reason": profile.reference_reason}
    if not 1 <= max_windows <= 120 or not 1 <= max_seconds <= 300:
        raise ValueError("Reference observation budget out of bounds")
    started = time.perf_counter()
    try:
        import numpy as np
        with wave.open(str(audio_path), "rb") as stream:
            if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (RATE, 1, 2):
                raise ValueError("Expected 16k mono PCM16 WAV")
            duration = stream.getnframes() / RATE
        if duration < 1:
            return {**out, "status": "unknown", "reason": "audio_too_short"}
        if windows is None:
            size = min(2.0, duration)
            count = min(max_windows, max(1, int(duration // size)))
            windows = [(float(start), float(start + size)) for start in np.linspace(0, duration - size, count)]
        else:
            windows = list(windows)
            if len(windows) > max_windows:
                raise ValueError("Too many requested observation windows")
        for start, end in windows:
            if not all(math.isfinite(float(v)) for v in (start, end)) or not 0 <= start < end <= duration or not 1 <= end - start <= 30:
                raise ValueError("Invalid observation window")
        if not windows:
            return {**out, "status": "unknown", "reason": "no_observation_windows"}
        if not profile.voice_manifest_path or sha256_file(profile.voice_manifest_path) != profile.voice_manifest_sha256:
            raise ValueError("Reference manifest changed after selection")
        if not profile.embedding_path or sha256_file(profile.embedding_path) != profile.embedding_sha256:
            raise ValueError("Reference vectors changed after selection")
        with np.load(profile.embedding_path, allow_pickle=False) as archive:
            reference = archive["mean_profile"].copy()
        encoder = encoder_factory(profile.encoder_path, profile.encoder_sha256)
        for start, end in windows:
            if time.perf_counter() - started >= max_seconds:
                return {**out, "status": "partial", "reason": "time_budget", "elapsed_sec": time.perf_counter() - started}
            vector = encoder.embed(read_pcm16(audio_path, start=start, end=end))
            score = float(vector @ reference)
            if not math.isfinite(score):
                raise ValueError("Nonfinite reference observation")
            out["windows"].append({"start": float(start), "end": float(end), "cosine": max(-1.0, min(1.0, score)),
                                   "speaker": "unknown", "identity_proven": False})
        return {**out, "status": "observed", "reason": "uncalibrated_cosine_not_identity",
                "elapsed_sec": time.perf_counter() - started, "source_duration_sec": duration,
                "coverage": "bounded_windows_not_full_voice_tracking"}
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError, wave.Error) as error:
        return {**out, "status": "unknown", "reason": type(error).__name__, "elapsed_sec": time.perf_counter() - started}
