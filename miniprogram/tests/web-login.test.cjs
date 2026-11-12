const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function page(service) {
  let definition;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../pages/web-login/web-login.js'), 'utf8'), {
    Page: value => definition = value,
    wx: { navigateBack() {}, redirectTo() {} },
    require: name => name.endsWith('/api') ? { api: () => service } : { getBaseUrl: () => 'http://test' },
  });
  return Object.assign(definition, { data: { ...definition.data }, setData(value) { Object.assign(this.data, value); } });
}
test('scan signs in and displays confirmation without automatically approving', async () => {
  const calls = [];
  const p = page({ login: async () => ({ id: 1 }), scanWebLogin: async scene => { calls.push(scene); return { verification: 'ABC123', expires_in: 100 }; }, confirmWebLogin: async () => assert.fail('must explicitly confirm') });
  await p.onLoad({ scene: 'a'.repeat(32) });
  assert.equal(p.data.state, 'ready'); assert.equal(p.data.verification, 'ABC123');
  assert.equal(calls.length, 1);
});
test('invalid scene never authenticates; duplicate confirmation submits once', async () => {
  let count = 0;
  const p = page({ login: async () => ({ id: 1 }), scanWebLogin: async () => ({ verification: 'ABC123' }), confirmWebLogin: async (scene, approve) => { count++; assert.equal(approve, true); } });
  await p.onLoad({ scene: 'invalid' }); assert.equal(p.data.state, 'error');
  await p.onLoad({ scene: 'a'.repeat(32) });
  await Promise.all([p.confirmLogin(), p.confirmLogin()]);
  assert.equal(count, 1); assert.equal(p.data.state, 'approved');
});
