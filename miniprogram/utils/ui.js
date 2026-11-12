function showError(error) { wx.showModal({ title: '操作未完成', content: error.message || '请检查网络后重试。', showCancel: false }); }
function confirm(content) { return new Promise(resolve => wx.showModal({ title: '请确认', content, success: result => resolve(result.confirm), fail: () => resolve(false) })); }
function taskId(options) {
  if (!/^[1-9]\d*$/.test(options.task_id || '')) { wx.showToast({ title: '任务编号无效', icon: 'none' }); return null; }
  return options.task_id;
}
module.exports = { showError, confirm, taskId };
