const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function harness(response) {
  const values = new Map(), calls = [];
  const context = { location: { origin: 'http://127.0.0.1:8000' }, URL, Headers, Response,
    sessionStorage: { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
    fetch: async (url, options) => { calls.push({ url, options }); return response || new Response('{}', { status: 200 }); }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../js/auth.js'), 'utf8'), context);
  return { auth: context.WorkspaceAuth, calls, values, context };
}
test('web tasks require WeChat login and attach the confirmed browser session', async () => {
  const { auth, calls } = harness();
  assert.equal((await auth.fetch('/api/tasks')).status, 401);
  assert.equal(calls.length, 0);
  auth.acceptSession({ access_token: 't'.repeat(43), user: { id: 1, provider: 'wechat' } });
  await auth.fetch('/api/tasks', { method: 'POST' });
  assert.equal(calls[0].options.headers.get('Authorization'), 'Bearer ' + 't'.repeat(43));
});
test('web never attaches its session to another origin', async () => {
  const { auth, calls } = harness();
  auth.acceptSession({ access_token: 't'.repeat(43), user: { id: 1, provider: 'wechat' } });
  await auth.fetch('https://elsewhere.example/api/tasks');
  assert.equal(calls[0].options && calls[0].options.headers, undefined);
});
test('web 401 clears token and never retries a generation write', async () => {
  const { auth, calls } = harness(new Response('{}', { status: 401 }));
  auth.acceptSession({ access_token: 't'.repeat(43), user: { id: 1, provider: 'wechat' } });
  await auth.fetch('/api/tasks/1/pipeline', { method: 'POST' });
  assert.equal(calls.length, 1);
  assert.equal((await auth.fetch('/api/tasks')).status, 401);
  assert.equal(calls.length, 1);
});
test('web refuses malformed or local sessions and exposes no manual-token import', () => {
  const { auth, calls } = harness();
  assert.equal(auth.setToken, undefined);
  assert.throws(() => auth.acceptSession({ access_token: 'a'.repeat(32), user: { id: 1, provider: 'wechat' } }));
  assert.throws(() => auth.acceptSession({ access_token: 'a'.repeat(43), user: { id: 1, provider: 'local' } }));
  assert.equal(calls.length, 0);
});
test('missing login redirects once and never sends the pending write', async () => {
  const { auth, context, calls } = harness();
  const redirects = [];
  Object.assign(context.location, { pathname: '/outline.html', search: '?task_id=4', assign: url => redirects.push(url) });
  await auth.fetch('/api/tasks/4/regenerate', { method: 'POST' });
  await auth.fetch('/api/tasks/4');
  assert.equal(calls.length, 0);
  assert.deepEqual(redirects, ['/login.html?next=%2Foutline.html%3Ftask_id%3D4']);
});
