"""Local evidence acquisition and preparation for one public VOD."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import urllib.parse

from .acquire import fetch_json, save_json
from .local_files import plain_path, read_json

PRODUCER_REVISION = "loki-2.9.4.2"


def write_transcript_srt(run, segments):
    def timestamp(second):
        ms = max(0, round(float(second) * 1000))
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    blocks = []
    for segment in segments:
        text = str(segment["text"]).strip()
        if text and segment["end"] > segment["start"]:
            blocks.append(f"{len(blocks)+1}\n{timestamp(segment['start'])} --> {timestamp(segment['end'])}\n{text}\n")
    path = plain_path(run / "preprocessed.srt")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def collect_comment_rows(number):
    """Baseline public-page API pagination, parent/reply ordering and minimization.

    The transport uses anonymous HTTPS instead of a persistent browser profile.
    Account fields and real comment IDs are never persisted or sent to Codex.
    """
    root = f"https://apis.naver.com/nng_main/nng_comment_api/v1/type/STREAMING_VIDEO/id/{number}/comments"
    rows, seen, thread_index, offset = [], set(), 0, 0
    status = "complete"

    def add(entry, thread_key, order):
        comment = entry.get("comment") or {}
        text = str(comment.get("content") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text or text in seen:
            return
        seen.add(text)
        rows.append({"text": text, "like_count": (entry.get("buffNerf") or {}).get("buffCount"),
                     "reply_count": comment.get("replyCount"), "source_order": len(rows)+1,
                     "thread_key": thread_key, "thread_order": order})

    for page_index in range(4):
        try:
            payload = fetch_json(root + f"?limit=30&offset={offset}&orderType=POPULAR&pagingType=PAGE")
            if payload.get("code") != 200 or not isinstance(payload.get("content"), dict):
                raise ValueError("Comment API did not return a valid page")
        except Exception:
            return rows, "partial" if rows else "fetch_failed"
        content = payload["content"]
        ordinary = (content.get("comments") or {}).get("data") or []
        parents = ((content.get("bestComments") or []) if page_index == 0 else []) + ordinary
        for parent in parents:
            thread_index += 1
            thread_key = f"thread_{thread_index:04}"
            add(parent, thread_key, 0)
            replies = parent.get("replyComments") or []
            comment = parent.get("comment") or {}
            parent_id = str(comment.get("commentId") or "")
            if parent_id and int(comment.get("replyCount") or 0) > len(replies) and thread_index <= 24:
                try:
                    reply = fetch_json(root + "/" + urllib.parse.quote(parent_id, safe="") + "/replyComments?offset=0")
                    if reply.get("code") != 200:
                        raise ValueError("Reply API failed")
                    page = ((reply.get("content") or {}).get("comments") or {}).get("data") or []
                    if page:
                        replies = page
                except Exception:
                    status = "partial"
            for index, reply in enumerate(replies):
                add(reply, thread_key, index + 1)
            if len(rows) >= 160:
                break
        if len(rows) >= 160:
            status = "partial"
            break
        if len(ordinary) < 30:
            break
        offset += len(ordinary)
    else:
        status = "partial"
    return rows, status


def comment_evidence(run, vod, *, refresh=False):
    from .timeline_comment_collector import (
        TimelineCommentBundle, _bundle_to_json, _bundle_from_json,
        _normalize_comment_text, select_timeline_comments_with_learning,
        build_private_timeline_anchors_for_bundle, build_private_comment_semantic_context,
    )
    from .timeline_comment_anchors import build_timeline_comment_context
    target = plain_path(run / "timeline_comments.json")
    bundle = None
    if target.exists() and not refresh:
        cached = read_json(target)
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
            if (cached.get("video_no") == str(vod.video_no) and 0 <= age < 6*3600
                    and cached.get("collection_status") == "complete"):
                bundle = _bundle_from_json(cached)
        except (KeyError, ValueError, TypeError):
            pass
    if bundle is None:
        rows, status = collect_comment_rows(str(vod.video_no))
        deduped, seen = [], set()
        for row in rows:
            text = _normalize_comment_text(row.get("text"))
            key = " ".join(text.split()).casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            deduped.append({**row, "text": text})
            if len(deduped) >= 80:
                break
        selected, learning = select_timeline_comments_with_learning(
            deduped, platform="chzzk", max_selected=6, min_timecodes=2, min_score=12)
        bundle = TimelineCommentBundle(video_no=str(vod.video_no),
            source_url=f"https://chzzk.naver.com/video/{vod.video_no}",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status=status if status != "complete" else ("ok" if selected else "no_timeline_comments"),
            total_comments_seen=len(deduped), selected_count=len(selected),
            comments=selected, candidate_learning=learning)
        from .timeline_comment_collector import extract_timecodes
        bundle.timeline_comment_count = sum(bool(extract_timecodes(row["text"])) for row in deduped)
        bundle.anchors = build_private_timeline_anchors_for_bundle(bundle)
        save_json(target, {**_bundle_to_json(bundle), "collection_status": status,
                           "producer_revision": PRODUCER_REVISION, "transport": "anonymous_public_https"})
    context = build_timeline_comment_context(bundle.anchors, source_status=bundle.status,
        selected_comment_count=bundle.selected_count, source_kind="chzzk", duration_sec=vod.duration)
    context["private_comment_semantic_context"] = build_private_comment_semantic_context(bundle)
    return bundle, context


def signal_evidence(run, vod, chats, srt_path):
    from .highlight_candidate_pipeline import build_chat_highlight_candidates, merge_audio_reaction_highlights
    from .audio_features import build_audio_metadata_from_wav_files
    chat_peaks, producer_ref = build_chat_highlight_candidates(
        chats, duration_sec=vod.duration, cfg={}, source_video_id=str(vod.video_no))
    audio_path = plain_path(run / "audio.wav")
    digest = hashlib.sha256()
    with audio_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    digest.update(srt_path.read_bytes())
    digest.update(json.dumps(chat_peaks, sort_keys=True).encode())
    digest.update(PRODUCER_REVISION.encode())
    fingerprint = digest.hexdigest()
    target = plain_path(run / "audio_reaction_metadata.json")
    audio = read_json(target) if target.exists() else {}
    if audio.get("input_fingerprint") != fingerprint or audio.get("status") == "analysis_failed":
        try:
            audio = build_audio_metadata_from_wav_files(audio_path, offsets_sec=[0],
                duration_sec=vod.duration, chat_peaks=chat_peaks, srt_path=srt_path,
                window_sec=5, min_percentile=.95, min_gap_sec=30,
                chat_overlap_sec=60, max_peaks=20, include_windows=False)
            audio["status"] = "complete"
        except Exception as error:
            audio = {"status":"analysis_failed", "error_type":type(error).__name__,
                     "audio_reaction_peaks":[]}
            print("Audio evidence unavailable; continuing with transcript/chat. Retry generation to retry analysis.", flush=True)
        audio["input_fingerprint"] = fingerprint
        audio["producer_revision"] = PRODUCER_REVISION
        save_json(target, audio)
    combined = merge_audio_reaction_highlights(chat_peaks, audio)
    for row in combined:
        row["producer_run_ref"] = producer_ref
    save_json(run / "signal_highlights.json", {"producer_revision": PRODUCER_REVISION,
        "producer_run_ref": producer_ref, "chat_highlights": chat_peaks, "combined": combined})
    return chat_peaks, audio, combined


def clip_evidence(run, vod, *, refresh=False):
    from .chzzk_user_clips import load_chzzk_user_clips, fetch_chzzk_user_clips
    plain_path(run / "user_clips.json")
    if not vod.broadcast_start_at:
        payload = fetch_json(f"https://api.chzzk.naver.com/service/v3/videos/{vod.video_no}")
        source = payload.get("content") or {}
        vod.broadcast_start_at = str(source.get("liveOpenDate") or "")
        vod.replay_publish_at = str(source.get("publishDate") or "")
        vod.publish_date = vod.broadcast_start_at
        metadata = read_json(run / "metadata.json")
        metadata.update(broadcast_start_at=vod.broadcast_start_at,
                        replay_publish_at=vod.replay_publish_at, publish_date=vod.publish_date)
        save_json(run / "metadata.json", metadata)
    bundle = None if refresh else load_chzzk_user_clips(str(vod.video_no), run, cache_ttl_hours=6)
    if bundle is None:
        bundle = fetch_chzzk_user_clips(str(vod.video_no), vod.channel_id, {}, run,
            work_root=run.parent, max_clips=30, offset_budget=30,
            enable_offset_extraction=True, enable_engagement_extraction=True,
            broadcast_start_at=vod.broadcast_start_at, vod_duration_sec=vod.duration)
    return bundle
