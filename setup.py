"""Folder setup: python setup.py [--check] [--cuda]. No account configuration."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--cuda", action="store_true", help="Install optional NVIDIA runtime (large download)")
    parser.add_argument("--voice", action="store_true", help="Install optional local voice encoder runtime; no weights or profile")
    args = parser.parse_args()
    if not (3, 12) <= sys.version_info[:2] <= (3, 13):
        raise SystemExit("Install Python 3.12-3.13, then rerun this command.")
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    browser_env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".models" / "playwright")}
    if not args.check:
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
        elif subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True).returncode:
            subprocess.run([str(python), "-m", "ensurepip"], check=True, cwd=ROOT)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt"), "-c", str(ROOT / "requirements-lock.txt")], check=True, cwd=ROOT)
        subprocess.run([str(python), "-m", "playwright", "install", "chromium"],
                       check=True, cwd=ROOT, env=browser_env)
        if args.cuda or args.voice:
            packages=["torch==2.11.0"] + (["torchaudio==2.11.0"] if args.voice else [])
            index="https://download.pytorch.org/whl/"+("cu128" if args.cuda else "cpu")
            subprocess.run([str(python), "-m", "pip", "install", *packages, "--index-url", index], check=True, cwd=ROOT)
    problems = []
    if not python.is_file():
        problems.append("Missing local .venv; run python setup.py")
    else:
        result = subprocess.run([str(python), "-c", "import flask, faster_whisper, bs4, lxml; import ttaem_cva.workflow, ttaem_cva.source_signals, ttaem_cva.review_signals, ttaem_cva.community, ttaem_cva.content_cards; from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); assert Path(p.chromium.executable_path).is_file(); p.stop()"], cwd=ROOT, capture_output=True, env=browser_env)
        if result.returncode:
            problems.append("Runtime packages/import check failed; run python setup.py")
    for command in ("ffmpeg", "ffprobe"):
        if not shutil.which(command):
            problems.append("Install FFmpeg and expose " + command + " on PATH (see docs/SETUP.md)")
    for document in ("AGENTS.md", "CLAUDE.md", "docs/AGENT_POLICY.md", "docs/POLICY.md", "LICENSE"):
        if not (ROOT / document).is_file():
            problems.append("Missing policy file: " + document)
    if problems:
        print("\n".join(problems))
        return 1
    print("READY: local runtime and FFmpeg. Use this folder's Codex session for summarization.")
    print("First transcription downloads the local Whisper model. CPU supported; CUDA is optional.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
