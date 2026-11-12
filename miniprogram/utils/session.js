const { networkError } = require('./network-error');
const { createTransport } = require('./transport');
const sessionKey = server => 'auth-session:' + server.replace(/\/+$/, '');
function createSession(platform, baseUrl, now = () => Date.now()) {
  const pending = new Map();
  function stored(server = baseUrl()) {
    const value = platform.getStorageSync(sessionKey(server));
    return value && typeof value.token === 'string' && value.token.length >= 40 && Number.isInteger(value.userId) && value.userId > 0 && value.expiresAt > now() + 30000 ? value : null;
  }
  function invalidate(server = baseUrl()) { platform.removeStorageSync(sessionKey(server)); }
  async function login(server) {
    const code = await new Promise((resolve, reject) => platform.login({ timeout: 10000,
      success(result) { result.code ? resolve(result.code) : reject(new Error('微信未返回登录凭证，请重新登录。')); },
      fail() { reject(new Error('微信登录暂未完成，请稍后重试。')); }
    }));
    const data = await new Promise((resolve, reject) => createTransport(platform, server).request({
      path: '/api/auth/wechat', method: 'POST', data: { code },
      header: { 'Content-Type': 'application/json' }, timeout: 15000,
      success(response) {
        let body = response.data;
        if (typeof body === 'string') { try { body = JSON.parse(body); } catch (_) { body = null; } }
        if (response.statusCode !== 200) return reject(Object.assign(new Error(body && body.message || (response.statusCode === 404 ? '后端缺少微信登录接口，请重启更新后的 Python 服务。' : '登录失败，请检查服务连接和后端微信配置。')), { code: body && body.code, status: response.statusCode }));
        resolve(body);
      }, fail(error) { reject(networkError(error, server)); }
    }));
    if (!data || typeof data.access_token !== 'string' || data.access_token.length < 40 || !data.user || !Number.isInteger(data.user.id) || data.user.id < 1 || !(data.expires_in > 30)) throw new Error('服务未返回有效登录状态。');
    const value = { token: data.access_token, userId: data.user.id, expiresAt: now() + data.expires_in * 1000 };
    platform.setStorageSync(sessionKey(server), value);
    return value;
  }
  async function authorization(server = baseUrl()) {
    const saved = stored(server);
    if (saved) return 'Bearer ' + saved.token;
    if (!pending.has(server)) {
      const task = login(server).finally(() => pending.delete(server));
      pending.set(server, task);
    }
    return 'Bearer ' + (await pending.get(server)).token;
  }
  return { authorization, invalidate, currentUser: (server = baseUrl()) => { const value = stored(server); return value ? { id: value.userId } : null; } };
}
module.exports = { createSession, sessionKey };
