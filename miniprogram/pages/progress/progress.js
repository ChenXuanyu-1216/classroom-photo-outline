const { api } = require('../../utils/api');
const { inspectTask } = require('../../utils/workflow');
const { taskId, showError, confirm } = require('../../utils/ui');
const { openWorkspace } = require('../../utils/navigation');
Page({
  data: { id: '', title: '正在读取任务', message: '', pages: [], hasOutline: false, running: false, busy: false, reading: false, error: '', canGenerate: false },
  onLoad(options) { this._id = taskId(options); this._start = options.start === '1'; this.setData({ id: this._id || '' }); },
  onShow() { this._visible = true; this._epoch = (this._epoch || 0) + 1; this.refresh(); },
  onHide() { this.stop(); },
  onUnload() { this.stop(); },
  stop() { this._visible = false; this._epoch++; clearTimeout(this._timer); },
  async refresh() {
    if (!this._id || this._reading || this.data.busy || !this._visible) return;
    clearTimeout(this._timer);
    this._reading = true;
    this.setData({ reading: true });
    const epoch = this._epoch;
    let startTask = false;
    try {
      const state = await inspectTask(api(), this._id);
      if (!this._visible || epoch !== this._epoch) return;
      this.setData({ title: state.title, message: state.message, pages: state.pages.map(page => Object.assign({}, page, {
        canRetry: page.failed_flag && (page.error_retryable !== false || /AUTH|CONFIG|BALANCE|BUDGET/.test(page.error_code || ''))
      })), hasOutline: state.hasOutline, running: state.running, canGenerate: !state.running, error: '' });
      if (this._start) {
        this._start = false;
        if (!state.running && state.task.status === 'pending' && !state.hasOutline) {
          startTask = true;
        }
      }
      if (state.running) this._timer = setTimeout(() => this.refresh(), 2000);
    } catch (error) {
      // An uncertain submission is reconciled by reads, never an automatic POST.
      this._start = false;
      if (this._visible && epoch === this._epoch) this.setData({ error: error.message, title: '暂时无法确认状态' });
    } finally {
      this._reading = false;
      this.setData({ reading: false });
      // If hidden then shown during a request, resume the new page lifecycle.
      if (this._visible && epoch !== this._epoch) this.refresh();
    }
    if (startTask && this._visible && epoch === this._epoch) await this.submit('pipeline');
  },
  async submit(operation, page) {
    if (this._reading || this.data.busy || this.data.running) return;
    this.setData({ busy: true, error: '' });
    try {
      if (operation === 'recognize') await api().recognize(this._id, page);
      else await api().pipeline(this._id);
      this.setData({ running: true, canGenerate: false });
    } catch (error) {
      this.setData({ error: `${error.message} 请先重新检查状态；无需重新上传。` });
      showError(error);
    } finally {
      this.setData({ busy: false });
      if (this._visible) this.refresh();
    }
  },
  async generate() {
    if (this.data.busy || this.data.running) return;
    if (this.data.hasOutline && !await confirm('重新生成会替换当前大纲。请先保存需要保留的内容，是否继续？')) return;
    this.submit('pipeline');
  },
  retryPage(event) { this.submit('recognize', Number(event.currentTarget.dataset.page)); },
  outline() { wx.navigateTo({ url: `/pages/outline/outline?task_id=${this._id}` }); },
  history() { openWorkspace(wx, getCurrentPages(), 'history'); }
});
