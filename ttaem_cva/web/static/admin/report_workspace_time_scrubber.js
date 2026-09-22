(() => {
  'use strict';

  const MOVE_THRESHOLD_PX = 3;
  const PIXELS_PER_SECOND = 2;

  function timeDelta(deltaX, coarse = false) {
    return Math.round(Number(deltaX || 0) / PIXELS_PER_SECOND) * (coarse ? 10 : 1);
  }

  class ReportWorkspaceTimeScrubber {
    constructor(root, options) {
      this.root = root;
      this.options = options;
      this.button = root.querySelector('[data-time-display-button]');
      this.valueNode = root.querySelector('[data-time-value]');
      this.deltaNode = root.querySelector('[data-time-delta]');
      this.input = root.querySelector('[data-time-input]');
      this.active = null;
      this.editSession = null;

      this.button.addEventListener('pointerdown', event => this.onPointerDown(event));
      this.button.addEventListener('pointermove', event => this.onPointerMove(event));
      this.button.addEventListener('pointerup', event => this.onPointerUp(event));
      this.button.addEventListener('pointercancel', () => this.cancelPointer());
      this.button.addEventListener('click', event => event.preventDefault());
      this.button.addEventListener('keydown', event => this.onButtonKeyDown(event));
      this.input.addEventListener('input', () => this.previewInput());
      this.input.addEventListener('keydown', event => this.onInputKeyDown(event));
      this.input.addEventListener('blur', () => this.finishInput());
      this.sync(this.options.getValue());
    }

    sync(value, delta = null) {
      const formatted = this.options.format(value);
      this.valueNode.textContent = formatted;
      this.button.setAttribute('aria-label', `${this.root.dataset.label} ${formatted}. 좌우로 드래그해 조정하거나 클릭해 직접 입력`);
      this.button.title = `${formatted} — 잡고 좌우로 끌어 조정 · 클릭해 직접 입력`;
      if (!this.editSession) this.input.value = formatted;
      const showDelta = Number.isFinite(delta) && delta !== 0;
      this.deltaNode.hidden = !showDelta;
      this.deltaNode.textContent = showDelta ? `${delta > 0 ? '+' : '−'}${Math.abs(delta)}초` : '';
    }

    onPointerDown(event) {
      if (event.button !== 0 || this.editSession) return;
      event.currentTarget.focus();
      const snapshot = this.options.begin();
      if (event.pointerType === 'touch') {
        this.enterInput(snapshot);
        return;
      }
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
      }
      this.active = {
        pointerId: event.pointerId,
        snapshot,
        startValue: this.options.getValue(),
        startX: event.clientX,
        moved: false,
      };
    }

    onPointerMove(event) {
      const active = this.active;
      if (!active || active.pointerId !== event.pointerId) return;
      const deltaX = event.clientX - active.startX;
      if (!active.moved && Math.abs(deltaX) < MOVE_THRESHOLD_PX) return;
      active.moved = true;
      event.preventDefault();
      this.button.classList.add('scrubbing');
      const delta = timeDelta(deltaX, event.shiftKey);
      const result = this.options.preview(active.startValue + delta);
      if (result.ok) this.sync(result.value, delta);
    }

    onPointerUp(event) {
      const active = this.active;
      if (!active || active.pointerId !== event.pointerId) return;
      this.active = null;
      this.button.classList.remove('scrubbing');
      if (active.moved) {
        this.deltaNode.hidden = true;
        this.options.commit(active.snapshot);
        return;
      }
      this.enterInput(active.snapshot);
    }

    cancelPointer() {
      if (!this.active) return;
      const snapshot = this.active.snapshot;
      this.active = null;
      this.button.classList.remove('scrubbing');
      this.deltaNode.hidden = true;
      this.options.cancel(snapshot);
    }

    onButtonKeyDown(event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        this.enterInput(this.options.begin());
        return;
      }
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      const snapshot = this.options.begin();
      const direction = event.key === 'ArrowRight' ? 1 : -1;
      const amount = direction * (event.shiftKey ? 10 : 1);
      const result = this.options.preview(this.options.getValue() + amount);
      if (result.ok) this.options.commit(snapshot);
      else this.options.cancel(snapshot, false);
    }

    enterInput(snapshot) {
      if (this.editSession) return;
      this.editSession = {snapshot};
      this.button.hidden = true;
      this.input.hidden = false;
      this.input.value = this.options.format(this.options.getValue());
      this.input.focus();
      this.input.select();
    }

    previewInput() {
      if (!this.editSession) return;
      const value = this.options.parse(this.input.value);
      if (!Number.isFinite(value)) return;
      const result = this.options.preview(value);
      if (result.ok) this.sync(result.value);
    }

    onInputKeyDown(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        this.cancelInput();
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        this.finishInput();
      }
    }

    finishInput() {
      if (!this.editSession) return;
      const session = this.editSession;
      const value = this.options.parse(this.input.value);
      const result = Number.isFinite(value) ? this.options.preview(value) : {ok: false};
      this.editSession = null;
      this.input.hidden = true;
      this.button.hidden = false;
      if (!result.ok) {
        this.options.cancel(session.snapshot, false);
        this.options.invalid();
        return;
      }
      this.options.commit(session.snapshot);
    }

    cancelInput() {
      if (!this.editSession) return;
      const snapshot = this.editSession.snapshot;
      this.editSession = null;
      this.input.hidden = true;
      this.button.hidden = false;
      this.options.cancel(snapshot);
    }
  }

  ReportWorkspaceTimeScrubber.timeDelta = timeDelta;
  window.ReportWorkspaceTimeScrubber = ReportWorkspaceTimeScrubber;
})();
