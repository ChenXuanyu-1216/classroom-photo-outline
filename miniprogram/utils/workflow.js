async function inspectTask(api, id) {
  const task = await api.task(id);
  const pages = (await api.pages(id)).pages;
  if (!Array.isArray(pages) || !task.status) throw new Error('任务数据不完整，请重新检查。');
  let outline;
  try { outline = await api.outline(id); }
  catch (error) { if (error.code !== 'PIPELINE_ERROR') throw error; }
  const running = !!task.active_job || ['queued', 'running'].includes(task.status);
  const failedPages = pages.filter(page => page.failed_flag);
  const hasOutline = !!outline && typeof outline.markdown === 'string';
  const recognitionUpdated = !running && task.last_job && task.last_job.operation === 'recognize' && ['success', 'partial'].includes(task.last_job.status);
  return { task, pages, failedPages, running, hasOutline,
    title: running ? (task.status === 'queued' ? '任务已排队' : '后台处理中')
      : recognitionUpdated ? '识别已更新，请生成大纲' : task.status === 'failed' ? '处理未完成' : hasOutline ? (failedPages.length ? '部分页面已整理' : '整理完成') : '等待开始整理',
    message: running ? '离开页面后后台仍会继续。返回时自动检查状态。'
      : task.error_msg || (recognitionUpdated ? '本次重试只更新识别结果，点击生成按钮后才会更新大纲。' : failedPages.length ? '失败页已保留，可逐页重试，再生成大纲。' : hasOutline ? '请校对内容和原图来源，再保存与导出。' : '照片已保存，点击开始整理。')
  };
}
module.exports = { inspectTask };
