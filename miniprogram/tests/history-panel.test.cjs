const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function panel(service, confirmAction = async () => false) {
  let definition;
  let server = 'https://one.example';
  const events = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../components/history-panel/history-panel.js'), 'utf8'), {
    Component(value) { definition = value; },
    wx: { removeStorageSync() {} },
    require(id) {
      if (id.endsWith('/api')) return { api: () => service };
      if (id.endsWith('/settings')) return { getBaseUrl: () => server, draftKey: id => `draft:${id}` };
      return { showError() {}, confirm: confirmAction };
    }
  });
  const value = Object.assign({ data: structuredClone(definition.data), _attached: true, setData(patch) { Object.assign(this.data, patch); }, triggerEvent(name, detail) { events.push([name, detail]); } }, definition.methods);
  return { value, events, setServer(next) { server = next; } };
}
test('revoked login clears previously cached records instead of retaining their titles', async () => {
  let revoked = false;
  const { value, events } = panel({ currentUser: () => ({ id: 1 }), list: async () => {
    if (revoked) throw Object.assign(new Error('登录过期'), { status: 401 });
    return { tasks: [{ task_id: 1, title: 'private old record' }] };
  } });
  await value.load();
  assert.equal(value.data.tasks.length, 1);
  revoked = true;
  await value.load();
  assert.equal(value.data.tasks.length, 0);
  assert.ok(events.some(([name, detail]) => name === 'authchange' && detail.userId === null));
});
test('a late response from a previous backend cannot repopulate reset history', async () => {
  let finish;
  const env = panel({ currentUser: () => ({ id: 1 }), list: () => new Promise(resolve => { finish = resolve; }) });
  const pending = env.value.load();
  env.setServer('https://two.example'); env.value.reset();
  finish({ tasks: [{ task_id: 1, title: 'old server record' }] });
  await pending;
  assert.equal(env.value.data.tasks.length, 0);
  assert.equal(env.value.data.loading, false);
});


test('a list response started before deletion cannot restore the deleted row', async () => {
  let finishOldList, reads = 0, removed = 0;
  const { value } = panel({
    currentUser: () => ({ id: 1 }),
    list: async () => {
      reads++;
      if (reads === 1) return new Promise(resolve => { finishOldList = resolve; });
      return { tasks: [] };
    },
    remove: async id => { assert.equal(id, 7); removed++; }
  }, async () => true);
  value.data.tasks = [{ task_id: 7, title: 'cached record' }];
  const pending = value.load();
  await value.remove({ currentTarget: { dataset: { id: 7 } } });
  finishOldList({ tasks: [{ task_id: 7, title: 'old list response' }] });
  await pending;
  assert.equal(removed, 1);
  assert.equal(reads, 2);
  assert.equal(value.data.tasks.length, 0);
  assert.equal(value.data.loading, false);
});
