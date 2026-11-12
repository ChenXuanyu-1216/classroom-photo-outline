const { test } = require('node:test');
const assert = require('node:assert/strict');
const { openWorkspace } = require('../utils/navigation');
test('return from progress reuses the original workspace and preserves selected photos', () => {
  const files = [{ path: 'retained-photo.jpg' }];
  const root = { route: 'pages/upload/upload', data: { files }, selectWorkspace(page) { this.data.activeView = page; } };
  const calls = [];
  const platform = { navigateBack: value => calls.push(value.delta), navigateTo() { throw new Error('must not create a duplicate workspace'); }, redirectTo() { throw new Error('must not replace the workspace'); } };
  openWorkspace(platform, [root, { route: 'pages/progress/progress' }, { route: 'pages/history/history' }], 'history', true);
  assert.equal(root.data.files, files);
  assert.equal(root.data.activeView, 'history');
  assert.deepEqual(calls, [2]);
});
