const { api } = require('../../utils/api');
const { draftKey } = require('../../utils/settings');
const { showError, confirm } = require('../../utils/ui');
const { openWorkspace } = require('../../utils/navigation');
const labels = { pending: '待整理', queued: '排队中', running: '处理中', success: '已完成', partial: '部分完成', failed: '未完成', deleting: '删除中' };
Page({
  data: { tasks: [], loading: false, error: '', deleting: '' },
  onLoad() { this._forwarding = true; openWorkspace(wx, getCurrentPages(), 'history', true); },
  onShow() { if (!this._forwarding) this.load(); },
  onPullDownRefresh() { this.load().finally(() => wx.stopPullDownRefresh()); },
  async load() {
    if (this.data.loading) return;
    this.setData({ loading: true, error: '' });
    try {
      const result = await api().list();
      if (!Array.isArray(result.tasks)) throw new Error('整理记录格式不正确。');
      this.setData({ tasks: result.tasks.map(item => Object.assign({}, item, {
        label: labels[item.status] || item.status, date: (item.created_at || '').replace('T', ' ').slice(0, 16),
        active: ['queued', 'running', 'deleting'].includes(item.status)
      })) });
    } catch (error) { this.setData({ error: error.message }); }
    finally { this.setData({ loading: false }); }
  },
  async open(event) {
    const id = event.currentTarget.dataset.id;
    try {
      const task = await api().task(id);
      let page = 'progress';
      if (!task.active_job && !['queued', 'running'].includes(task.status)) {
        try { await api().outline(id); page = 'outline'; }
        catch (error) { if (error.code !== 'PIPELINE_ERROR') throw error; }
      }
      wx.navigateTo({ url: `/pages/${page}/${page}?task_id=${id}` });
    } catch (error) { showError(error); }
  },
  async remove(event) {
    if (this.data.deleting) return;
    const id = event.currentTarget.dataset.id;
    if (!await confirm('删除后将移除此任务的照片、大纲和处理记录，无法恢复。确认删除？')) return;
    this.setData({ deleting: id });
    try { await api().remove(id); wx.removeStorageSync(draftKey(id)); await this.load(); }
    catch (error) { showError(error); }
    finally { this.setData({ deleting: '' }); }
  },
  create() { wx.reLaunch({ url: '/pages/upload/upload' }); }
});
