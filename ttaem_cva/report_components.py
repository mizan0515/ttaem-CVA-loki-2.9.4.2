"""Selected original report viewer markup; input is the explicit public DTO only."""
from __future__ import annotations
import json
from typing import Any
_VIEWER_EDITOR_OVERVIEW_LANE_KEYS = {'audio_waveform', 'chat_volume'}
VIEWER_EDITOR_TOOLS_RENDER_VERSION = 'loki-2.9.4.2-public'

def _build_viewer_editor_tools_html(
    payload: dict[str, Any],
    *,
    public_projection: bool = True,
) -> str:
    event_count = len([
        row for row in (payload.get("events") or [])
        if isinstance(row, dict)
        and str(row.get("kind") or "") not in _VIEWER_EDITOR_OVERVIEW_LANE_KEYS
    ])
    clip_count = int((payload.get("edit_export_preview") or {}).get("clip_count") or 0)
    scene_event_kinds = {
        str(row.get("kind") or "")
        for row in (payload.get("events") or [])
        if isinstance(row, dict) and str(row.get("kind") or "")
    }
    lane_count = len(
        [
            row for row in (payload.get("lanes") or [])
            if isinstance(row, dict)
            and str(row.get("key") or "") not in _VIEWER_EDITOR_OVERVIEW_LANE_KEYS
            and (
                str(row.get("key") or "") in scene_event_kinds
                or int(row.get("event_count") or 0) > 0
            )
        ]
    )
    data_json = _viewer_editor_json(payload)
    return f'''
<div class="card viewer-editor-tools" id="youtube-editor-tools" data-report-block="viewer-editor-tools" data-viewer-editor-render-version="{VIEWER_EDITOR_TOOLS_RENDER_VERSION}">
  <div class="viewer-editor-head">
    <div>
      <h2 class="viewer-editor-title">편집자 워크스페이스</h2>
      <p class="viewer-editor-lead">유튜브 편집자가 훑어볼 만한 시각을 한곳에 모았습니다. 시간축에서 장면을 고르면 요약과 Story, 원본 위치를 확인할 수 있습니다.</p>
    </div>
  </div>
  <div class="viewer-editor-grid">
    <section class="viewer-editor-panel" aria-labelledby="viewerEditorSceneTitle">
      <h3 id="viewerEditorSceneTitle">1. 장면 찾기</h3>
      <p>요약에 들어간 주요 순간과 Highlight를 같은 시간축에서 살펴봅니다.</p>
      <div class="viewer-editor-filter">
        <label for="viewerEditorFilter">자료 필터</label>
        <select id="viewerEditorFilter"></select>
        <span id="viewerEditorCount">{lane_count}개 자료 · {event_count}개 장면</span>
      </div>
      <div class="viewer-editor-density-control">
        <label for="viewerEditorDensityRange">알갱이 표시 밀도</label>
        <input id="viewerEditorDensityRange" type="range" min="0" max="10" step="1" value="6" aria-label="장면 찾기 알갱이 표시 밀도">
        <span id="viewerEditorDensityValue" class="viewer-editor-density-value">핵심</span>
      </div>
      <div class="viewer-editor-zoom" aria-label="장면 찾기 확대 축소">
        <button type="button" class="viewer-editor-zoom-btn" id="viewerEditorZoomOutBtn" title="시간축 축소" aria-label="시간축 축소">-</button>
        <button type="button" class="viewer-editor-zoom-btn" id="viewerEditorZoomInBtn" title="시간축 확대" aria-label="시간축 확대">+</button>
        <button type="button" class="viewer-editor-zoom-btn" id="viewerEditorZoomResetBtn" title="시간축 기본 배율">기본</button>
        <input id="viewerEditorZoomRange" type="range" min="10" max="260" step="5" value="100" aria-label="시간축 확대 배율">
        <span id="viewerEditorZoomValue" class="viewer-editor-zoom-value">1.00x</span>
        <span>전체 보기 0.10x · Ctrl+휠 확대/축소</span>
      </div>
      <div class="viewer-editor-axis" id="viewerEditorAxis" aria-label="유튜브 편집자용 전체 영상 장면 찾기"></div>
    </section>
    <section class="viewer-editor-panel evidence-panel" aria-labelledby="viewerEditorEvidenceTitle">
      <h3 id="viewerEditorEvidenceTitle">2. 선택한 시각의 근거</h3>
      <p id="viewerEditorEvidenceIntro">시간축에서 항목을 선택하면 근거와 방송 맥락이 표시됩니다.</p>
      <div id="viewerEditorEvidence"></div>
    </section>
  </div>
  <script id="viewerEditorToolsData" type="application/json">{data_json}</script>
</div>'''

def _viewer_editor_json(data: dict[str, Any]) -> str:
    return (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
