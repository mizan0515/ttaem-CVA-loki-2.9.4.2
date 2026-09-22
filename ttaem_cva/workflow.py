"""Single-VOD orchestration around the adopted semantic component contracts."""
from __future__ import annotations
from dataclasses import fields, asdict
import logging
from .acquire import save_json
from .llm_gateway import call_llm
from .models import VODInfo
from .local_files import plain_path, read_json

def generate(run):
    from .manager_outline import build_cached_support_package, finalize_manager_outline
    from .outline.payload import prepare_chunk_user_prompt, prepare_merge_observations
    from .outline.prompts import CHUNK_SYSTEM_PROMPT, MERGE_SYSTEM_PROMPT
    from .outline.check import sanitize_chunk_observation, validate_chunk_observation
    from .outline.parse import parse_manager_outline
    from .broadcast_map_prompt_budget import coalesce_manager_outline_chunks
    from .broadcast_map_evidence import build_broadcast_map_evidence_bundle, project_broadcast_map_evidence_for_prompt, build_point_candidate_shadow_receipt
    from .story_packet_selector import run_story_point_selector_shadow
    from .vod.editor import apply_story_point_shadow_to_normal_consumers
    from .editorial_highlight_pipeline import _run_approved_story_highlights
    run = plain_path(run)
    metadata = read_json(run / "metadata.json")
    vod = VODInfo(**{f.name: metadata[f.name] for f in fields(VODInfo) if f.name in metadata})
    transcript = read_json(run / "transcript.json")
    from .subtitle_enhancement.timing import retime
    from .subtitle_enhancement.turn_display import preserve_interjections
    cues = [{k: segment[k] for k in ('start','end','text')} for segment in transcript['segments']]
    words = [{'start': w['start'], 'end': w['end'], 'text': w['word'].strip()} for s in transcript['segments'] for w in s.get('words', [])]
    speech, timing = retime(cues, words, [], transcript['duration'], True)
    speech, turns = preserve_interjections(speech, words, [], transcript['duration'], True)
    save_json(run / 'preprocessing.json', {'timing': timing, 'turns': turns, 'segments': speech})
    transcript = {**transcript, 'segments': speech}
    chats_data = read_json(run / "chats.json")
    chats = chats_data["rows"]
    from .context_inputs import correct_transcript
    speech = correct_transcript(run, vod, speech, chats)
    from .source_signals import write_transcript_srt, signal_evidence, comment_evidence, clip_evidence
    from .chunker import chunk_srt
    from .clip_anchors import _select_promoted_anchored_clips
    from .user_clips_filter import is_eligible_in_vod_clip
    from .timed_evidence import build_timed_evidence_manifest
    from .summary_pre_generation_evidence import build_summary_pre_generation_evidence_pack
    from .summary_local_vector_index import build_summary_local_vector_index
    srt_path = write_transcript_srt(run, speech)
    chat_peaks, audio, signals = signal_evidence(run, vod, chats, srt_path)
    clip_bundle = clip_evidence(run, vod)
    metadata = read_json(run / "metadata.json")
    clips = [asdict(c) for c in clip_bundle.clips if is_eligible_in_vod_clip(c, vod.video_no)]
    promoted = _select_promoted_anchored_clips(clip_bundle, vod)
    source_chunks = chunk_srt(str(srt_path), max_chars=8000, overlap_sec=30,
        highlights=signals, highlight_radius_sec=300, cold_sample_sec=30,
        promoted_anchors=promoted["strict"], anchor_radius_sec=60)
    if not source_chunks:
        raise ValueError("No transcript chunks; cannot generate a summary")
    comment_bundle, comment_context = comment_evidence(run, vod)
    from .context_inputs import background_evidence
    context_text, lexicon, recent_context, community_posts = background_evidence(run, vod, chats, clips, comment_bundle)
    timed = build_timed_evidence_manifest(vod_info=vod, chunks=source_chunks,
        srt_path=srt_path, repo_root=run.parent.parent, work_dir=run.parent)
    pre_generation = build_summary_pre_generation_evidence_pack(
        timed_evidence_manifest=timed, highlights=signals)
    vectors = build_summary_local_vector_index(chunks=source_chunks, timed_evidence_manifest=timed)
    evidence = build_broadcast_map_evidence_bundle(video_no=vod.video_no, chunks=source_chunks, chats=chats,
        timeline_comment_context=comment_context, context_doc_text=context_text, lexicon_terms=lexicon,
        streamer_recent_context=recent_context, community_posts=community_posts, signal_highlights=signals,
        audio_reaction_metadata=audio, viewer_clips=clips, timed_evidence_manifest=timed,
        pre_generation_evidence_pack=pre_generation, local_vector_index=vectors, keyframe_manifest_present=False)
    from .visual_support import visual_support
    visual_rows = visual_support(run, vod)
    support = build_cached_support_package(chunks=source_chunks, chats=chats, audio_metadata=audio,
        timestamp_comments=[], viewer_clips=clips, visual_rows=visual_rows, duration_sec=vod.duration,
        timeline_comment_context=comment_context,
        broadcast_map_evidence=project_broadcast_map_evidence_for_prompt(evidence))
    chunks = coalesce_manager_outline_chunks(source_chunks)
    save_json(run / "generation-evidence.json", {"source_chunks": source_chunks,
        "outline_chunk_count":len(chunks), "chat_peak_count":len(chat_peaks),
        "audio_peak_count":len(audio.get("audio_reaction_peaks") or []),
        "comments_status":comment_bundle.status,"comments_seen":comment_bundle.total_comments_seen,
        "comments_selected":comment_bundle.selected_count,"clips_status":clip_bundle.status,
        "clips_selected":len(clips),"promoted_anchors":promoted,"support":support,
        "timed_evidence_manifest":timed,"pre_generation_pack":pre_generation,
        "local_vector_index":vectors,"broadcast_map_evidence":evidence})
    cfg = {"agent_jobs_dir": str(run / "jobs")}
    observations = []
    for i, chunk in enumerate(chunks):
        local_chats = [c for c in chats if chunk["start_ms"] <= c["ms"] < chunk["end_ms"]]
        request, budget = prepare_chunk_user_prompt(chunk, local_chats, vod, support_package=support)
        response = call_llm(request, CHUNK_SYSTEM_PROMPT, config=cfg, context={"phase": f"observation-{i+1}-of-{len(chunks)}"})
        validate_chunk_observation(response)
        response = sanitize_chunk_observation(response)
        observations.append(
            f"## chunk_{chunk['index']:02d} "
            f"({chunk['start_hhmmss']}~{chunk['end_hhmmss']})\n\n{response}"
        )
    from .content_cards import load_approved_content
    content_cards = load_approved_content(run, [c['text'] for c in source_chunks]+[c['msg'] for c in chats])
    save_json(run / "content-card-usage.json", {"cards":content_cards})
    _, request, _ = prepare_merge_observations(observations, vod, model="codex-session",
        call_count=len(observations)+1, content_info_cards=content_cards, support_package=support)
    raw = call_llm(request, MERGE_SYSTEM_PROMPT, config=cfg, context={"phase": "final-outline"})
    text = finalize_manager_outline(raw, duration_sec=vod.duration, call_count=len(observations)+1,
        replay_chat_used=bool(chats), model="codex-session", support_package=support,
        content_info_cards=content_cards)
    outline = parse_manager_outline(text, duration_sec=vod.duration)
    plain_path(run / "outline.txt").write_text(text, encoding="utf-8")
    shadow = build_point_candidate_shadow_receipt(outline_text=text, duration_sec=vod.duration,
        chunks=chunks, chats=chats, timeline_comment_context=comment_context, evidence_bundle=evidence,
        chunk_observations=observations)
    if outline.get("points"):
        shadow = run_story_point_selector_shadow(shadow_receipt=shadow, config=cfg, video_no=vod.video_no,
                                                raw_response_path=run / "story.raw.txt")
        outline, _ = apply_story_point_shadow_to_normal_consumers(outline, shadow)
    save_json(run / "story-private.json", shadow)
    highlights = _run_approved_story_highlights(cfg=cfg, video_no=vod.video_no, outline_text=text,
        outline=outline, duration_sec=vod.duration, chunks=chunks, chats=chats,
        broadcast_map_evidence=evidence, point_candidate_shadow=shadow,
        output_path=run / "highlight-private.json", markdown_path=run / "highlight.md",
        quality_path=run / "highlight-quality.json", logger=logging.getLogger("ttaem_cva"))
    from collections import Counter
    counts=Counter(int(c['ms']//10000)*10 for c in chats)
    chat_projection={'status':chats_data['status'], 'buckets':[{'start_sec':sec,'count':counts[sec]} for sec in range(0,vod.duration,10)]}
    from .review_signals import build_private_review
    private_review = build_private_review(vod, speech, chats, chat_peaks, audio, comment_context, clips)
    private_review['source_summary'] = {
        'comments_status':comment_bundle.status, 'comments_seen':comment_bundle.total_comments_seen,
        'comments_selected':comment_bundle.selected_count, 'clips_status':clip_bundle.status,
        'clips_selected':len(clips), 'chat_status':chats_data['status'],
        'audio_status':audio.get('status','complete'),
        'community_status':read_json(run/'community.json')['status'],
    }
    save_json(run / "review-signals.json", private_review)
    result = {"chat_projection":chat_projection, "metadata": metadata, "outline": outline, "highlights": highlights,
              "viewer_clips":clips, "viewer_clips_status":clip_bundle.status,
              "source_status":{"comments":comment_bundle.status,"chat":chats_data["status"],
                               "clips":clip_bundle.status},
              "stories": (shadow.get("story_point_selection") or {}).get("selected_points", [])}
    save_json(run / "result-private.json", result)
    return result
