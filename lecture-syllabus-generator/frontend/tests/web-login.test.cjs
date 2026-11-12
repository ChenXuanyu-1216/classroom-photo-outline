const { test } = require('node:test');
const assert = require('node:assert/strict');
const { safeReturn, createFlow } = require('../js/web-login.js');

test('login return only accepts known pages on the same origin', () => {
  const origin = 'http://localhost:8000';
  for (const bad of ['https://evil.example', '//evil.example', 'javascript:alert(1)', '/api/tasks', '/login.html']) assert.equal(safeReturn(bad, origin), '/history.html');
  assert.equal(safeReturn('/outline.html?task_id=1', origin), '/outline.html?task_id=1');
  assert.equal(safeReturn('/evaluation.html?task_id=22&page_no=1', origin), '/evaluation.html?task_id=22&page_no=1');
  assert.equal(safeReturn('https://evil.example/evaluation.html?task_id=22', origin), '/history.html');
});
test('QR polling waits for explicit confirmation and stops after success', async () => {
  let timer, state, success, polls = 0;
  const flow = createFlow({ request: async path => path.endsWith('challenges') ? { scene: 'a'.repeat(32), qr_image: 'data:image/png;base64,x', expires_in: 300 } : (++polls === 1 ? { status: 'scanned' } : { status: 'confirmed', access_token: 't'.repeat(43), user: { id: 1, provider: 'wechat' } }),
    onChange: value => state = value, onSuccess: value => success = value, setTimer: callback => { timer = callback; return 1; }, clearTimer: () => { timer = null; } });
  await flow.refresh();
  assert.equal(state.status, 'waiting');
  await timer();
  assert.equal(state.status, 'scanned'); assert.equal(success, undefined);
  await timer();
  assert.equal(success.user.id, 1); assert.equal(timer, null);
});
test('expired QR and disposal stop polling without treating them as login', async () => {
  let timer, state;
  const flow = createFlow({ request: async path => path.endsWith('challenges') ? { scene: 'a'.repeat(32), expires_in: 300 } : { status: 'expired' },
    onChange: value => state = value, onSuccess: () => assert.fail('must not log in'), setTimer: callback => { timer = callback; return 1; }, clearTimer: () => { timer = null; } });
  await flow.refresh(); await timer();
  assert.equal(state.status, 'expired'); assert.equal(timer, null);
  flow.dispose();
});
test('restoring a cached page can resume with a fresh QR', async () => {
  let count = 0, timer;
  const flow = createFlow({ request: async () => { count++; return { scene: 'a'.repeat(32), expires_in: 300 }; },
    onChange() {}, onSuccess() {}, setTimer: callback => { timer = callback; return 1; }, clearTimer: () => { timer = null; } });
  await flow.refresh(); flow.dispose();
  assert.equal(timer, null);
  await flow.resume();
  assert.equal(count, 2); assert.equal(typeof timer, 'function');
});
