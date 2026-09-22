(() => {
  'use strict';

  const S = window.ReportWorkspaceState;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  }

  class ReportWorkspaceTimeline {
    constructor(root, options) {
      this.root = root;
      this.options = options;
      this.zoom = 1;
      this.playhead = 0;
      this.viewport = root.querySelector('[data-timeline-viewport]');
      this.canvas = root.querySelector('[data-timeline-canvas]');
      this.status = root.querySelector('#timelineStatus');
      this.defaultStatus = this.status.textContent;
      this.zoomInput = root.querySelector('[data-timeline-zoom]');
      this.zoomReadout = root.querySelector('[data-timeline-zoom-readout]');
      root.querySelector('[data-timeline-fit]').addEventListener('click', () => this.setZoom(1));
      this.zoomInput.addEventListener('input', () => this.setZoom(Number(this.zoomInput.value)));
      this.viewport.addEventListener('wheel', event => this.onWheel(event), {passive: false});
      this.canvas.addEventListener('click', event => this.onClick(event));
      this.canvas.addEventListener('keydown', event => this.onKeyDown(event));
      this.canvas.addEventListener('pointerdown', event => this.onPointerDown(event));
    }

    setZoom(value, anchorClientX = null) {
      const previousWidth = Math.max(1, this.canvas.offsetWidth);
      const viewportWidth = Math.max(1, this.viewport.clientWidth);
      const viewportRect = this.viewport.getBoundingClientRect();
      const anchorX = Number.isFinite(anchorClientX)
        ? Math.max(0, Math.min(viewportWidth, anchorClientX - viewportRect.left))
        : viewportWidth / 2;
      const anchor = (this.viewport.scrollLeft + anchorX) / previousWidth;
      this.zoom = Math.max(1, Math.min(8, Number(value) || 1));
      this.zoomInput.value = String(this.zoom);
      this.zoomReadout.textContent = `${this.zoom}×`;
      this.canvas.style.width = `${this.zoom * 100}%`;
      this.viewport.scrollLeft = Math.max(0, anchor * this.canvas.offsetWidth - anchorX);
      this.updateHandleLayout();
    }

    onWheel(event) {
      if (!event.ctrlKey || !event.deltaY) return;
      event.preventDefault();
      const direction = event.deltaY < 0 ? 1 : -1;
      this.setZoom(this.zoom + direction * .5, event.clientX);
    }

    setPlayhead(seconds) {
      this.playhead = Math.max(0, Number(seconds) || 0);
      const line = this.canvas.querySelector('[data-timeline-playhead]');
      const model = this.options.getModel();
      if (line && model) {
        const ratio = Math.min(1, this.playhead / S.duration(model));
        line.style.left = `calc(84px + ${ratio * 100}% - ${ratio * 84}px)`;
      }
    }

    itemMarkup(type, item, spanIndex = 0, rowIndex = 0) {
      const model = this.options.getModel();
      const duration = S.duration(model);
      const start = S.itemStart(type, item, spanIndex);
      const end = S.itemEnd(type, item, spanIndex);
      const left = Math.max(0, Math.min(100, start / duration * 100));
      const width = type === 'Point' ? 0 : Math.max(0, (end - start) / duration * 100);
      const selected = model.selection && model.selection.type === type && model.selection.id === String(item.id || '') && Number(model.selection.spanIndex || 0) === spanIndex;
      const label = type === 'Highlight' ? `H${rowIndex + 1}.${spanIndex + 1}` : type;
      const time = type === 'Point' ? S.hms(start) : `${S.hms(start)}–${S.hms(end)}`;
      if (type === 'Point') {
        return `<button type="button" class="timeline-point${selected ? ' selected' : ''}" style="left:${left}%" data-timeline-item data-type="Point" data-id="${escapeHtml(item.id || '')}" data-span-index="0" aria-pressed="${selected ? 'true' : 'false'}" aria-label="Point ${escapeHtml(time)} ${escapeHtml(item.title || '')}. 좌우 화살표로 이동"><span>${escapeHtml(item.title || 'Point')}</span></button>`;
      }
      const handles = selected
        ? '<span class="timeline-handle start" data-timeline-handle="start" aria-hidden="true"></span><span class="timeline-handle end" data-timeline-handle="end" aria-hidden="true"></span>'
        : '';
      return `<div role="button" tabindex="0" class="timeline-bar ${type.toLowerCase()}${selected ? ' selected' : ''}" style="left:${left}%;width:max(${width}%,3px)" data-timeline-item data-type="${escapeHtml(type)}" data-id="${escapeHtml(item.id || '')}" data-span-index="${spanIndex}" aria-pressed="${selected ? 'true' : 'false'}" aria-label="${escapeHtml(label)} ${escapeHtml(time)} ${escapeHtml(item.title || '')}. 좌우 화살표로 이동"><span class="timeline-bar-label">${escapeHtml(label)} · ${escapeHtml(item.title || '')}</span>${handles}</div>`;
    }

    updateHandleLayout() {
      const selected = this.canvas.querySelector('.timeline-bar.selected');
      if (!selected) {
        this.status.textContent = this.defaultStatus;
        return;
      }
      const compact = selected.getBoundingClientRect().width < 34;
      selected.classList.toggle('compact-handles', compact);
      this.status.textContent = compact
        ? '선택 구간이 좁습니다. Ctrl+휠로 확대하면 양쪽 손잡이가 나타납니다.'
        : this.defaultStatus;
    }

    render() {
      const model = this.options.getModel();
      if (!model) return;
      const ranges = model.outline.ranges || [];
      const selectedItem = S.selected(model);
      const parentId = model.selection && model.selection.type === 'D1'
        ? model.selection.id
        : model.selection && model.selection.type === 'D2' ? String((selectedItem || {}).parent_id || '') : '';
      const d1 = ranges.filter(row => row.level === 'D1');
      const d2 = ranges.filter(row => row.level === 'D2' && (!parentId || String(row.parent_id || '') === parentId));
      const points = model.outline.points || [];
      const highlights = model.highlights || [];
      const axis = [0, .25, .5, .75, 1].map(ratio => `<span style="left:${ratio * 100}%">${escapeHtml(S.hms(S.duration(model) * ratio))}</span>`).join('');
      const lane = (label, type, items) => `<div class="timeline-lane" data-timeline-track data-type="${type}"><span class="timeline-lane-label">${label}</span><div class="timeline-lane-track">${items}</div></div>`;
      this.canvas.innerHTML = `<div class="timeline-axis">${axis}</div>
        ${lane('D1', 'D1', d1.map((item, index) => this.itemMarkup('D1', item, 0, index)).join(''))}
        ${lane(parentId ? '선택 D1의 D2' : 'D2', 'D2', d2.map((item, index) => this.itemMarkup('D2', item, 0, index)).join(''))}
        ${lane('Point', 'Point', points.map((item, index) => this.itemMarkup('Point', item, 0, index)).join(''))}
        ${lane('Highlight', 'Highlight', highlights.map((item, rowIndex) => (item.source_spans || []).map((span, spanIndex) => this.itemMarkup('Highlight', item, spanIndex, rowIndex)).join('')).join(''))}
        <div class="timeline-playhead" data-timeline-playhead aria-hidden="true"></div>`;
      this.canvas.style.width = `${this.zoom * 100}%`;
      this.setPlayhead(this.playhead);
      this.updateHandleLayout();
    }

    onClick(event) {
      const item = event.target.closest('[data-timeline-item]');
      if (item) {
        this.options.onSelect(item.dataset.type, item.dataset.id, Number(item.dataset.spanIndex || 0), true);
        return;
      }
      const track = event.target.closest('.timeline-lane-track, .timeline-axis');
      if (!track) return;
      const rect = track.getBoundingClientRect();
      const seconds = Math.max(0, Math.min(S.duration(this.options.getModel()), (event.clientX - rect.left) / Math.max(1, rect.width) * S.duration(this.options.getModel())));
      this.options.onSeek(seconds);
    }

    requestedTimes(item, type, spanIndex, mode, delta) {
      const start = S.itemStart(type, item, spanIndex);
      const end = S.itemEnd(type, item, spanIndex);
      if (type === 'Point') return [start + delta, start + delta];
      if (mode === 'start') return [start + delta, end];
      if (mode === 'end') return [start, end + delta];
      return [start + delta, end + delta];
    }

    changeFromElement(element, mode, delta) {
      const model = this.options.getModel();
      const type = element.dataset.type;
      const id = element.dataset.id;
      const spanIndex = Number(element.dataset.spanIndex || 0);
      const item = S.rows(model, type).find(row => String(row.id || '') === id);
      if (!item) return false;
      const [start, end] = this.requestedTimes(item, type, spanIndex, mode, delta);
      const result = this.options.onChange(type, id, spanIndex, start, end, `${type} 시간 조정`);
      if (result.ok) this.options.onSeek(result.start);
      return result.ok;
    }

    onKeyDown(event) {
      const item = event.target.closest('[data-timeline-item]');
      if (!item || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const edge = event.target.closest('[data-timeline-handle]')?.dataset.timelineHandle || 'move';
      const delta = (event.key === 'ArrowRight' ? 1 : -1) * (event.shiftKey ? 10 : 1);
      this.changeFromElement(item, edge, delta);
    }

    onPointerDown(event) {
      if (event.button !== 0) return;
      const item = event.target.closest('[data-timeline-item]');
      if (!item || item.getAttribute('aria-pressed') !== 'true') return;
      const track = item.closest('.timeline-lane-track');
      if (!track) return;
      event.preventDefault();
      const mode = event.target.closest('[data-timeline-handle]')?.dataset.timelineHandle || 'move';
      const startX = event.clientX;
      const originalWidth = item.getBoundingClientRect().width;
      let lastX = startX;
      let moved = false;
      item.classList.add('dragging');
      const move = moveEvent => {
        lastX = moveEvent.clientX;
        moved = moved || Math.abs(lastX - startX) >= 4;
        if (!moved) return;
        const deltaX = lastX - startX;
        if (item.dataset.type === 'Point') {
          item.style.transform = `translateX(calc(-5px + ${deltaX}px))`;
        } else if (mode === 'start') {
          item.style.transform = `translateX(${deltaX}px)`;
          item.style.width = `${Math.max(14, originalWidth - deltaX)}px`;
        } else if (mode === 'end') {
          item.style.width = `${Math.max(14, originalWidth + deltaX)}px`;
        } else {
          item.style.transform = `translateX(${deltaX}px)`;
        }
      };
      const cleanup = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', finish);
        window.removeEventListener('pointercancel', cancel);
      };
      const finish = () => {
        cleanup();
        const delta = (lastX - startX) / Math.max(1, track.getBoundingClientRect().width) * S.duration(this.options.getModel());
        if (moved) this.changeFromElement(item, mode, delta);
        this.render();
      };
      const cancel = () => { cleanup(); this.render(); };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', finish, {once: true});
      window.addEventListener('pointercancel', cancel, {once: true});
    }
  }

  window.ReportWorkspaceTimeline = ReportWorkspaceTimeline;
})();
