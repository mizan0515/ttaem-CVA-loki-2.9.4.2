(() => {
  'use strict';

  const clone = value => JSON.parse(JSON.stringify(value));

  class ReportWorkspaceHistory {
    constructor(limit = 20) {
      this.limit = Math.max(1, Number(limit) || 20);
      this.past = [];
      this.future = [];
    }

    commit(snapshot, label) {
      this.past.push({snapshot: clone(snapshot), label: String(label || '수정')});
      if (this.past.length > this.limit) this.past.splice(0, this.past.length - this.limit);
      this.future = [];
    }

    undo(currentSnapshot) {
      const entry = this.past.pop();
      if (!entry) return null;
      this.future.push({snapshot: clone(currentSnapshot), label: entry.label});
      return {snapshot: clone(entry.snapshot), label: entry.label};
    }

    redo(currentSnapshot) {
      const entry = this.future.pop();
      if (!entry) return null;
      this.past.push({snapshot: clone(currentSnapshot), label: entry.label});
      return {snapshot: clone(entry.snapshot), label: entry.label};
    }

    clear() {
      this.past = [];
      this.future = [];
    }

    get canUndo() { return this.past.length > 0; }
    get canRedo() { return this.future.length > 0; }
    get undoLabel() { return this.canUndo ? this.past[this.past.length - 1].label : ''; }
    get redoLabel() { return this.canRedo ? this.future[this.future.length - 1].label : ''; }
  }

  window.ReportWorkspaceHistory = ReportWorkspaceHistory;
})();
