"""Local Whisper transcription, with private model cache and original timestamps."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
from .acquire import save_json
from .local_files import plain_path, read_json

def transcribe(run, *, device="auto", model="large-v3-turbo"):
    run = plain_path(run)
    target = plain_path(run / "transcript.json")
    plain_path(run / "audio.wav")
    if target.exists():
        return read_json(target)
    os.environ.setdefault("HF_HOME", str(run.parent.parent / ".models"))
    handles = []
    spec = importlib.util.find_spec("torch")
    if spec and os.name == "nt":
        lib = Path(spec.origin).parent / "lib"
        os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
        handles.append(os.add_dll_directory(str(lib)))
    from dataclasses import fields
    from .models import VODInfo
    from .context_inputs import load_local_context, spelling_terms, wiki_terms
    from .lexicon import format_for_whisper
    metadata = read_json(run / "metadata.json")
    vod = VODInfo(**{f.name:metadata[f.name] for f in fields(VODInfo) if f.name in metadata})
    context, vocabulary = load_local_context(run, vod)
    terms = spelling_terms(vod, read_json(run / "chats.json")["rows"],
                           context=context, vocabulary=vocabulary,
                           wiki=wiki_terms(run, vod, fetch_external=True))
    prompt = format_for_whisper(terms)
    save_json(run / "stt-hints.json", {"terms":terms,"initial_prompt":prompt})
    import ctranslate2
    if device == "auto":
        device = "cuda" if spec and ctranslate2.get_cuda_device_count() else "cpu"
    from faster_whisper import WhisperModel, BatchedInferencePipeline
    print(f"Local STT: {model}, {device}; first use downloads model weights", flush=True)
    local = plain_path(run.parent.parent / ".models" / ("faster-whisper-" + model))
    engine = WhisperModel(str(local) if (local / "model.bin").exists() else model, device=device, compute_type="float16" if device == "cuda" else "int8",
                          download_root=str(run.parent.parent / ".models"))
    processor = BatchedInferencePipeline(model=engine)
    segments, info = processor.transcribe(str(run / "audio.wav"), language="ko", beam_size=5,
                                          batch_size=8 if device == "cuda" else 2,
                                          word_timestamps=True, vad_filter=True, initial_prompt=prompt)
    rows = []
    for segment in segments:
        rows.append({"start": segment.start, "end": segment.end, "text": segment.text.strip(),
                     "words": [{"start": w.start, "end": w.end, "word": w.word, "probability": w.probability}
                               for w in segment.words or []]})
        if len(rows) % 100 == 0:
            print(f"STT: {segment.end:.0f}/{info.duration:.0f} seconds", flush=True)
    result = {"model": model, "backend": "faster-whisper", "device": device,
              "language": info.language, "duration": info.duration, "segments": rows}
    if not rows:
        raise RuntimeError("No speech detected; a summary cannot be fabricated")
    save_json(target, result)
    return result
