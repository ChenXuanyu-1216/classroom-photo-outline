const { multipart } = require('./multipart');
const { createSession } = require('./session');
const { networkError } = require('./network-error');
const { createTransport, cloudTarget } = require('./transport');
const { uploadCloud, downloadCloud } = require('./cloud-files');
function readResponse(response) {
  let data = response.data;
  if (response.statusCode === 204) return {};
  if (typeof data === 'string') { try { data = JSON.parse(data); } catch (_) { data = null; } }
  if (!data || typeof data !== 'object') throw new Error('服务返回的数据无法读取，请检查后端地址。');
  if (response.statusCode < 200 || response.statusCode >= 300) {
    const message = data.message || (data.detail ? '请求参数不符合后端要求。' : '请求失败，请检查服务。');
    throw Object.assign(new Error(message), { code: data.code, retryable: data.retryable !== false, status: response.statusCode });
  }
  return data;
}
function createApi(platform, baseUrl, session = createSession(platform, baseUrl)) {
  const requestAt = async (server, path, method = 'GET', data, header = {}, protectedRequest = true) => {
    const authorization = protectedRequest ? await session.authorization(server) : null;
    return new Promise((resolve, reject) => {
    createTransport(platform, server).request({ path, method, data,
      header: Object.assign({}, header, authorization ? { Authorization: authorization } : {}), timeout: 60000,
      success(response) {
        if (response.statusCode === 401) session.invalidate(server);
        try { resolve(readResponse(response)); } catch (error) { reject(error); }
      },
      fail(error) { reject(networkError(error, server)); }
    });
    });
  };
  const request = async (...args) => requestAt(baseUrl(), ...args);
  const taskPath = id => {
    if (!/^[1-9]\d*$/.test(String(id))) throw new Error('任务编号无效。');
    return `/api/tasks/${id}`;
  };
  const writeJob = (id, operation, query = '') => request(`${taskPath(id)}/${operation}${query}`, 'POST', undefined, { Prefer: 'respond-async' });
  async function download(path, task, kind, page) {
    const server = baseUrl();
    const authorization = await session.authorization(server);
    if (cloudTarget(server)) return downloadCloud(platform, (...args) => requestAt(server, ...args),
      task, kind, page, server + ':user-' + session.currentUser(server).id);
    return new Promise((resolve, reject) => platform.downloadFile({ url: server.replace(/\/+$/, '') + path,
      header: { Authorization: authorization }, timeout: 60000,
      success(result) {
        if (result.statusCode === 401) session.invalidate(server);
        if (result.statusCode !== 200 || !result.tempFilePath) return reject(Object.assign(new Error(result.statusCode === 401 ? '登录已失效，请重新登录后下载。' : '文件下载失败，请检查任务是否存在和下载域名配置。'), { status: result.statusCode }));
        resolve(result.tempFilePath);
      }, fail: error => reject(networkError(error, server))
    }));
  }
  return {
    meta: () => request('/api/meta', 'GET', undefined, {}, false),
    currentUser: () => session.currentUser(),
    login: async () => { await session.authorization(); return session.currentUser(); },
    scanWebLogin: scene => request('/api/auth/web/scan', 'POST', { scene }, { 'Content-Type': 'application/json' }),
    confirmWebLogin: (scene, approve) => request('/api/auth/web/confirm', 'POST', { scene, approve }, { 'Content-Type': 'application/json' }),
    async logout() { const server = baseUrl(); try { await request('/api/auth/logout', 'POST'); } finally { session.invalidate(server); } },
    list: () => request('/api/tasks'),
    task: id => request(taskPath(id)),
    pages: id => request(`${taskPath(id)}/pages`),
    sourceFile: (id, page) => download(`${taskPath(id)}/pages/${Number(page)}/image`, id, 'source', page),
    outline: id => request(`${taskPath(id)}/outline`),
    save: (id, markdown) => request(`${taskPath(id)}/outline`, 'PUT', { markdown }, { 'Content-Type': 'application/json' }),
    remove: id => request(taskPath(id), 'DELETE'),
    pipeline: id => writeJob(id, 'pipeline'),
    regenerate: id => writeJob(id, 'regenerate'),
    recognize: (id, page) => writeJob(id, 'recognize', `?page_no=${Number(page)}`),
    async upload(files) {
      const server = baseUrl();
      if (cloudTarget(server)) {
        await session.authorization(server);
        return uploadCloud(platform, (...args) => requestAt(server, ...args), files,
          server + ':user-' + session.currentUser(server).id);
      }
      const read = path => new Promise((resolve, reject) => platform.getFileSystemManager().readFile({ filePath: path,
        success: result => resolve(result.data), fail: () => reject(new Error('照片无法读取，请重新选择。')) }));
      const body = await multipart(files, read);
      return request('/api/tasks', 'POST', body.body, { 'Content-Type': body.contentType });
    },
    async exportWord(id) {
      const filePath = await download(`${taskPath(id)}/export`, id, 'export');
      await new Promise((resolve, reject) => platform.openDocument({ filePath, fileType: 'docx', showMenu: true,
        success: resolve, fail: () => reject(new Error('文件已下载，但预览失败，请在真机重试。')) }));
    },
    async previewPage(id, page) {
      const path = await download(`${taskPath(id)}/pages/${Number(page)}/image`, id, 'source', page);
      platform.previewImage({ urls: [path], current: path });
    }
  };
}
let instance;
function api() {
  if (!instance) instance = createApi(wx, require('./settings').getBaseUrl);
  return instance;
}
module.exports = { createApi, readResponse, networkError, api };
