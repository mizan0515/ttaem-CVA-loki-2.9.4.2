(() => {
  'use strict';

  class ReportPlayer {
    constructor(video, onState) {
      this.video = video;
      this.hls = null;
      this.onState = onState || (() => {});
    }

    async load(source) {
      this.destroy();
      this.onState('loading', '영상 연결 중');
      if (window.Hls && window.Hls.isSupported()) {
        this.hls = new window.Hls({enableWorker: true, backBufferLength: 90});
        this.hls.loadSource(source);
        this.hls.attachMedia(this.video);
        this.hls.on(window.Hls.Events.MANIFEST_PARSED, () => this.onState('ready', '재생 준비됨'));
        this.hls.on(window.Hls.Events.ERROR, (_event, data) => {
          if (data && data.fatal) this.onState('error', '영상 연결 실패 · ' + String(data.details || data.type || 'unknown').replace(/[^a-zA-Z0-9_-]/g, ''));
        });
      } else if (this.video.canPlayType('application/vnd.apple.mpegurl')) {
        this.video.src = source;
        this.video.addEventListener('loadedmetadata', () => this.onState('ready', '재생 준비됨'), {once: true});
      } else {
        throw new Error('이 브라우저는 HLS 재생을 지원하지 않습니다.');
      }
    }

    seek(seconds) {
      if (!Number.isFinite(seconds)) return;
      const apply = () => {
        this.video.currentTime = Math.max(0, seconds);
        this.video.play().catch(() => {});
      };
      if (this.video.readyState >= 1) apply();
      else this.video.addEventListener('loadedmetadata', apply, {once: true});
    }

    async pictureInPicture() {
      if (!document.pictureInPictureEnabled || typeof this.video.requestPictureInPicture !== 'function') {
        throw new Error('이 브라우저에서는 작게 띄우기를 지원하지 않습니다.');
      }
      if (document.pictureInPictureElement) await document.exitPictureInPicture();
      else await this.video.requestPictureInPicture();
    }

    destroy() {
      if (this.hls) this.hls.destroy();
      this.hls = null;
      this.video.removeAttribute('src');
      this.video.load();
    }
  }

  window.ReportPlayer = ReportPlayer;
})();
