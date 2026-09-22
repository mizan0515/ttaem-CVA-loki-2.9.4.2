"""Single public CHZZK VOD acquisition. Never reads browser sessions or cookies."""
from __future__ import annotations
import json
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from .local_files import plain_path, read_json

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://chzzk.naver.com/"}

def video_number(url):
    match = re.fullmatch(r"https://chzzk\.naver\.com/video/([0-9]{1,16})/?", url)
    if not match:
        raise ValueError("A single https://chzzk.naver.com/video/NUMBER URL is required")
    return match[1]

def fetch(url, *, headers=None):
    request = urllib.request.Request(url, headers={**HEADERS, **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(16_000_001)
        if len(payload) > 16_000_000:
            raise ValueError("Remote response exceeds size limit")
        return payload

def fetch_json(url):
    return json.loads(fetch(url))

def save_json(path, value):
    path = plain_path(path)
    temp = plain_path(path.with_suffix(path.suffix + ".tmp"))
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

def prepare(url, run):
    number = video_number(url)
    run = plain_path(run)
    run.mkdir(parents=True, exist_ok=True)
    metadata = fetch_json(f"https://api.chzzk.naver.com/service/v3/videos/{number}").get("content")
    if not metadata:
        raise RuntimeError("VOD is unavailable without authentication")
    channel = metadata.get("channel") or {}
    public = {"video_no": number, "title": metadata.get("videoTitle", ""),
              "channel_id": channel.get("channelId", ""), "channel_name": channel.get("channelName", ""),
              "duration": int(metadata.get("duration") or 0), "publish_date": metadata.get("liveOpenDate", ""),
              "broadcast_start_at": metadata.get("liveOpenDate", ""),
              "replay_publish_at": metadata.get("publishDate", ""),
              "category": metadata.get("videoCategoryValue", ""),
              "source_url": url}
    if public["duration"] <= 0:
        raise ValueError("VOD duration unavailable")
    save_json(run / "metadata.json", public)
    audio = plain_path(run / "audio.wav")
    if not audio.exists():
        executable = shutil.which("ffmpeg")
        if not executable:
            raise RuntimeError("FFmpeg is required; run setup.py --check")
        vid, key = metadata.get("videoId"), metadata.get("inKey")
        if not vid or not key:
            raise RuntimeError("VOD playback not available without authentication")
        manifest = fetch("https://apis.naver.com/neonplayer/vodplay/v2/playback/" + urllib.parse.quote(vid, safe="") + "?" + urllib.parse.urlencode({"key": key}), headers={"Accept": "application/dash+xml"})
        root = ET.fromstring(manifest)
        ns = {"m": "urn:mpeg:dash:schema:mpd:2011"}
        candidates = []
        for adaptation in root.findall(".//m:AdaptationSet", ns):
            for rep in adaptation.findall("m:Representation", ns):
                base = rep.find("m:BaseURL", ns)
                source = (base.text or "").strip() if base is not None else ""
                if not source or source.endswith("/hls/"):
                    source = rep.get("{urn:naver:vod:2020}m3u") or ""
                mime = rep.get("mimeType") or adaptation.get("mimeType", "")
                if source.startswith("https://") and (mime.startswith("audio/") or rep.get("audioSamplingRate")):
                    candidates.append((int(rep.get("bandwidth") or 0), source))
        if not candidates:
            raise RuntimeError("No public audio representation available")
        source = max(candidates)[1]
        print("Downloading and decoding VOD audio", flush=True)
        temporary = plain_path(run / "audio.partial.wav")
        process = subprocess.run([executable, "-nostdin", "-y", "-v", "error", "-i", source,
                                  "-vn", "-ac", "1", "-ar", "16000", str(temporary)], capture_output=True,
                                 timeout=max(600, public["duration"] * 2))
        if process.returncode:
            raise RuntimeError(f"Audio acquisition failed (exit {process.returncode}); signed playback URL suppressed")
        import wave
        with wave.open(str(temporary)) as wav:
            actual = wav.getnframes() / wav.getframerate()
        if abs(actual - public["duration"]) > 5:
            raise RuntimeError(f"Incomplete audio: {actual:.1f}/{public['duration']} seconds")
        temporary.replace(audio)
    chat_path = plain_path(run / "chats.json")
    previous_chats = read_json(chat_path) if chat_path.is_file() else {}
    if previous_chats.get('status') != 'complete':
        rows, cursor, seen, status = [], "0", set(), "complete"
        started = time.monotonic()
        while cursor is not None:
            if str(cursor) in seen or time.monotonic() - started > 300:
                status = "partial"
                break
            seen.add(str(cursor))
            try:
                data = fetch_json(f"https://api.chzzk.naver.com/service/v1/videos/{number}/chats?" + urllib.parse.urlencode({"playerMessageTime": cursor}))
                if data.get("code") != 200:
                    status = "partial" if rows else "unavailable"
                    break
                content = data.get("content") or {}
                chats = content.get("videoChats") or []
                if not chats:
                    break
                for item in chats:
                    ms = item.get("playerMessageTime", item.get("messageTime", 0))
                    if 0 <= int(ms) <= public["duration"] * 1000:
                        rows.append({"ms": int(ms), "msg": str(item.get("content") or "")[:2000]})
                cursor = content.get("nextPlayerMessageTime")
            except (OSError, ValueError):
                status = "partial" if rows else "unavailable"
                break
            time.sleep(.08)
        if status != 'complete' and len(rows) < len(previous_chats.get('rows', [])):
            rows = previous_chats['rows']
            status = 'partial'
        save_json(chat_path, {"status": status, "rows": rows})
        print(f"Replay chat: {status}, {len(rows)} rows", flush=True)
    return public
