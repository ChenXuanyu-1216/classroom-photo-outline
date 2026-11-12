const { api } = require('../../utils/api');
const { getBaseUrl, saveBaseUrl } = require('../../utils/settings');
const { showError, confirm } = require('../../utils/ui');
const { maxBatchBytes } = require('../../config');
const { pickPhotos, getPhotoPrivacy } = require('../../utils/photo-picker');
Page({
  data: { activeView: 'upload', turning: '', historyBusy: false, files: [], busy: false, loginBusy: false, userId: null, server: '', settingsOpen: false, meta: null, connectionError: '', createdId: '', maxFiles: 20, totalMB: '0.0', privacyOpen: false, privacyContractName: '《用户隐私保护指引》' },
  onLoad(options) { this._unloaded = false; if (options && options.view === 'history') this.setData({ activeView: 'history' }); },
  onUnload() { this._unloaded = true; clearTimeout(this._turnTimer); },
  switchWorkspace(event) { this.selectWorkspace(event.detail.page); },
  selectWorkspace(page) {
    if (!['upload', 'history'].includes(page) || page === this.data.activeView || this.data.busy || this.data.loginBusy || this.data.historyBusy) return;
    const panel = this.selectComponent && this.selectComponent('#history-pane');
    if (page === 'history' && panel) panel.prepare();
    clearTimeout(this._turnTimer);
    this.setData({ activeView: page, turning: page === 'history' ? 'to-history' : 'to-upload' });
    this._turnTimer = setTimeout(() => this.setData({ turning: '' }), 620);
  },
  newUpload() { this.selectWorkspace('upload'); },
  historyBusy(event) { this.setData({ historyBusy: !!event.detail.busy }); },
  accountChanged(event) { this.setData({ userId: event.detail.userId || null }); },
  resetHistory() { const panel = this.selectComponent && this.selectComponent('#history-pane'); if (panel) panel.reset(); },
  onShow() {
    try { const user = api().currentUser(); this.setData({ server: getBaseUrl(), userId: user && user.id }); this.connect(); }
    catch (error) { this.setData({ connectionError: error.message, meta: null, userId: null }); }
  },
  async login() {
    if (this.data.loginBusy || this.data.busy) return;
    this.setData({ loginBusy: true });
    try { const user = await api().login(); this.setData({ userId: user.id }); wx.showToast({ title: '登录成功', icon: 'success' }); }
    catch (error) { showError(error); }
    finally { this.setData({ loginBusy: false }); }
  },
  async logout() {
    if (this.data.busy || this.data.loginBusy || !await confirm('退出后此设备需重新微信登录才能访问课堂资料，已有服务器记录会保留。是否退出？')) return;
    this.setData({ loginBusy: true });
    try { await api().logout(); }
    catch (error) { showError(error); }
    finally { this.setData({ userId: null, loginBusy: false, files: [], createdId: '', totalMB: '0.0' }); this.resetHistory(); }
  },
  async connect() {
    const generation = (this._connectionGeneration || 0) + 1;
    this._connectionGeneration = generation;
    this.setData({ meta: null, connectionError: '' });
    try {
      const meta = await api().meta();
      if (this._connectionGeneration !== generation) return;
      if (!meta.model_provider || !meta.max_files_per_batch) throw new Error('后端配置信息不完整。');
      this.setData({ meta, maxFiles: Math.min(20, meta.max_files_per_batch) });
    } catch (error) {
      if (this._connectionGeneration === generation) this.setData({ connectionError: error.message });
    }
  },
  toggleSettings() { this.setData({ settingsOpen: !this.data.settingsOpen }); },
  serverInput(event) { this.setData({ server: event.detail.value }); },
  saveServer() {
    if (this.data.busy || this.data.loginBusy) return;
    try { saveBaseUrl(this.data.server); const user = api().currentUser(); this.setData({ createdId: '', userId: user && user.id }); this.resetHistory(); this.connect(); } catch (error) { showError(error); }
  },
  async choose() {
    if (this.data.busy || this.data.createdId || this._choosing) return;
    const count = Math.min(9, this.data.maxFiles - this.data.files.length);
    if (count <= 0) return wx.showToast({ title: '已达本批张数上限', icon: 'none' });
    this._choosing = true;
    try {
      const privacy = await getPhotoPrivacy(wx);
      if (this._unloaded) return;
      if (privacy.needAuthorization) {
        this.setData({ privacyOpen: true, privacyContractName: privacy.privacyContractName || '《用户隐私保护指引》' });
        return;
      }
      const selected = await pickPhotos(wx, count);
      if (!selected || this._unloaded) return;
      const additions = selected.map((file, index) => ({ path: file.path, size: file.size,
        name: file.path.split('/').pop(), key: `${Date.now()}-${index}-${file.path}` }));
      const files = this.data.files.concat(additions);
      if (files.some(file => file.size > ((this.data.meta && this.data.meta.max_file_mb) || 10) * 1024 * 1024)) return showError(new Error('单张照片超过后端大小限制，请压缩后再选。'));
      const total = files.reduce((size, file) => size + file.size, 0);
      if (total > maxBatchBytes) return showError(new Error('本批照片合计不能超过8MB，请减少张数。'));
      this.setData({ files, totalMB: (total / 1024 / 1024).toFixed(1) });
    } catch (error) { if (!this._unloaded) showError(error); }
    finally { this._choosing = false; }
  },
  agreePhotoPrivacy() {
    // The native button verifies the gesture; choose() rechecks platform consent.
    this.setData({ privacyOpen: false });
    return this.choose();
  },
  declinePhotoPrivacy() { this.setData({ privacyOpen: false }); },
  openPhotoPrivacy() {
    if (typeof wx.openPrivacyContract !== 'function') return showError(new Error('当前微信版本无法打开隐私指引，请升级微信后重试。'));
    wx.openPrivacyContract({ fail: () => showError(new Error('暂时无法打开隐私指引，请稍后重试。')) });
  },
  move(event) {
    if (this.data.busy || this.data.createdId) return;
    const index = Number(event.currentTarget.dataset.index), target = index + Number(event.currentTarget.dataset.step);
    const files = this.data.files.slice();
    if (target < 0 || target >= files.length) return;
    [files[index], files[target]] = [files[target], files[index]];
    this.setData({ files });
  },
  remove(event) {
    if (this.data.busy || this.data.createdId) return;
    const files = this.data.files.filter((_, index) => index !== Number(event.currentTarget.dataset.index));
    this.setData({ files, totalMB: (files.reduce((size, file) => size + file.size, 0) / 1024 / 1024).toFixed(1) });
  },
  preview(event) { const path = this.data.files[Number(event.currentTarget.dataset.index)].path; wx.previewImage({ current: path, urls: this.data.files.map(file => file.path) }); },
  async upload() {
    if (this.data.busy) return;
    if (this.data.createdId) return this.openCreated();
    if (!this.data.meta || !this.data.files.length) return;
    if (this.data.files.length > this.data.maxFiles) return showError(new Error('照片张数超过当前后端限制，请移除部分照片。'));
    this.setData({ busy: true });
    try {
      const result = await api().upload(this.data.files);
      if (!result.task_id) throw new Error('上传结果无法确认，请先查看整理记录。');
      this.setData({ createdId: result.task_id });
      if (result.rejected && result.rejected.length) await new Promise(resolve => wx.showModal({ title: '部分照片未接收',
        content: result.rejected.map(item => `${item.filename}：${item.reason}`).join('\n'), showCancel: false, complete: resolve }));
      this.openCreated();
    } catch (error) {
      if (error.code === 'NETWORK_ERROR') error.message += '\n上传结果可能尚未确认，请先查看整理记录，避免重复上传。';
      showError(error);
    } finally { this.setData({ busy: false }); }
  },
  openCreated() { wx.navigateTo({ url: `/pages/progress/progress?task_id=${this.data.createdId}&start=1`, success: () => this.setData({ files: [], createdId: '', totalMB: '0.0' }) }); },
  history() { this.selectWorkspace('history'); }
});
