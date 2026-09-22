"""Public DTO adapter for the adopted report.html/viewer/card template family.

Presentation is reused from Loki 2.9.4.2. This adapter never receives raw chat,
transcripts, evidence ledgers, profiles or provider metadata.
"""
import html
import re
from pathlib import Path
from .outline.schema import hms_to_seconds, seconds_to_hms
from .report_components import _build_viewer_editor_tools_html, _viewer_editor_json
from .editorial_highlight_cards import render_editorial_highlight_cards

ROOT=Path(__file__).resolve().parent/'web'

def render_report(data, *, private_workbench=None):
    e=html.escape
    number=data['video_no'];source='https://chzzk.naver.com/video/'+number
    stories={s['point_ref']:s for s in data['stories']}
    points=[];story_view={};projection=[];items=[]
    for row in data['ranges']:
        projection.append({**row,'start_sec':hms_to_seconds(row['start']),
                           'end_sec':hms_to_seconds(row['end']),'video_no':number})
    for point in data['points']:
        ref='public-story-'+point['id']
        story=stories.get(point['id'],{})
        text=story.get('description') or point['content']
        points.append({**point,'story_ref':ref})
        story_view[ref]={'why_notable':text,'title':story.get('title',point['title'])}
        second=hms_to_seconds(point['timestamp'])
        projection.append({**point,'level':'Point','start_sec':second,'end_sec':second,
                           'story_title':story.get('title',''),'story_content':text,'video_no':number})
        items.append(f'''<div class="t-item mood-hot" data-segment-id="{e(point['id'])}" data-outline-id="{e(point['id'])}">
  <div class="t-head"><a class="tc" href="{source}?currentTime={second}" target="ttaem-chzzk-companion" rel="opener">{e(point['timestamp'])}</a>
  <span class="t-title">{e(point['title'])}</span><span class="mood"></span></div>
  <div class="t-body">{e(text)}</div></div>''')
    highlights=[]
    for index,h in enumerate(data['highlights']):
        highlights.append({'highlight_id':h['id'],'title':h['title'],'story_summary':h['summary'],
                           'point_refs':h['point_refs'],'source_spans':h['source_spans']})
        for span_index,span in enumerate(h['source_spans']):
            projection.append({'id':h['id']+'-span-'+str(span_index),'level':'Highlight',
                               'highlight_id':h['id'],'highlight_label':'H'+str(index+1),
                               'source_span_index':span_index,'source_span_count':len(h['source_spans']),
                               'title':h['title'],'content':h['summary'],'summary':h['summary'],
                               'start_sec':span['start_sec'],'end_sec':span['end_sec'],
                               'start':seconds_to_hms(span['start_sec']),'end':seconds_to_hms(span['end_sec']),
                               'source_spans':h['source_spans'],'point_refs':h['point_refs'],'video_no':number})
    view={'points':points,'points_by_id':{p['id']:p for p in points},
          'stories_by_ref':story_view,'highlights':highlights}
    timeline=f'''<div class="card"><div class="card-head"><h2>📍 타임라인 상세 요약</h2>
    <button class="t-toggle" data-toggle-evidence>근거 모두 펼치기</button></div><div class="card-body">
    <div class="timeline" data-manager-outline-hierarchy="pending">{''.join(items) or '<p>확인된 주요 순간이 없습니다.</p>'}</div></div></div>'''
    editorial=f'''<div class="card editorial-review-packet" id="editorial-highlights">
    <div class="card-head"><h2>🎬 편집 Highlight {len(highlights)}</h2></div>
    <div class="card-body">{render_editorial_highlight_cards(view,video_no=number)}</div></div>'''
    buckets=data.get('chat_buckets',[])
    events=[{**row,'kind':'timeline' if row['level']=='Point' else 'highlight',
             'label':row['title'],'summary':row.get('story_content') or row.get('content',''),
             'evidence':[{'label':'Story' if row['level']=='Point' else 'Highlight 요약',
                          'text':row.get('story_content') or row.get('content',''),
                          'start_sec':row['start_sec'],'video_no':number}]}
            for row in projection if row['level'] in ('Point','Highlight')]
    lanes=[{'key':'timeline','label':'주요 순간','event_count':len(points)},
           {'key':'highlight','label':'Highlight','event_count':sum(len(h['source_spans']) for h in highlights)}]
    from .clip_cards import _render_user_clip_card, _wrap_user_clips_card
    from .models import VODInfo, UserClip
    vod=VODInfo(number,data['title'],'',data['channel_name'],data['duration'],data.get('published_at',''))
    clip_rows=data.get('viewer_clips',[])
    cards=[]
    for clip in sorted(clip_rows,key=lambda c:(-(c.get('like_count') or 0),-(c.get('play_count') or 0),c['offset_sec'])):
        cards.append(_render_user_clip_card(UserClip(**clip,video_no=number,offset_status='ok',engagement_status='ok'),vod))
        events.append({'id':'viewer-clip-'+clip['clip_uid'],'kind':'viewer_clip','clip_uid':clip['clip_uid'],'start_sec':clip['offset_sec'],
            'end_sec':clip['offset_sec'],'title':clip['title'],'label':clip['title'],'summary':'같은 VOD로 시점이 확인된 시청자 클립',
            'video_no':number,'evidence':[{'label':'시청자 클립','text':clip['title'],
            'start_sec':clip['offset_sec'],'video_no':number}]})
    if clip_rows:
        lanes.append({'key':'viewer_clip','label':'시청자 클립','event_count':len(clip_rows)})
    clip_html=_wrap_user_clips_card('🎯 시청자 클립',f'<p class="user-clips-summary">이 VOD 시점 매칭 시청자 클립 {len(clip_rows)}개</p><div class="user-clips-grid">'+''.join(cards)+'</div>') if cards else ''
    viewer={'video_no':number,'duration_sec':data['duration'],'outline_projection':projection,
            'events':events, 'lanes':lanes, 'chat_buckets':buckets,
            'labels':{'timeline':'주요 순간','highlight':'Highlight','viewer_clip':'시청자 클립'}, 'viewer_labels':{},
            'colors':{'timeline':'#7aa2f7','highlight':'#bb9af7','viewer_clip':'#f9b572'},'purposes':{}}
    if private_workbench is not None:
        visible_clip_ids={row['clip_uid'] for row in clip_rows}
        for row in private_workbench['events']:
            if row['lane']=='viewer_clip' and row.get('clip_uid') in visible_clip_ids: continue
            viewer['events'].append({**row,'id':row['event_id'],'kind':row['lane'],'title':row['label'],
                'summary':row['label'],'video_no':number})
        viewer['lanes'].extend(row for row in private_workbench['lanes'] if row['key']!='viewer_clip' or not clip_rows)
        viewer['labels'].update(private_workbench['labels'])
    tools=_build_viewer_editor_tools_html(viewer,public_projection=False)
    if private_workbench is not None:
        tools='<p role="note">로컬 분석 근거 · 현재 수집 자료 기준 · 공개 내보내기에 포함되지 않습니다.</p>'+tools
        status=private_workbench.get('source_summary',{})
        if status:
            labels={'ok':'확인 완료','complete':'완료','partial':'일부만 수집',
                'no_timeline_comments':'시간표 선별 0개','no_clips':'확인된 클립 없음',
                'fetch_failed':'수집 실패','analysis_failed':'분석 실패','unavailable':'자료 없음',
                'age_skipped':'오래된 방송으로 생략','blocked':'접근 제한',
                'blocked_cooldown':'접근 제한 후 재시도 대기','manual':'사용자 입력'}
            state_label=lambda key:labels.get(status.get(key),'확인 필요')
            notes=[f"댓글: 수집 {status.get('comments_seen',0)}개 · 시간표 선정 {status.get('comments_selected',0)}개 ({state_label('comments_status')})",
                   f"채팅: {state_label('chat_status')}",f"클립: {status.get('clips_selected',0)}개 ({state_label('clips_status')})",
                   f"오디오: {state_label('audio_status')}",f"커뮤니티: {state_label('community_status')}"]
            tools='<p role="status">'+e(' · '.join(notes))+'</p>'+tools
        tools=tools.replace('요약에 들어간 주요 순간과 Highlight를 같은 시간축에서 살펴봅니다.',
            '채팅·소리·자막·댓글·클립은 검토 근거이며, 그 자체로 요약의 사실을 확정하지 않습니다.')
    entry=f'''<div class="editor-jump report-editor-entry" data-editor-entry data-editor-entry-state="fallback"
    data-editor-mode="companion" data-editor-video-no="{number}" data-editor-chzzk-url="{source}">
    <a class="report-editor-inline-button editor-companion-entry-link" data-editor-entry-primary href="{source}"
    target="ttaem-chzzk-companion" rel="opener" aria-label="영상 연결">영상 연결</a>
    <span class="editor-entry-status" data-editor-entry-status hidden></span>
    <a class="editor-jump-link report-editor-tool-link" href="#youtube-editor-tools" aria-label="장면 도구로 이동" title="장면 도구로 이동">
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M7 4v16"></path><path d="M11 7h9"></path><path d="M11 12h9"></path><path d="M11 17h9"></path></svg>
    <span class="report-editor-visually-hidden">장면 도구</span></a></div>'''
    chart={'labels':[seconds_to_hms(b['start_sec']) for b in buckets],
           'counts':[b['count'] for b in buckets],'bucket_secs':[b['start_sec'] for b in buckets], 'video_no':number}
    template=(ROOT/'templates/summary.html').read_text(encoding='utf-8')
    values={key:'' for key in re.findall(r'@@([A-Z0-9_]+)@@',template)}
    values.update(REPORT_TITLE=e(data['title']),CHANNEL_NAME=e(data['channel_name']),VOD_ID=number,
                  DISPLAY_TITLE=e(data['title']),PUBLISHED_AT=e(data.get('published_at','')),
                  DURATION=seconds_to_hms(data['duration']),DURATION_STAT=seconds_to_hms(data['duration']),
                  TOP_VOD_LINK=f'<span class="top-vod-link-row"><a class="top-vod-link" href="{source}" target="_blank" rel="noopener">원본 방송 보기</a></span>',
                  EDITOR_JUMP=entry,
                  CHAT_COUNT=f"{data['chat_count']:,}" if data.get('chat_count') is not None else '자료 없음',
                  SIGNAL_SCENE_COUNT=str(len(clip_rows)),
                  EDITORIAL_HIGHLIGHT_STAT=f'<div class="stat"><div class="stat-label">편집 Highlight</div><div class="stat-value accent">{len(highlights)}</div></div>',
                  CHAT_CHART_STATE='' if data.get('chat_status')=='complete' else '<p>채팅 자료가 없거나 일부만 수집됐습니다.</p>',
                  HERO_HTML=f'<div class="hero"><div class="bleed-inner"><p class="summary-lead">{e(data["summary"])}</p></div></div>' if data['summary'] else '',
                  TIMELINE_HTML=timeline,EDITORIAL_REVIEW_HTML=editorial,VIEWER_TOOLS_HTML=tools,USER_CLIPS_HTML=clip_html,
                  NOTES_HTML=f'<details class="card"><summary class="card-head">확인이 더 필요한 내용</summary><div class="card-body">{e(data["uncertainty"])}</div></details>' if data['uncertainty'] else '',
                  CHART_DATA='<script type="application/json" id="reportChartData">'+_viewer_editor_json(chart)+'</script>')
    return re.sub(r'@@([A-Z0-9_]+)@@',lambda match:values[match[1]],template)
