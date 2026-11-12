const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function header(current, stack, locked = false) {
  let component;
  const calls = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../components/studio-header/studio-header.js'), 'utf8'), {
    require: require('node:module').createRequire(path.join(__dirname, '../components/studio-header/studio-header.js')),
    Component(value) { component = value; }, getCurrentPages: () => stack.map(route => ({ route })),
    wx: { navigateTo: options => calls.push(['navigate', options.url]), navigateBack: options => calls.push(['back', options.delta]), showToast() {} }
  });
  return { instance: Object.assign({ properties: { current, locked } }, component.methods), calls };
}

test('tapping active upload tab leaves selected photos on the existing page', () => {
  const { instance, calls } = header('upload', ['pages/upload/upload']);
  assert.equal(typeof instance.go, 'function');
  instance.go({ currentTarget: { dataset: { page: 'upload' } } });
  assert.deepEqual(calls, []);
});

test('returning from history reuses the upload page and its selection', () => {
  const { instance, calls } = header('history', ['pages/upload/upload', 'pages/history/history']);
  instance.go({ currentTarget: { dataset: { page: 'upload' } } });
  assert.deepEqual(calls, [['back', 1]]);
});

test('navigation during an in-flight upload is blocked', () => {
  const { instance, calls } = header('upload', ['pages/upload/upload'], true);
  instance.go({ currentTarget: { dataset: { page: 'history' } } });
  assert.deepEqual(calls, []);
});
