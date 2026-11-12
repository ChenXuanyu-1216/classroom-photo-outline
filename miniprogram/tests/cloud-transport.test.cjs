const { test } = require('node:test');
const assert = require('node:assert/strict');
const target = 'cloud://fixture-env-123/kezhao-api';

function platform() {
  const storage = new Map(), events = [];
  let logins = 0, inits = 0;
  const wx = {
    events, storage, get logins() { return logins; }, get inits() { return inits; },
    getStorageSync: key => storage.get(key), setStorageSync: (key, val) => storage.set(key, val),
    removeStorageSync: key => storage.delete(key),
    login(o) { logins++; o.success({ code: 'fixture-code' }); },
    request() { throw new Error('Cloud request escaped to HTTP'); },
    cloud: {
      init() { inits++; },
      callContainer(o) {
        events.push(o);
        o.success({ statusCode: 200, data: o.path.endsWith('/wechat')
          ? { access_token: 't'.repeat(43), expires_in: 3600, user: { id: 17 } }
          : { tasks: [] } });
      }
    }
  };
  return wx;
}

test('cloud login and APIs share one init/login and keep auth in headers', async () => {
  const { createApi } = require('../utils/api');
  const wx = platform(), api = createApi(wx, () => target);
  await Promise.all([api.list(), api.list(), api.list()]);
  assert.equal(wx.logins, 1);
  assert.equal(wx.inits, 1);
  assert.deepEqual(wx.events[0].data, { code: 'fixture-code' });
  for (const event of wx.events) {
    assert.equal(event.config.env, 'fixture-env-123');
    assert.equal(event.header['X-WX-SERVICE'], 'kezhao-api');
    assert.ok(event.timeout <= 15000);
    assert.ok(!event.path.includes('Bearer'));
  }
  assert.equal(wx.events[1].header.Authorization, 'Bearer ' + 't'.repeat(43));
});

test('Cloud Hosting target is passed per request without selecting a CloudBase environment at init', async () => {
  const { createApi } = require('../utils/api');
  const wx = platform(), initialize = wx.cloud.init;
  wx.cloud.init = options => {
    if (options && options.env) throw new Error('Cloud Hosting ID cannot initialize a CloudBase environment');
    initialize();
  };
  const result = await createApi(wx, () => target).list();
  assert.deepEqual(result, { tasks: [] });
  assert.equal(wx.inits, 1);
  assert.ok(wx.events.every(event => event.config.env === 'fixture-env-123'));
});

test('two owned cloud targets share SDK initialization while requests and sessions stay isolated', async () => {
  const { createApi } = require('../utils/api');
  const wx = platform();
  await createApi(wx, () => target).list();
  await createApi(wx, () => 'cloud://another-env-123/kezhao-api').list();
  assert.equal(wx.inits, 1);
  assert.equal(wx.logins, 2);
  assert.deepEqual(wx.events.map(event => event.config.env),
    ['fixture-env-123', 'fixture-env-123', 'another-env-123', 'another-env-123']);
});

test('cloud 401 invalidates only that target without replaying generation', async () => {
  const { createApi } = require('../utils/api');
  const wx = platform(), original = wx.cloud.callContainer;
  let writes = 0;
  wx.storage.set('auth-session:https://local.example', { token: 'other' });
  wx.cloud.callContainer = o => {
    if (o.path.endsWith('/wechat')) return original(o);
    writes++; o.success({ statusCode: 401, data: { code: 'AUTH_EXPIRED', message: '重新登录' } });
  };
  await assert.rejects(createApi(wx, () => target).pipeline(1), /重新登录/);
  assert.equal(writes, 1);
  assert.equal(wx.storage.has('auth-session:' + target), false);
  assert.equal(wx.storage.has('auth-session:https://local.example'), true);
});

test('trial/release ignore cached LAN and require actual cloud configuration', () => {
  const config = require('../config'), settings = require('../utils/settings');
  const oldWx = global.wx, oldCloud = config.cloud;
  try {
    global.wx = { getStorageSync: () => 'http://172.20.10.2:8000',
      getAccountInfoSync: () => ({ miniProgram: { envVersion: 'release' } }) };
    config.cloud = { env: '', service: 'kezhao-api' };
    assert.throws(() => settings.getBaseUrl(), /云环境/);
    config.cloud.env = 'fixture-env-123';
    assert.equal(settings.getBaseUrl(), target);
    assert.throws(() => settings.saveBaseUrl('http://127.0.0.1:8000'), /开发版/);
    global.wx.getAccountInfoSync = () => ({ miniProgram: { envVersion: 'develop' } });
    assert.equal(settings.getBaseUrl(), 'http://172.20.10.2:8000');
  } finally { global.wx = oldWx; config.cloud = oldCloud; }
});

test('cloud network errors explain cloud service rather than LAN instructions', async () => {
  const { createApi } = require('../utils/api');
  const wx = platform();
  wx.cloud.callContainer = o => o.fail({ errMsg: 'callContainer:fail timeout' });
  await assert.rejects(createApi(wx, () => target).meta(), error =>
    /云/.test(error.message) && !/电脑/.test(error.message));
});

test('cloud environments and draft ownership remain separate', async () => {
  const { createSession } = require('../utils/session');
  const wx = platform();
  let server = target;
  const session = createSession(wx, () => server);
  await session.authorization();
  server = 'cloud://another-env-123/kezhao-api';
  await session.authorization();
  assert.equal(wx.logins, 2);
  assert.equal(wx.inits, 1);
  const settings = require('../utils/settings'), config = require('../config');
  const oldWx = global.wx, oldCloud = config.cloud;
  try {
    global.wx = wx;
    wx.getAccountInfoSync = () => ({ miniProgram: { envVersion: 'trial' } });
    config.cloud = { env: 'fixture-env-123', service: 'kezhao-api' };
    const first = settings.draftKey(23);
    config.cloud.env = 'another-env-123';
    assert.notEqual(first, settings.draftKey(23));
    wx.storage.set('auth-session:' + server, { userId: 18 });
    assert.ok(settings.draftKey(23).includes('user-18'));
  } finally { global.wx = oldWx; config.cloud = oldCloud; }
});

module.exports = { platform, target };

test('missing cloud config is shown inside upload, history and QR screens without crashing', async () => {
  const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), { createRequire } = require('node:module');
  for (const relative of ['pages/upload/upload.js', 'pages/web-login/web-login.js', 'components/history-panel/history-panel.js']) {
    let definition;
    const filename = path.resolve(__dirname, '..', relative), normal = createRequire(filename);
    const fail = () => { throw new Error('云环境未配置'); };
    vm.runInNewContext(fs.readFileSync(filename, 'utf8'), { Page: v => definition = v, Component: v => definition = v,
      setTimeout, clearTimeout, wx: {}, require(id) {
        if (id.endsWith('/settings')) return { getBaseUrl: fail };
        if (id.endsWith('/api')) return { api: () => ({ currentUser: fail }) };
        return normal(id);
      } });
    const instance = Object.assign({}, definition, definition.methods, { _attached: true, data: { ...definition.data },
      setData(value) { Object.assign(this.data, value); } });
    if (relative.includes('web-login')) { await instance.onLoad({ scene: 'a'.repeat(32) }); assert.match(instance.data.error, /云环境/); }
    else if (relative.includes('history-panel')) { await instance.load(); assert.match(instance.data.error, /云环境/); }
    else { instance.onShow(); assert.match(instance.data.connectionError, /云环境/); }
  }
});
