const { api } = require('../../utils/api');
const { getBaseUrl } = require('../../utils/settings');
Page({
  data: { state: 'loading', verification: '', error: '', busy: false },
  async onLoad(options) {
    this._gone = false;
    let scene;
    try { scene = decodeURIComponent(options && options.scene || ''); } catch (_) { scene = ''; }
    if (!/^[a-f0-9]{32}$/.test(scene)) { this.setData({ state: 'error', error: '登录码无效，请使用微信扫描网页上的小程序码。' }); return; }
    this._scene = scene;
    this.setData({ state: 'loading', error: '' });
    try {
      this._server = getBaseUrl();
      await api().login();
      if (this._gone || this._server !== getBaseUrl()) return;
      const result = await api().scanWebLogin(scene);
      if (!this._gone) this.setData({ state: 'ready', verification: result.verification });
    } catch (error) { if (!this._gone) this.setData({ state: 'error', error: error.message + ' 请确认小程序与网页连接的是同一后端。' }); }
  },
  onUnload() { this._gone = true; },
  async submit(approve) {
    if (this.data.busy || this.data.state !== 'ready') return;
    if (this._server !== getBaseUrl()) { this.setData({ state: 'error', error: '服务地址已更改，请重新扫码。' }); return; }
    this.setData({ busy: true });
    try {
      await api().confirmWebLogin(this._scene, approve);
      if (!this._gone) this.setData({ state: approve ? 'approved' : 'denied' });
    } catch (error) { if (!this._gone) this.setData({ state: 'error', error: error.message }); }
    finally { if (!this._gone) this.setData({ busy: false }); }
  },
  confirmLogin() { return this.submit(true); },
  rejectLogin() { return this.submit(false); },
  goHome() { wx.redirectTo({ url: '/pages/upload/upload' }); },
});
