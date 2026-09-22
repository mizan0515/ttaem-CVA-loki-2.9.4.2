const CHZZK_COMPANION_WINDOW_NAME = 'ttaem-chzzk-companion';
const DEFAULT_EDITOR_MODE = 'companion';
let chzzkCompanionWindow = null;

function setChzzkCompanionStatus(message, state) {
  document.querySelectorAll('[data-editor-companion-status]').forEach((node) => {
    node.textContent = message;
    node.setAttribute('data-state', state);
  });
}

function navigateChzzkCompanion(value) {
  const url = normalizeChzzkCompanionUrl(value);
  if (!url) return false;
  try {
    if (chzzkCompanionWindow && !chzzkCompanionWindow.closed) {
      chzzkCompanionWindow.location.href = url;
      setChzzkCompanionStatus('연결한 영상에 이동 요청을 보냈습니다.', 'connected');
      return true;
    }
  } catch (_error) {
  }
  chzzkCompanionWindow = null;
  try {
    const reportScope = (location.host + location.pathname).replace(/[^a-zA-Z0-9_-]/g, '-');
    chzzkCompanionWindow = window.open(url, `${CHZZK_COMPANION_WINDOW_NAME}-${reportScope}`);
    if (chzzkCompanionWindow && !chzzkCompanionWindow.closed) {
      setChzzkCompanionStatus('영상 연결됨 · 다음 장면과 시간도 같은 영상에서 열립니다.', 'connected');
      return true;
    }
  } catch (_error) {
  }
  chzzkCompanionWindow = null;
  setChzzkCompanionStatus('영상을 열지 못했습니다. 팝업 차단을 확인한 뒤 링크를 다시 클릭해 주세요.', 'error');
  return false;
}

function handleChzzkCompanionClick(event) {
  if (event.defaultPrevented || event.button > 0) return;
  const link = event.target.closest?.('a[href*="chzzk.naver.com/video/"]');
  if (!link) return;
  const url = normalizeChzzkCompanionUrl(link.href);
  if (!url) return;
  const connect = link.hasAttribute('data-editor-entry-primary');
  const splitClick = connect && event.ctrlKey && event.altKey && !event.shiftKey && !event.metaKey;
  if (!splitClick && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)) return;
  if (!connect && !new URL(url).searchParams.has('currentTime')) return;
  event.preventDefault();
  navigateChzzkCompanion(url);
}

function isChzzkCompanionEditorMode() {
  return DEFAULT_EDITOR_MODE === 'companion';
}

function normalizeChzzkCompanionUrl(value) {
  try {
    const url = new URL(String(value || ''), location.href);
    if (url.protocol !== 'https:' || url.hostname !== 'chzzk.naver.com') return '';
    if (url.port || url.username || url.password) return '';
    if (!/^\/video\/\d+\/?$/.test(url.pathname)) return '';
    return url.href;
  } catch (_err) {
    return '';
  }
}

function getChzzkCompanionGuide(userAgent = window.navigator?.userAgent || '') {
  if (/Whale\//i.test(userAgent)) {
    return {
      browser: 'Whale 듀얼 탭',
      action: '우클릭 → 듀얼 탭에서 링크 열기',
      help: '영상 연결을 우클릭한 뒤 듀얼 탭에서 링크 열기를 선택하세요. 웨일의 듀얼 탭 연결을 켜 두고 장면·시간을 일반 클릭하세요.',
    };
  }
  if (/Chrome\//i.test(userAgent) && !/Edg\/|OPR\/|Macintosh/i.test(userAgent)) {
    return {
      browser: 'Chrome 분할 보기',
      action: 'Ctrl+Alt+왼쪽 두 번 클릭',
      help: '영상 연결을 Ctrl+Alt를 누른 채 마우스 왼쪽 버튼으로 두 번 클릭하세요. 이후 장면과 시간은 일반 클릭으로 이동합니다.',
    };
  }
  return {
    browser: '영상 나란히 보기',
    action: '영상 연결 클릭 후 브라우저 분할 메뉴',
    help: '영상 연결을 클릭하고, 열린 영상 탭과 보고서 탭을 브라우저의 분할 보기 메뉴로 묶으세요. 이후 장면과 시간은 일반 클릭으로 이동합니다.',
  };
}

function initEditorCompanionMode() {
  if (!isChzzkCompanionEditorMode()) return;
  const entry = document.querySelector('[data-editor-entry]');
  const workspace = document.getElementById('youtube-editor-tools');
  const primaryControl = document.querySelector('[data-editor-entry-primary]');
  if (!entry || !workspace || !primaryControl) return;
  if (entry.dataset.companionInitialized === '1') return;
  entry.dataset.companionInitialized = '1';

  const explicitUrl = entry.getAttribute('data-editor-chzzk-url') || '';
  const sourceLink = document.querySelector('a[href*="chzzk.naver.com/video/"]');
  const chzzkUrl = explicitUrl || sourceLink?.href || '';
  const companionGuide = getChzzkCompanionGuide();
  function setEntryState(state, message) {
    entry.setAttribute('data-editor-entry-state', state);
    const status = entry.querySelector('[data-editor-entry-status]');
    if (status) {
      status.textContent = message || '';
      status.hidden = !message;
    }
  }

  function installCompanionEditorMode() {
    const safeChzzkUrl = normalizeChzzkCompanionUrl(chzzkUrl);
    if (!safeChzzkUrl) {
      setEntryState('error', 'CHZZK 주소 없음');
      return;
    }
    document.body.classList.add('editor-companion-mode');
    const existingLink = primaryControl.tagName === 'A' ? primaryControl : null;
    const vodLink = existingLink || document.createElement('a');
    vodLink.className = primaryControl.className;
    vodLink.setAttribute('data-editor-entry-primary', '');
    vodLink.removeAttribute('data-report-editor');
    vodLink.href = safeChzzkUrl;
    vodLink.target = CHZZK_COMPANION_WINDOW_NAME;
    vodLink.rel = 'opener';
    vodLink.classList.add('editor-companion-entry-link');
    vodLink.textContent = '영상 연결';
    vodLink.setAttribute('aria-label', `영상 연결. ${companionGuide.browser}: ${companionGuide.action}`);
    if (!existingLink) primaryControl.replaceWith(vodLink);

    let guide = entry.querySelector('[data-editor-companion-guide]');
    if (!guide) {
      guide = document.createElement('section');
      guide.className = 'editor-companion-guide';
      guide.setAttribute('data-editor-companion-guide', '');
      guide.setAttribute('role', 'tooltip');
      guide.setAttribute('aria-label', companionGuide.browser);
      guide.innerHTML = `
        <span class="editor-companion-tooltip-label">${companionGuide.browser}</span>
        <span class="editor-companion-tooltip-flow">
          <span><strong class="editor-companion-keyword">${companionGuide.action}</strong></span>
          <span class="editor-companion-tooltip-arrow" aria-hidden="true">→</span>
          <span>이후 <strong class="editor-companion-keyword">장면·시간 클릭</strong>으로 영상 이동</span>
        </span>
      `;
      vodLink.insertAdjacentElement('afterend', guide);
    }
    guide.id = 'editor-companion-entry-tooltip';
    vodLink.setAttribute('aria-describedby', guide.id);

    const sceneLink = entry.querySelector('.report-editor-tool-link');
    if (sceneLink) {
      sceneLink.classList.add('editor-companion-scene-link');
      if (!sceneLink.querySelector('.editor-companion-scene-label')) {
        const sceneLabel = document.createElement('span');
        sceneLabel.className = 'editor-companion-scene-label';
        sceneLabel.textContent = '장면 도구로 이동';
        sceneLink.appendChild(sceneLabel);
      }
      let secondaryActions = entry.querySelector('[data-editor-companion-secondary]');
      if (!secondaryActions) {
        secondaryActions = document.createElement('div');
        secondaryActions.className = 'editor-companion-secondary-actions';
        secondaryActions.setAttribute('data-editor-companion-secondary', '');
        guide.insertAdjacentElement('afterend', secondaryActions);
      }
      secondaryActions.appendChild(sceneLink);
    }

    let workspaceGuide = workspace.querySelector('[data-editor-companion-workspace-guide]');
    if (!workspaceGuide) {
      workspaceGuide = document.createElement('section');
      workspaceGuide.className = 'editor-companion-workspace-guide';
      workspaceGuide.setAttribute('data-editor-companion-workspace-guide', '');
      workspaceGuide.setAttribute('aria-label', companionGuide.browser);
      const workspaceVodLink = vodLink.cloneNode(true);
      workspaceVodLink.classList.add('editor-companion-workspace-link');
      workspaceGuide.appendChild(workspaceVodLink);
      const workspaceTooltip = guide.cloneNode(true);
      workspaceTooltip.classList.add('editor-companion-workspace-tooltip');
      workspaceTooltip.id = 'editor-companion-workspace-tooltip';
      workspaceVodLink.setAttribute('aria-describedby', workspaceTooltip.id);
      workspaceGuide.appendChild(workspaceTooltip);
      const workspaceHead = workspace.querySelector('.viewer-editor-head');
      if (workspaceHead) workspaceHead.insertAdjacentElement('afterend', workspaceGuide);
      else workspace.prepend(workspaceGuide);
    }

    [entry, workspaceGuide].forEach((host) => {
      if (host.querySelector('[data-editor-companion-status]')) return;
      const status = document.createElement('span');
      status.setAttribute('data-editor-companion-status', '');
      status.className = 'editor-companion-status';
      status.setAttribute('role', 'status');
      status.textContent = '연결된 영상이 없으면 장면·시간 클릭으로 새 탭을 열고 연결합니다.';
      host.appendChild(status);
    });
    let help = workspaceGuide.querySelector('[data-editor-companion-help]');
    if (!help) {
      help = document.createElement('details');
      help.setAttribute('data-editor-companion-help', '');
      help.className = 'editor-companion-help';
      help.innerHTML = `<summary>영상 연결 도움말</summary><p>${companionGuide.help}</p><p>연결된 영상이 없거나 영상을 닫았어도 장면·시간을 클릭하면 해당 시점으로 새 탭을 열고 연결합니다. 새 탭으로 열리면 그 영상 탭과 보고서 탭을 브라우저의 분할 보기로 묶으세요. 이미 연결된 영상이 있으면 같은 탭을 계속 사용합니다.</p>`;
      workspaceGuide.appendChild(help);
    }
    document.addEventListener('click', handleChzzkCompanionClick, true);
  }

  installCompanionEditorMode();
}

const chartData = JSON.parse(document.getElementById("reportChartData").textContent);

const labels = chartData.labels;
const counts = chartData.counts;
const bucketSecs = chartData.bucket_secs;
const peakSecs = [];
const audioPeakSecs = [];
const vodVideoNo = chartData.video_no;
const windowSec = 10;
const peakSet = new Set(peakSecs.map(s => Math.floor(s / windowSec) * windowSec));
const audioPeakSet = new Set(audioPeakSecs.map(s => Math.floor(s / windowSec) * windowSec));
const bgColors = labels.map((_, i) =>
  audioPeakSet.has(i * windowSec) ? 'rgba(224,175,104,0.85)' :
  peakSet.has(i * windowSec) ? 'rgba(247,118,142,0.75)' : 'rgba(122,162,247,0.35)'
);
const audioPeakMarkers = labels.map((_, i) =>
  audioPeakSet.has(i * windowSec) ? Math.max(counts[i] || 0, 1) : null
);
if (labels.length > 0) {
  new Chart(document.getElementById('chatChart'), {
    type: 'bar',
    data: { labels, datasets: [
      { label: '채팅 밀도', data: counts, backgroundColor: bgColors, borderWidth: 0, borderRadius: 2 },
      {
        type: 'line',
        label: '오디오 피크',
        data: audioPeakMarkers,
        showLine: false,
        pointStyle: 'triangle',
        pointRadius: 6,
        pointHoverRadius: 8,
        borderColor: 'rgba(224,175,104,1)',
        backgroundColor: 'rgba(224,175,104,1)'
      }
    ] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: audioPeakSecs.length > 0, labels: { color: '#a9b1d6' } }, tooltip: {
        backgroundColor: '#24283b', borderColor: '#363b54', borderWidth: 1,
        titleColor: '#c0caf5', bodyColor: '#c0caf5'
      }},
      onClick: (_event, elements) => {
        if (!elements.length) return;
        const sec = bucketSecs[elements[0].index];
        if (typeof sec !== 'number') return;
        navigateChzzkCompanion(`https://chzzk.naver.com/video/${vodVideoNo}?currentTime=${Math.max(0, Math.floor(sec))}`);
      },
      onHover: (event, elements) => {
        if (event.native && event.native.target) {
          event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
        }
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#565c7e', font: { size: 10 }, maxTicksLimit: 12 } },
        y: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#565c7e', font: { size: 11 } } }
      }
    }
  });
}

document.querySelectorAll('.t-item').forEach(item => {
  item.addEventListener('click', (e) => {
    if (e.target.closest('a, button')) return;
    const open = item.getAttribute('data-open') === '1';
    item.setAttribute('data-open', open ? '0' : '1');
  });
});
function toggleAll(btn) {
  const items = document.querySelectorAll('.t-item');
  const anyClosed = Array.from(items).some(i => i.getAttribute('data-open') !== '1');
  items.forEach(i => i.setAttribute('data-open', anyClosed ? '1' : '0'));
  btn.textContent = anyClosed ? '근거 모두 접기' : '근거 모두 펼치기';
}


document.querySelector("[data-toggle-evidence]")?.addEventListener("click", function(){toggleAll(this);});


function initEditorEntryState() {
  const entry = document.querySelector('[data-editor-entry]');
  if (!entry) return;
  const statusEl = entry.querySelector('[data-editor-entry-status]');
  const primaryBtn = entry.querySelector('[data-editor-entry-primary]');
  const setState = (state, message) => {
    entry.setAttribute('data-editor-entry-state', state);
    if (statusEl) {
      statusEl.textContent = message || '';
      statusEl.hidden = !message;
    }
  };
  const target = document.getElementById('youtube-editor-tools');
  setState('loading', '');
  window.setTimeout(() => {
    if (entry.getAttribute('data-editor-entry-state') !== 'loading') return;
    if (target) {
      setState('fallback', '');
    } else {
      setState('error', '워크스페이스 없음');
    }
  }, 0);
  if (primaryBtn) {
    primaryBtn.addEventListener('click', () => {
      setState('loading', '');
      window.setTimeout(() => {
        if (target) {
          setState('fallback', '');
        } else {
          setState('error', '열기 실패');
        }
      }, 180);
    });
  }
  window.addEventListener('chzzk-editor-extension-ready', () => {
    setState('installed', '');
  });
  window.addEventListener('chzzk-editor-extension-missing', () => {
    setState('missing', '');
  });
  window.addEventListener('chzzk-editor-extension-error', () => {
    setState('error', '확인 필요');
  });
}

function initViewerEditorTools() {
  const dataNode = document.getElementById('viewerEditorToolsData');
  const axisEl = document.getElementById('viewerEditorAxis');
  const evidenceEl = document.getElementById('viewerEditorEvidence');
  if (!dataNode || !axisEl || !evidenceEl) return;
  const dataSrc = dataNode.getAttribute('data-src') || '';
  function loadMessage(message) {
    const html = `<div class="viewer-editor-empty">${message}</div>`;
    axisEl.innerHTML = html;
    evidenceEl.innerHTML = html;
  }
  function parseInlineData() {
    try { return JSON.parse(dataNode.textContent || '{}'); } catch (_err) { return {}; }
  }
  async function loadEditorToolsData() {
    if (!dataSrc) return parseInlineData();
    const response = await fetch(dataSrc, { cache: 'default' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  function startViewerEditorTools(data) {
  const events = Array.isArray(data.events) ? data.events : [];
  const outlineProjection = Array.isArray(data.outline_projection) ? data.outline_projection : [];
  const eventsById = new Map(events.map((event) => [String(event && event.id || ''), event]).filter(([id]) => id));
  const editPointClusterView = data.edit_point_clusters && typeof data.edit_point_clusters === 'object' ? data.edit_point_clusters : {};
  const editPointClusters = Array.isArray(editPointClusterView.clusters) ? editPointClusterView.clusters : [];
  const buckets = Array.isArray(data.chat_buckets) ? data.chat_buckets : [];
  const subtitles = Array.isArray(data.subtitles) ? data.subtitles : [];
  const audioWaveform = data.audio_waveform && typeof data.audio_waveform === 'object' ? data.audio_waveform : {};
  const waveformSamples = Array.isArray(audioWaveform.samples) ? audioWaveform.samples : [];
  const preview = data.edit_export_preview && typeof data.edit_export_preview === 'object' ? data.edit_export_preview : {};
  const duration = Math.max(1, Number(data.duration_sec || 0), Number(audioWaveform.duration_sec || 0), ...events.map((event) => Number(event.end_sec || event.start_sec || 0)), ...outlineProjection.map((event) => Number(event.end_sec || event.start_sec || 0)), ...buckets.map((row) => Number(row.end_sec || row.start_sec || 0)), ...waveformSamples.map((row) => Number(row.end_sec || row.start_sec || 0)));
  const labelsMap = data.labels || {};
  const viewerLabelsMap = data.viewer_labels || {};
  const colorsMap = data.colors || {};
  const purposesMap = data.purposes || {};
  const viewerClipSource = data.viewer_clip_source && typeof data.viewer_clip_source === 'object'
    ? data.viewer_clip_source
    : null;
  const overviewOnlyLaneKinds = new Set(['audio_waveform', 'chat_volume']);
  const eventCountsByKind = events.reduce((map, event) => {
    const kind = String(event && (event.kind || event.lane) || '');
    if (kind) map.set(kind, (map.get(kind) || 0) + 1);
    return map;
  }, new Map());
  function laneActualEventCount(kind) {
    return eventCountsByKind.get(String(kind || '')) || 0;
  }
  function shouldHideSceneLane(key) {
    if (!overviewOnlyLaneKinds.has(key) && laneActualEventCount(key) === 0) return true;
    return key === 'comment_replay' && laneActualEventCount('chapter') > 0;
  }
  function isRenderableSceneLane(lane) {
    const key = String(lane && lane.key || '');
    if (!key) return false;
    if (shouldHideSceneLane(key)) return false;
    const role = String(lane && lane.display_role || '').toLowerCase();
    if (role === 'overview' || overviewOnlyLaneKinds.has(key)) return false;
    return laneActualEventCount(key) > 0;
  }
  const lanes = Array.isArray(data.lanes) && data.lanes.length
    ? data.lanes.filter(isRenderableSceneLane)
    : Array.from(new Set(events.map((event) => event.kind).filter(Boolean))).map((kind) => ({ key: kind, label: labelsMap[kind] || kind, event_count: laneActualEventCount(kind), display_role: 'scene' })).filter(isRenderableSceneLane);
  const kinds = lanes.map((lane) => lane.key);
  const filterEl = document.getElementById('viewerEditorFilter');
  const countEl = document.getElementById('viewerEditorCount');
  const zoomRange = document.getElementById('viewerEditorZoomRange');
  const zoomValue = document.getElementById('viewerEditorZoomValue');
  const zoomOutBtn = document.getElementById('viewerEditorZoomOutBtn');
  const zoomInBtn = document.getElementById('viewerEditorZoomInBtn');
  const zoomResetBtn = document.getElementById('viewerEditorZoomResetBtn');
  const densityRange = document.getElementById('viewerEditorDensityRange');
  const densityValue = document.getElementById('viewerEditorDensityValue');
  const baseTimelineWidth = Math.max(760, Math.min(1600, Math.round(duration / 22)));
  const timelineLabelColumnWidth = 144;
  let timelineZoom = 1;
  let timelineAutoFit = true;
  let timelineFitFrame = 0;
  let markerDensityLevel = 6;
  let axisRendered = false;
  let selectedEventId = '';
  let selectedHighlightId = '';
  let selectedHighlightSpanIndex = -1;
  let renderedAxisEvents = new Map();
  let densityScrollFrame = 0;

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function fmt(sec) {
    const n = Math.max(0, Math.floor(Number(sec || 0)));
    const h = Math.floor(n / 3600);
    const m = Math.floor((n % 3600) / 60);
    const s = n % 60;
    const two = (v) => String(v).padStart(2, '0');
    return h ? `${two(h)}:${two(m)}:${two(s)}` : `${two(m)}:${two(s)}`;
  }
  function displayTime(row, fallbackSec) {
    const vodLabel = String(row && row.vod_label || '').trim();
    const seek = row && row.seek_sec != null ? Number(row.seek_sec) : NaN;
    const base = Number.isFinite(seek) ? seek : fallbackSec;
    if (vodLabel) return `${vodLabel} ${fmt(base)}`;
    const tc = String(row && row.timecode || '').trim();
    return tc || fmt(base);
  }
  function near(row, sec, radius) {
    const start = Number(row && row.start_sec);
    const endRaw = row && row.end_sec != null ? Number(row.end_sec) : start;
    const end = Number.isFinite(endRaw) ? endRaw : start;
    return Number.isFinite(start) && Number.isFinite(sec) && start - radius <= sec && sec <= end + radius;
  }
  function seekUrl(sec, row) {
    const videoNo = String(row && (row.video_no || row.source_video_no) || data.video_no || '');
    if (!videoNo) return '';
    const targetSec = row && row.seek_sec != null ? row.seek_sec : sec;
    return `https://chzzk.naver.com/video/${encodeURIComponent(videoNo)}?currentTime=${Math.max(0, Math.floor(Number(targetSec || 0)))}`;
  }
  function evidenceSourceRow(sec) {
    const videoNo = String(data.evidence_video_no || '');
    return videoNo ? { video_no: videoNo, seek_sec: sec } : null;
  }
  function syncSplitEditorSeek(sec, row, navigateCompanion = false) {
    const url = seekUrl(sec, row);
    if (!url) return '';
    if (navigateCompanion) navigateChzzkCompanion(url);
    return url;
  }
  let activeAxisDisclosure = null;
  function axisDisclosurePosition(panel, trigger) {
    const gap = 10, edge = 12;
    const anchor = trigger.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const width = panelRect.width, height = panelRect.height;
    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
    const maxLeft = Math.max(edge, window.innerWidth - width - edge);
    const maxTop = Math.max(edge, window.innerHeight - height - edge);
    const candidates = [
      {left: anchor.right + gap, top: anchor.top + (anchor.height - height) / 2},
      {left: anchor.left - width - gap, top: anchor.top + (anchor.height - height) / 2},
      {left: anchor.left + (anchor.width - width) / 2, top: anchor.bottom + gap},
      {left: anchor.left + (anchor.width - width) / 2, top: anchor.top - height - gap},
    ].map((candidate) => ({left: clamp(candidate.left, edge, maxLeft), top: clamp(candidate.top, edge, maxTop)}));
    const visibleMarkers = Array.from(axisEl.querySelectorAll('.viewer-editor-marker:not([aria-hidden="true"]),.viewer-editor-outline-range,.viewer-editor-outline-point'))
      .filter((marker) => marker !== trigger && marker.getClientRects().length)
      .map((marker) => marker.getBoundingClientRect());
    const overlapArea = (candidate, marker) => Math.max(0, Math.min(candidate.left + width, marker.right) - Math.max(candidate.left, marker.left)) * Math.max(0, Math.min(candidate.top + height, marker.bottom) - Math.max(candidate.top, marker.top));
    const best = candidates
      .map((candidate, index) => ({...candidate, score: visibleMarkers.reduce((total, marker) => total + overlapArea(candidate, marker), 0), index}))
      .sort((a, b) => a.score - b.score || a.index - b.index)[0];
    panel.style.left = `${Math.round(best.left)}px`;
    panel.style.top = `${Math.round(best.top)}px`;
  }
  function hideAxisHierarchyPreview() {
    const preview = document.getElementById('viewer-editor-collision-shared-preview');
    if (preview) preview.hidden = true;
    axisEl.querySelectorAll('[aria-describedby="viewer-editor-collision-shared-preview"]').forEach((node) => node.removeAttribute('aria-describedby'));
  }
  function setAxisDisclosure(panel, trigger, open, restoreFocus = false) {
    const cluster = trigger.closest('.viewer-editor-collision-cluster');
    if (open) {
      if (activeAxisDisclosure && activeAxisDisclosure.panel !== panel) {
        setAxisDisclosure(activeAxisDisclosure.panel, activeAxisDisclosure.trigger, false);
      }
      hideAxisHierarchyPreview();
      panel.classList.add('viewer-editor-floating-disclosure');
      document.body.append(panel);
      panel.hidden = false;
      axisDisclosurePosition(panel, trigger);
      trigger.setAttribute('aria-expanded', 'true');
      cluster?.classList.add('viewer-editor-disclosure-open');
      activeAxisDisclosure = { panel, trigger };
      return;
    }
    panel.hidden = true;
    panel.classList.remove('viewer-editor-floating-disclosure');
    panel.style.left = '';
    panel.style.top = '';
    trigger.setAttribute('aria-expanded', 'false');
    cluster?.classList.remove('viewer-editor-disclosure-open');
    panel.remove();
    if (activeAxisDisclosure && activeAxisDisclosure.panel === panel) activeAxisDisclosure = null;
    if (restoreFocus && trigger.isConnected) trigger.focus();
  }
  function closeActiveAxisDisclosure(exceptTrigger = null, restoreFocus = false) {
    if (!activeAxisDisclosure || activeAxisDisclosure.trigger === exceptTrigger) return;
    setAxisDisclosure(activeAxisDisclosure.panel, activeAxisDisclosure.trigger, false, restoreFocus);
  }
  document.addEventListener('pointerdown', (event) => {
    if (!activeAxisDisclosure) return;
    const { panel, trigger } = activeAxisDisclosure;
    if (panel.contains(event.target) || trigger.contains(event.target)) return;
    closeActiveAxisDisclosure();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !activeAxisDisclosure) return;
    event.preventDefault();
    closeActiveAxisDisclosure(null, true);
  });
  if (typeof axisEl.addEventListener === 'function') {
    axisEl.addEventListener('scroll', () => closeActiveAxisDisclosure(), {passive: true});
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', () => closeActiveAxisDisclosure());
  }
  function bindMarkerActivation(marker, activate) {
    marker.addEventListener('click', activate);
    marker.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      activate();
    });
  }
  function highlightIdForEvent(event) {
    return String(event && (event.highlight_id || (Number.isInteger(event.source_span_index) ? event.id : '')) || '');
  }
  function highlightSpanIndexForEvent(event) {
    const index = Number(event && event.source_span_index);
    return Number.isInteger(index) ? index : -1;
  }
  function syncAxisMarkerSelection() {
    axisEl.querySelectorAll('[data-event-id]').forEach((marker) => {
      const isHighlightSibling = Boolean(selectedHighlightId) && marker.dataset.highlightId === selectedHighlightId;
      const isCurrentSpan = isHighlightSibling && Number(marker.dataset.highlightSpanIndex) === selectedHighlightSpanIndex;
      marker.classList.toggle('selected', isHighlightSibling || marker.dataset.eventId === selectedEventId);
      marker.classList.toggle('highlight-group-selected', isHighlightSibling);
      marker.classList.toggle('highlight-span-current', isCurrentSpan);
      marker.setAttribute('aria-pressed', String(isHighlightSibling || marker.dataset.eventId === selectedEventId));
      if (isCurrentSpan) marker.setAttribute('aria-current', 'true');
      else marker.removeAttribute('aria-current');
    });
    const highlightRow = axisEl.querySelector('.viewer-editor-outline-row[data-outline-level="Highlight"]');
    const selectionStatus = highlightRow && highlightRow.querySelector('.viewer-editor-highlight-selection-status');
    if (highlightRow && selectionStatus) {
      const selectedMarkers = Array.from(highlightRow.querySelectorAll('[data-event-id][data-highlight-id]'))
        .filter((marker) => marker.dataset.highlightId === selectedHighlightId);
      const firstMarker = selectedMarkers.find((marker) => Number(marker.dataset.highlightSpanIndex) === 0) || selectedMarkers[0];
      const spanCount = Math.max(0, Number(firstMarker && firstMarker.dataset.highlightSpanCount || 0));
      const highlightLabel = String(firstMarker && firstMarker.dataset.highlightGroupLabel || '').trim() || 'Highlight';
      const hasSelection = Boolean(selectedHighlightId) && selectedMarkers.length > 0;
      highlightRow.classList.toggle('highlight-group-active', hasSelection);
      selectionStatus.hidden = !hasSelection;
      selectionStatus.textContent = hasSelection ? `${highlightLabel} 선택 · ${spanCount}장면` : '';
    }
  }
  function selectAxisEvent(event) {
    selectedEventId = String(event && event.id || '');
    selectedHighlightId = highlightIdForEvent(event);
    selectedHighlightSpanIndex = selectedHighlightId ? highlightSpanIndexForEvent(event) : -1;
    syncAxisMarkerSelection();
  }
  function setHighlightGroupEmphasis(highlightId, active) {
    if (!highlightId) return;
    axisEl.querySelectorAll('[data-highlight-id]').forEach((marker) => {
      if (marker.dataset.highlightId === highlightId) marker.classList.toggle('highlight-group-emphasis', Boolean(active));
    });
  }
  function activateAxisEventMarker(marker) {
    closeActiveAxisDisclosure(marker);
    const event = renderedAxisEvents.get(String(marker.dataset.eventId || '')) || events.find((item) => item.id === marker.dataset.eventId);
    syncSplitEditorSeek(event && event.start_sec, event, true);
    renderEvidence(event || null);
  }
  function clipUrl(uid) {
    const raw = String(uid || '').trim();
    if (!/^[A-Za-z0-9_-]{6,80}$/.test(raw)) return '';
    if (raw === 'viewer_clip_anchor') return '';
    return `https://chzzk.naver.com/clips/${encodeURIComponent(raw)}`;
  }
  function timeLink(sec, row) {
    const url = seekUrl(sec, row);
    const label = displayTime(row, sec);
    return url ? `<a class="viewer-editor-time-link" href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>` : esc(label);
  }
  function timeChip(sec, row) {
    const url = seekUrl(sec, row);
    const label = displayTime(row, sec);
    return url ? `<a class="viewer-editor-chip" href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>` : `<span class="viewer-editor-chip">${esc(label)}</span>`;
  }
  function contextSourceRow(row) {
    const scoped = { ...(row || {}) };
    if (scoped.video_no && scoped.vod_label && scoped.seek_sec != null) return scoped;
    const samples = Array.isArray(scoped.samples) ? scoped.samples : [];
    const sample = samples.find((item) => item && (item.video_no || item.vod_label || item.seek_sec != null));
    if (!sample) return scoped;
    if (!scoped.video_no && sample.video_no) scoped.video_no = sample.video_no;
    if (!scoped.vod_label && sample.vod_label) scoped.vod_label = sample.vod_label;
    if (scoped.seek_sec == null && sample.seek_sec != null) {
      const rowStart = Number(scoped.start_sec);
      const sampleSec = Number(sample.sec);
      const sampleSeek = Number(sample.seek_sec);
      scoped.seek_sec = Number.isFinite(rowStart) && Number.isFinite(sampleSec) && Number.isFinite(sampleSeek)
        ? rowStart - (sampleSec - sampleSeek)
        : sampleSeek;
    }
    return scoped;
  }
  function hasVerifiedExplicitSource(row) {
    if (!row || typeof row !== 'object') return false;
    const status = String(row.verifier_status || row.source_verification_status || '').trim().toUpperCase();
    return row.verified_source === true || row.source_verified === true || status === 'VERIFIED_SOURCE_VIDEO';
  }
  function canonicalSelectedSourceRow(row, selectedEvent) {
    const scoped = contextSourceRow(row);
    if (hasVerifiedExplicitSource(scoped) && (scoped.video_no || scoped.source_video_no)) return scoped;
    const canonicalVideoNo = String(selectedEvent && (selectedEvent.video_no || selectedEvent.source_video_no) || '').trim();
    if (!canonicalVideoNo) return scoped;
    return { ...scoped, video_no: canonicalVideoNo, source_video_no: canonicalVideoNo };
  }
  function timecodeToSec(value) {
    const parts = String(value || '').split(':').map((part) => Number(part));
    if (parts.some((part) => !Number.isFinite(part))) return NaN;
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    return NaN;
  }
  function originStartSec(event) {
    const value = event && event.anchor_origin_start_sec != null ? Number(event.anchor_origin_start_sec) : Number(event && event.start_sec);
    return Number.isFinite(value) ? value : NaN;
  }
  function originSeekSec(event) {
    const value = event && event.anchor_origin_seek_sec != null ? Number(event.anchor_origin_seek_sec) : Number(event && event.seek_sec);
    return Number.isFinite(value) ? value : NaN;
  }
  function sourceTimeTarget(localOrGlobalSec, event) {
    const sec = Number(localOrGlobalSec);
    if (!Number.isFinite(sec)) return null;
    const rootStart = originStartSec(event);
    const rootSeek = originSeekSec(event);
    const hasSourceLocal = event && event.vod_label && Number.isFinite(rootStart) && Number.isFinite(rootSeek);
    const startSec = hasSourceLocal ? rootStart + (sec - rootSeek) : sec;
    const row = {
      ...(event || {}),
      start_sec: startSec,
      sec: startSec,
      timecode: hasSourceLocal ? (event && event.timecode) : fmt(startSec),
      anchor_origin_start_sec: Number.isFinite(rootStart) ? rootStart : startSec,
      anchor_origin_seek_sec: Number.isFinite(rootSeek) ? rootSeek : (event && event.seek_sec),
    };
    if (hasSourceLocal) row.seek_sec = sec;
    return row;
  }
  function explicitEvidenceTarget(item, event) {
    const explicit = item && item.start_sec != null ? Number(item.start_sec) : item && item.sec != null ? Number(item.sec) : NaN;
    if (!Number.isFinite(explicit)) return null;
    const row = {
      ...(event || {}),
      ...(item || {}),
      start_sec: explicit,
      sec: explicit,
      timecode: item && item.timecode ? item.timecode : fmt(explicit),
      anchor_origin_start_sec: originStartSec(event),
      anchor_origin_seek_sec: originSeekSec(event),
    };
    return row;
  }
  function pushEvidenceTarget(targets, row, label) {
    if (!row) return;
    const sec = Number(row.start_sec != null ? row.start_sec : row.sec);
    if (!Number.isFinite(sec)) return;
    const videoNo = String(row.video_no || row.source_video_no || '').trim();
    const key = `${videoNo}|${Math.round(sec * 10) / 10}`;
    if (targets.some((target) => target.key === key)) return;
    targets.push({ key, label: label || '근거 시각', row });
  }
  function evidenceTargetsForEvent(event, selectedSec) {
    const targets = [];
    const cardRow = explicitEvidenceTarget({ start_sec: Number(selectedSec) }, event) || sourceTimeTarget(selectedSec, event);
    pushEvidenceTarget(targets, cardRow, '카드 시각');
    const rows = []
      .concat(Array.isArray(event && event.evidence) ? event.evidence : [])
      .concat(Array.isArray(event && event.guidance) ? event.guidance : []);
    rows.forEach((item) => {
      const label = friendlyEvidenceLabel(item && (item.label || item.title || item.type) || '근거');
      pushEvidenceTarget(targets, explicitEvidenceTarget(item, event), label);
      const text = `${item && item.timecode || ''} ${item && item.text || ''}`;
      const re = /\b(\d{1,2}:\d{2}(?::\d{2})?)\b/g;
      for (const match of text.matchAll(re)) {
        const sec = timecodeToSec(match[1]);
        pushEvidenceTarget(targets, sourceTimeTarget(sec, event), `${label} ${match[1]}`);
      }
    });
    return targets.slice(0, 8);
  }
  function renderEvidenceTargetPicker(targets, activeSec) {
    if (!Array.isArray(targets) || targets.length <= 1) return '';
    const buttons = targets.map((target, index) => {
      const sec = Number(target.row && target.row.start_sec);
      const active = Number.isFinite(sec) && Math.abs(sec - Number(activeSec || 0)) < 0.5;
      return `<button type="button" class="viewer-editor-chip ${active ? 'selected' : ''}" data-evidence-target-index="${index}">${esc(target.label)} · ${esc(displayTime(target.row, sec))}</button>`;
    }).join('');
    return `<div class="viewer-editor-target-picker" aria-label="근거 시각 선택"><strong>근거 시각 선택</strong><div class="viewer-editor-target-buttons">${buttons}</div></div>`;
  }
  function linkTimecodesText(text, row) {
    const raw = String(text || '');
    const re = /\b(\d{1,2}:\d{2}(?::\d{2})?)\b/g;
    let out = '';
    let last = 0;
    for (const match of raw.matchAll(re)) {
      out += esc(raw.slice(last, match.index));
      const sec = timecodeToSec(match[1]);
      out += timeLink(sec, row || {});
      last = match.index + match[1].length;
    }
    out += esc(raw.slice(last));
    return out;
  }
  function renderEvidenceRow(row) {
    if (row.clipUrl) {
      const label = '시청자 클립';
      const rawText = String(row.text || '').trim();
      const looksLikeUid = /^[A-Za-z0-9_-]{6,80}$/.test(rawText);
      const text = rawText && !looksLikeUid ? `${esc(rawText)} · 치지직 클립 열기` : '치지직 클립 열기';
      return `<div class="viewer-editor-row"><strong>${label}</strong><br><a class="viewer-editor-evidence-link" href="${esc(row.clipUrl)}" target="_blank" rel="noopener">${text}</a></div>`;
    }
    const rawLabel = row.label || '근거';
    const labelText = friendlyEvidenceLabel(rawLabel);
    const bodyText = friendlyEvidenceText(rawLabel, row.text || '');
    const labelSec = row.sec != null ? row.sec : row.start_sec;
    const label = labelSec != null ? `${esc(labelText)} · ${timeLink(labelSec, row)}` : linkTimecodesText(labelText, row);
    return `<div class="viewer-editor-row"><strong>${label}</strong><br>${linkTimecodesText(bodyText, row)}</div>`;
  }
  function normalizeEvidenceIdentityText(value) {
    return String(value || '').trim().replace(/\s+/g, ' ').toLowerCase();
  }
  function clusterMemberIdentityKey(row) {
    const sec = Number(row && row.start_sec);
    const roundedSec = Number.isFinite(sec) ? Math.round(sec) : 0;
    const title = normalizeEvidenceIdentityText(friendlyEventTitle(row) || optionLabel(row && row.kind) || '편집 후보');
    return `${roundedSec}|${title}|${seekUrl(Number.isFinite(sec) ? sec : 0, row)}`;
  }
  function groupedClusterMemberRows(rows) {
    const groups = [];
    const byKey = new Map();
    (rows || []).forEach((row) => {
      if (!row) return;
      const key = clusterMemberIdentityKey(row);
      let group = byKey.get(key);
      if (!group) {
        group = { key, primary: row, rows: [], kinds: [] };
        byKey.set(key, group);
        groups.push(group);
      }
      group.rows.push(row);
      const kind = String(row.kind || '').trim();
      if (kind && !group.kinds.includes(kind)) group.kinds.push(kind);
    });
    return groups;
  }
  function renderClusterMemberSourceBadges(group) {
    const kinds = Array.isArray(group && group.kinds) ? group.kinds : [];
    if (!kinds.length) return '';
    const sourceBadges = kinds.map((kind) => `<span class="viewer-editor-chip" data-cluster-source-kind="${esc(kind)}">${esc(optionLabel(kind))}</span>`).join('');
    const countBadge = group.rows && group.rows.length > 1
      ? `<span class="viewer-editor-chip" data-evidence-group-size="${group.rows.length}">근거 출처 ${group.rows.length}개</span>`
      : '';
    return `<div class="viewer-editor-source-badges" aria-label="묶인 근거 출처">${countBadge}${sourceBadges}</div>`;
  }
  function renderClusterMemberRow(group, index, isRest) {
    const row = group && group.primary ? group.primary : group;
    const identity = group && group.key ? group.key : clusterMemberIdentityKey(row);
    const sec = Number(row && row.start_sec);
    const rank = isRest ? `나머지 ${Math.max(1, index - 2)}` : `대표 ${index + 1}`;
    const label = row && row.vod_label ? `${row.vod_label} · ${optionLabel(row.kind)}` : optionLabel(row && row.kind);
    const title = friendlyEventTitle(row) || label || '편집 후보';
    const link = row && row.kind === 'viewer_clip' ? viewerClipLinkInfo([row], row) : { url: '', title: '' };
    const body = link.url
      ? `<a class="viewer-editor-evidence-link" href="${esc(link.url)}" target="_blank" rel="noopener">${esc(link.title || title)} · 치지직 클립 열기</a>`
      : linkTimecodesText(title, row);
    const kinds = Array.isArray(group && group.kinds) ? group.kinds : [row && row.kind].filter(Boolean);
    return `<div class="viewer-editor-row cluster-member ${isRest ? 'rest' : 'top'}" data-cluster-member-rank="${isRest ? 'rest' : 'top'}" data-cluster-member-kind="${esc(row && row.kind || '')}" data-cluster-source-kinds="${esc(kinds.join(' '))}" data-evidence-group-size="${group && group.rows ? group.rows.length : 1}" data-evidence-identity="${esc(identity)}">
      <span class="viewer-editor-member-rank">${esc(rank)}</span>
      <strong>${esc(label)} · ${timeLink(Number.isFinite(sec) ? sec : 0, row)}</strong>
      ${renderClusterMemberSourceBadges(group)}
      <br>${body}
    </div>`;
  }
  function renderEditPointClusterEvidence(event, sec) {
    const cluster = event && event.cluster ? event.cluster : {};
    const rows = Array.isArray(event && event.cluster_events) ? event.cluster_events : [];
    const clusterId = editPointClusterId(cluster);
    selectedEventId = String(event && event.id || `edit_point_cluster_${clusterId}`);
    axisEl.querySelectorAll('[data-event-id]').forEach((marker) => marker.classList.remove('selected'));
    axisEl.querySelectorAll('[data-edit-point-cluster-id]').forEach((marker) => {
      marker.classList.toggle('selected', selectedEventId === `edit_point_cluster_${marker.dataset.editPointClusterId || ''}`);
    });
    const introEl = document.getElementById('viewerEditorEvidenceIntro');
    if (introEl) introEl.hidden = true;
    const selectedColor = markerColor('edit_point_cluster');
    const signalCount = Math.max(0, Number(cluster.signal_count || rows.length || 0));
    const zoneFamilyKeys = Array.isArray(event && event.heatmap_zone_families) ? event.heatmap_zone_families : [];
    const familyKeys = Array.from(new Set(clusterFamilyKeys(cluster).concat(zoneFamilyKeys))).filter(Boolean);
    const familyChipHtml = familyKeys.slice(0, 7)
      .map((kind) => `<span class="viewer-editor-chip" data-selected-signal-family="${esc(kind)}">${esc(optionLabel(kind))}</span>`)
      .join('');
    const viewerClipCount = Math.max(0, Number(cluster.viewer_clip_count || rows.filter((row) => row.kind === 'viewer_clip').length));
    const overflowCount = Math.max(0, Number(cluster.overflow_event_count || Math.max(0, rows.length - 3)));
    const zoneRelatedCount = Math.max(0, Number(event && event.heatmap_zone_related_count || 0));
    const groupedRows = groupedClusterMemberRows(rows);
    const topRows = groupedRows.slice(0, 3);
    const restRows = groupedRows.slice(3);
    const memberRows = topRows.map((row, index) => renderClusterMemberRow(row, index, false))
      .concat(restRows.map((row, index) => renderClusterMemberRow(row, index + 3, true)));
    const subtitleContext = subtitles.filter((row) => near(row, sec, 45)).slice(0, 6);
    const bucketContext = buckets.filter((row) => near(row, sec, 45)).slice(0, 5);
    evidenceEl.innerHTML = `<div class="viewer-editor-selected-card" style="--viewer-marker-color:${esc(selectedColor)}" data-selected-edit-point-cluster="${esc(clusterId)}" data-selected-signal-families="${esc(familyKeys.join(' '))}">
      <span class="viewer-editor-selected-kicker">먼저 볼 편집 후보 묶음</span>
      <div class="viewer-editor-selected-title">${esc(fmt(sec))} 주변 신호 ${signalCount}개</div>
      <div class="viewer-editor-selected-meta">
        ${timeChip(sec, event)}
        <span class="viewer-editor-chip">자료 ${Math.max(0, Number(cluster.family_count || 0))}종</span>
        <span class="viewer-editor-chip">시청자 클립 ${viewerClipCount}개</span>
        <span class="viewer-editor-chip">신뢰 ${esc(clusterConfidenceLabel(cluster.confidence))}</span>
        ${zoneRelatedCount ? `<span class="viewer-editor-chip">주변 묶음 ${zoneRelatedCount}개</span>` : ''}
        ${familyChipHtml}
        ${overflowCount ? `<span class="viewer-editor-chip warning">나머지 ${overflowCount}개도 펼침</span>` : ''}
      </div>
    </div>
    <div class="viewer-editor-row reason"><strong>왜 먼저 보나요</strong><br>${esc(clusterFamilyText(cluster))}가 같은 시간대에 겹친 편집 후보입니다. 요약 사실이 아니라 사람이 먼저 확인할 탐색 묶음입니다.</div>
    ${renderContextSummary(subtitleContext, bucketContext)}
    <div class="viewer-editor-list" data-edit-point-cluster-members="${groupedRows.length}" data-edit-point-cluster-source-events="${rows.length}">${memberRows.join('') || '<div class="viewer-editor-empty">묶음 안에 표시할 근거가 없습니다.</div>'}</div>`;
  }
  function viewerClipLinkInfo(eventList, selectedEvent) {
    const selectedStart = Number(selectedEvent && selectedEvent.start_sec);
    const selectedTitle = friendlyEventTitle(selectedEvent);
    for (const candidate of eventList || []) {
      if (!candidate || candidate.kind !== 'viewer_clip') continue;
      const sameStart = Number.isFinite(selectedStart) && Math.abs(Number(candidate.start_sec || 0) - selectedStart) <= 1;
      const sameTitle = !selectedTitle || friendlyEventTitle(candidate) === selectedTitle;
      if (!sameStart || !sameTitle) continue;
      for (const item of candidate.evidence || []) {
        const uid = String(item.label || '').trim();
        const url = clipUrl(uid);
        if (url) return { uid, url, title: String(item.text || candidate.title || selectedTitle || '시청자 클립').trim() };
      }
    }
    return { uid: '', url: '', title: '' };
  }
  function optionLabel(kind) {
    if (kind === 'chat') return '전체 채팅량';
    return viewerLabelsMap[kind] || labelsMap[kind] || kind || '기타';
  }
  function lanePurpose(kind) {
    return purposesMap[kind] || '편집 후보';
  }
  function markerColor(kind) {
    if (kind === 'edit_point_cluster') return 'rgba(158,206,106,0.86)';
    return colorsMap[kind] || 'rgba(122,162,247,0.86)';
  }
  function friendlyAudioSignalText(text) {
    const lowered = String(text || '').toLowerCase();
    if (!lowered.includes('audio_energy_peak') && !lowered.includes('voice_reaction_candidate') && !lowered.includes('chat_audio_overlap') && !lowered.includes('clipping_or_limiter_diagnostic')) return '';
    const parts = [];
    if (lowered.includes('audio_energy_peak')) parts.push('소리가 크게 튄 구간');
    if (lowered.includes('voice_reaction_candidate')) parts.push('목소리 리액션이 커진 후보');
    if (lowered.includes('chat_audio_overlap')) parts.push('채팅 반응도 같이 몰린 시각');
    if (lowered.includes('clipping_or_limiter_diagnostic')) parts.push('음량이 거칠게 잡힌 신호');
    return `${parts.join(' · ')}입니다. 편집 컷 후보로만 보고, 실제 장면 의미는 자막과 화면 흐름으로 확인하세요.`;
  }
  function friendlyEventTitle(event) {
    const raw = String(event && event.title || '').trim();
    if (!friendlyAudioSignalText(raw)) return raw;
    const lowered = raw.toLowerCase();
    if (lowered.includes('chat_audio_overlap')) return '소리와 채팅이 함께 튄 구간';
    if (lowered.includes('voice_reaction_candidate')) return '목소리 리액션이 커진 구간';
    if (lowered.includes('clipping_or_limiter_diagnostic')) return '음량이 갑자기 거칠어진 구간';
    return '소리가 크게 튄 구간';
  }
  function friendlyEvidenceLabel(label) {
    const raw = String(label || '').trim();
    const lowered = raw.toLowerCase();
    if (!raw) return '근거';
    if (lowered.includes('chat_or_text_evidence')) return '채팅/자막 근거';
    if (lowered.includes('asr segment') || lowered.includes('subtitle') || raw.includes('자막')) return '자막 근거';
    if (lowered.includes('chat_bucket') || raw.includes('채팅')) return raw.includes('대표') ? '대표 채팅 반응' : '채팅 반응';
    if (lowered.includes('viewer_clip') || raw.includes('클립')) return '시청자 클립';
    if (lowered.includes('audio') || raw.includes('오디오')) return '오디오 반응';
    if (lowered.includes('highlight_candidate')) return '자동 하이라이트 후보';
    if (lowered.includes('summary_markdown')) return '요약에 적힌 장면';
    if (lowered.includes('subtitle_chunk') || lowered.includes('timed_evidence')) return '자막 근거';
    if (lowered.includes('semantic_vector') || lowered.includes('timestamp_overlap')) return '가까운 방송 근거';
    if (lowered.includes('chat_density_peak')) return '채팅 반응';
    if (lowered.includes('supporting_signal')) return '보조 신호';
    if (lowered.includes('proximity_signal')) return '가까운 관련 신호';
    if (lowered.includes('chzzk_comment_timeline') || lowered === 'chapter') return '댓글 시간표 후보';
    if (lowered.includes('segment_ledger') || raw.includes('요약') || raw.includes('timeline')) return '요약에 적힌 장면';
    if (lowered.includes('comment')) return '댓글 시간표 후보';
    if (lowered === 'source' || lowered === 'supporting' || lowered === 'candidate') return '참고 자료';
    return raw;
  }
  function friendlyEvidenceText(label, text) {
    const raw = String(text || '').trim();
    const joined = `${String(label || '')} ${raw}`;
    if (!raw) return '';
    const audioText = friendlyAudioSignalText(joined);
    if (audioText) return audioText;
    if (joined.includes('viewer_clip_anchor')) return '시청자 클립에서 잡힌 위치입니다.';
    if (joined.includes('audio_reaction_peak')) return '오디오 반응이 겹친 참고 신호입니다.';
    if (joined.includes('chat_density_peak')) return '채팅이 몰린 시각입니다.';
    if (joined.includes('supporting_signal_only')) return '보조 신호입니다. 장면 판단은 자막이나 기존 요약과 함께 보세요.';
    if (joined.includes('deterministic_injection')) return '시청자 클립 기준으로 보강된 후보입니다.';
    if (
      /^[{].*[}]$/.test(raw)
      || raw.includes("'source_type'")
      || raw.includes('"source_type"')
      || /summary_markdown|semantic_vector|subtitle_chunk|timed_evidence|timestamp_overlap|match_reason|evidence_id/i.test(joined)
    ) return '장면 내용은 주변 자막과 요약으로 확인하세요.';
    return raw;
  }
  function fallbackGuidance(kind) {
    const messages = [];
    const add = (title, text) => {
      const key = `${title}|${text}`;
      if (!messages.some((item) => `${item.title}|${item.text}` === key)) messages.push({ title, text });
    };
    if (kind === 'chat_laughter') {
      add('웃음/놀람 채팅', '웃기거나 놀란 반응이 채팅에 많이 잡힌 구간입니다. 리액션 컷 후보로 보고, 정확한 장면 내용은 자막과 요약으로 확인하세요.');
    } else if (kind === 'chat_surprise') {
      add('채팅 급증 구간', '채팅량이 갑자기 늘어난 구간입니다. 무슨 일이 났는지 찾는 출발점으로 보고, 웃긴 장면인지 이슈 장면인지는 자막과 화면 흐름으로 확인하세요.');
    } else if (kind === 'chat') {
      add('채팅 반응 신호', '채팅이 몰린 위치입니다. 장면 내용은 자막이나 기존 요약과 함께 확인하세요.');
    }
    return messages;
  }
  function isTimeOnlyTitle(title, sec) {
    const raw = String(title || '').trim();
    if (!raw) return true;
    return raw === fmt(sec) || /^\d{1,2}:\d{2}(:\d{2})?$/.test(raw);
  }
  function selectedTitle(event, sec) {
    const raw = friendlyEventTitle(event);
    if (!isTimeOnlyTitle(raw, sec)) return raw;
    if (event && (event.kind === 'timeline' || event.kind === 'existing_segments')) return '요약에 포함된 장면';
    if (event && event.kind === 'highlight') return '하이라이트 추천 장면';
    const label = optionLabel(event && event.kind);
    return label.endsWith('후보') ? label : `${label} 후보`;
  }
  function selectedSummaryStatus(event, nearbyEvents) {
    const kind = String(event && event.kind || '');
    const explicitLabels = Array.isArray(event && event.status_labels) ? event.status_labels.filter(Boolean) : [];
    if (explicitLabels.length) {
      return { label: explicitLabels.join(' · '), inSummary: true, warning: false };
    }
    if (kind === 'timeline' || kind === 'existing_segments') {
      return { label: '요약에 포함됨', inSummary: true, warning: false };
    }
    if (kind === 'highlight') {
      return { label: '하이라이트 추천', inSummary: true, warning: false };
    }
    const nearSummary = nearbyEvents.some((row) => row.kind === 'timeline' || row.kind === 'existing_segments');
    if (nearSummary) return { label: '요약 근처 장면', inSummary: true, warning: false };
    const nearHighlight = nearbyEvents.some((row) => row.kind === 'highlight');
    if (nearHighlight) return { label: '하이라이트 근처', inSummary: true, warning: false };
    return { label: '요약 밖 참고 장면', inSummary: false, warning: true };
  }
  function contextLine(row, selectedEvent) {
    const scoped = canonicalSelectedSourceRow(row, selectedEvent);
    return `<div class="viewer-editor-context-line"><span class="viewer-editor-context-time">${timeLink(scoped.start_sec, scoped)}</span><span class="viewer-editor-context-text">${linkTimecodesText(scoped.text || '', scoped)}</span></div>`;
  }
  function renderContextSummary(nearbySubtitles, nearbyBuckets, selectedEvent) {
    const subtitleRows = nearbySubtitles
      .filter((row) => row && row.text)
      .slice(0, 3);
    const hiddenSubtitleRows = nearbySubtitles
      .filter((row) => row && row.text)
      .slice(3);
    const subtitleHtml = subtitleRows.length
      ? subtitleRows.map((row) => contextLine(row, selectedEvent)).join('') + (hiddenSubtitleRows.length ? `<details class="viewer-editor-context-more"><summary>자막 ${hiddenSubtitleRows.length}줄 더 보기</summary>${hiddenSubtitleRows.map((row) => contextLine(row, selectedEvent)).join('')}</details>` : '')
      : '<div class="viewer-editor-empty">전체 자막은 공개 페이지에 포함하지 않습니다.</div>';
    const chatRows = nearbyBuckets
      .filter((row) => Number(row.count || 0) > 0 || (row.samples || []).length)
      .slice(0, 2);
    const chatHtml = chatRows.length
      ? chatRows.map((row) => {
          const scoped = canonicalSelectedSourceRow(row, selectedEvent);
          const sample = (scoped.samples || []).map((item) => item.text).filter(Boolean).slice(0, 3).join(' / ');
          return `<div class="viewer-editor-context-line"><span class="viewer-editor-context-time">${timeLink(scoped.start_sec, scoped)}</span><span class="viewer-editor-context-text">채팅 ${Number(scoped.count || 0)}개${sample ? ` · ${linkTimecodesText(sample, scoped)}` : ''}</span></div>`;
        }).join('')
      : '<div class="viewer-editor-empty">근처 채팅 샘플 없음</div>';
    return `<div class="viewer-editor-context" aria-label="선택 시각 주변 방송 맥락">
      <div class="viewer-editor-context-card"><strong>자막 흐름</strong>${subtitleHtml}</div>
      <div class="viewer-editor-context-card"><strong>채팅 반응</strong>${chatHtml}</div>
    </div>`;
  }
  function clampZoom(value) {
    return Math.max(0.1, Math.min(2.6, Number(value) || 1));
  }
  function fitTimelineZoom() {
    if (!axisEl) return 0.1;
    const axisStyle = window.getComputedStyle(axisEl);
    const horizontalPadding = (parseFloat(axisStyle.paddingLeft) || 0) + (parseFloat(axisStyle.paddingRight) || 0);
    const availableWidth = Math.max(0, axisEl.clientWidth - timelineLabelColumnWidth - horizontalPadding);
    return availableWidth > 0 ? clampZoom(Math.min(1, availableWidth / baseTimelineWidth)) : 0.1;
  }
  function syncTimelineZoomControls() {
    if (zoomRange) zoomRange.value = String(Math.round(timelineZoom * 100));
    if (zoomValue) zoomValue.textContent = `${timelineZoom.toFixed(2)}x`;
  }
  function applyTimelineZoom(nextZoom, anchorClientX) {
    if (!axisEl) return;
    const canvas = axisEl.querySelector('.viewer-editor-axis-canvas');
    if (!canvas) return;
    const rect = axisEl.getBoundingClientRect();
    const oldWidth = Math.max(96, Math.round(baseTimelineWidth * timelineZoom));
    const anchorX = Number.isFinite(anchorClientX) ? anchorClientX : rect.left + rect.width / 2;
    const cursorX = Math.max(0, axisEl.scrollLeft + anchorX - rect.left - timelineLabelColumnWidth);
    const timeRatio = oldWidth ? Math.max(0, Math.min(1, cursorX / oldWidth)) : 0;
    timelineZoom = clampZoom(nextZoom);
    const nextWidth = Math.max(96, Math.round(baseTimelineWidth * timelineZoom));
    canvas.style.setProperty('--viewer-editor-w', `${nextWidth}px`);
    axisEl.scrollLeft = Math.max(0, timeRatio * nextWidth + timelineLabelColumnWidth - (anchorX - rect.left));
    syncTimelineZoomControls();
    renderAxis({ preserveScroll: true });
  }
  function scheduleTimelineAutoFit() {
    if (!axisEl || !axisRendered || !timelineAutoFit) return;
    if (timelineFitFrame) window.cancelAnimationFrame(timelineFitFrame);
    timelineFitFrame = window.requestAnimationFrame(() => {
      timelineFitFrame = window.requestAnimationFrame(() => {
        timelineFitFrame = 0;
        if (!timelineAutoFit) return;
        const fittedZoom = fitTimelineZoom();
        if (Math.abs(fittedZoom - timelineZoom) > 0.001) {
          applyTimelineZoom(fittedZoom);
        } else {
          syncTimelineZoomControls();
        }
      });
    });
  }
  function useManualTimelineZoom(nextZoom, anchorClientX) {
    timelineAutoFit = false;
    applyTimelineZoom(nextZoom, anchorClientX);
  }
  function renderFilter() {
    if (!filterEl) return;
    filterEl.innerHTML = '<option value="">전체</option>' + lanes.map((lane) => `<option value="${esc(lane.key)}">${esc(optionLabel(lane.key))}</option>`).join('');
    filterEl.addEventListener('change', renderAxis);
  }
  function renderScale() {
    const tickCount = 6;
    const ticks = [];
    for (let i = 0; i <= tickCount; i += 1) {
      const ratio = i / tickCount;
      ticks.push(`<span style="left:${ratio * 100}%">${fmt(duration * ratio)}</span>`);
    }
    return ticks.join('');
  }
  function renderWaveformRow() {
    if (!waveformSamples.length) {
      return '';
    }
    const bars = waveformSamples.map((row) => {
      const start = Math.max(0, Number(row.start_sec || 0));
      const end = Math.max(start, Number(row.end_sec || start));
      const value = Math.max(0, Math.min(1, Number(row.value || 0)));
      const center = start + ((end - start) / 2);
      const x = Math.max(0, Math.min(100, (center / duration) * 100));
      const amplitude = 2.2 + value * 17.2;
      const hot = value >= 0.78;
      return `<line class="viewer-editor-waveform-bar ${hot ? 'hot' : ''}" x1="${x.toFixed(3)}" y1="${(20 - amplitude).toFixed(3)}" x2="${x.toFixed(3)}" y2="${(20 + amplitude).toFixed(3)}"><title>${fmt(start)} 소리 파형 ${(value * 100).toFixed(0)}%</title></line>`;
    }).join('');
    return `<div class="viewer-editor-waveform-row" data-waveform-row="true"><div class="viewer-editor-waveform-label"><strong>소리 파형</strong><span>진폭 샘플 · ${waveformSamples.length}개</span></div><div class="viewer-editor-waveform" aria-label="실제 오디오 샘플 기반 소리 파형"><svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><line class="viewer-editor-waveform-midline" x1="0" y1="20" x2="100" y2="20"></line>${bars}</svg></div></div>`;
  }
  function chatBucketCount(row) {
    if (!row || row.count == null || row.count === '') return { count: 0, missing: true };
    const value = Number(row.count);
    if (!Number.isFinite(value)) return { count: 0, missing: true };
    return { count: Math.max(0, value), missing: false };
  }
  function bucketOverlapsRange(row, range) {
    const start = Math.max(0, Number(row.start_sec || 0));
    const end = Math.max(start, Number(row.end_sec || start + 30));
    return end >= range.startSec && start <= range.endSec;
  }
  function currentVisibleTimeRange(timelineWidth) {
    const viewportWidth = axisEl ? axisEl.clientWidth : timelineWidth;
    const startPx = Math.max(0, (axisEl ? axisEl.scrollLeft : 0) - timelineLabelColumnWidth);
    const endPx = Math.min(timelineWidth, startPx + Math.max(1, viewportWidth - timelineLabelColumnWidth));
    if (endPx <= startPx || viewportWidth >= timelineWidth + timelineLabelColumnWidth) {
      return { startSec: 0, endSec: duration };
    }
    return {
      startSec: Math.max(0, Math.min(duration, (startPx / timelineWidth) * duration)),
      endSec: Math.max(0, Math.min(duration, (endPx / timelineWidth) * duration)),
    };
  }
  function maxChatCount(rows) {
    return Math.max(1, ...rows.map((row) => chatBucketCount(row).count));
  }
  function densityBucketClass(chatCount, missing, visibleMaxChat, hot) {
    if (missing) return 'missing';
    if (chatCount <= 0) return 'zero';
    const low = chatCount < Math.max(2, visibleMaxChat * 0.12);
    return `${hot ? 'hot' : ''}${low ? ' low' : ''}`.trim();
  }
  function scheduleDensityScaleRefresh() {
    if (densityScrollFrame) return;
    densityScrollFrame = window.requestAnimationFrame(() => {
      densityScrollFrame = 0;
      renderAxis({ preserveScroll: true });
    });
  }
  function markerDensityLabel() {
    return ['전체', '거의 전체', '많음', '넓게', '균형+', '균형', '핵심', '핵심+', '정예', '최정예', '최소'][markerDensityLevel] || '핵심';
  }
  function markerDensityRatio() {
    return [1, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.22, 0.15, 0.1][markerDensityLevel] || 0.4;
  }
  function protectedDensityKind(kind) {
    return ['timeline', 'highlight', 'chapter', 'viewer_clip'].includes(String(kind || ''));
  }
  function highPriorityDensityKind(kind) {
    return ['timeline', 'existing_segments', 'highlight', 'chapter', 'viewer_clip'].includes(String(kind || ''));
  }
  function markerPriorityKindFamily(kind) {
    const raw = String(kind || '').toLowerCase();
    if (raw === 'timeline' || raw === 'existing_segments' || raw === 'highlight' || raw === 'chapter') return 'summary';
    if (raw === 'viewer_clip') return 'viewer_clip';
    if (raw.includes('chat') || raw.includes('comment')) return 'chat';
    if (raw.includes('audio') || raw.includes('voice')) return 'audio';
    if (raw.includes('subtitle') || raw.includes('asr')) return 'subtitle';
    if (raw.includes('visual') || raw.includes('scene')) return 'visual';
    if (raw.includes('live')) return 'live';
    return raw || 'other';
  }
  function markerPriorityTextFamily(text) {
    const raw = String(text || '').toLowerCase();
    if (!raw) return '';
    if (raw.includes('summary') || raw.includes('timeline') || raw.includes('highlight') || raw.includes('chapter') || raw.includes('요약')) return 'summary';
    if (raw.includes('viewer_clip') || raw.includes('clip') || raw.includes('클립')) return 'viewer_clip';
    if (raw.includes('chat') || raw.includes('comment') || raw.includes('채팅') || raw.includes('댓글')) return 'chat';
    if (raw.includes('audio') || raw.includes('voice') || raw.includes('오디오') || raw.includes('소리')) return 'audio';
    if (raw.includes('subtitle') || raw.includes('asr') || raw.includes('자막')) return 'subtitle';
    if (raw.includes('visual') || raw.includes('scene') || raw.includes('화면')) return 'visual';
    if (raw.includes('live')) return 'live';
    return '';
  }
  function markerPriorityFamilies(event) {
    const families = new Set([markerPriorityKindFamily(event && event.kind)]);
    const addText = (value) => {
      const family = markerPriorityTextFamily(value);
      if (family) families.add(family);
    };
    (event && Array.isArray(event.source_signals) ? event.source_signals : []).forEach(addText);
    (event && Array.isArray(event.signals) ? event.signals : []).forEach(addText);
    (event && Array.isArray(event.evidence) ? event.evidence : [])
      .concat(event && Array.isArray(event.guidance) ? event.guidance : [])
      .forEach((row) => {
        addText(row && (row.kind || row.type || row.source_type || row.label || row.title || row.text));
      });
    return Array.from(families).filter(Boolean);
  }
  function markerPrioritySec(event, fallbackIndex) {
    const value = event && event.start_sec != null ? Number(event.start_sec) : Number(fallbackIndex || 0);
    return Number.isFinite(value) ? value : Number(fallbackIndex || 0);
  }
  function markerPriorityNearbyFamilies(event, contextEvents, radiusSec = 12) {
    const sec = markerPrioritySec(event, 0);
    const families = new Set(markerPriorityFamilies(event));
    (Array.isArray(contextEvents) ? contextEvents : []).forEach((candidate) => {
      const otherSec = markerPrioritySec(candidate, NaN);
      if (!Number.isFinite(otherSec) || Math.abs(otherSec - sec) > radiusSec) return;
      markerPriorityFamilies(candidate).forEach((family) => families.add(family));
    });
    return families;
  }
  function markerPriorityNearbyKind(contextEvents, event, kinds, radiusSec = 12) {
    const sec = markerPrioritySec(event, 0);
    return (Array.isArray(contextEvents) ? contextEvents : []).some((candidate) => {
      const kind = String(candidate && candidate.kind || '');
      if (!kinds.includes(kind)) return false;
      const otherSec = markerPrioritySec(candidate, NaN);
      return Number.isFinite(otherSec) && Math.abs(otherSec - sec) <= radiusSec;
    });
  }
  function markerPriorityEngagement(event) {
    const fields = ['play_count', 'like_count', 'read_count', 'reaction_count', 'chat_count', 'message_count', 'count'];
    const total = fields.reduce((sum, key) => sum + Math.max(0, Number(event && event[key] || 0)), 0);
    return total > 0 ? Math.min(14, Math.log10(total + 1) * 4) : 0;
  }
  function markerDisplayPriority(kind, event, index, laneEvents, contextEvents) {
    let score = 0;
    const familyCount = markerPriorityNearbyFamilies(event, contextEvents).size;
    const rowFamilies = markerPriorityFamilies(event).length;
    if (highPriorityDensityKind(kind)) score += kind === 'viewer_clip' ? 70 : 82;
    if (markerPriorityNearbyKind(contextEvents, event, ['timeline', 'existing_segments', 'highlight', 'chapter'], 15)) score += 48;
    if (markerPriorityNearbyKind(contextEvents, event, ['viewer_clip'], 15)) score += 34;
    if (familyCount >= 4) score += 54;
    else if (familyCount === 3) score += 40;
    else if (familyCount === 2) score += 18;
    if (rowFamilies >= 3) score += 18;
    else if (rowFamilies === 2) score += 8;
    const evidenceCount = (Array.isArray(event && event.evidence) ? event.evidence.length : 0)
      + (Array.isArray(event && event.guidance) ? event.guidance.length : 0);
    score += Math.min(12, evidenceCount * 3);
    score += markerPriorityEngagement(event);
    if (event && (event.video_no || event.source_video_no || event.seek_sec != null || event.vod_label)) score += 4;
    return {
      score,
      startSec: markerPrioritySec(event, index),
      index,
      id: String(event && (event.id || event.event_id) || `${kind}_${index}`),
    };
  }
  function laneEventsForDensity(kind, laneEvents, contextEvents) {
    const rows = Array.isArray(laneEvents) ? laneEvents : [];
    if (markerDensityLevel <= 0 || protectedDensityKind(kind) || rows.length <= 2) return rows;
    const target = Math.max(1, Math.ceil(rows.length * markerDensityRatio()));
    if (target >= rows.length) return rows;
    const ranked = rows
      .map((row, index) => ({ row, priority: markerDisplayPriority(kind, row, index, rows, contextEvents || rows) }))
      .sort((a, b) => {
        if (b.priority.score !== a.priority.score) return b.priority.score - a.priority.score;
        if (a.priority.startSec !== b.priority.startSec) return a.priority.startSec - b.priority.startSec;
        if (a.priority.index !== b.priority.index) return a.priority.index - b.priority.index;
        return a.priority.id.localeCompare(b.priority.id);
      });
    const selected = new Set();
    if (target === 1) {
      selected.add(ranked[0].priority.index);
    } else {
      const coverageTarget = Math.min(target, Math.max(2, Math.ceil(target / 2)));
      for (let slot = 0; slot < coverageTarget; slot += 1) {
        selected.add(Math.round(slot * (rows.length - 1) / (coverageTarget - 1)));
      }
      for (const item of ranked) {
        if (selected.size >= target) break;
        selected.add(item.priority.index);
      }
    }
    return rows.filter((_row, index) => selected.has(index));
  }
  function eventsForDensity(eventList) {
    const byKind = new Map();
    (eventList || []).forEach((event) => {
      const kind = String(event && event.kind || '');
      if (!byKind.has(kind)) byKind.set(kind, []);
      byKind.get(kind).push(event);
    });
    return Array.from(byKind.entries()).flatMap(([kind, rows]) => laneEventsForDensity(kind, rows, eventList));
  }
  function viewerClipClusterKey(event) {
    const sec = Math.round(Number(event && event.start_sec || 0));
    return `viewer_clip_${sec}`;
  }
  function clipClusterEvents(event) {
    if (event && Array.isArray(event.clip_cluster_events)) return event.clip_cluster_events;
    return event ? [event] : [];
  }
  function clusterLaneEvents(kind, laneEvents) {
    const rows = Array.isArray(laneEvents) ? laneEvents : [];
    if (kind !== 'viewer_clip' || rows.length <= 1) return rows;
    const byStart = new Map();
    rows.forEach((event) => {
      const key = viewerClipClusterKey(event);
      if (!byStart.has(key)) byStart.set(key, []);
      byStart.get(key).push(event);
    });
    return Array.from(byStart.entries()).flatMap(([key, cluster]) => {
      if (cluster.length <= 1) return cluster;
      const first = cluster[0];
      return [Object.assign({}, first, {
        id: `cluster_${key}`,
        title: `시청자 클립 ${cluster.length}개`,
        evidence: [],
        clip_cluster_count: cluster.length,
        clip_cluster_events: cluster
      })];
    });
  }
  function clipRowsForCluster(clusterEvents) {
    const rows = [];
    (clusterEvents || []).forEach((clip, index) => {
      const link = viewerClipLinkInfo([clip], clip);
      const fallbackTitle = friendlyEventTitle(clip) || `시청자 클립 ${index + 1}`;
      rows.push({
        label: `시청자 클립 ${index + 1}`,
        time: fmt(Number(clip.start_sec || 0)),
        sec: clip.start_sec,
        start_sec: clip.start_sec,
        seek_sec: clip.seek_sec,
        video_no: clip.video_no,
        source_video_no: clip.source_video_no,
        vod_label: clip.vod_label,
        timecode: clip.timecode,
        text: link.title || fallbackTitle,
        clipUrl: link.url
      });
    });
    return rows;
  }
  function clusterEventIdSet(clusterEvents) {
    return new Set((clusterEvents || []).map((clip) => String(clip && clip.id || '')).filter(Boolean));
  }
  function editPointClusterId(cluster) {
    return String(cluster && cluster.cluster_id || '');
  }
  function editPointClusterEvents(cluster) {
    const ids = Array.isArray(cluster && cluster.event_ids) ? cluster.event_ids : [];
    const representativeRows = (Array.isArray(cluster && cluster.representative_events) ? cluster.representative_events : [])
      .map((row) => eventsById.get(String(row && (row.event_id || row.id) || '')) || row)
      .filter((row) => row && typeof row === 'object');
    const rows = [];
    const seen = new Set();
    ids.forEach((id, index) => {
      const key = String(id || '');
      const resolved = key ? eventsById.get(key) : null;
      const fallback = representativeRows[index] || null;
      const row = resolved || fallback;
      if (!row) return;
      const rowKey = String(row.id || row.event_id || key || `${row.kind || 'event'}_${row.start_sec || index}_${index}`);
      if (seen.has(rowKey)) return;
      seen.add(rowKey);
      rows.push(row);
    });
    representativeRows.forEach((row, index) => {
      const rowKey = String(row.id || row.event_id || `${row.kind || 'event'}_${row.start_sec || index}_${index}`);
      if (seen.has(rowKey)) return;
      seen.add(rowKey);
      rows.push(row);
    });
    return rows;
  }
  function renderableEditPointClusters() {
    const clusters = editPointClusters
      .filter((cluster) => editPointClusterId(cluster) && editPointClusterEvents(cluster).length)
      .sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0));
    return clusters;
  }
  function clusterConfidenceLabel(value) {
    const raw = String(value || '').toLowerCase();
    if (raw === 'high') return '높음';
    if (raw === 'medium') return '중간';
    if (raw === 'low') return '낮음';
    return '검토용';
  }
  function clusterFamilyText(cluster) {
    return clusterFamilyKeys(cluster).map((kind) => optionLabel(kind)).filter(Boolean).slice(0, 4).join(' · ') || '편집 후보';
  }
  function clusterFamilyKeys(cluster) {
    return (Array.isArray(cluster && cluster.signal_families) ? cluster.signal_families : [])
      .map((kind) => String(kind || '').trim())
      .filter(Boolean);
  }
  function editPointClusterEvent(clusterId) {
    const cluster = renderableEditPointClusters().find((row) => editPointClusterId(row) === clusterId);
    if (!cluster) return null;
    const rows = editPointClusterEvents(cluster);
    const sourceRow = rows.find((row) => row && (row.video_no || row.source_video_no || row.seek_sec != null || row.vod_label)) || rows[0] || {};
    const start = Math.max(0, Number(cluster.start_sec || (rows[0] && rows[0].start_sec) || 0));
    const event = {
      id: `edit_point_cluster_${clusterId}`,
      kind: 'edit_point_cluster',
      kind_label: '먼저 볼 구간',
      start_sec: start,
      end_sec: Math.max(start, Number(cluster.end_sec || start)),
      title: `${fmt(start)} 편집 후보 묶음`,
      cluster,
      cluster_events: rows,
    };
    ['video_no', 'source_video_no', 'vod_label', 'seek_sec', 'end_seek_sec', 'timecode'].forEach((key) => {
      if (sourceRow && sourceRow[key] != null) event[key] = sourceRow[key];
    });
    return event;
  }
  function clusterHeatScore(cluster) {
    const signalCount = Math.max(0, Number(cluster && cluster.signal_count || 0));
    const familyCount = Math.max(0, Number(cluster && cluster.family_count || 0));
    const viewerClipCount = Math.max(0, Number(cluster && cluster.viewer_clip_count || 0));
    const confidence = String(cluster && cluster.confidence || 'low').toLowerCase();
    const confidenceWeight = confidence === 'high' ? 2.2 : confidence === 'medium' ? 1.1 : 0.25;
    return signalCount + (familyCount * 1.35) + (viewerClipCount * 1.15) + confidenceWeight;
  }
  const HEATMAP_PROFILE_SAMPLE_COUNT = 120;
  const MAX_PROMINENT_HEAT_ZONES = 12;
  const MIN_PROMINENT_HEAT_ZONES = 5;
  function clusterHeatClass(cluster, score, maxScore) {
    const signalCount = Math.max(0, Number(cluster && cluster.signal_count || 0));
    const familyCount = Math.max(0, Number(cluster && cluster.family_count || 0));
    if (signalCount <= 1 && familyCount <= 1) return 'low';
    if (score >= Math.max(3, maxScore * 0.66)) return 'hot';
    if (score >= Math.max(2, maxScore * 0.34)) return 'medium';
    return 'low';
  }
  function clusterTimeBounds(cluster) {
    const start = Math.max(0, Number(cluster && cluster.start_sec || 0));
    const rawEnd = Math.max(start + 18, Number(cluster && cluster.end_sec || start + 45));
    return { start, end: Math.min(duration, rawEnd), center: Math.min(duration, Math.max(0, (start + rawEnd) / 2)) };
  }
  function heatmapProfileSamples(scored) {
    const sampleCount = HEATMAP_PROFILE_SAMPLE_COUNT;
    const smoothingSec = Math.max(120, Math.min(420, duration * 0.018));
    const samples = Array.from({ length: sampleCount }, (_, index) => {
      const sec = duration * (index / Math.max(1, sampleCount - 1));
      let value = 0;
      scored.forEach((row) => {
        const bounds = row.bounds || clusterTimeBounds(row.cluster);
        const halfWidth = Math.max(45, (bounds.end - bounds.start) / 2);
        const radius = smoothingSec + halfWidth;
        const distance = Math.abs(sec - bounds.center);
        if (distance > radius) return;
        const influence = 1 - (distance / radius);
        value += row.score * influence * influence;
      });
      return { sec, value };
    });
    const maxValue = Math.max(1, ...samples.map((sample) => sample.value));
    return samples.map((sample) => ({ sec: sample.sec, value: sample.value, normalized: sample.value / maxValue }));
  }
  function heatmapAreaPath(samples) {
    if (!samples.length) return '';
    const points = samples.map((sample, index) => {
      const x = (index / Math.max(1, samples.length - 1)) * 1000;
      const y = 42 - (Math.max(0, Math.min(1, sample.normalized)) * 34);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return `M0,44 L${points.join(' L')} L1000,44 Z`;
  }
  function heatmapLinePath(samples) {
    if (!samples.length) return '';
    return samples.map((sample, index) => {
      const x = (index / Math.max(1, samples.length - 1)) * 1000;
      const y = 42 - (Math.max(0, Math.min(1, sample.normalized)) * 34);
      return `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
  }
  function heatmapVisualZones(scored, maxScore) {
    const baseZoneRadiusSec = Math.max(140, Math.min(420, duration * 0.014));
    const minDistanceSec = Math.max(180, Math.min(540, duration * 0.018));
    const selected = [];
    const candidates = scored
      .map((row) => ({ ...row, bounds: row.bounds || clusterTimeBounds(row.cluster) }))
      .sort((a, b) => b.score - a.score);
    candidates.forEach((candidate) => {
      if (selected.length >= MAX_PROMINENT_HEAT_ZONES) return;
      const tooClose = selected.some((zone) => Math.abs(zone.center - candidate.bounds.center) < minDistanceSec);
      const heatClass = clusterHeatClass(candidate.cluster, candidate.score, maxScore);
      if (tooClose || (heatClass === 'low' && selected.length >= MIN_PROMINENT_HEAT_ZONES)) return;
      const strengthRatio = Math.max(0.24, Math.min(1, candidate.score / Math.max(1, maxScore)));
      const zoneRadiusSec = baseZoneRadiusSec * (0.68 + (strengthRatio * 0.72));
      const zoneStart = Math.max(0, candidate.bounds.center - zoneRadiusSec);
      const zoneEnd = Math.min(duration, candidate.bounds.center + zoneRadiusSec);
      const related = candidates.filter((row) => {
        const centerDistance = Math.abs(row.bounds.center - candidate.bounds.center);
        return centerDistance <= zoneRadiusSec || (row.bounds.start <= zoneEnd && row.bounds.end >= zoneStart);
      });
      const familyKeys = Array.from(new Set(related.flatMap((row) => clusterFamilyKeys(row.cluster)))).filter(Boolean);
      const relatedSignalCount = related.reduce((sum, row) => sum + Math.max(0, Number(row.cluster && row.cluster.signal_count || 0)), 0);
      selected.push({
        cluster: candidate.cluster,
        index: candidate.index,
        score: candidate.score,
        heatClass,
        center: candidate.bounds.center,
        start: zoneStart,
        end: zoneEnd,
        familyKeys,
        relatedCount: related.length,
        relatedSignalCount,
      });
    });
    return selected.sort((a, b) => a.start - b.start);
  }
  function renderHeatmapOverviewRow() {
    const clusters = renderableEditPointClusters();
    if (!clusters.length) {
      return '';
    }
    const scored = clusters.map((cluster, index) => ({ cluster, index, score: clusterHeatScore(cluster), bounds: clusterTimeBounds(cluster) }));
    const maxScore = Math.max(1, ...scored.map((row) => row.score));
    const profileSamples = heatmapProfileSamples(scored);
    const zones = heatmapVisualZones(scored, maxScore);
    const areaPath = heatmapAreaPath(profileSamples);
    const linePath = heatmapLinePath(profileSamples);
    const zoneButtons = zones.map((zone, index) => {
      const { cluster, score } = zone;
      const clusterId = editPointClusterId(cluster);
      const start = Math.max(0, zone.start);
      const end = Math.min(duration, zone.end);
      const left = Math.max(0, Math.min(100, (start / duration) * 100));
      const width = Math.max(2.2, Math.min(100 - left, ((end - start) / duration) * 100));
      const signalCount = Math.max(0, Number(cluster.signal_count || 0));
      const familyCount = Math.max(0, Number(cluster.family_count || 0));
      const viewerClipCount = Math.max(0, Number(cluster.viewer_clip_count || 0));
      const familyKeys = zone.familyKeys.length ? zone.familyKeys : clusterFamilyKeys(cluster);
      const heatClass = zone.heatClass;
      const title = `${fmt(zone.center)} ${clusterFamilyText(cluster)} 열 구간 · 묶음 ${zone.relatedCount}개 · 신호 ${zone.relatedSignalCount || signalCount}개 · 자료 ${familyKeys.length || familyCount}종 · 클립 ${viewerClipCount}개`;
      const selected = selectedEventId === `edit_point_cluster_${clusterId}`;
      return `<button type="button" class="viewer-editor-heatmap-zone ${heatClass}${selected ? ' selected' : ''}" data-real-heatmap-zone="true" data-heatmap-prominent-zone="true" data-heatmap-zone-visual="hit-target" data-edit-point-cluster-id="${esc(clusterId)}" data-overview-rank="${index + 1}" data-overview-strength="${esc(heatClass)}" data-cluster-signal-count="${signalCount}" data-cluster-family-count="${familyCount}" data-cluster-viewer-clip-count="${viewerClipCount}" data-cluster-signal-families="${esc(familyKeys.join(' '))}" data-heatmap-zone-related-count="${zone.relatedCount}" data-sec="${Math.round(zone.center)}" aria-label="${esc(`${fmt(zone.center)} 먼저 볼 열 구간. ${title}. 클릭하면 근거 펼침`)}" title="${esc(`${title} · 클릭하면 근거 펼침`)}" style="left:${left}%;width:${width}%"></button>`;
    }).join('');
    const surface = `<svg class="viewer-editor-heatmap-surface" viewBox="0 0 1000 44" preserveAspectRatio="none" aria-hidden="true" data-real-heatmap-surface="true" data-heatmap-area="true" data-heatmap-profile-samples="${profileSamples.length}"><defs><linearGradient id="viewerEditorHeatmapGradient" x1="0" y1="1" x2="0" y2="0"><stop offset="0%" stop-color="rgba(122,162,247,0.10)"/><stop offset="58%" stop-color="rgba(224,175,104,0.36)"/><stop offset="100%" stop-color="rgba(247,118,142,0.70)"/></linearGradient></defs><path class="heatmap-fill" d="${areaPath}"></path><path class="heatmap-line" d="${linePath}"></path></svg>`;
    const legend = '<span class="low">약함</span><span class="medium">중간</span><span class="hot">강함</span>';
    return `<div class="viewer-editor-density-row" data-real-heatmap-row="true" data-heatmap-zone-count="${zones.length}" data-heatmap-background-density="true"><div class="viewer-editor-density-label"><strong>먼저 볼 구간</strong><span>겹친 신호는 봉우리로, 약한 신호는 배경 밀도로 표시</span><div class="viewer-editor-heatmap-legend" aria-label="열지도 강도 범례">${legend}</div></div><div class="viewer-editor-density viewer-editor-heatmap-track" aria-label="전체 방송 편집 후보 열지도">${surface}${zoneButtons}</div></div>`;
  }
  function renderAxis(options = {}) {
    closeActiveAxisDisclosure();
    const filter = filterEl ? filterEl.value : '';
    const defaultHiddenLaneKinds = new Set(['existing_segments', 'highlight']);
    const visibleEvents = filter
      ? events.filter((event) => event.kind === filter)
      : events.filter((event) => !defaultHiddenLaneKinds.has(String(event.kind || '')));
    const visibleSceneEvents = eventsForDensity(visibleEvents);
    renderedAxisEvents = new Map();
    const preservedScrollLeft = options.preserveScroll ? axisEl.scrollLeft : null;
    const timelineWidth = Math.max(96, Math.round(baseTimelineWidth * timelineZoom));
    const visibleRange = currentVisibleTimeRange(timelineWidth);
    const visibleBuckets = buckets.filter((row) => bucketOverlapsRange(row, visibleRange));
    const globalMaxChat = maxChatCount(buckets);
    const visibleMaxChat = maxChatCount(visibleBuckets.length ? visibleBuckets : buckets);
    const densityScaleLabel = buckets.length
      ? `현재 화면 최대 ${visibleMaxChat}개 / 전체 방송 최대 ${globalMaxChat}개 · ${buckets.length}개`
      : '이 신호 없음';
    const densityBars = buckets.map((row) => {
      const start = Number(row.start_sec || 0);
      const end = Number(row.end_sec || start + 30);
      const left = Math.max(0, Math.min(100, (start / duration) * 100));
      const width = Math.max(0.25, Math.min(100 - left, ((end - start) / duration) * 100));
      const countInfo = chatBucketCount(row);
      const chatCount = countInfo.count;
      const h = chatCount > 0 ? Math.max(5, Math.round((chatCount / visibleMaxChat) * 30)) : 0;
      const globalH = chatCount > 0 ? Math.max(1, Math.round((chatCount / globalMaxChat) * 30)) : 0;
      const hot = Number(row.reaction_count || 0) > 0;
      const stateClass = densityBucketClass(chatCount, countInfo.missing, visibleMaxChat, hot);
      const stateText = countInfo.missing ? '채팅 수 미확인' : chatCount > 0 ? `채팅 ${chatCount}개` : '채팅 0개';
      const scaleText = `현재 화면 기준 최대 ${visibleMaxChat}개, 전체 방송 기준 최대 ${globalMaxChat}개`;
      return `<button type="button" class="viewer-editor-density-bar ${stateClass}" data-sec="${start}" aria-label="${esc(`${fmt(start)} ${stateText}. ${scaleText}`)}" title="${esc(`${fmt(start)} ${stateText} · ${scaleText}`)}" style="left:${left}%;width:${width}%"><span style="--bar-h:${h}px;--global-h:${globalH}px"></span></button>`;
    }).join('');
    const eventLaneKeys = new Set(events.map((event) => String(event && (event.kind || event.lane) || '')).filter(Boolean));
    const signalTrackDefinitions = lanes
      .filter((lane) => {
        const key = String(lane && lane.key || '');
        if (!filter && defaultHiddenLaneKinds.has(key)) return false;
        if (filter && defaultHiddenLaneKinds.has(key) && key !== filter) return false;
        return eventLaneKeys.has(key);
      })
      .map((lane) => {
        const key = String(lane && lane.key || '');
        return {
          key,
          label: String(viewerLabelsMap[key] || labelsMap[key] || lane.label || key),
          eventKinds: [key],
        };
      });
    const configuredSignalTrackKeys = new Set(signalTrackDefinitions.map((definition) => definition.key));
    Array.from(eventLaneKeys).forEach((key) => {
      if (defaultHiddenLaneKinds.has(key) && key !== filter) return;
      if (configuredSignalTrackKeys.has(key) || key.startsWith('outline_')) return;
      signalTrackDefinitions.push({key, label:String(viewerLabelsMap[key]||labelsMap[key]||key), eventKinds:[key]});
      configuredSignalTrackKeys.add(key);
    });
    function signalStateText(definition, matchingLanes, eventCount) {
      const statuses = matchingLanes.map((lane) => String(lane.status || '').toLowerCase());
      if (statuses.some((status) => status === 'loading')) return '불러오는 중';
      if (statuses.some((status) => status === 'error' || status === 'failed')) return '불러오지 못했습니다 · 다시 시도';
      if (eventCount > 0) return `${eventCount}개`;
      if (matchingLanes.length) return '이 신호 없음';
      return '이번 방송에서는 사용되지 않음';
    }
    function viewerClipSourceSummary() {
      if (!viewerClipSource) return null;
      const orderType = String(viewerClipSource.order_type || 'unknown').toUpperCase();
      const status = String(viewerClipSource.status || 'unknown').toLowerCase();
      const statusText = ({
        partial: '일부 응답',
        present: '확인됨',
        present_zero: '응답 완료 · 후보 없음',
        missing: '자료 없음',
        invalid: '날짜 정보 오류',
        excluded: 'same-VOD offset 제외',
      })[status] || '상태 확인 필요';
      const verifiedCount = Math.max(0, Number(viewerClipSource.verified_offset_count || 0));
      const engagementText = viewerClipSource.engagement_available ? '반응 수치 있음' : '반응 수치 없음';
      const coverage = viewerClipSource.temporal_coverage && typeof viewerClipSource.temporal_coverage === 'object'
        ? viewerClipSource.temporal_coverage
        : {};
      const bucketCount = Math.max(0, Number(coverage.bucket_count || 0));
      const nonemptyBucketCount = Math.max(0, Number(coverage.nonempty_bucket_count || 0));
      const coverageText = bucketCount > 0
        ? `시간 분산 ${nonemptyBucketCount}/${bucketCount}`
        : '시간 분산 미확인';
      const label = ({
        partial: `${orderType} 일부 · 검증 ${verifiedCount}개`,
        present: `${orderType} · 검증 ${verifiedCount}개`,
        present_zero: `${orderType} · 확인된 클립 없음`,
        missing: '출처 자료 없음',
        invalid: '출처 날짜 확인 필요',
        excluded: '대상 VOD 구간 없음',
      })[status] || '출처 상태 확인 필요';
      return {
        label,
        title: `${orderType} · ${statusText} · same-VOD offset ${verifiedCount}개 · ${coverageText} · ${engagementText}`,
      };
    }
    function preserveDensityCollisionCandidates(allEvents, selectedEvents) {
      const selectedIds = new Set((selectedEvents || []).map((event) => String(event && event.id || '')));
      const projected = (allEvents || []).map((event, index) => {
        const start = Math.max(0, Number(event && event.start_sec || 0));
        const end = Number(event && event.end_sec);
        const left = (start / duration) * timelineWidth;
        const rangeWidth = Number.isFinite(end) && end > start + 1 ? ((end - start) / duration) * timelineWidth : 0;
        const width = Math.max(14, rangeWidth);
        return {marker:event, left, right:left + width, width, index};
      }).sort((a, b) => a.left - b.left || a.index - b.index);
      fixedRepresentativeCollisionGroups(projected, 3)
        .filter((group) => group.items.length > 1)
        .forEach((group) => group.items.forEach((item) => selectedIds.add(String(item.marker && item.marker.id || ''))));
      return (allEvents || []).filter((event) => selectedIds.has(String(event && event.id || '')));
    }
    const renderSignalTrackRows = (definitions) => definitions.map((definition) => {
      const kind = definition.key;
      if (filter && !definition.eventKinds.includes(filter)) return '';
      const matchingLanes = lanes.filter((lane) => definition.eventKinds.includes(String(lane.key || '')));
      const laneAllEvents = visibleEvents.filter((event) => definition.eventKinds.includes(String(event.kind || '')));
      const densityEvents = laneEventsForDensity(kind, laneAllEvents, visibleEvents);
      const laneEvents = clusterLaneEvents(kind, densityEvents);
      const markers = laneEvents.map((event) => {
        const start = Math.max(0, Number(event.start_sec || 0));
        const end = event.end_sec != null ? Number(event.end_sec) : NaN;
        const left = Math.max(0, Math.min(100, (start / duration) * 100));
        const hasRange = Number.isFinite(end) && end > start + 1;
        const width = hasRange ? Math.max(0.75, Math.min(100 - left, ((end - start) / duration) * 100)) : 0;
        const colorStyle = `--viewer-marker-color:${markerColor(kind)}`;
        const style = hasRange ? `left:${left}%;width:${width}%;${colorStyle}` : `left:${left}%;${colorStyle}`;
        const clusterCount = Number(event.clip_cluster_count || 0);
        const clusterAttrs = clusterCount > 1 ? ` data-cluster-count="${clusterCount}" aria-label="${esc(`${fmt(start)} 시청자 클립 ${clusterCount}개 묶음`)}"` : '';
        const clusterClass = clusterCount > 1 ? ' clip-cluster viewer-editor-cluster-grain' : '';
        renderedAxisEvents.set(String(event.id || ''), event);
        return `<button type="button" class="viewer-editor-marker ${hasRange ? 'range' : ''}${clusterClass}" data-kind="${esc(kind)}" data-event-id="${esc(event.id)}"${clusterAttrs} title="${esc(`${fmt(start)} ${friendlyEventTitle(event) || ''}`)}" style="${style}"></button>`;
      }).join('');
      const stateText = signalStateText(definition, matchingLanes, laneEvents.length);
      const hiddenCount = Math.max(0, laneAllEvents.length - densityEvents.length);
      const clusterCount = kind === 'viewer_clip' ? laneEvents.length : 0;
      const laneCountText = laneAllEvents.length
        ? hiddenCount
          ? `${densityEvents.length}/${laneAllEvents.length}개 표시`
          : kind === 'viewer_clip' && clusterCount !== laneAllEvents.length
            ? `${clusterCount}묶음 · ${laneAllEvents.length}개`
            : `${laneEvents.length}개`
        : stateText;
      const trackAttribute = kind === 'existing_segments'
        ? `data-summary-track="${esc(kind)}"`
        : `data-signal-track="${esc(kind)}"`;
      const sourceSummary = kind === 'viewer_clip' ? viewerClipSourceSummary() : null;
      const sourceSummaryHtml = sourceSummary
        ? `<span class="viewer-editor-lane-source" title="${esc(sourceSummary.title)}">${esc(sourceSummary.label)}</span>`
        : '';
      return `<div class="viewer-editor-lane-row" ${trackAttribute}><div class="viewer-editor-lane-label"><strong>${esc(definition.label)}</strong><span>${esc(laneCountText)}</span>${sourceSummaryHtml}</div><div class="viewer-editor-track">${markers || `<div class="viewer-editor-empty">${esc(stateText)}</div>`}</div></div>`;
    }).join('');
    const summaryLaneRows = renderSignalTrackRows(signalTrackDefinitions.filter((definition) => definition.key === 'existing_segments'));
    const laneRows = renderSignalTrackRows(signalTrackDefinitions.filter((definition) => definition.key !== 'existing_segments'));
    const projectedHighlights = outlineProjection.filter((event) => String(event.level || '') === 'Highlight');
    const outlineGroups = [
      { level: 'D1', key: 'flow', label: '방송 흐름', color: '#7aa2f7' },
      { level: 'D2', key: 'subject', label: '세부 흐름', color: '#9ece6a' },
      { level: 'Point', key: 'scene', label: '주요 장면', color: '#f7768e' },
      { level: 'Highlight', key: 'inspect', label: 'Highlight', color: '#e0af68', events: projectedHighlights },
    ];
    const outlineRows = outlineGroups.map((group) => {
      const groupLabel = String(group.label || group.title || '구조');
      const groupedEvents = group.events
        ? group.events
        : outlineProjection.filter((event) => String(event.level || '') === group.level);
      if (!groupedEvents.length) return '';
      const markerEvents = groupedEvents.flatMap((event, highlightIndex) => {
        const spans = group.level === 'Highlight' && Array.isArray(event.source_spans)
          ? event.source_spans.filter((span) => Number(span && span.end_sec) >= Number(span && span.start_sec))
          : [];
        if (!spans.length) return [{...event, __highlight_index: highlightIndex}];
        return spans.map((span, spanIndex) => ({
          ...event,
          highlight_id: String(event.highlight_id || event.id || ''),
          start_sec: Number(span.start_sec),
          end_sec: Number(span.end_sec),
          source_span_index: spanIndex,
          source_span_count: spans.length,
          source_span_reason: String(span.reason || ''),
          source_span_exclusion_before: span.exclusion_before && typeof span.exclusion_before === 'object'
            ? span.exclusion_before
            : null,
          __highlight_index: highlightIndex,
        }));
      });
      const markers = markerEvents.map((event, index) => {
        const start = Math.max(0, Number(event.start_sec || 0));
        const end = event.end_sec != null ? Number(event.end_sec) : NaN;
        const left = Math.max(0, Math.min(100, (start / duration) * 100));
        const hasRange = Number.isFinite(end) && end > start + 1;
        const width = hasRange ? Math.max(0.25, Math.min(100 - left, ((end - start) / duration) * 100)) : 0;
        const spanSuffix = Number.isInteger(event.source_span_index) ? `-span-${event.source_span_index}` : '';
        const id = `outline-${String(event.kind || group.level)}-${String(event.id || start)}${spanSuffix}`;
        const markerTitle = String(event.title || '');
        const statusLabels = Array.isArray(event.status_labels) ? event.status_labels.filter(Boolean) : [];
        const markerEvent = {...event, id, highlight_id: group.level === 'Highlight' ? String(event.highlight_id || event.id || '') : '', title: markerTitle, kind_label: groupLabel, timecode: group.level === 'Point' ? String(event.timestamp || '') : `${fmt(start)}-${fmt(end)}`};
        renderedAxisEvents.set(id, markerEvent);
        const style = hasRange ? `left:${left}%;width:${width}%;--outline-stack:${index % 3}` : `left:${left}%;--outline-stack:${index % 3}`;
        const spanText = Number.isInteger(event.source_span_index)
          ? `, 장면 ${event.source_span_index + 1}/${event.source_span_count}`
          : '';
        const ariaType = groupLabel;
        const statusText = statusLabels.length ? ` · ${statusLabels.join(' · ')}` : '';
        const overviewText = group.level === 'Highlight'
          ? (event.source_span_index === 0 ? `H${event.__highlight_index + 1}` : '')
          : (hasRange ? markerTitle : '');
        return `<button type="button" class="${hasRange ? 'viewer-editor-outline-range' : 'viewer-editor-outline-point'} viewer-editor-outline-marker" data-outline-item="true" data-event-id="${esc(id)}"${Number.isInteger(event.source_span_index) ? ` data-highlight-id="${esc(String(event.highlight_id || event.id || ''))}" data-highlight-span-index="${event.source_span_index}" data-highlight-span-count="${event.source_span_count}"` : ''} data-evidence-preview="${esc(`${markerTitle}${spanText}${statusText}`)}" aria-label="${esc(`${ariaType}, ${markerTitle}${spanText}, ${fmt(start)}${hasRange ? `부터 ${fmt(end)}` : ''}${statusText}`)}" title="${esc(`${markerTitle}${spanText} · ${fmt(start)}${hasRange ? `-${fmt(end)}` : ''}${statusText}`)}" style="${style}">${esc(overviewText)}</button>`;
      }).join('');
      const pointCount = outlineProjection.filter((event) => String(event.level || '') === 'Point').length;
      const stateText = groupedEvents.length
        ? `${groupedEvents.length}개`
        : group.level === 'Highlight'
          ? `완결된 편집 Highlight 없음 · Point ${pointCount}개 유지`
          : '이 구조 없음';
      const highlightSelectionStatus = group.level === 'Highlight'
        ? '<span class="viewer-editor-highlight-selection-status" hidden></span>'
        : '';
      return `<div class="viewer-editor-lane-row viewer-editor-outline-row" data-kind-row="viewer-editor-outline-${esc(group.key)}" data-outline-track="${esc(group.level)}" data-outline-level="${esc(group.level)}" style="--viewer-editor-outline-color:${group.color}"><div class="viewer-editor-lane-label"><strong>${esc(groupLabel)}</strong><span>${stateText}</span>${highlightSelectionStatus}</div><div class="viewer-editor-track viewer-editor-outline-track">${markers || `<div class="viewer-editor-empty">${stateText}</div>`}</div></div>`;
    }).join('');
    axisEl.innerHTML = `<div class="viewer-editor-axis-canvas">
      <div class="viewer-editor-scale-row"><div class="viewer-editor-scale-label"><strong>전체 흐름</strong><span>${fmt(duration)}</span></div><div class="viewer-editor-scale">${renderScale()}</div></div>
      ${renderHeatmapOverviewRow()}
      ${renderWaveformRow()}
      ${buckets.length ? `<div class="viewer-editor-density-row"><div class="viewer-editor-density-label"><strong>전체 채팅량</strong><span>${densityScaleLabel}</span></div><div class="viewer-editor-density">${densityBars}</div></div>` : ''}
      ${outlineRows}
      ${summaryLaneRows}
      ${laneRows}
    </div>`;
    if (densityValue) densityValue.textContent = markerDensityLabel();
    if (countEl) countEl.textContent = `표시 중인 장면 ${visibleSceneEvents.length}/${visibleEvents.length}개`;
    const canvas = axisEl.querySelector('.viewer-editor-axis-canvas');
    if (canvas) canvas.style.setProperty('--viewer-editor-w', `${timelineWidth}px`);
    if (preservedScrollLeft != null) axisEl.scrollLeft = preservedScrollLeft;
    axisEl.removeEventListener('scroll', scheduleDensityScaleRefresh);
    axisEl.addEventListener('scroll', scheduleDensityScaleRefresh, { passive: true });
    axisEl.querySelectorAll('[data-event-id]').forEach((marker) => {
      const isHighlightSibling = Boolean(selectedHighlightId) && marker.dataset.highlightId === selectedHighlightId;
      marker.classList.toggle('selected', isHighlightSibling || marker.dataset.eventId === selectedEventId);
      marker.classList.toggle('highlight-group-selected', isHighlightSibling);
      const isCurrentHighlightSpan = isHighlightSibling && Number(marker.dataset.highlightSpanIndex) === selectedHighlightSpanIndex;
      marker.classList.toggle('highlight-span-current', isCurrentHighlightSpan);
      marker.setAttribute('aria-pressed', String(isHighlightSibling || marker.dataset.eventId === selectedEventId));
      if (isCurrentHighlightSpan) marker.setAttribute('aria-current', 'true');
      else marker.removeAttribute('aria-current');
      const activate = () => activateAxisEventMarker(marker);
      bindMarkerActivation(marker, activate);
      if (marker.dataset.highlightId) {
        marker.addEventListener('pointerenter', () => setHighlightGroupEmphasis(marker.dataset.highlightId, true));
        marker.addEventListener('pointerleave', () => setHighlightGroupEmphasis(marker.dataset.highlightId, false));
        marker.addEventListener('focusin', () => setHighlightGroupEmphasis(marker.dataset.highlightId, true));
        marker.addEventListener('focusout', (event) => {
          if (event.relatedTarget && event.relatedTarget.dataset && event.relatedTarget.dataset.highlightId === marker.dataset.highlightId) return;
          setHighlightGroupEmphasis(marker.dataset.highlightId, false);
        });
      }
    });
    axisEl.querySelectorAll('[data-edit-point-cluster-id]').forEach((marker) => {
      marker.classList.toggle('selected', selectedEventId === `edit_point_cluster_${marker.dataset.editPointClusterId || ''}`);
      const activate = () => {
        const event = editPointClusterEvent(String(marker.dataset.editPointClusterId || ''));
        if (event) {
          event.heatmap_zone_families = String(marker.dataset.clusterSignalFamilies || '').split(/\s+/).filter(Boolean);
          event.heatmap_zone_related_count = Number(marker.dataset.heatmapZoneRelatedCount || 0);
        }
        syncSplitEditorSeek(event && event.start_sec, event, true);
        renderEvidence(event || null);
      };
      bindMarkerActivation(marker, activate);
    });
    axisEl.querySelectorAll('[data-sec]').forEach((bar) => {
      if (bar.dataset.editPointClusterId) return;
      const activate = () => {
        const sec = Number(bar.dataset.sec || 0);
        const sourceRow = evidenceSourceRow(sec);
        syncSplitEditorSeek(sec, sourceRow, true);
        renderEvidence({ start_sec: sec, kind: 'chat', kind_label: '전체 채팅량', title: '전체 채팅량', evidence: [], ...(sourceRow || {}) });
      };
      bindMarkerActivation(bar, activate);
    });
    installAxisCollisionDisclosure();
    syncAxisMarkerSelection();
    bindNativeViewerClipClusters();
  }
  function fixedRepresentativeCollisionGroups(points, tolerance) {
    const groups = [];
    points.forEach((item) => {
      const width = Number.isFinite(item.width) ? item.width : Math.max(0, item.right - item.left);
      const group = width <= 32
        ? groups.find((candidate) => candidate.representativeWidth <= 32 && item.left <= candidate.representativeRight + tolerance && item.right >= candidate.representativeLeft - tolerance)
        : null;
      if (group) {
        group.items.push({...item, width});
        return;
      }
      groups.push({
        representativeLeft: item.left,
        representativeRight: item.right,
        representativeWidth: width,
        items: [{...item, width}],
      });
    });
    return groups;
  }
  function semanticMarkerMembers(marker, detailFor) {
    const event = detailFor(marker);
    const expected = Number(marker && marker.dataset.clusterCount || 0);
    const start = Number(event && event.start_sec);
    if (String(event && event.kind || '') !== 'viewer_clip' || !Number.isFinite(start) || expected < 2) return [event];
    const members = clipClusterEvents(event).filter((member) =>
      String(member && member.kind || '') === 'viewer_clip' && Number(member && member.start_sec) === start
    );
    return members.length === expected ? members : [event];
  }
  function bindNativeViewerClipClusters() {
    axisEl.querySelectorAll('.viewer-editor-marker.clip-cluster').forEach((marker) => {
      if (marker.dataset.nativeClipBound === '1') return;
      const event = renderedAxisEvents.get(String(marker.dataset.eventId || '')) || {};
      const members = semanticMarkerMembers(marker, () => event);
      if (members.length < 2) return;
      const track = marker.closest('.viewer-editor-track');
      if (!track) return;
      marker.dataset.nativeClipBound = '1';
      const pinned = document.createElement('span');
      const list = document.createElement('span');
      const id = `viewer-editor-native-cluster-${String(marker.dataset.eventId || event.start_sec || '').replace(/[^a-zA-Z0-9_-]/g, '')}`;
      pinned.id = id;
      pinned.className = 'viewer-editor-collision-pinned viewer-editor-native-clip-pinned';
      pinned.hidden = true;
      pinned.setAttribute('role', 'dialog');
      pinned.setAttribute('aria-label', '시청자 클립 묶음');
      pinned.style.left = `${marker.offsetLeft + 18}px`;
      list.setAttribute('role', 'list');
      list.setAttribute('aria-label', '시청자 클립 목록');
      members.forEach((member) => {
        const row = document.createElement('span');
        const time = document.createElement('time');
        const text = document.createElement('span');
        const evidence = [].concat(member.evidence || []).find((item) => item && (item.text || item.title));
        row.className = 'viewer-editor-collision-member viewer-editor-native-clip-list-member';
        row.setAttribute('role', 'listitem');
        time.textContent = fmt(member.start_sec || 0);
        text.textContent = `${member.kind_label || member.kind || '시청자 클립'} · ${member.title || '제목 없음'} — ${evidence ? String(evidence.text || evidence.title) : '확인 가능한 자막·채팅 근거 없음'}`;
        row.append(time, text); list.append(row);
      });
      pinned.append(list);
      const close = (restoreFocus = false) => setAxisDisclosure(pinned, marker, false, restoreFocus);
      const toggle = () => setAxisDisclosure(pinned, marker, pinned.hidden);
      marker.setAttribute('aria-controls', id);
      marker.setAttribute('aria-expanded', 'false');
      marker.addEventListener('click', toggle);
      marker.addEventListener('keydown', (event) => { if (event.key === 'Escape') { event.preventDefault(); close(); } });
      pinned.addEventListener('keydown', (event) => { if (event.key === 'Escape') { event.preventDefault(); close(true); } });
      track.append(pinned);
    });
  }
  function collisionGroupMembers(group, detailFor) {
    const seen = new Set();
    return group.items
      .flatMap((item) => semanticMarkerMembers(item.marker, detailFor).map((detail) => ({ marker: item.marker, detail })))
      .filter((member) => {
        const id = String(member.detail.id || member.marker.dataset.eventId || '');
        if (!id || seen.has(id)) return false;
        seen.add(id);
        return true;
      })
      .sort((a, b) => Number(a.detail.start_sec || 0) - Number(b.detail.start_sec || 0) || String(a.detail.id || '').localeCompare(String(b.detail.id || '')));
  }
  function collisionExpandedTrackHeight(collisions, detailFor) {
    const largestCanonicalGroup = Math.max(
      1,
      ...collisions.map((group) => collisionGroupMembers(group, detailFor).length),
    );
    return 34 + (largestCanonicalGroup - 1) * 30;
  }
  function installAxisCollisionDisclosure() {
    axisEl.querySelectorAll('.viewer-editor-collision-cluster,.viewer-editor-lane-disclosure,.viewer-editor-collision-pinned:not(.viewer-editor-native-clip-pinned),.viewer-editor-highlight-span-cluster').forEach((node) => node.remove());
    axisEl.querySelectorAll('.viewer-editor-marker.viewer-editor-collision-source').forEach((marker) => {
      marker.classList.remove('viewer-editor-collision-source');
      marker.removeAttribute('aria-hidden');
      marker.tabIndex = 0;
    });
    const detailFor = (marker) => renderedAxisEvents.get(String(marker.dataset.eventId || '')) || {};
    const evidenceText = (event) => {
      const rows = [].concat(event.evidence || []);
      const first = rows.find((row) => row && (row.text || row.title));
      return first ? String(first.text || first.title) : '확인 가능한 자막·채팅 근거 없음';
    };
    const activateSemanticMember = (marker, event) => {
      closeActiveAxisDisclosure(null, true);
      selectAxisEvent(event || detailFor(marker) || null);
      syncSplitEditorSeek(event && event.start_sec, event, true);
      renderEvidence(event || detailFor(marker) || null);
    };
    const memberButton = (marker, event) => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'viewer-editor-collision-member';
      if (event.highlight_id) button.dataset.highlightId = String(event.highlight_id);
      if (Number.isInteger(event.source_span_index)) {
        button.dataset.highlightSpanIndex = String(event.source_span_index);
        button.dataset.highlightSpanCount = String(event.source_span_count || 1);
        button.setAttribute('aria-label', `${event.kind_label || optionLabel(event.kind)}, 장면 ${event.source_span_index + 1}/${event.source_span_count || 1}, ${fmt(event.start_sec || 0)}부터 ${fmt(event.end_sec || event.start_sec || 0)}`);
      }
      if (event.highlight_id) {
        button.addEventListener('pointerenter', () => setHighlightGroupEmphasis(String(event.highlight_id), true));
        button.addEventListener('pointerleave', () => setHighlightGroupEmphasis(String(event.highlight_id), false));
        button.addEventListener('focusin', () => setHighlightGroupEmphasis(String(event.highlight_id), true));
        button.addEventListener('focusout', () => setHighlightGroupEmphasis(String(event.highlight_id), false));
      }
      const time = document.createElement('time'); time.textContent = fmt(event.start_sec || 0);
      const text = document.createElement('span');
      const spanNumber = Number.isInteger(event.source_span_index)
        ? `장면 ${event.source_span_index + 1}/${event.source_span_count} · ${fmt(event.start_sec || 0)}-${fmt(event.end_sec || event.start_sec || 0)}`
        : '';
      const exclusion = event.source_span_exclusion_before && String(event.source_span_exclusion_before.reason || '').trim();
      const spanMeaning = spanNumber
        ? `${spanNumber}${event.source_span_reason ? ` · ${event.source_span_reason}` : ''}${exclusion ? ` · 앞 공백 제외: ${exclusion}` : ''}`
        : evidenceText(event);
      text.textContent = `${event.kind_label || optionLabel(event.kind)} · ${friendlyEventTitle(event) || marker.title || '제목 없음'} — ${spanMeaning}`;
      button.append(time, text);
      bindMarkerActivation(button, () => activateSemanticMember(marker, event));
      return button;
    };
    const mountPinned = (track, trigger, members, label, className = '') => {
      const pinned = document.createElement('span');
      pinned.className = `viewer-editor-collision-pinned ${className}`.trim(); pinned.hidden = true;
      pinned.setAttribute('role', 'dialog'); pinned.setAttribute('aria-label', label);
      members.forEach(({marker, event}) => pinned.append(memberButton(marker, event)));
      const close = (restoreFocus = false) => setAxisDisclosure(pinned, trigger, false, restoreFocus);
      const toggle = (event) => { event.preventDefault(); event.stopPropagation(); setAxisDisclosure(pinned, trigger, pinned.hidden); };
      trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-expanded', 'false');
      trigger.addEventListener('click', toggle);
      trigger.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') toggle(event); if (event.key === 'Escape') close(true); });
      pinned.addEventListener('keydown', (event) => { if (event.key === 'Escape') { event.preventDefault(); close(true); } });
      track.append(pinned);
    };
    axisEl.querySelectorAll('.viewer-editor-axis-canvas > .viewer-editor-lane-row:not(.viewer-editor-outline-row):not([hidden])').forEach((row) => {
      const label = row.querySelector('.viewer-editor-lane-label'), track = row.querySelector('.viewer-editor-track');
      if (!label || !track) return;
      const markers = Array.from(track.querySelectorAll(':scope > [data-event-id]'));
      const ordinaryMarkers = markers.filter((marker) => !marker.classList.contains('viewer-editor-cluster-control') && !marker.classList.contains('clip-cluster'));
      const points = ordinaryMarkers
        .map((marker) => ({marker, rect: marker.getBoundingClientRect()}))
        .filter((item) => item.rect.width > 0 && item.rect.height > 0)
        .map((item) => ({marker: item.marker, left: item.rect.left, right: item.rect.right, width: item.rect.width}))
        .sort((a, b) => a.left - b.left);
      const groups = fixedRepresentativeCollisionGroups(points, 3);
      const ordinaryCollisions = groups.filter((group) => collisionGroupMembers(group, detailFor).length > 1);
      const nativeClipGroups = markers
        .filter((marker) => marker.classList.contains('clip-cluster') || marker.classList.contains('viewer-editor-cluster-grain'))
        .map((marker) => ({marker, members: semanticMarkerMembers(marker, detailFor)}))
        .filter((group) => group.members.length > 1);
      if (!ordinaryCollisions.length && !nativeClipGroups.length) return;
      const disclosure = document.createElement('details'); disclosure.className = 'viewer-editor-lane-disclosure';
      const summary = document.createElement('summary'); summary.setAttribute('aria-label', '겹친 신호 펼치기'); disclosure.append(summary); label.append(disclosure);
      const render = (expanded) => {
        row.classList.toggle('viewer-editor-lane-expanded', expanded);
        row.querySelectorAll('.viewer-editor-collision-cluster,.viewer-editor-native-clip-member').forEach((node) => node.remove());
        markers.forEach((marker) => { marker.classList.remove('viewer-editor-collision-source'); marker.removeAttribute('aria-hidden'); marker.tabIndex = 0; marker.style.transform = ''; });
        ordinaryCollisions.forEach((group) => {
          const canonicalMembers = collisionGroupMembers(group, detailFor);
          if (expanded) { canonicalMembers.forEach(({marker}, index) => { marker.style.transform = `translateY(${index * 30}px)`; }); return; }
          canonicalMembers.forEach(({marker}) => { marker.classList.add('viewer-editor-collision-source'); marker.setAttribute('aria-hidden', 'true'); marker.tabIndex = -1; });
          const representative = canonicalMembers[0].marker, event = canonicalMembers[0].detail, cluster = document.createElement('span'), trigger = representative.cloneNode(false);
          cluster.className = 'viewer-editor-collision-cluster'; cluster.style.left = `${representative.offsetLeft}px`;
          trigger.className = `viewer-editor-marker viewer-editor-collision-trigger viewer-editor-cluster-grain${representative.classList.contains('range') ? ' range' : ''}`;
          trigger.style.left = '0px';
          trigger.style.width = `${Math.min(20, Math.max(8, representative.offsetWidth))}px`;
          trigger.removeAttribute('aria-hidden');
          trigger.tabIndex = 0;
          trigger.dataset.clusterMemberIds = canonicalMembers.map((member) => String(member.detail.id || member.marker.dataset.eventId)).join(',');
          trigger.dataset.clusterCount = String(canonicalMembers.length); trigger.setAttribute('aria-label', `같은 위치 신호 ${canonicalMembers.length}개`);
          event.__collision_members = canonicalMembers.map((member) => member.detail);
          cluster.append(trigger); track.append(cluster); mountPinned(cluster, trigger, canonicalMembers.map(({marker, detail}) => ({marker, event: detail})), '겹친 신호 목록');
        });
        nativeClipGroups.forEach(({marker, members}) => {
          if (!expanded) return;
          marker.classList.add('viewer-editor-collision-source');
          marker.setAttribute('aria-hidden', 'true'); marker.tabIndex = -1;
          members.forEach((event, index) => {
            const member = marker.cloneNode(false);
            member.classList.remove('clip-cluster', 'viewer-editor-cluster-grain', 'viewer-editor-collision-source', 'selected');
            member.classList.add('viewer-editor-native-clip-member');
            member.dataset.eventId = String(event.id || '');
            member.dataset.kind = String(event.kind || 'viewer_clip');
            delete member.dataset.clusterCount;
            delete member.dataset.eventCount;
            delete member.dataset.nativeClipBound;
            member.removeAttribute('aria-controls');
            member.removeAttribute('aria-expanded');
            member.removeAttribute('aria-haspopup');
            member.removeAttribute('aria-describedby');
            member.style.left = `${Math.max(0, Math.min(100, (Number(event.start_sec || 0) / duration) * 100))}%`;
            member.style.transform = `translateY(${index * 30}px)`;
            member.style.zIndex = String(10 - index);
            member.title = `${fmt(event.start_sec || 0)} ${event.title || event.kind_label || '시청자 클립'}`;
            member.setAttribute('aria-label', `${fmt(event.start_sec || 0)} ${event.kind_label || event.kind || '시청자 클립'} ${event.title || '제목 없음'}`);
            member.addEventListener('click', (clickEvent) => { clickEvent.stopPropagation(); marker.click(); });
            track.append(member);
          });
        });
        const largestNativeClipGroup = Math.max(1, ...nativeClipGroups.map((group) => group.members.length));
        const expandedMemberCount = Math.max(collisionExpandedTrackHeight(ordinaryCollisions, detailFor), 34 + (largestNativeClipGroup - 1) * 30);
        track.style.minHeight = expanded ? `${expandedMemberCount}px` : '';
        summary.setAttribute('aria-label', expanded ? '겹친 신호 접기' : '겹친 신호 펼치기');
        summary.setAttribute('aria-expanded', String(expanded));
      };
      disclosure.addEventListener('toggle', () => render(disclosure.open)); render(false);
    });
    axisEl.querySelectorAll('.viewer-editor-outline-row[data-outline-level="Highlight"]:not([hidden])').forEach((row) => {
      const label = row.querySelector('.viewer-editor-lane-label');
      const track = row.querySelector('.viewer-editor-track');
      if (!label || !track) return;
      const canonicalMarkers = Array.from(
        track.querySelectorAll(':scope > [data-event-id][data-highlight-id][data-highlight-span-index]')
      );
      if (!canonicalMarkers.length) return;
      const grouped = new Map();
      canonicalMarkers.forEach((marker) => {
        const highlightId = String(marker.dataset.highlightId || '');
        if (!highlightId) return;
        if (!grouped.has(highlightId)) grouped.set(highlightId, []);
        grouped.get(highlightId).push(marker);
      });
      const highlightGroups = Array.from(grouped.entries())
        .map(([highlightId, markers]) => ({
          highlightId,
          markers: markers.sort((a, b) => Number(a.dataset.highlightSpanIndex || 0) - Number(b.dataset.highlightSpanIndex || 0)),
        }))
        .sort((a, b) => Number(detailFor(a.markers[0]).start_sec || 0) - Number(detailFor(b.markers[0]).start_sec || 0));
      highlightGroups.forEach((group, highlightIndex) => {
        const groupLabel = `H${highlightIndex + 1}`;
        const firstEvent = detailFor(group.markers[0]);
        group.label = groupLabel;
        group.title = friendlyEventTitle(firstEvent) || `Highlight ${highlightIndex + 1}`;
        group.markers.forEach((marker) => {
          marker.dataset.highlightGroupLabel = groupLabel;
          const accessibleName = String(marker.getAttribute('aria-label') || '').trim();
          if (accessibleName && !accessibleName.startsWith(`${groupLabel},`)) {
            marker.setAttribute('aria-label', `${groupLabel}, ${accessibleName}`);
          }
        });
      });
      const selectionStatus = label.querySelector('.viewer-editor-highlight-selection-status');
      const laneToggle = document.createElement('button');
      const toggleName = document.createElement('strong');
      const toggleCount = document.createElement('span');
      laneToggle.type = 'button';
      laneToggle.className = 'viewer-editor-highlight-lane-toggle';
      toggleName.textContent = 'Highlight';
      toggleCount.textContent = `${highlightGroups.length}개`;
      laneToggle.append(toggleName, toggleCount);
      label.replaceChildren(laneToggle);
      if (selectionStatus) label.append(selectionStatus);
      const groupContainer = document.createElement('div');
      groupContainer.className = 'viewer-editor-highlight-expanded-groups';
      groupContainer.hidden = true;
      highlightGroups.forEach((group) => {
        const groupRow = document.createElement('div');
        const groupLabel = document.createElement('div');
        const groupTrack = document.createElement('div');
        const groupName = document.createElement('strong');
        const groupTitle = document.createElement('span');
        groupRow.className = 'viewer-editor-highlight-group-row';
        groupRow.setAttribute('role', 'group');
        groupRow.setAttribute('aria-label', `${group.label}, ${group.title}, 장면 ${group.markers.length}개`);
        groupLabel.className = 'viewer-editor-highlight-group-label';
        groupName.textContent = group.label;
        groupTitle.textContent = group.title;
        groupLabel.append(groupName, groupTitle);
        groupTrack.className = 'viewer-editor-outline-track viewer-editor-highlight-group-track';
        groupRow.append(groupLabel, groupTrack);
        groupContainer.append(groupRow);
        group.row = groupRow;
        group.track = groupTrack;
      });
      row.append(groupContainer);
      const groupColors = ['#e0af68', '#7dcfff', '#bb9af7', '#9ece6a', '#f7768e'];
      highlightGroups.forEach((group, index) => {
        const color = groupColors[index % groupColors.length];
        group.row.style.setProperty('--viewer-editor-highlight-group-color', color);
        group.markers.forEach((marker) => {
          marker.style.setProperty('--viewer-editor-highlight-group-color', color);
        });
      });
      const render = (expanded) => {
        row.classList.toggle('viewer-editor-lane-expanded', expanded);
        groupContainer.hidden = !expanded;
        canonicalMarkers.forEach((marker) => {
          marker.classList.remove('viewer-editor-collision-source');
          marker.classList.remove('viewer-editor-highlight-overview-span');
          marker.style.transform = '';
          marker.textContent = '';
        });
        if (expanded) {
          highlightGroups.forEach((group) => {
            group.track.append(...group.markers);
            group.markers.forEach((marker) => {
              marker.removeAttribute('aria-hidden');
              marker.tabIndex = 0;
            });
          });
        } else {
          highlightGroups.forEach((group) => {
            track.append(...group.markers);
            group.markers.forEach((marker) => {
              marker.classList.add('viewer-editor-highlight-overview-span');
              marker.removeAttribute('aria-hidden');
              marker.tabIndex = 0;
            });
          });
        }
        laneToggle.setAttribute('aria-label', expanded ? 'Highlight별 구간 접기' : 'Highlight별 구간 펼치기');
        laneToggle.setAttribute('aria-expanded', String(expanded));
      };
      laneToggle.addEventListener('click', () => {
        render(laneToggle.getAttribute('aria-expanded') !== 'true');
      });
      render(false);
    });
  }
  function installHierarchyPreviews() {
    if (axisEl.dataset.hierarchyPreviewBound === '1') return;
    axisEl.dataset.hierarchyPreviewBound = '1';
    const sharedPreview = document.createElement('aside');
    const preview = sharedPreview;
    preview.className = 'viewer-editor-hierarchy-preview viewer-editor-collision-shared-preview';
    preview.id = 'viewer-editor-collision-shared-preview';
    preview.setAttribute('role', 'tooltip');
    preview.hidden = true;
    document.body.append(preview);
    const targetFor = (node) => node && node.closest ? node.closest('.viewer-editor-marker[data-event-id], .viewer-editor-outline-range[data-event-id], .viewer-editor-outline-point[data-event-id]') : null;
    const detailFor = (marker) => {
      const event = renderedAxisEvents.get(String(marker && marker.dataset.eventId || '')) || {};
      const evidence = [].concat(Array.isArray(event.evidence) ? event.evidence : []);
      const first = evidence.find((row) => row && (row.text || row.title));
      const clusterCount = Math.max(1, Number(marker && marker.dataset.clusterCount || event.cluster_count || event.event_count || 1));
      return {
        time: displayTime(event, Number(event.start_sec || 0)),
        source: event.kind_label || optionLabel(event.kind),
        title: friendlyEventTitle(event) || marker.getAttribute('data-evidence-preview') || '편집 후보',
        evidence: first ? String(first.text || first.title || '') : '',
        count: clusterCount,
      };
    };
    const show = (marker) => {
      if (!marker || activeAxisDisclosure) return;
      const detail = detailFor(marker);
      preview.innerHTML = `<span class="viewer-editor-hierarchy-preview-meta"><span>${esc(detail.time)}</span><span>${esc(detail.source)}</span></span><strong>${esc(detail.title)}</strong><p>${esc(detail.evidence)}</p><p>${detail.count > 1 ? `${detail.count}개 묶음 · 선택하면 펼쳐서 확인` : '선택하면 상세 근거 확인'}</p>`;
      preview.hidden = false;
      marker.setAttribute('aria-describedby', preview.id);
      const rect = marker.getBoundingClientRect();
      const left = Math.max(12, Math.min(window.innerWidth - 260, rect.left));
      preview.style.left = `${left}px`;
      const below = rect.bottom + 8;
      preview.style.top = `${below + preview.offsetHeight > window.innerHeight ? Math.max(8, rect.top - preview.offsetHeight - 8) : below}px`;
    };
    const hide = (marker) => {
      if (marker && marker.getAttribute('aria-describedby') === preview.id) marker.removeAttribute('aria-describedby');
      preview.hidden = true;
    };
    axisEl.addEventListener('mouseover', (event) => {
      const marker = targetFor(event.target);
      if (marker && !marker.contains(event.relatedTarget)) show(marker);
    });
    axisEl.addEventListener('mouseout', (event) => {
      const marker = targetFor(event.target);
      if (marker && !marker.contains(event.relatedTarget)) hide(marker);
    });
    axisEl.addEventListener('focusin', (event) => show(targetFor(event.target)));
    axisEl.addEventListener('focusout', (event) => hide(targetFor(event.target)));
  }
  function renderDetailedSummaryHierarchy() {
    const timeline = document.querySelector('.timeline');
    if (!timeline || timeline.dataset.managerOutlineHierarchy === 'ready') return;
    const d1Rows = outlineProjection.filter((row) => String(row.level || '') === 'D1');
    const d2Rows = outlineProjection.filter((row) => String(row.level || '') === 'D2');
    const pointRows = outlineProjection.filter((row) => String(row.level || '') === 'Point');
    const cards = Array.from(timeline.querySelectorAll(':scope > .t-item'));
    if (!d1Rows.length || !cards.length) {
      if (timeline.dataset.managerOutlineHierarchy === 'pending') {
        timeline.dataset.managerOutlineHierarchy = 'error';
      }
      return;
    }
    const parseTime = (value) => {
      const match = String(value || '').match(/\b\d{1,2}:\d{2}(?::\d{2})?\b/);
      return match ? match[0].split(':').reduce((total, token) => total * 60 + Number(token || 0), 0) : Number.NaN;
    };
    const projectionById = new Map(outlineProjection.map((row) => [String(row.id || ''), row]));
    const pointProjectionBySecond = new Map(pointRows.map((row) => [Number(row.start_sec || parseTime(row.timestamp)), row]));
    const parsedCardRows = cards.map((card) => ({
      card,
      sec: parseTime(card.querySelector('.tc') && card.querySelector('.tc').textContent),
    }));
    const cardRows = parsedCardRows.map((row) => {
      const outlineId = String(row.card.dataset.outlineId || row.card.dataset.segmentId || '');
      const outline = projectionById.get(outlineId) || pointProjectionBySecond.get(row.sec) || {};
      const outlineStart = Number(outline.start_sec);
      return {
        ...row,
        sec: Number.isFinite(outlineStart) ? outlineStart : row.sec,
        outline,
      };
    });
    const assigned = new Set();
    const heading = (level, row) => {
      const el = document.createElement(level === 'D1' ? 'h3' : 'h4');
      el.className = level === 'D1' ? 'viewer-detail-flow-title' : 'viewer-detail-subflow-title';
      const title = document.createElement('span');
      const timeLink = document.createElement('a');
      const time = document.createElement('time');
      title.textContent = String(row.title || (level === 'D1' ? '방송 흐름' : '세부 흐름'));
      time.textContent = `${String(row.start || '')}–${String(row.end || '')}`;
      timeLink.className = 'viewer-detail-heading-time';
      timeLink.href = seekUrl(Number(row.start_sec || 0), row);
      timeLink.target = '_blank';
      timeLink.rel = 'noopener';
      timeLink.append(time);
      el.append(title, timeLink);
      return el;
    };
    const pointList = (rows) => {
      const list = document.createElement('div');
      list.className = 'viewer-detail-scene-list';
      list.setAttribute('aria-label', '주요 장면');
      rows.forEach(({card, sec, outline}) => {
        card.classList.add('viewer-editor-point-row');
        if (!card.querySelector('.viewer-editor-point-icon-slot')) {
          const iconSlot = document.createElement('span');
          iconSlot.className = 'viewer-editor-point-icon-slot viewer-editor-icon-slot';
          iconSlot.setAttribute('aria-hidden', 'true');
          card.prepend(iconSlot);
        }
        const head = card.querySelector('.t-head');
        const mood = head && head.querySelector('.mood');
        if (mood && String(mood.textContent || '').trim() === 'Point') mood.textContent = '';
        const evidenceBadges = Array.from(card.querySelectorAll('.injected-badge,.audio-peak-badge'));
        const exactViewerClip = events.find((event) => String(event && event.kind || '') === 'viewer_clip' && Math.abs(Number(event.start_sec || 0) - sec) <= 1);
        if (exactViewerClip && !evidenceBadges.some((badge) => badge.classList.contains('injected-badge'))) {
          const badge = document.createElement('span');
          badge.className = 'injected-badge';
          badge.title = '시청자 클립 anchor와 같은 시각의 Point';
          badge.textContent = '📎 시청자 클립';
          evidenceBadges.push(badge);
        }
        const nearestAudio = events
          .filter((event) => String(event && event.kind || '') === 'audio_peak')
          .map((event) => {
            const start = Number(event.start_sec || 0);
            const end = Number(event.end_sec != null ? event.end_sec : start);
            const delta = start <= sec && sec <= end ? 0 : Math.min(Math.abs(start - sec), Math.abs(end - sec));
            return {event, delta};
          })
          .filter((row) => row.delta <= 30)
          .sort((a, b) => a.delta - b.delta)[0];
        if (nearestAudio && !evidenceBadges.some((badge) => badge.classList.contains('audio-peak-badge'))) {
          const badge = document.createElement('span');
          badge.className = 'audio-peak-badge';
          badge.title = '오디오 반응 피크와 가까운 Point';
          badge.textContent = `🔊 오디오 피크 ${nearestAudio.delta === 0 ? '구간 내' : `±${Math.round(nearestAudio.delta)}s`}`;
          evidenceBadges.push(badge);
        }
        const statusLabels = Array.isArray(outline && outline.status_labels)
          ? outline.status_labels.filter((label) => label && label !== '요약에 포함됨')
          : [];
        let evidenceRow = card.querySelector('.viewer-detail-point-evidence-row');
        if ((evidenceBadges.length || statusLabels.length) && !evidenceRow) {
          evidenceRow = document.createElement('div');
          evidenceRow.className = 'viewer-detail-point-evidence-row';
          evidenceRow.setAttribute('aria-label', '이 Point의 보조 근거');
          if (head) head.insertAdjacentElement('afterend', evidenceRow);
          else card.prepend(evidenceRow);
        }
        evidenceBadges.forEach((badge) => {
          if (badge.parentElement !== evidenceRow) evidenceRow.append(badge);
        });
        statusLabels.forEach((label) => {
          if (Array.from(evidenceRow.children).some((child) => child.dataset.outlineStatus === label)) return;
          const statusBadge = document.createElement('span');
          statusBadge.className = 'viewer-editor-status-badge';
          statusBadge.dataset.outlineStatus = label;
          statusBadge.textContent = label;
          evidenceRow.append(statusBadge);
        });
        if (outline && outline.id) card.id = `manager-outline-${String(outline.id).replace(/[^a-zA-Z0-9_-]/g, '')}`;
        let body = card.querySelector('.t-body');
        const storyTitle = String(outline && outline.story_title || '').trim();
        const storyContent = String(outline && outline.story_content || '').trim();
        if (storyTitle || storyContent) {
          if (!body) {
            body = document.createElement('div');
            body.className = 't-body';
            card.append(body);
          }
          body.replaceChildren();
          if (storyTitle) {
            const storyTitleNode = document.createElement('strong');
            storyTitleNode.className = 'viewer-detail-point-story-title';
            storyTitleNode.textContent = storyTitle;
            body.append(storyTitleNode);
          }
          if (storyContent) {
            const storyContentNode = document.createElement('p');
            storyContentNode.className = 'viewer-detail-point-story-content';
            storyContentNode.textContent = storyContent;
            body.append(storyContentNode);
          }
        }
        const bodyText = String(body && body.textContent || '').trim();
        const internalBodyToken = /(?:supporting_signal_only|proximity_signal|temporal_candidate|deterministic_injection)/i.test(bodyText);
        if (body && (!bodyText || /읽기 전용 (정확한 시각|방송 목차)/.test(bodyText) || internalBodyToken)) {
          const evidence = events
            .filter((event) => {
              const eventStart = Number(event.start_sec || 0);
              const eventEnd = Number(event.end_sec != null && event.end_sec !== '' ? event.end_sec : eventStart);
              return eventStart <= sec && sec <= eventEnd;
            })
            .flatMap((event) => [].concat(event.evidence || []))
            .find((item) => {
              const candidate = String(item && (item.text || item.title) || '').trim();
              return candidate && !/(?:supporting_signal_only|proximity_signal|temporal_candidate|deterministic_injection)/i.test(candidate);
            });
          if (evidence) {
            body.textContent = friendlyEvidenceText(
              evidence.label || evidence.title || '',
              evidence.text || evidence.title || ''
            );
          } else {
            body.remove();
          }
        }
        if (body && body.isConnected && String(body.textContent || '').trim()) {
          const storyDisclosure = document.createElement('details');
          storyDisclosure.className = 'viewer-detail-point-story';
          const storySummary = document.createElement('summary');
          storySummary.className = 'viewer-detail-point-story-summary';
          const titleNode = head && head.querySelector('.t-title');
          const pointTitle = String(titleNode && titleNode.textContent || outline && outline.title || '주요 장면').trim();
          const chevron = document.createElement('span');
          chevron.className = 'viewer-detail-point-story-chevron';
          chevron.setAttribute('aria-hidden', 'true');
          chevron.textContent = '›';
          if (titleNode) storySummary.append(titleNode, chevron);
          else {
            const fallbackTitle = document.createElement('span');
            fallbackTitle.className = 't-title';
            fallbackTitle.textContent = pointTitle;
            storySummary.append(fallbackTitle, chevron);
          }
          const syncStoryDisclosureLabel = () => {
            storySummary.setAttribute('aria-label', `${pointTitle} Story ${storyDisclosure.open ? '접기' : '펼치기'}`);
          };
          storySummary.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            storyDisclosure.open = !storyDisclosure.open;
          });
          storyDisclosure.addEventListener('toggle', () => {
            list.querySelectorAll('details.viewer-detail-point-story[open]').forEach((details) => {
              if (details === storyDisclosure) return;
              details.open = false;
            });
            syncStoryDisclosureLabel();
          });
          syncStoryDisclosureLabel();
          body.classList.add('viewer-detail-point-story-body');
          storyDisclosure.append(storySummary, body);
          if (head) head.append(storyDisclosure);
          else card.prepend(storyDisclosure);
        }
        list.append(card);
      });
      return list;
    };
    const navigator = document.createElement('nav');
    navigator.className = 'timeline-navigator';
    navigator.setAttribute('aria-label', '상세 타임라인 목차 이동');
    const duration = Math.max(1, Number(data.duration_sec || 0), ...outlineProjection.map((row) => Number(row.end_sec || row.start_sec || 0)));
    const ruler = document.createElement('div'); ruler.className = 'timeline-nav-ruler';
    const rulerAxis = document.createElement('span'); rulerAxis.className = 'timeline-nav-ruler-axis';
    const rulerStart = document.createElement('span'); rulerStart.textContent = '00:00';
    const rulerEnd = document.createElement('span'); rulerEnd.textContent = fmt(duration);
    rulerAxis.append(rulerStart, rulerEnd); ruler.append(document.createElement('span'), rulerAxis); navigator.append(ruler);
    const addNavRow = (label, rows, kind) => {
      const line = document.createElement('div'); line.className = 'timeline-nav-row';
      const name = document.createElement('span'); name.className = 'timeline-nav-label'; name.textContent = label;
      const axis = document.createElement('span'); axis.className = 'timeline-nav-axis';
      rows.forEach((row, index) => {
        const button = document.createElement('button'); button.type = 'button';
        button.className = `timeline-nav-button ${kind === 'scene' ? 'timeline-nav-point' : 'timeline-nav-range'}`;
        const start = Number(row.start_sec || 0), end = Number(row.end_sec || start);
        button.style.left = `${Math.max(0, Math.min(100, start / duration * 100))}%`;
        button.textContent = String(row.title || label);
        if (kind !== 'scene') button.style.width = `${Math.max(0.8, (end - start) / duration * 100)}%`;
        button.title = `${row.start || row.timestamp || ''} · ${row.title || label}`;
        button.setAttribute('aria-label', button.title);
        button.addEventListener('click', () => document.getElementById(`manager-outline-${String(row.id || index).replace(/[^a-zA-Z0-9_-]/g, '')}`)?.scrollIntoView({behavior: 'smooth', block: 'start'}));
        axis.append(button);
      });
      line.append(name, axis); navigator.append(line);
    };
    addNavRow('방송 흐름', d1Rows, 'flow');
    addNavRow('세부 흐름', d2Rows, 'subject');
    addNavRow('주요 장면', pointRows, 'scene');
    const hierarchy = document.createElement('div');
    hierarchy.className = 'viewer-detail-hierarchy';
    hierarchy.append(navigator);
    d1Rows.forEach((d1) => {
      const flow = document.createElement('section');
      flow.className = 'viewer-detail-flow';
      flow.id = `manager-outline-${String(d1.id || '').replace(/[^a-zA-Z0-9_-]/g, '')}`;
      flow.tabIndex = 0;
      flow.append(heading('D1', d1));
      const children = d2Rows.filter((row) => String(row.parent_id || '') === String(d1.id || ''));
      children.forEach((d2) => {
        const start = Number(d2.start_sec || 0), end = Number(d2.end_sec || start);
        const inside = cardRows.filter((row) => {
          if (assigned.has(row.card)) return false;
          const level = String(row.outline.level || '');
          if (['D1', 'D2', 'Candidate'].includes(level)) return false;
          if (row.sec >= start && row.sec < end) return true;
          if (level !== 'Point' || row.sec !== end) return false;
          return !d2Rows.some((sibling) => (
            String(sibling.id || '') !== String(d2.id || '')
            && Number(sibling.start_sec || 0) === row.sec
          ));
        });
        inside.forEach((row) => assigned.add(row.card));
        const subflow = document.createElement('section');
        subflow.className = 'viewer-detail-subflow';
        subflow.id = `manager-outline-${String(d2.id || '').replace(/[^a-zA-Z0-9_-]/g, '')}`;
        subflow.tabIndex = 0;
        subflow.append(heading('D2', d2));
        if (inside.length) subflow.append(pointList(inside));
        flow.append(subflow);
      });
      const start = Number(d1.start_sec || 0), end = Number(d1.end_sec || start);
      const direct = cardRows.filter((row) => {
        if (assigned.has(row.card)) return false;
        const level = String(row.outline.level || '');
        return !['D1', 'D2', 'Candidate'].includes(level) && row.sec >= start && row.sec < end;
      });
      direct.forEach((row) => assigned.add(row.card));
      if (direct.length) flow.append(pointList(direct));
      hierarchy.append(flow);
    });
    const unowned = cardRows.filter((row) => !assigned.has(row.card));
    if (unowned.length) hierarchy.append(pointList(unowned));
    timeline.replaceChildren(hierarchy);
    timeline.dataset.managerOutlineHierarchy = 'ready';
  }
  function highlightSourceSpanChips(event) {
    const spans = Array.isArray(event && event.source_spans) ? event.source_spans : [];
    if (!spans.length) return '';
    return `<nav class="viewer-editor-highlight-scenes" aria-label="선택한 Highlight 원본 장면">${spans.map((span, index) => {
      const start = Number(span && span.start_sec);
      const end = Number(span && span.end_sec);
      if (!Number.isFinite(start) || !Number.isFinite(end)) return '';
      const url = seekUrl(start, event);
      const label = `장면 ${index + 1}/${spans.length}, ${fmt(start)}부터 ${fmt(end)}까지`;
      const current = index === selectedHighlightSpanIndex ? ' aria-current="true"' : '';
      return url
        ? `<a class="viewer-editor-chip viewer-editor-highlight-scene-chip" data-highlight-span-index="${index}" data-start="${start}" href="${esc(url)}" target="_blank" rel="noopener"${current} aria-label="${esc(`${label}, 시작으로 이동`)}">[${esc(fmt(start))}–${esc(fmt(end))}]</a>`
        : `<span class="viewer-editor-chip viewer-editor-highlight-scene-chip" aria-label="${esc(label)}">[${esc(fmt(start))}–${esc(fmt(end))}]</span>`;
    }).join('')}</nav>`;
  }
  function highlightPointChips(event) {
    const refs = Array.isArray(event && event.point_refs) ? event.point_refs.map(String) : [];
    if (!refs.length) return '';
    const points = refs.map((ref) => outlineProjection.find((row) => String(row && row.level || '') === 'Point' && String(row && row.id || '') === ref)).filter(Boolean);
    if (!points.length) return '';
    return `<div class="viewer-editor-highlight-points" aria-label="선택한 Highlight 연결 Point">${points.map((point) => {
      const start = Number(point.start_sec);
      const url = Number.isFinite(start) ? seekUrl(start, point) : '';
      const title = String(point.title || '주요 장면');
      return url
        ? `<a class="viewer-editor-chip" data-start="${start}" href="${esc(url)}" target="_blank" rel="noopener" aria-label="${esc(`${point.timestamp || fmt(start)} ${title} Point로 이동`)}">${esc(point.timestamp || fmt(start))} · ${esc(title)}</a>`
        : `<span class="viewer-editor-chip">${esc(point.timestamp || '')} · ${esc(title)}</span>`;
    }).join('')}</div>`;
  }
  function renderEvidence(event) {
    if (!event || typeof event !== 'object') {
      const introEl = document.getElementById('viewerEditorEvidenceIntro');
      if (introEl) introEl.hidden = false;
      evidenceEl.innerHTML = '<div class="viewer-editor-empty">장면 찾기에서 시각을 선택하면 근거가 여기에 표시됩니다.</div>';
      return;
    }
    const sec = Number(event && event.start_sec);
    if (!Number.isFinite(sec)) {
      const introEl = document.getElementById('viewerEditorEvidenceIntro');
      if (introEl) introEl.hidden = false;
      evidenceEl.innerHTML = '<div class="viewer-editor-empty">장면 찾기에서 시각을 선택하면 근거가 여기에 표시됩니다.</div>';
      return;
    }
    if (event.kind === 'edit_point_cluster') {
      renderEditPointClusterEvidence(event, sec);
      return;
    }
    const introEl = document.getElementById('viewerEditorEvidenceIntro');
    if (introEl) introEl.hidden = true;
    syncSplitEditorSeek(sec, event);
    selectAxisEvent(event);
    axisEl.querySelectorAll('[data-edit-point-cluster-id]').forEach((marker) => marker.classList.remove('selected'));
    const clusterEvents = clipClusterEvents(event);
    const clusterEventIds = clusterEventIdSet(clusterEvents);
    const nearbyEvents = events
      .filter((row) => near(row, sec, 45) && !clusterEventIds.has(String(row && row.id || '')))
      .slice(0, 8);
    const nearbyBuckets = buckets.filter((row) => near(row, sec, 45)).slice(0, 5);
    const nearbySubtitles = subtitles.filter((row) => near(row, sec, 45)).slice(0, 6);
    const selectedTitleText = selectedTitle(event, sec);
    const selectedClipLink = event.kind === 'viewer_clip' ? viewerClipLinkInfo(clusterEvents.concat(nearbyEvents), clusterEvents[0] || event) : { uid: '', url: '', title: '' };
    const evidenceTargets = evidenceTargetsForEvent(event, sec);
    const clusterEvidenceRows = event.kind === 'viewer_clip' ? clipRowsForCluster(clusterEvents) : [];
    const extraEvidenceRows = []
      .concat((event.evidence || []).map((item) => {
        const rawLabel = String(item.label || '').trim();
        const label = event.kind === 'audio_peak' ? '소리 변화 근거' : friendlyEvidenceLabel(rawLabel || '근거');
        let text = friendlyEvidenceText(label, item.text || '');
        if (event.kind === 'audio_peak' && (!text || text === '보조 신호입니다. 장면 판단은 자막이나 기존 요약과 함께 보세요.')) {
          text = '소리 변화가 감지된 참고 구간입니다. 자막과 화면 흐름으로 쓸 장면인지 확인하세요.';
        }
        const ownUrl = event.kind === 'viewer_clip' ? clipUrl(rawLabel) : '';
        const url = ownUrl || selectedClipLink.url;
        if (url && (!text || text.includes('시청자 클립에서 잡힌 위치') || text.includes('구조화된 참고 신호'))) {
          text = selectedClipLink.title || selectedTitleText || '시청자 클립 열기';
        }
        const row = { label, text, clipUid: rawLabel || selectedClipLink.uid, clipUrl: url };
        ['start_sec', 'sec', 'seek_sec', 'end_sec', 'end_seek_sec', 'video_no', 'source_video_no', 'vod_label', 'timecode'].forEach((key) => {
          if (item && item[key] != null && item[key] !== '') row[key] = item[key];
        });
        return canonicalSelectedSourceRow(row, event);
      }))
      .concat(nearbyEvents.filter((row) => row.id !== event.id).map((row) => canonicalSelectedSourceRow({
        label: row.vod_label ? `${row.vod_label} · ${optionLabel(row.kind)}` : optionLabel(row.kind),
        sec: row.start_sec,
        seek_sec: row.seek_sec,
        video_no: row.video_no,
        vod_label: row.vod_label,
        timecode: row.timecode,
        text: isTimeOnlyTitle(row.title, row.start_sec) || friendlyEventTitle(row) === selectedTitleText ? '' : friendlyEventTitle(row)
      }, event)))
      .filter((row) => row.text)
      .slice(0, 10);
    const evidenceRows = clusterEvidenceRows.concat(extraEvidenceRows);
    const summaryStatus = selectedSummaryStatus(event, nearbyEvents);
    const hasTimestampEvidence = nearbySubtitles.some((row) => row && row.text)
      || nearbyBuckets.some((row) => row && (Number(row.count || 0) > 0 || (row.samples || []).length));
    const guidanceHtml = hasTimestampEvidence
      ? ''
      : '<div class="viewer-editor-row reason">확인 가능한 자막·채팅 근거 없음</div>';
    const selectedColor = markerColor(event.kind);
    const kindLabel = selectedHighlightId ? 'Highlight' : optionLabel(event.kind);
    const titleMatchesKind = selectedTitleText === kindLabel;
    const chipRows = [timeChip(sec, event)];
    if (summaryStatus.label === kindLabel) {
      chipRows.push(`<span class="viewer-editor-chip ${summaryStatus.warning ? 'warning' : ''}">${esc(summaryStatus.label)}</span>`);
    } else if (summaryStatus.label === '요약에 포함됨' && (event.kind === 'timeline' || event.kind === 'existing_segments')) {
      chipRows.push(`<span class="viewer-editor-chip">${esc(summaryStatus.label)}</span>`);
    } else if (titleMatchesKind) {
      chipRows.push(`<span class="viewer-editor-chip ${summaryStatus.warning ? 'warning' : ''}">${esc(summaryStatus.label)}</span>`);
    } else {
      chipRows.push(`<span class="viewer-editor-chip">${esc(kindLabel)}</span>`);
      chipRows.push(`<span class="viewer-editor-chip ${summaryStatus.warning ? 'warning' : ''}">${esc(summaryStatus.label)}</span>`);
    }
    evidenceEl.innerHTML = `<div class="viewer-editor-selected-card" style="--viewer-marker-color:${esc(selectedColor)}">
      <span class="viewer-editor-selected-kicker">장면 찾기에서 선택한 항목</span>
      <div class="viewer-editor-selected-title">${esc(selectedTitleText)}</div>
      <div class="viewer-editor-selected-meta">
        ${chipRows.join('')}
      </div>
      ${selectedHighlightId ? highlightSourceSpanChips(event) + highlightPointChips(event) : ''}
    </div>
    ${renderEvidenceTargetPicker(evidenceTargets, sec)}
    ${renderContextSummary(nearbySubtitles, nearbyBuckets, event)}
    ${guidanceHtml}
    ${seekUrl(sec, event) ? `<div class="viewer-editor-row"><strong>원본 확인</strong><br><a href="${esc(seekUrl(sec, event))}" target="_blank" rel="noopener" style="color:var(--tc)">치지직에서 이 시각 열기</a></div>` : ''}
    <div class="viewer-editor-list">${evidenceRows.map(renderEvidenceRow).join('') || '<div class="viewer-editor-empty">근처 근거가 없습니다.</div>'}</div>`;
    evidenceEl.querySelectorAll('[data-evidence-target-index]').forEach((button) => {
      button.addEventListener('click', () => {
        const index = Number(button.getAttribute('data-evidence-target-index'));
        const target = evidenceTargets[index];
        if (!target || !target.row) return;
        syncSplitEditorSeek(Number(target.row.start_sec ?? target.row.sec), target.row, true);
        renderEvidence(target.row);
      });
    });
  }
  function renderViewerEditorToolsNow() {
    if (axisRendered) return;
    axisRendered = true;
    renderFilter();
    timelineZoom = fitTimelineZoom();
    syncTimelineZoomControls();
    renderAxis();
    scheduleTimelineAutoFit();
    installHierarchyPreviews();
    renderDetailedSummaryHierarchy();
    if (events.length) {
      const initialCluster = events[0].kind === 'viewer_clip'
        ? renderedAxisEvents.get(`cluster_${viewerClipClusterKey(events[0])}`)
        : null;
      renderEvidence(initialCluster || events[0]);
    } else {
      renderEvidence(null);
    }
  }
  evidenceEl.innerHTML = '<div class="viewer-editor-empty">장면 찾기에서 시각을 선택하면 근거가 여기에 표시됩니다.</div>';
  window.setTimeout(() => {
    document.querySelectorAll('.timeline[data-manager-outline-hierarchy="pending"]').forEach((timeline) => {
      timeline.dataset.managerOutlineHierarchy = 'error';
    });
  }, 2500);
  renderDetailedSummaryHierarchy();
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        renderViewerEditorToolsNow();
        observer.disconnect();
      }
    }, { rootMargin: '600px 0px' });
    observer.observe(document.getElementById('youtube-editor-tools'));
  } else {
    window.setTimeout(renderViewerEditorToolsNow, 400);
  }
  if (axisEl) {
    const handleZoomWheel = (event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      event.stopPropagation();
      useManualTimelineZoom(timelineZoom * (event.deltaY < 0 ? 1.14 : 1 / 1.14), event.clientX);
    };
    axisEl.addEventListener('wheel', handleZoomWheel, { passive: false });
    const sectionEl = document.getElementById('youtube-editor-tools');
    if (sectionEl) sectionEl.addEventListener('wheel', handleZoomWheel, { passive: false });
    if ('ResizeObserver' in window) {
      let lastAxisWidth = axisEl.clientWidth;
      const axisResizeObserver = new ResizeObserver(() => {
        const nextAxisWidth = axisEl.clientWidth;
        if (Math.abs(nextAxisWidth - lastAxisWidth) < 1) return;
        lastAxisWidth = nextAxisWidth;
        scheduleTimelineAutoFit();
      });
      axisResizeObserver.observe(axisEl);
    } else {
      window.addEventListener('resize', scheduleTimelineAutoFit);
    }
  }
  if (zoomRange) zoomRange.addEventListener('input', () => useManualTimelineZoom(Number(zoomRange.value || 100) / 100));
  if (densityRange) densityRange.addEventListener('input', () => {
    markerDensityLevel = Math.max(0, Math.min(10, Number(densityRange.value || 0)));
    renderAxis({ preserveScroll: true });
  });
  if (zoomOutBtn) zoomOutBtn.addEventListener('click', () => useManualTimelineZoom(timelineZoom / 1.25));
  if (zoomInBtn) zoomInBtn.addEventListener('click', () => useManualTimelineZoom(timelineZoom * 1.25));
  if (zoomResetBtn) zoomResetBtn.addEventListener('click', () => useManualTimelineZoom(1));
  }

  if (!dataSrc) {
    startViewerEditorTools(parseInlineData());
    return;
  }

  let started = false;
  const startOnce = async () => {
    if (started) return;
    started = true;
    loadMessage('장면 도구 데이터를 불러오는 중입니다. 요약 본문은 그대로 볼 수 있습니다.');
    try {
      const payload = await loadEditorToolsData();
      startViewerEditorTools(payload && typeof payload === 'object' ? payload : {});
    } catch (_err) {
      loadMessage('장면 도구 데이터를 불러오지 못했습니다. 요약 본문은 그대로 볼 수 있고, 페이지를 새로고침해 다시 시도할 수 있습니다.');
    }
  };
  startOnce();
}
function initViewerClipPlayers() {
  if (document.documentElement.dataset.viewerClipPlayersInitialized === '1') return;
  document.documentElement.dataset.viewerClipPlayersInitialized = '1';

  const closePlayer = (dialog, returnFocus) => {
    dialog.querySelector('iframe')?.remove();
    if (dialog.open) dialog.close();
    dialog.remove();
    if (returnFocus?.isConnected) returnFocus.focus();
  };

  document.addEventListener('click', (event) => {
      const button = event.target.closest('.user-clip-play[data-clip-id]');
      if (!button) return;
      const clipId = button.dataset.clipId || '';
      if (!clipId) return;
      document.querySelector('.user-clip-dialog')?.remove();

      const params = new URLSearchParams({
        parent: window.location.hostname || 'localhost',
        autoplay: '0',
        muted: '1',
      });
      const frame = document.createElement('iframe');
      frame.className = 'user-clip-dialog-frame';
      frame.src = `https://chzzk.naver.com/embed/clip/${encodeURIComponent(clipId)}?${params}`;
      frame.title = `${button.dataset.clipTitle || 'CHZZK 클립'} 플레이어`;
      frame.allow = 'autoplay; clipboard-write; web-share';
      frame.allowFullscreen = true;
      const dialog = document.createElement('dialog');
      dialog.className = 'user-clip-dialog';
      dialog.setAttribute('aria-label', frame.title);
      dialog.innerHTML = `<div class="user-clip-dialog-head"><strong></strong><button type="button" class="user-clip-dialog-close" aria-label="클립 영상 닫기">×</button></div><div class="user-clip-dialog-body"></div>`;
      dialog.querySelector('strong').textContent = button.dataset.clipTitle || 'CHZZK 클립';
      dialog.querySelector('.user-clip-dialog-body').appendChild(frame);
      document.body.appendChild(dialog);
      dialog.querySelector('.user-clip-dialog-close').addEventListener('click', () => closePlayer(dialog, button));
      dialog.addEventListener('cancel', (event) => {
        event.preventDefault();
        closePlayer(dialog, button);
      });
      dialog.addEventListener('click', (event) => {
        if (event.target === dialog) closePlayer(dialog, button);
      });
      dialog.showModal();
  });
}
function initPointStoryDisclosures() {
  document.querySelectorAll('details.point-evidence-panel').forEach((details) => {
    const summary = details.querySelector(':scope > summary');
    const title = String(summary && summary.querySelector('.standalone-point-disclosure-title')?.textContent || '주요 장면').trim();
    if (!summary || details.dataset.storyDisclosureReady === 'true') return;
    const syncLabel = () => summary.setAttribute('aria-label', `${title} Story ${details.open ? '접기' : '펼치기'}`);
    details.dataset.storyDisclosureReady = 'true';
    summary.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      details.open = !details.open;
    });
    details.addEventListener('toggle', syncLabel);
    syncLabel();
  });
}
function initViewerEditorRuntime() {
  initEditorCompanionMode();
  initEditorEntryState();
  initViewerEditorTools();
  initViewerClipPlayers();
  initPointStoryDisclosures();
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initViewerEditorRuntime);
} else {
  initViewerEditorRuntime();
}
