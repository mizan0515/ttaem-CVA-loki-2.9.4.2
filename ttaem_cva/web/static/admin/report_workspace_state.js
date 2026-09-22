(() => {
  'use strict';

  const clone = value => JSON.parse(JSON.stringify(value));
  const hmsPattern = /^([0-9]{1,3}):([0-5][0-9]):([0-5][0-9])$/;

  function seconds(value) {
    if (Number.isFinite(Number(value)) && !String(value).includes(':')) return Math.max(0, Number(value));
    const match = String(value || '').match(hmsPattern);
    return match ? Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) : NaN;
  }

  function hms(value) {
    const safe = Math.max(0, Math.round(Number(value) || 0));
    const hours = Math.floor(safe / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const secs = safe % 60;
    return [hours, minutes, secs].map(part => String(part).padStart(2, '0')).join(':');
  }

  function create(report) {
    const outline = clone(report.structured_outline || {ranges: [], points: [], candidates: []});
    outline.ranges = Array.isArray(outline.ranges) ? outline.ranges : [];
    outline.points = Array.isArray(outline.points) ? outline.points : [];
    outline.candidates = [];
    const packet = report.qg1_review_packet ? clone(report.qg1_review_packet) : null;
    const storyByRef = Object.fromEntries(((packet && packet.stories) || []).map(row => [String(row.story_ref || ''), row]));
    outline.points.forEach(point => {
      point.story_why_notable = String((storyByRef[String(point.story_ref || '')] || {}).why_notable || '');
    });
    const pointById = Object.fromEntries(outline.points.map(row => [String(row.id || ''), row]));
    const highlights = ((packet && packet.editorial_highlights) || []).map(row => ({
      ...clone(row),
      id: String(row.highlight_id || ''),
      point_labels: (row.point_refs || []).map(ref => pointById[String(ref)]).filter(Boolean).map(point => `${point.timestamp || ''} ${point.title || 'Point'}`),
    }));
    return {report: clone(report), outline, packet, highlights, activeType: 'D1', selection: null, dirty: false};
  }

  function rows(state, type = state.activeType) {
    if (type === 'D1' || type === 'D2') return state.outline.ranges.filter(row => row.level === type);
    if (type === 'Point') return state.outline.points;
    if (type === 'Highlight') return state.highlights;
    return [];
  }

  function itemStart(type, item, spanIndex = 0) {
    if (type === 'Point') return seconds(item.timestamp);
    if (type === 'Highlight') return Number(((item.source_spans || [])[spanIndex] || {}).start_sec);
    return seconds(item.start);
  }

  function itemEnd(type, item, spanIndex = 0) {
    if (type === 'Point') return itemStart(type, item, spanIndex);
    if (type === 'Highlight') return Number(((item.source_spans || [])[spanIndex] || {}).end_sec);
    return seconds(item.end);
  }

  function selected(state) {
    if (!state.selection) return null;
    return rows(state, state.selection.type).find(row => String(row.id || '') === state.selection.id) || null;
  }

  function update(state, patch) {
    const item = selected(state);
    if (!item) return;
    Object.assign(item, patch);
    state.dirty = true;
  }

  function updateSpan(state, index, patch) {
    const item = selected(state);
    if (!item || state.selection.type !== 'Highlight' || !(item.source_spans || [])[index]) return;
    Object.assign(item.source_spans[index], patch);
    state.dirty = true;
  }

  function duration(state) {
    const explicit = Number(state.outline.duration_sec);
    if (Number.isFinite(explicit) && explicit > 0) return explicit;
    const times = [];
    state.outline.ranges.forEach(row => times.push(seconds(row.end)));
    state.outline.points.forEach(row => times.push(seconds(row.timestamp)));
    state.highlights.forEach(row => (row.source_spans || []).forEach(span => times.push(Number(span.end_sec || 0))));
    return Math.max(1, ...times.filter(Number.isFinite));
  }

  function snapshot(state) {
    return clone({
      outline: state.outline,
      highlights: state.highlights,
      activeType: state.activeType,
      selection: state.selection,
      dirty: state.dirty,
    });
  }

  function restore(state, saved) {
    state.outline = clone(saved.outline);
    state.highlights = clone(saved.highlights);
    state.activeType = saved.activeType;
    state.selection = saved.selection ? clone(saved.selection) : null;
    state.dirty = !!saved.dirty;
  }

  function validateRange(state, item, start, end) {
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end > duration(state)) return '방송 범위를 벗어날 수 없습니다.';
    if (end <= start) return '끝 시각은 시작 시각보다 뒤여야 합니다.';
    const ranges = state.outline.ranges;
    if (item.level === 'D2') {
      const parent = ranges.find(row => String(row.id || '') === String(item.parent_id || ''));
      if (!parent || start < seconds(parent.start) || end > seconds(parent.end)) return 'D2는 소속 D1 안에 있어야 합니다.';
      const overlaps = ranges.some(row => row.level === 'D2' && String(row.parent_id || '') === String(item.parent_id || '') && String(row.id || '') !== String(item.id || '') && start < seconds(row.end) && end > seconds(row.start));
      if (overlaps) return '같은 D1의 D2끼리는 겹칠 수 없습니다.';
    } else {
      const missesChild = ranges.some(row => row.level === 'D2' && String(row.parent_id || '') === String(item.id || '') && (seconds(row.start) < start || seconds(row.end) > end));
      if (missesChild) return 'D1은 소속 D2를 모두 포함해야 합니다.';
      const overlaps = ranges.some(row => row.level === 'D1' && String(row.id || '') !== String(item.id || '') && start < seconds(row.end) && end > seconds(row.start));
      if (overlaps) return 'D1끼리는 겹칠 수 없습니다.';
    }
    return '';
  }

  function setTime(state, type, id, spanIndex, start, end = start) {
    const item = rows(state, type).find(row => String(row.id || '') === String(id || ''));
    if (!item) return {ok: false, error: '선택한 항목을 찾을 수 없습니다.'};
    const max = duration(state);
    const nextStart = Math.round(Number(start));
    const nextEnd = Math.round(Number(end));
    if (type === 'Point') {
      if (!Number.isFinite(nextStart) || nextStart < 0 || nextStart > max) return {ok: false, error: 'Point는 방송 범위를 벗어날 수 없습니다.'};
      item.timestamp = hms(nextStart);
    } else if (type === 'Highlight') {
      const spans = clone(item.source_spans || []);
      if (!spans[spanIndex]) return {ok: false, error: 'Highlight 구성 구간을 찾을 수 없습니다.'};
      spans[spanIndex].start_sec = nextStart;
      spans[spanIndex].end_sec = nextEnd;
      let previousEnd = -1;
      for (const span of spans) {
        const spanStart = Number(span.start_sec);
        const spanEnd = Number(span.end_sec);
        if (!Number.isFinite(spanStart) || !Number.isFinite(spanEnd) || spanStart < 0 || spanEnd > max) return {ok: false, error: 'Highlight는 방송 범위를 벗어날 수 없습니다.'};
        if (spanEnd <= spanStart) return {ok: false, error: 'Highlight의 끝은 시작보다 뒤여야 합니다.'};
        if (spanStart < previousEnd) return {ok: false, error: 'Highlight 구성 구간은 시간순이어야 하고 서로 겹칠 수 없습니다.'};
        previousEnd = spanEnd;
      }
      item.source_spans = spans;
    } else {
      const error = validateRange(state, item, nextStart, nextEnd);
      if (error) return {ok: false, error};
      item.start = hms(nextStart);
      item.end = hms(nextEnd);
    }
    state.dirty = true;
    return {ok: true, start: nextStart, end: nextEnd};
  }

  function newId(prefix) {
    return `${prefix}-new-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
  }

  function gaps(start, end, occupied) {
    const result = [];
    let cursor = start;
    [...occupied].sort((left, right) => left[0] - right[0]).forEach(([rowStart, rowEnd]) => {
      if (rowStart > cursor) result.push([cursor, rowStart]);
      cursor = Math.max(cursor, rowEnd);
    });
    if (cursor < end) result.push([cursor, end]);
    return result;
  }

  function chooseGap(candidates, atSeconds) {
    const at = Number(atSeconds);
    return candidates.find(([start, end]) => Number.isFinite(at) && at >= start && at < end) || candidates[0] || null;
  }

  function addItem(state, type, atSeconds = 0) {
    const ranges = state.outline.ranges;
    const max = duration(state);
    let item = null;
    if (type === 'D1') {
      const open = chooseGap(gaps(0, max, ranges.filter(row => row.level === 'D1').map(row => [seconds(row.start), seconds(row.end)])), atSeconds);
      if (!open) return {ok: false, error: '새 D1을 넣을 빈 시간이 없습니다.'};
      const start = Math.round(Math.max(open[0], Math.min(Number(atSeconds) || open[0], open[1] - 1)));
      const end = Math.floor(Math.min(start + 60, open[1]));
      if (end <= start) return {ok: false, error: '새 D1을 넣을 빈 시간이 너무 짧습니다.'};
      item = {id: newId('range'), level: 'D1', parent_id: '', start: hms(start), end: hms(end), title: '새 D1', content: ''};
      const nextD1Index = ranges.findIndex(row => row.level === 'D1' && seconds(row.start) > start);
      ranges.splice(nextD1Index < 0 ? ranges.length : nextD1Index, 0, item);
    } else if (type === 'D2') {
      const parent = state.selection && state.selection.type === 'D1' ? rows(state, 'D1').find(row => String(row.id || '') === state.selection.id) : null;
      if (!parent) return {ok: false, error: 'D2를 추가하려면 먼저 소속 D1을 선택해 주세요.'};
      const siblings = ranges.filter(row => row.level === 'D2' && String(row.parent_id || '') === String(parent.id || ''));
      const open = chooseGap(gaps(seconds(parent.start), seconds(parent.end), siblings.map(row => [seconds(row.start), seconds(row.end)])), atSeconds);
      if (!open) return {ok: false, error: '선택한 D1 안에 새 D2를 넣을 빈 시간이 없습니다.'};
      const start = Math.round(Math.max(open[0], Math.min(Number(atSeconds) || open[0], open[1] - 1)));
      const end = Math.floor(Math.min(start + 60, open[1]));
      if (end <= start) return {ok: false, error: '새 D2를 넣을 빈 시간이 너무 짧습니다.'};
      item = {id: newId('range'), level: 'D2', parent_id: parent.id, start: hms(start), end: hms(end), title: '새 D2', content: ''};
      const parentIndex = ranges.indexOf(parent);
      const siblingIndexes = siblings.map(row => ranges.indexOf(row));
      const nextSibling = siblings.find(row => seconds(row.start) > start);
      const insertionIndex = nextSibling
        ? ranges.indexOf(nextSibling)
        : Math.max(parentIndex + 1, siblingIndexes.length ? Math.max(...siblingIndexes) + 1 : parentIndex + 1);
      ranges.splice(insertionIndex, 0, item);
    } else if (type === 'Point') {
      const current = selected(state);
      const fallback = state.selection && current ? itemStart(state.selection.type, current, Number(state.selection.spanIndex || 0)) : 0;
      const timestamp = Math.max(0, Math.min(max, Number.isFinite(Number(atSeconds)) ? Number(atSeconds) : fallback));
      const storyRef = state.packet ? newId('story') : '';
      item = {id: newId('point'), timestamp: hms(timestamp), title: '새 Point', content: '', story_why_notable: '', ...(storyRef ? {story_ref: storyRef} : {})};
      state.outline.points.push(item);
    } else {
      return {ok: false, error: 'Highlight는 승인된 편집 결과에서만 가져올 수 있습니다.'};
    }
    state.activeType = type;
    state.selection = {type, id: String(item.id || ''), spanIndex: 0};
    state.dirty = true;
    return {ok: true, item};
  }

  function deleteSelected(state) {
    const item = selected(state);
    const selection = state.selection;
    if (!item || !selection) return {ok: false, error: '삭제할 항목을 먼저 선택해 주세요.'};
    if (selection.type === 'Highlight') return {ok: false, error: 'Highlight는 승인된 편집 결과에서 관리합니다.'};
    if (selection.type === 'D1' && state.outline.ranges.some(row => row.level === 'D2' && String(row.parent_id || '') === String(item.id || ''))) return {ok: false, error: 'D2가 있는 D1은 삭제할 수 없습니다. D2를 먼저 삭제해 주세요.'};
    if (selection.type === 'Point' && state.highlights.some(highlight => (highlight.point_refs || []).map(String).includes(String(item.id)))) return {ok: false, error: '이 Point는 Highlight가 사용 중이어서 삭제할 수 없습니다.'};
    const key = selection.type === 'Point' ? 'points' : 'ranges';
    state.outline[key] = state.outline[key].filter(row => String(row.id || '') !== selection.id);
    state.selection = null;
    state.dirty = true;
    return {ok: true, label: selection.type};
  }

  function serialize(state) {
    const outline = clone(state.outline);
    delete outline.highlights;
    outline.candidates = [];
    (outline.points || []).forEach(point => { delete point.story_why_notable; });
    if (!state.packet) return {outline, editorial_review_packet: null};
    const packet = clone(state.packet);
    const originalPointById = Object.fromEntries(((packet.broadcast_map || {}).points || []).map(row => [String(row.id || ''), row]));
    const originalStoryByRef = Object.fromEntries((packet.stories || []).map(row => [String(row.story_ref || ''), row]));
    const points = state.outline.points.map(point => {
      const original = originalPointById[String(point.id || '')] || {};
      const projected = {...original, ...clone(point)};
      delete projected.story_why_notable;
      return projected;
    });
    packet.broadcast_map = {...(packet.broadcast_map || {}), points};
    packet.stories = points.map(point => {
      const uiPoint = state.outline.points.find(row => String(row.id || '') === String(point.id || '')) || {};
      const original = originalStoryByRef[String(point.story_ref || '')] || {};
      return {...original, story_ref: String(point.story_ref || ''), title: String(original.title || point.title || 'Point 근거'), why_notable: String(uiPoint.story_why_notable || ''), roles: original.roles || {}, evidence_refs: original.evidence_refs || []};
    });
    const byId = Object.fromEntries(state.highlights.map(row => [String(row.id || ''), row]));
    packet.editorial_highlights = (packet.editorial_highlights || []).map(row => {
      const edited = byId[String(row.highlight_id || '')];
      return edited ? {...row, title: edited.title, story_summary: edited.story_summary, source_spans: clone(edited.source_spans || [])} : row;
    });
    return {outline, editorial_review_packet: packet};
  }

  window.ReportWorkspaceState = {
    create, rows, selected, update, updateSpan, duration, snapshot, restore,
    setTime, addItem, deleteSelected, serialize, seconds, hms, itemStart, itemEnd,
  };
})();
