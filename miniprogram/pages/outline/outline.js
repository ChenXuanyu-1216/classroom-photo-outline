const { api } = require('../../utils/api');
const { draftKey } = require('../../utils/settings');
const { showError, confirm, taskId } = require('../../utils/ui');
const { formatOutline } = require('../../utils/outline-format');
Page({
  data: { id: '', markdown: '', blocks: [], version: 0, warnings: [], pages: [], mode: '', demo: false, dirty: false, busy: false, loading: true, error: '', draftWarning: '', editing: false },
  onLoad(options) { this._id = taskId(options); this.setData({ id: this._id || '' }); if (this._id) this.load(); },
  async load() {
    if (this._loading || this.data.busy) return;
    this._loading = true;
    this.setData({ loading: true, error: '' });
    try {
      const outline = await api().outline(this._id);
      if (typeof outline.markdown !== 'string') throw new Error('大纲数据不完整。');
      const pages = await api().pages(this._id);
      this._savedMarkdown = outline.markdown;
      this.setData({ markdown: outline.markdown, blocks: formatOutline(outline.markdown), version: outline.version, warnings: outline.warnings || [],
        mode: outline.recognition_mode, pages: pages.pages.map(page => Object.assign({}, page, { imageUrl: '' })), dirty: false });
      this.loadSources(pages.pages);
      // Legacy outlines may be labelled unverified even in mock mode.
      try { const meta = await api().meta(); this.setData({ demo: meta.model_provider === 'mock' && outline.recognition_mode !== 'deepseek' }); } catch (_) { /* Keep the unverified warning visible. */ }
      const draft = wx.getStorageSync(draftKey(this._id));
      if (draft && typeof draft.markdown === 'string' && draft.markdown !== outline.markdown) {
        if (await confirm('此设备有未保存的本地草稿。是否恢复？服务器上的大纲暂不改变。')) {
          this.setData({ markdown: draft.markdown, blocks: formatOutline(draft.markdown), dirty: true, editing: true });
        } else wx.removeStorageSync(draftKey(this._id));
      }
    } catch (error) { this.setData({ error: error.message }); }
    finally { this._loading = false; this.setData({ loading: false }); }
  },
  onUnload() { this._sourceEpoch = (this._sourceEpoch || 0) + 1; },
  async loadSources(pages) {
    const epoch = (this._sourceEpoch || 0) + 1;
    this._sourceEpoch = epoch;
    for (const page of pages) {
      if (this._sourceEpoch !== epoch) return;
      try {
        const imageUrl = await api().sourceFile(this._id, page.page_no);
        if (this._sourceEpoch !== epoch) return;
        this.setData({ pages: this.data.pages.map(item => item.page_no === page.page_no ? Object.assign({}, item, { imageUrl }) : item) });
      } catch (error) { if (error.status === 401) return; }
    }
  },
  edit(event) {
    const markdown = event.detail.value;
    const dirty = markdown !== this._savedMarkdown;
    this.setData({ markdown, dirty });
    // Only a local draft; never silently write edits to the backend.
    try {
      if (dirty) wx.setStorageSync(draftKey(this._id), { markdown, version: this.data.version });
      else wx.removeStorageSync(draftKey(this._id));
    } catch (_) { this.setData({ draftWarning: '本地草稿空间不足，请及时保存到服务器。' }); }
  },
  toggleEdit() { this.setData({ editing: !this.data.editing, blocks: formatOutline(this.data.markdown) }); },
  async persist() {
    const current = await api().outline(this._id);
    // Regeneration can restart version numbering; compare the loaded content too.
    const serverChanged = current.version !== this.data.version || current.markdown !== this._savedMarkdown;
    if (!this.data.dirty) {
      if (serverChanged) {
        this._savedMarkdown = current.markdown;
        this.setData({ markdown: current.markdown, blocks: formatOutline(current.markdown), version: current.version, warnings: current.warnings || [], mode: current.recognition_mode || this.data.mode });
        return confirm('服务器上的大纲已有更新，已载入最新版本。是否继续导出最新版本？');
      }
      return true;
    }
    if (serverChanged && !await confirm('服务器上的大纲已有更新。继续保存将用本次编辑覆盖最新内容，是否继续？')) return false;
    const markdown = this.data.markdown;
    const saved = await api().save(this._id, markdown);
    this._savedMarkdown = markdown;
    this.setData({ version: saved.version, dirty: this.data.markdown !== markdown });
    if (!this.data.dirty) wx.removeStorageSync(draftKey(this._id));
    return true;
  },
  async save() {
    if (this.data.busy || this.data.loading || this.data.error) return;
    this.setData({ busy: true });
    try { if (await this.persist()) wx.showToast({ title: '已保存', icon: 'success' }); }
    catch (error) { showError(error); }
    finally { this.setData({ busy: false }); }
  },
  async exportWord() {
    if (this.data.busy || this.data.loading || this.data.error) return;
    this.setData({ busy: true });
    try {
      if (!await this.persist()) return;
      await api().exportWord(this._id);
    } catch (error) { showError(error); }
    finally { this.setData({ busy: false }); }
  },
  async preview(event) {
    try { await api().previewPage(this._id, event.currentTarget.dataset.page); } catch (error) { showError(error); }
  },
  async regenerate() {
    if (this.data.busy || this.data.loading) return;
    if (!await confirm('重新生成会替换服务器上的大纲，并可能调用付费模型。是否继续？')) return;
    if (this.data.dirty && !await confirm('当前仍有未保存编辑，本地草稿会保留。仍要重新生成吗？')) return;
    this.setData({ busy: true });
    try {
      await api().regenerate(this._id);
      wx.redirectTo({ url: `/pages/progress/progress?task_id=${this._id}` });
    } catch (error) { showError(error); }
    finally { this.setData({ busy: false }); }
  },
  progress() { wx.navigateTo({ url: `/pages/progress/progress?task_id=${this._id}` }); }
});
