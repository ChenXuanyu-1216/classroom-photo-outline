const { drawKnowledgeScene } = require('../../utils/knowledge-scene');
const { openWorkspace } = require('../../utils/navigation');
Component({
  properties: { current: { type: String, value: 'upload' }, compact: { type: Boolean, value: false }, locked: { type: Boolean, value: false }, workspace: { type: Boolean, value: false } },
  data: { speed: 1, duration: 12 },
  lifetimes: {
    attached() { this._sceneDisposed = false; this._sceneHidden = false; const stored = wx.getStorageSync('paper-motion-speed'); const speed = [0.5, 1, 2].includes(stored) ? stored : 1; this.setData({ speed, duration: 12 / speed }); },
    ready() { this.initScene(); },
    detached() { this._sceneDisposed = true; this.stopScene(); this._scene = null; }
  },
  pageLifetimes: { show() { this._sceneHidden = false; this.startScene(); }, hide() { this._sceneHidden = true; this.stopScene(); }, resize() { this.initScene(); } },
  methods: {
    initScene() {
      if (this.properties.compact) return;
      this.stopScene();
      this.createSelectorQuery().select('.knowledge-canvas').fields({ node: true, size: true }).exec(results => {
        const result = results && results[0];
        if (this._sceneDisposed || !result || !result.node || !result.width || !result.height) return;
        const ratio = Math.min((wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()).pixelRatio || 1, 2);
        const canvas = result.node;
        canvas.width = result.width * ratio; canvas.height = result.height * ratio;
        const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
        this._scene = { canvas, ctx, width: result.width, height: result.height };
        this.startScene();
      });
    },
    startScene() {
      if (this._sceneDisposed || this._sceneHidden || !this._scene || this._frame !== undefined && this._frame !== null) return;
      let last = 0;
      const draw = stamp => {
        if (!this._scene) return;
        if (!last || stamp - last >= 32) {
          this._sceneTime = (this._sceneTime || 0) + (last ? Math.min((stamp - last) / 1000, .1) : 0) * this.data.speed;
          last = stamp;
          drawKnowledgeScene(this._scene.ctx, this._scene.width, this._scene.height, this._sceneTime);
        }
        this._frame = this._scene.canvas.requestAnimationFrame(draw);
      };
      this._frame = this._scene.canvas.requestAnimationFrame(draw);
    },
    stopScene() { if (this._scene && this._frame !== undefined && this._frame !== null) this._scene.canvas.cancelAnimationFrame(this._frame); this._frame = null; },
    go(event) {
      const page = event.currentTarget.dataset.page;
      if (this.properties.locked) return wx.showToast({ title: '当前操作进行中，请稍候', icon: 'none' });
      if (page === this.properties.current || !['upload', 'history'].includes(page)) return;
      if (this.properties.workspace) return this.triggerEvent('navigate', { page });
      openWorkspace(wx, getCurrentPages(), page);
    },
    changeSpeed() { const speed = this.data.speed === 1 ? 2 : this.data.speed === 2 ? 0.5 : 1; this.setData({ speed, duration: 12 / speed }); wx.setStorageSync('paper-motion-speed', speed); }
  }
});
