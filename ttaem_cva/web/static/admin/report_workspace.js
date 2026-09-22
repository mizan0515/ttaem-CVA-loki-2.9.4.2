(() => {
  'use strict';

  const S = window.ReportWorkspaceState;
  const params = new URLSearchParams(location.search);
  const base = String(params.get('base') || '').trim();
  const el = id => document.getElementById(id);
  let state = null;
  let player = null;
  let timePreviewFrame = 0;
  let pendingPreviewSeek = null;
  const history = new window.ReportWorkspaceHistory(20);
  const timeline = new window.ReportWorkspaceTimeline(el('workspaceTimeline'), {
    getModel: () => state,
    onSelect: (type, id, spanIndex, seek) => select(type, id, spanIndex, seek),
    onSeek: seconds => { if (player) player.seek(seconds); },
    onChange: (type, id, spanIndex, start, end, label) => changeTime(type, id, spanIndex, start, end, label),
  });

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  }

  function confirmLocal(message) {
    return new Promise(resolve => {
      const dialog = document.createElement('dialog');
      const label = document.createElement('p');
      label.textContent = message;
      dialog.setAttribute('aria-label', '로컬 변경 확인');
      const cancel = document.createElement('button');
      cancel.textContent = '취소';
      const accept = document.createElement('button');
      accept.textContent = '확인하고 적용';
      const finish = value => { dialog.close(); dialog.remove(); resolve(value); };
      cancel.addEventListener('click', () => finish(false));
      accept.addEventListener('click', () => finish(true));
      dialog.addEventListener('cancel', event => { event.preventDefault(); finish(false); });
      dialog.append(label, cancel, accept);
      document.body.append(dialog);
      dialog.showModal();
      cancel.focus();
    });
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {headers: {'Content-Type': 'application/json', 'X-CVA-Local':'1'}, ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `요청 실패 (${response.status})`);
      error.code = data.code || '';
      throw error;
    }
    return data;
  }

  function setStatus(message, kind = '') {
    el('status').textContent = message;
    el('status').dataset.kind = kind;
  }

  function timeLabel(type, item) {
    if (type === 'Point') return item.timestamp || '시각 없음';
    if (type === 'Highlight') {
      const spans = item.source_spans || [];
      if (!spans.length) return '구간 없음';
      return spans.map(span => `${S.hms(span.start_sec)}–${S.hms(span.end_sec)}`).join(' · ');
    }
    return `${item.start || '—'}–${item.end || '—'}`;
  }

  function select(type, id, spanIndex = 0, seek = true) {
    state.activeType = type;
    state.selection = {type, id: String(id), spanIndex};
    renderTabs();
    renderList();
    renderEditor();
    timeline.render();
    const item = S.selected(state);
    if (!item) return;
    el('selectedTitle').textContent = item.title || type;
    el('selectedTime').textContent = `${type} · ${timeLabel(type, item)}`;
    if (seek && player) player.seek(S.itemStart(type, item, spanIndex));
  }

  function renderTabs() {
    document.querySelectorAll('#typeTabs [data-type]').forEach(button => {
      const active = button.dataset.type === state.activeType;
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }

  function renderList() {
    const rows = S.rows(state);
    el('sceneCount').textContent = String(rows.length);
    el('sceneList').innerHTML = rows.map(item => {
      const active = state.selection && state.selection.type === state.activeType && state.selection.id === String(item.id || '');
      const spans = state.activeType === 'Highlight' ? (item.source_spans || []) : [];
      const spanButtons = spans.length > 1 ? `<div class="span-buttons" aria-label="Highlight 구성 구간">${spans.map((span, index) => `<button type="button" data-span="${index}">${index + 1}. ${S.hms(span.start_sec)}–${S.hms(span.end_sec)}</button>`).join('')}</div>` : '';
      return `<article class="scene-card${active ? ' active' : ''}" data-id="${escapeHtml(item.id || '')}" tabindex="0">
        <div class="scene-card-top"><span>${escapeHtml(state.activeType)}</span><time>${escapeHtml(timeLabel(state.activeType, item))}</time></div>
        <strong>${escapeHtml(item.title || '제목 없음')}</strong>
        <p>${escapeHtml(item.content || item.story_summary || item.story_why_notable || '')}</p>${spanButtons}
      </article>`;
    }).join('') || '<div class="empty-list">이 유형의 항목이 없습니다.</div>';
    el('sceneList').querySelectorAll('.scene-card').forEach(card => {
      const activate = event => {
        if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
        if (event.type === 'keydown') event.preventDefault();
        const span = event.target.closest('[data-span]');
        select(state.activeType, card.dataset.id, span ? Number(span.dataset.span) : 0);
      };
      card.addEventListener('click', activate);
      card.addEventListener('keydown', activate);
    });
  }

  function field(label, name, value, type = 'text', help = '') {
    return `<label><span>${escapeHtml(label)}</span><input name="${name}" type="${type}" value="${escapeHtml(value || '')}" ${type === 'text' && name !== 'title' ? 'pattern="[0-9]{2,3}:[0-5][0-9]:[0-5][0-9]"' : ''}>${help ? `<small>${escapeHtml(help)}</small>` : ''}</label>`;
  }

  function timeField(label, name, value) {
    const inputId = `time-${name.replace(/[^a-z0-9_-]/gi, '-')}`;
    return `<div class="time-field" data-time-field data-name="${escapeHtml(name)}" data-label="${escapeHtml(label)}">
      <div class="time-field-heading"><label for="${inputId}">${escapeHtml(label)}</label><small>클릭: 직접 입력</small></div>
      <button class="time-scrub-display" type="button" data-time-display-button aria-controls="${inputId}" aria-keyshortcuts="ArrowLeft ArrowRight Shift+ArrowLeft Shift+ArrowRight">
        <span class="time-scrub-grip" aria-hidden="true"></span>
        <span class="time-scrub-content"><strong class="time-scrub-value" data-time-value>${escapeHtml(value || '')}</strong><small>잡고 좌우로 끌기</small></span>
        <span class="time-scrub-icon" aria-hidden="true">↔</span><span class="time-scrub-delta" data-time-delta hidden></span>
      </button>
      <input id="${inputId}" name="${escapeHtml(name)}" type="text" value="${escapeHtml(value || '')}" pattern="[0-9]{2,3}:[0-5][0-9]:[0-5][0-9]" inputmode="numeric" data-time-input hidden>
    </div>`;
  }

  function timeEditGuide() {
    return `<p class="time-edit-guide"><span aria-hidden="true">↔</span><span><strong>시간값을 잡고 좌우로 끌어 조정</strong><small>클릭하면 직접 입력 · Shift를 누르면 10초씩 이동</small></span></p>`;
  }

  function renderEditor() {
    const item = S.selected(state);
    el('editorEmpty').hidden = !!item;
    el('editorFields').hidden = !item;
    el('typeBadge').textContent = item ? state.selection.type : '—';
    el('deleteItemButton').hidden = !item || state.selection.type === 'Highlight';
    if (!item) return;
    const type = state.selection.type;
    let html = field('제목', 'title', item.title);
    if (type === 'D1' || type === 'D2') {
      html += `${timeEditGuide()}<div class="time-grid">${timeField('시작', 'start', item.start)}${timeField('끝', 'end', item.end)}</div>`;
      html += `<label><span>장면 설명</span><textarea name="content" rows="5">${escapeHtml(item.content || '')}</textarea></label>`;
      if (type === 'D2') html += `<p class="data-note">상위 D1 연결 <code>${escapeHtml(item.parent_id || '없음')}</code>은 그대로 유지합니다.</p>`;
    } else if (type === 'Point') {
      html += timeEditGuide() + timeField('중요 내용 시작 시각', 'timestamp', item.timestamp);
      html += '<p class="data-note">Point는 시작·끝 범위가 아니라 정확한 한 시각입니다.</p>';
      html += `<label><span>왜 중요한가</span><textarea name="story_why_notable" rows="5">${escapeHtml(item.story_why_notable || '')}</textarea></label>`;
    } else {
      html += `<label><span>Highlight 설명</span><textarea name="story_summary" rows="5">${escapeHtml(item.story_summary || '')}</textarea></label>`;
      html += `<fieldset><legend>구성 구간 ${item.source_spans.length}개</legend>${timeEditGuide()}${item.source_spans.map((span, index) => `<div class="span-editor" data-span-index="${index}"><button type="button" data-seek-span="${index}">▶ ${index + 1}번 보기</button>${timeField('시작', `span_${index}_start`, S.hms(span.start_sec))}${timeField('끝', `span_${index}_end`, S.hms(span.end_sec))}</div>`).join('')}</fieldset>`;
      html += '<p class="data-note">Highlight 하나는 서로 떨어진 여러 구간을 가질 수 있습니다. 각 구간을 독립적으로 유지합니다.</p>';
    }
    el('editorFields').innerHTML = html;
    bindEditor(item, type);
  }

  function bindEditor(item, type) {
    const fields = el('editorFields');
    fields.querySelectorAll('input:not([data-time-input]), textarea').forEach(control => {
      let beforeEdit = null;
      control.addEventListener('focus', () => { beforeEdit = S.snapshot(state); });
      control.addEventListener('input', () => {
        const spanMatch = control.name.match(/^span_(\d+)_(start|end)$/);
        if (spanMatch) {
          const parsed = S.seconds(control.value);
          if (Number.isFinite(parsed)) S.updateSpan(state, Number(spanMatch[1]), {[`${spanMatch[2]}_sec`]: parsed});
        } else {
          S.update(state, {[control.name]: control.value});
        }
        el('saveButton').disabled = false;
        setStatus('수정 중입니다. 아직 저장되지 않았습니다.', 'dirty');
        renderList();
        if (control.validity.valid) timeline.render();
      });
      control.addEventListener('change', () => {
        if (!beforeEdit || JSON.stringify(beforeEdit) === JSON.stringify(S.snapshot(state))) return;
        const label = control.closest('label')?.querySelector('span')?.textContent?.trim() || '내용';
        history.commit(beforeEdit, `${label} 수정`);
        beforeEdit = null;
        updateHistoryControls();
      });
    });
    fields.querySelectorAll('[data-time-field]').forEach(root => bindTimeScrubber(root, item, type));
    fields.querySelectorAll('[data-seek-span]').forEach(button => button.addEventListener('click', () => {
      const index = Number(button.dataset.seekSpan);
      state.selection.spanIndex = index;
      player.seek(S.itemStart(type, item, index));
    }));
  }

  function timeFieldTarget(name, type) {
    const spanMatch = name.match(/^span_(\d+)_(start|end)$/);
    if (spanMatch) return {spanIndex: Number(spanMatch[1]), edge: spanMatch[2]};
    if (type === 'Point') return {spanIndex: 0, edge: 'point'};
    return {spanIndex: 0, edge: name === 'end' ? 'end' : 'start'};
  }

  function selectedTimeValue(type, item, target) {
    return target.edge === 'end' ? S.itemEnd(type, item, target.spanIndex) : S.itemStart(type, item, target.spanIndex);
  }

  function updateSelectedSummary() {
    const selected = S.selected(state);
    el('selectedTitle').textContent = selected ? (selected.title || state.activeType) : '장면을 선택하세요';
    el('selectedTime').textContent = selected
      ? `${state.activeType} · ${timeLabel(state.activeType, selected)}`
      : 'D1 · D2 · Point · Highlight를 같은 데이터로 탐색합니다.';
  }

  function cancelTimePreviewFrame(flushSeek = false) {
    const seek = pendingPreviewSeek;
    if (timePreviewFrame) cancelAnimationFrame(timePreviewFrame);
    timePreviewFrame = 0;
    pendingPreviewSeek = null;
    if (flushSeek && player && Number.isFinite(seek)) player.seek(seek);
  }

  function scheduleTimePreview(seconds) {
    pendingPreviewSeek = seconds;
    if (timePreviewFrame) return;
    timePreviewFrame = requestAnimationFrame(() => {
      timePreviewFrame = 0;
      const seek = pendingPreviewSeek;
      pendingPreviewSeek = null;
      renderList();
      timeline.render();
      updateSelectedSummary();
      if (player && Number.isFinite(seek)) player.seek(seek);
    });
  }

  function previewTime(type, id, target, nextValue) {
    const current = S.rows(state, type).find(row => String(row.id || '') === String(id || ''));
    if (!current) return {ok: false, error: '선택한 항목을 찾을 수 없습니다.'};
    const currentStart = S.itemStart(type, current, target.spanIndex);
    const currentEnd = S.itemEnd(type, current, target.spanIndex);
    const roundedValue = Math.round(Number(nextValue));
    const currentValue = target.edge === 'end' ? currentEnd : currentStart;
    if (roundedValue === currentValue) return {ok: true, value: currentValue};
    const start = target.edge === 'end' ? currentStart : roundedValue;
    const end = target.edge === 'start' ? currentEnd : roundedValue;
    const result = S.setTime(state, type, id, target.spanIndex, start, end);
    if (!result.ok) {
      setStatus(result.error, 'error');
      return result;
    }
    const value = target.edge === 'end' ? result.end : result.start;
    el('saveButton').disabled = false;
    setStatus(`${type} ${target.edge === 'end' ? '끝' : '시작'} 시각을 조정 중입니다. 아직 저장되지 않았습니다.`, 'dirty');
    scheduleTimePreview(value);
    return {...result, value};
  }

  function commitTimeEdit(before, label) {
    cancelTimePreviewFrame(true);
    const changed = JSON.stringify(before) !== JSON.stringify(S.snapshot(state));
    if (changed) history.commit(before, label);
    renderAll();
    if (changed) setStatus(`${label}을 반영했습니다. 저장하면 로컬 수정본에 기록됩니다.`, 'dirty');
  }

  function cancelTimeEdit(before, announce = true) {
    cancelTimePreviewFrame();
    S.restore(state, before);
    renderAll();
    if (announce) setStatus('시간 조정을 취소했습니다.', state.dirty ? 'dirty' : 'ok');
  }

  function bindTimeScrubber(root, item, type) {
    const target = timeFieldTarget(root.dataset.name, type);
    const id = String(item.id || '');
    const label = `${type} ${root.dataset.label} 시각 조정`;
    new window.ReportWorkspaceTimeScrubber(root, {
      begin: () => S.snapshot(state),
      getValue: () => selectedTimeValue(type, item, target),
      format: S.hms,
      parse: S.seconds,
      preview: value => previewTime(type, id, target, value),
      commit: before => commitTimeEdit(before, label),
      cancel: (before, announce = true) => cancelTimeEdit(before, announce),
      invalid: () => setStatus('시간은 시:분:초 형식이며 현재 장면의 허용 범위 안이어야 합니다.', 'error'),
    });
  }

  function renderHistory() {
    const revisions = state.report.manager_outline_revisions || [];
    el('historyCount').textContent = String(revisions.length);
    el('historyList').innerHTML = revisions.map(row => `<div><span><strong>${escapeHtml(row.revision_id || '')}</strong><small>${escapeHtml(row.created_at || row.saved_at || '')}</small></span><button type="button" data-restore="${escapeHtml(row.revision_id || '')}">이 버전 복원</button></div>`).join('') || '<p>아직 이전 저장본이 없습니다.</p>';
    el('historyList').querySelectorAll('[data-restore]').forEach(button => button.addEventListener('click', () => restore(button.dataset.restore)));
  }

  function adjacent(delta) {
    const rows = S.rows(state);
    if (!rows.length) return;
    const current = state.selection ? rows.findIndex(row => String(row.id || '') === state.selection.id) : -1;
    const next = rows[(current + delta + rows.length) % rows.length];
    select(state.activeType, next.id);
  }

  function updateHistoryControls() {
    el('undoButton').disabled = !history.canUndo;
    el('redoButton').disabled = !history.canRedo;
    el('undoButton').title = history.canUndo ? `${history.undoLabel} 되돌리기 (Ctrl+Z)` : '되돌릴 작업이 없습니다.';
    el('redoButton').title = history.canRedo ? `${history.redoLabel} 다시 실행 (Ctrl+Y)` : '다시 실행할 작업이 없습니다.';
  }

  function changeTime(type, id, spanIndex, start, end, label) {
    const before = S.snapshot(state);
    const result = S.setTime(state, type, id, spanIndex, start, end);
    if (!result.ok) {
      setStatus(result.error, 'error');
      return result;
    }
    history.commit(before, label || `${type} 시간 조정`);
    renderAll();
    setStatus(`${type} 시간을 조정했습니다. 저장하면 로컬 수정본에 반영됩니다.`, 'dirty');
    return result;
  }

  function addItem(type) {
    const before = S.snapshot(state);
    const currentTime = player ? Number(el('video').currentTime) || 0 : 0;
    const result = S.addItem(state, type, currentTime);
    if (!result.ok) {
      setStatus(result.error, 'error');
      return;
    }
    history.commit(before, `${type} 추가`);
    el('addItemMenu').open = false;
    renderAll();
    setStatus(`${type}을(를) 현재 재생 위치에 추가했습니다. 내용을 확인한 뒤 저장해 주세요.`, 'dirty');
    el('editorFields').querySelector('[name="title"]')?.focus();
  }

  async function deleteItem() {
    const item = S.selected(state);
    if (!item) return;
    const label = state.selection.type;
    if (!await confirmLocal(`${label} “${item.title || '제목 없음'}”을 로컬 수정본에서 제거할까요? 저장 전에는 되돌릴 수 있습니다.`)) return;
    const before = S.snapshot(state);
    const result = S.deleteSelected(state);
    if (!result.ok) {
      setStatus(result.error, 'error');
      return;
    }
    history.commit(before, `${label} 제거`);
    renderAll();
    setStatus(`${label}을(를) 제거했습니다. 저장하기 전까지 공개 결과에는 영향이 없습니다.`, 'dirty');
  }

  function undo() {
    const entry = history.undo(S.snapshot(state));
    if (!entry) return;
    S.restore(state, entry.snapshot);
    renderAll();
    setStatus(`${entry.label} 작업을 되돌렸습니다.`, state.dirty ? 'dirty' : 'ok');
  }

  function redo() {
    const entry = history.redo(S.snapshot(state));
    if (!entry) return;
    S.restore(state, entry.snapshot);
    renderAll();
    setStatus(`${entry.label} 작업을 다시 실행했습니다.`, state.dirty ? 'dirty' : 'ok');
  }

  async function save() {
    if (!state || !state.dirty) return;
    if (!el('editorForm').reportValidity()) {
      setStatus('시간은 시:분:초 형식으로 입력해 주세요. 예: 00:20:39', 'error');
      return;
    }
    const payload = S.serialize(state);
    el('saveButton').disabled = true;
    setStatus('구조를 검증하고 로컬 수정본으로 저장하는 중…');
    try {
      const data = await api('/api/manager-outline/save', {method: 'POST', body: JSON.stringify({
        expected_revision: state.report.revision,
        video_no: state.report.video_no,
        base: state.report.base,
        manager_markdown: state.report.manager_markdown,
        outline: payload.outline,
        editorial_review_packet: payload.editorial_review_packet,
      })});
      const selection = state.selection;
      state = S.create(data);
      history.clear();
      state.selection = selection;
      if (selection) state.activeType = selection.type;
      renderAll();
      setStatus('로컬 수정본을 저장했습니다. 공개 사이트는 바뀌지 않았습니다.', 'ok');
    } catch (error) {
      state.dirty = true;
      el('saveButton').disabled = false;
      setStatus(error.message || '저장하지 못했습니다. 입력은 화면에 남아 있습니다.', 'error');
    }
  }

  async function restore(revisionId) {
    if (!await confirmLocal('선택한 이전 버전을 새 로컬 수정본으로 복원할까요? 공개 사이트는 바뀌지 않습니다.')) return;
    setStatus('이전 버전을 복원하는 중…');
    try {
      const data = await api('/api/manager-outline/restore', {method: 'POST', body: JSON.stringify({expected_revision: state.report.revision,
        video_no: state.report.video_no, base: state.report.base, revision_id: revisionId})});
      state = S.create(data);
      history.clear();
      renderAll();
      setStatus('이전 버전을 새 로컬 수정본으로 복원했습니다.', 'ok');
    } catch (error) {
      setStatus(error.message || '이전 버전을 복원하지 못했습니다.', 'error');
    }
  }

  function renderAll() {
    renderTabs();
    renderList();
    renderEditor();
    renderHistory();
    updateSelectedSummary();
    el('saveButton').disabled = !state.dirty;
    el('generatedButton').hidden = !state.report.generated_differs;
    updateHistoryControls();
    document.querySelectorAll('[data-add-type="D2"]').forEach(button => {
      button.disabled = !(state.selection && state.selection.type === 'D1');
      button.title = button.disabled ? '먼저 소속 D1을 선택해 주세요.' : '';
    });
    timeline.render();
  }

  async function loadPlayback(videoNo) {
    player = new window.ReportPlayer(el('video'), (kind, message) => {
      el('playbackState').textContent = message;
      el('videoEmpty').hidden = kind !== 'error';
    });
    try {
      const data = await api(`/api/report-playback?video_no=${encodeURIComponent(videoNo)}&quality=720`);
      el('originalLink').href = data.playback.original_url;
      await player.load(data.playback.stream_url);
      el('pipButton').disabled = !(document.pictureInPictureEnabled && typeof el('video').requestPictureInPicture === 'function');
    } catch (error) {
      el('playbackState').textContent = error.code === 'auth_required' ? '로그인 필요' : '원본으로 확인';
      el('videoEmpty').hidden = false;
      el('originalLink').href = `https://chzzk.naver.com/video/${encodeURIComponent(videoNo)}`;
    }
  }

  async function load() {
    if (!base) throw new Error('base 파라미터가 없습니다. 리포트 목록에서 다시 여세요.');
    const report = await api(`/api/report?base=${encodeURIComponent(base)}`);
    if (report.kind === 'bundle' || !report.structured_outline) throw new Error('이 화면은 방송 구조가 있는 단일 VOD 리포트에서 사용할 수 있습니다.');
    state = S.create(report);
    const legacy = `/reports?publish=${encodeURIComponent(report.base)}`;
    el('legacyLink').href = legacy;
    el('advancedLink').href = legacy;
    el('reportTitle').textContent = report.title || `VOD ${report.video_no}`;
    el('reportMeta').textContent = `VOD ${report.video_no} · 현재 관리자 수정본 · 저장과 공개 분리`;
    const firstType = ['D1', 'D2', 'Point', 'Highlight'].find(type => S.rows(state, type).length);
    const first = firstType ? S.rows(state, firstType)[0] : null;
    if (first) {
      state.activeType = firstType;
      state.selection = {type: firstType, id: String(first.id || ''), spanIndex: 0};
    }
    renderAll();
    setStatus('관리자 수정본을 불러왔습니다. 장면을 누르면 영상이 해당 시각으로 이동합니다.', 'ok');
    loadPlayback(report.video_no);
  }

  document.querySelectorAll('#typeTabs [data-type]').forEach(button => button.addEventListener('click', () => {
    state.activeType = button.dataset.type;
    const first = S.rows(state)[0];
    state.selection = first ? {type: state.activeType, id: String(first.id || ''), spanIndex: 0} : null;
    renderAll();
  }));
  el('previousButton').addEventListener('click', () => adjacent(-1));
  el('nextButton').addEventListener('click', () => adjacent(1));
  el('saveButton').addEventListener('click', save);
  document.querySelectorAll('[data-add-type]').forEach(button => button.addEventListener('click', () => addItem(button.dataset.addType)));
  el('undoButton').addEventListener('click', undo);
  el('redoButton').addEventListener('click', redo);
  el('deleteItemButton').addEventListener('click', deleteItem);
  el('pipButton').addEventListener('click', () => player.pictureInPicture().catch(error => setStatus(error.message, 'error')));
  el('previewButton').addEventListener('click', () => {
    el('adoptGeneratedButton').hidden = true;
    el('previewTitle').textContent = '저장된 공개 모양 미리보기';
    el('previewNote').textContent = '현재 저장본 기준이며 자동 공개되지 않습니다.';
    el('previewFrame').src = `/api/saved-html?base=${encodeURIComponent(base)}&t=${Date.now()}`;
    el('previewDialog').showModal();
  });
  el('evidenceButton').addEventListener('click', () => {
    el('adoptGeneratedButton').hidden = true;
    el('previewTitle').textContent = '로컬 분석 근거';
    el('previewNote').textContent = '현재 수집·분석 자료 기준입니다. 공개 내보내기에 포함되지 않습니다.';
    el('previewFrame').src = `/preview/${encodeURIComponent(base)}/evidence.html`;
    el('previewDialog').showModal();
  });
  el('generatedButton').addEventListener('click', () => {
    el('adoptGeneratedButton').hidden = false;
    el('previewTitle').textContent = '새로 생성한 요약 미리보기';
    el('previewNote').textContent = '적용하면 기존 저장본은 이력에 보존됩니다. 외부에는 공개되지 않습니다.';
    el('previewFrame').src = `/preview/${encodeURIComponent(base)}/generated.html?revision=${encodeURIComponent(state.report.generated_revision)}`;
    el('previewDialog').showModal();
  });
  el('adoptGeneratedButton').addEventListener('click', async () => {
    if (state.dirty) {
      setStatus('편집 중인 내용이 있습니다. 먼저 수정본을 저장한 뒤 새 생성본을 적용하세요.', 'error');
      el('previewDialog').close();
      return;
    }
    if (!await confirmLocal('확인한 생성본을 현재 검토본으로 적용할까요? 기존 저장본은 이력에서 복원할 수 있습니다.')) return;
    try {
      const data = await api('/api/manager-outline/adopt-generated', {method:'POST', body:JSON.stringify({
        base, expected_revision:state.report.revision, generated_revision:state.report.generated_revision,
      })});
      state = S.create(data);
      history.clear();
      renderAll();
      el('previewDialog').close();
      setStatus('새 생성본을 검토본으로 적용했습니다. 기존 저장본은 이력에 보존했습니다.', 'ok');
    } catch (error) { setStatus(error.message, 'error'); el('previewDialog').close(); }
  });
  el('closePreviewButton').addEventListener('click', () => el('previewDialog').close());
  el('video').addEventListener('timeupdate', () => timeline.setPlayhead(el('video').currentTime));
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.altKey || !(event.ctrlKey || event.metaKey)) return;
    if (event.target.closest('input, textarea, [contenteditable="true"]')) return;
    const key = event.key.toLowerCase();
    const wantsUndo = key === 'z' && !event.shiftKey;
    const wantsRedo = key === 'y' || (key === 'z' && event.shiftKey);
    if (!wantsUndo && !wantsRedo) return;
    event.preventDefault();
    if (wantsRedo) redo();
    else undo();
  });
  window.addEventListener('beforeunload', event => {
    if (!state || !state.dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });

  load().catch(error => setStatus(error.message || '리포트를 불러오지 못했습니다.', 'error'));
})();
