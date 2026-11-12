const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createSession } = require('../utils/session');
const { createApi } = require('../utils/api');

function platform() {
  const storage = new Map();
  const events = [];
  let logins = 0;
  return { storage, events, get logins() { return logins; },
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key),
    login(options) { logins++; options.success({ code: 'wx-code-' + logins }); },
    request(options) {
      events.push(options);
      options.success({ statusCode: 200, data: { access_token: 't'.repeat(43), expires_in: 3600, user: { id: logins, provider: 'wechat' } } });
    }
  };
}

test('concurrent callers share one wx.login and never send claimed identity or AppSecret', async () => {
  const wx = platform(), session = createSession(wx, () => 'https://one.example');
  const headers = await Promise.all([session.authorization(), session.authorization(), session.authorization()]);
  assert.equal(wx.logins, 1);
  assert.ok(headers.every(value => value === 'Bearer ' + 't'.repeat(43)));
  assert.deepEqual(wx.events[0].data, { code: 'wx-code-1' });
  assert.equal(wx.events[0].url, 'https://one.example/api/auth/wechat');
});

test('expired sessions are renewed and backend addresses never share credentials', async () => {
  const wx = platform();
  let server = 'https://one.example', now = 100000;
  const session = createSession(wx, () => server, () => now);
  await session.authorization();
  now += 3600 * 1000;
  await session.authorization();
  assert.equal(wx.logins, 2);
  server = 'https://two.example';
  await session.authorization();
  assert.equal(wx.logins, 3);
  assert.ok(wx.storage.has('auth-session:https://one.example'));
  assert.ok(wx.storage.has('auth-session:https://two.example'));
});

test('401 clears a session without automatically retrying a generation write', async () => {
  const wx = platform();
  const original = wx.request;
  let writes = 0;
  wx.request = options => {
    if (options.url.endsWith('/api/auth/wechat')) return original(options);
    writes++;
    assert.ok(options.header.Authorization.startsWith('Bearer '));
    options.success({ statusCode: 401, data: { code: 'AUTH_EXPIRED', message: '登录过期' } });
  };
  const api = createApi(wx, () => 'https://one.example');
  await assert.rejects(api.pipeline(1), /登录过期/);
  assert.equal(writes, 1);
  assert.equal(wx.storage.has('auth-session:https://one.example'), false);
});

test('source and Word downloads carry Authorization in headers, never URL params', async () => {
  const wx = platform();
  const urls = [];
  wx.downloadFile = options => {
    assert.equal(options.header.Authorization, 'Bearer ' + 't'.repeat(43));
    assert.ok(!options.url.includes('token') && !options.url.includes('Bearer'));
    urls.push(options.url); options.success({ statusCode: 200, tempFilePath: '/tmp/private-file' });
  };
  wx.openDocument = options => options.success({});
  const api = createApi(wx, () => 'https://one.example');
  assert.equal(await api.sourceFile(1, 2), '/tmp/private-file');
  await api.exportWord(1);
  assert.equal(urls.length, 2);
});


test('login retains generic request failure diagnostics and never caches a failed session', async () => {
  const wx = platform();
  wx.request = options => options.fail({ errMsg: 'request:fail', errno: -1 });
  const session = createSession(wx, () => 'http://192.0.2.25:8000');
  await assert.rejects(session.authorization(), error => error.code === 'NETWORK_ERROR' && error.rawMessage === 'request:fail' && /后端/.test(error.message));
  assert.equal(wx.logins, 1);
  assert.equal(wx.storage.size, 0);
});
