const { api } = require('../../utils/api');
const { getBaseUrl, draftKey } = require('../../utils/settings');
const { showError, confirm } = require('../../utils/ui');
const labels = { pending: '待整理', queued: '排队中', running: '处理中', success: '已完成', partial: '部分完成', failed: '未完成', deleting: '删除中' };
Component({
  options: { styleIsolation: 'apply-shared' },
  properties: { active: { type: Boolean, value: false } },
  data: { tasks: [], loading: false, error: '', deleting: '' },
  observers: { active(value) { if (value && this._attached) this.load(); } },
  lifetimes: { attached() { this._attached = true; if (this.properties.active) this.load(); }, detached() { this._attached = false; this._generation = (this._generation || 0) + 1; } },
  pageLifetimes: { show() { if (this._attached && this.properties.active) this.load(); } },
  methods: {
    scope() { const user = api().currentUser(); return getBaseUrl() + '|user-' + (user ? user.id : 'signed-out'); },
    prepare() {
      try { const scope = this.scope(); if (scope !== this._scope) { this._scope = scope; this.setData({ tasks: [], error: '' }); } return true; }
      catch (error) { this.setData({ tasks: [], error: error.message }); return false; }
    },
    reset() { this._generation = (this._generation || 0) + 1; this._scope = null; this.setData({ tasks: [], loading: false, error: '' }); },
    async load() {
      if (this.data.loading || !this._attached) return;
      if (this.prepare() === false) return;
      const server = getBaseUrl(), generation = (this._generation || 0) + 1;
      this._generation = generation;
      this.setData({ loading: true, error: '' });
      try {
        const result = await api().list();
        if (!this._attached || generation !== this._generation || server !== getBaseUrl()) return;
        if (!Array.isArray(result.tasks)) throw new Error('整理记录格式不正确。');
        this._scope = this.scope();
        this.setData({ tasks: result.tasks.map(item => Object.assign({}, item, {
          label: labels[item.status] || item.status, date: (item.created_at || '').replace('T', ' ').slice(0, 16),
          active: ['queued', 'running', 'deleting'].includes(item.status)
        })) });
        const user = api().currentUser(); this.triggerEvent('authchange', { userId: user && user.id });
      } catch (error) {
        if (generation === this._generation) {
          this.setData(Object.assign({ error: error.message }, error.status === 401 ? { tasks: [] } : {}));
          if (error.status === 401) this.triggerEvent('authchange', { userId: null });
        }
      }
      finally { if (generation === this._generation) this.setData({ loading: false }); }
    },
    create() { this.triggerEvent('create'); },
    async open(event) {
      const id = event.currentTarget.dataset.id;
      try {
        const task = await api().task(id);
        let page = 'progress';
        if (!task.active_job && !['queued', 'running'].includes(task.status)) {
          try { await api().outline(id); page = 'outline'; } catch (error) { if (error.code !== 'PIPELINE_ERROR') throw error; }
        }
        wx.navigateTo({ url: `/pages/${page}/${page}?task_id=${id}` });
      } catch (error) { showError(error); }
    },
    async remove(event) {
      if (this.data.deleting) return;
      const id = event.currentTarget.dataset.id;
      if (!await confirm('删除后将移除此任务的照片、大纲和处理记录，无法恢复。确认删除？')) return;
      this.setData({ deleting: id }); this.triggerEvent('busy', { busy: true });
      try {
        await api().remove(id);
        wx.removeStorageSync(draftKey(id));
        // Invalidate a list read that may have captured the row before deletion.
        this._generation = (this._generation || 0) + 1;
        this.setData({ tasks: this.data.tasks.filter(item => String(item.task_id) !== String(id)), loading: false });
        await this.load();
      }
      catch (error) { showError(error); }
      finally { this.setData({ deleting: '' }); this.triggerEvent('busy', { busy: false }); }
    }
  }
});
