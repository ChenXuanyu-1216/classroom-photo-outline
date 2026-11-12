const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function loadPage(name, service, storage = new Map(), confirmAction = async () => true) {
  let definition;
  const errors = [];
  const filename = path.resolve(__dirname, `../pages/${name}/${name}.js`);
  const localRequire = createRequire(filename);
  const wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key), showToast() {}, showModal(options) { options.success({ confirm: true }); }
  };
  const context = { wx, setTimeout, clearTimeout, Page(value) { definition = value; },
    require(id) {
      if (id === '../../utils/api') return { api: () => service };
      if (id === '../../utils/settings') return { draftKey: id => `draft:${id}` };
      if (id === '../../utils/ui') return { taskId: options => options.task_id, confirm: confirmAction, showError(error) { errors.push(error); } };
      return localRequire(id);
    }
  };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), context, { filename });
  const page = Object.assign({}, definition, { data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } });
  return { page, wx, storage, errors };
}

test('export saves edited Markdown before downloading Word', async () => {
  const calls = [];
  const { page } = loadPage('outline', {
    outline: async () => ({ version: 1 }),
    save: async (_, markdown) => { calls.push(['save', markdown]); return { version: 2 }; },
    exportWord: async () => { calls.push(['export']); }
  });
  page._id = '7'; page.data = Object.assign(page.data, { dirty: true, loading: false, markdown: '最新修改', version: 1 });
  await page.exportWord();
  assert.deepEqual(calls, [['save', '最新修改'], ['export']]);
  assert.equal(page.data.dirty, false);
});

test('failed save never downloads stale Word and leaves draft dirty', async () => {
  let exports = 0;
  const { page } = loadPage('outline', { outline: async () => ({ version: 1 }),
    save: async () => { throw new Error('断网'); }, exportWord: async () => { exports++; } });
  page._id = '7'; Object.assign(page.data, { dirty: true, loading: false, markdown: '本地编辑', version: 1 });
  await page.exportWord();
  assert.equal(exports, 0);
  assert.equal(page.data.dirty, true);
  assert.equal(page.data.busy, false);
});

test('progress re-entry does not resubmit pipeline for queued task; hiding stops polling', async () => {
  let writes = 0;
  const { page } = loadPage('progress', {
    task: async () => ({ status: 'queued', active_job: { operation: 'pipeline', status: 'queued' } }),
    pages: async () => ({ pages: [] }),
    outline: async () => { throw Object.assign(new Error('未生成'), { code: 'PIPELINE_ERROR' }); },
    pipeline: async () => { writes++; }
  });
  page.onLoad({ task_id: '7', start: '1' });
  page._visible = true; page._epoch = 1;
  await page.refresh();
  assert.equal(writes, 0);
  assert.equal(page.data.running, true);
  assert.ok(page._timer);
  page.onHide();
  assert.equal(page._visible, false);
  assert.equal(page._timer._destroyed, true);
});

test('progress resume after uncertain submission never submits another pipeline', async () => {
  let writes = 0;
  const { page } = loadPage('progress', {
    task: async () => ({ status: 'pending' }), pages: async () => ({ pages: [] }),
    outline: async () => { throw Object.assign(new Error('未生成'), { code: 'PIPELINE_ERROR' }); },
    pipeline: async () => { writes++; throw new Error('response lost'); }
  });
  page.onLoad({ task_id: '7', start: '1' }); page._visible = true; page._epoch = 1;
  await page.refresh();
  await page.refresh();
  assert.equal(writes, 1);
  page.onHide();
});

test('progress cannot submit writes during a state read', async () => {
  let writes = 0;
  const { page } = loadPage('progress', { pipeline: async () => { writes++; } });
  page._id = '7'; page._reading = true;
  await page.submit('pipeline');
  assert.equal(writes, 0);
});

test('clean but stale editor loads and confirms latest version before export', async () => {
  let exported = false;
  const { page } = loadPage('outline', {
    outline: async () => ({ version: 2, markdown: '服务器新版', warnings: ['新版提示'] }),
    exportWord: async () => { assert.equal(page.data.markdown, '服务器新版'); exported = true; }
  });
  page._id = '7'; Object.assign(page.data, { loading: false, version: 1, markdown: '屏幕旧版', dirty: false });
  await page.exportWord();
  assert.equal(exported, true);
  assert.equal(page.data.version, 2);
});

test('workspace tabs switch immediately without waiting for login or history, retaining photo selection', () => {
  const { page, wx } = loadPage('upload', { list: () => new Promise(() => {}) });
  let nativeNavigations = 0;
  wx.navigateTo = () => { nativeNavigations++; };
  const photos = [{ path: 'selected.jpg' }];
  page.data.files = photos;
  page.selectWorkspace('history');
  assert.equal(page.data.activeView, 'history');
  assert.equal(page.data.turning, 'to-history');
  assert.equal(page.data.files, photos);
  assert.equal(nativeNavigations, 0);
  page.onUnload();
});

test('choosing photos waits for native privacy agreement instead of opening the album early', async () => {
  const { page, wx } = loadPage('upload', {});
  let needsAgreement = true, picks = 0;
  wx.getPrivacySetting = options => options.success({ needAuthorization: needsAgreement, privacyContractName: '《课堂隐私保护指引》' });
  wx.chooseMedia = options => { picks++; options.success({ tempFiles: [{ tempFilePath: 'photo.jpg', size: 1024 }] }); };
  await page.choose();
  assert.equal(page.data.privacyOpen, true);
  assert.equal(picks, 0);
  needsAgreement = false;
  await page.agreePhotoPrivacy({ detail: { errMsg: 'agreePrivacyAuthorization:ok' } });
  assert.equal(picks, 1); assert.equal(page.data.files.length, 1);
  assert.equal(page.data.privacyOpen, false);
});
test('declining privacy does not select or upload photos', async () => {
  const { page, wx } = loadPage('upload', {});
  wx.getPrivacySetting = options => options.success({ needAuthorization: true });
  wx.chooseMedia = () => assert.fail('must not open album');
  await page.choose();
  page.declinePhotoPrivacy();
  assert.equal(page.data.privacyOpen, false); assert.equal(page.data.files.length, 0);
});
test('native agreement event rechecks platform consent without assuming an error-message payload', async () => {
  const { page, wx } = loadPage('upload', {});
  wx.getPrivacySetting = options => options.success({ needAuthorization: false });
  wx.chooseMedia = options => options.success({ tempFiles: [{ tempFilePath: 'allowed.jpg', size: 100 }] });
  page.data.privacyOpen = true;
  await page.agreePhotoPrivacy({ detail: {} });
  assert.equal(page.data.files.length, 1);
});
test('a failed picker preserves existing photos and can be retried', async () => {
  const { page, wx, errors } = loadPage('upload', {});
  const previous = [{ path: 'existing.jpg', size: 100, key: 'old' }];
  page.data.files = previous;
  wx.chooseMedia = options => options.fail({ errno: 112, errMsg: 'chooseMedia:fail api scope is not declared in the privacy agreement' });
  await page.choose();
  assert.equal(errors[0].code, 'PHOTO_PRIVACY_UNDECLARED');
  assert.equal(page.data.files, previous); assert.equal(page._choosing, false);
});
test('a late picker callback cannot update an unloaded page', async () => {
  const { page, wx } = loadPage('upload', {});
  let picker, signalOpened;
  const opened = new Promise(resolve => { signalOpened = resolve; });
  wx.chooseMedia = options => { picker = options; signalOpened(); };
  const choosing = page.choose();
  await opened;
  page.onUnload();
  picker.success({ tempFiles: [{ tempFilePath: 'late.jpg', size: 100 }] });
  await choosing;
  assert.equal(page.data.files.length, 0);
});


test('same-version regeneration cannot silently overwrite the server from a dirty editor', async () => {
  let server = { version: 1, markdown: '# Original' }, saves = 0;
  const prompts = [];
  const { page, storage } = loadPage('outline', {
    outline: async () => server, pages: async () => ({ pages: [] }), meta: async () => ({ model_provider: 'mock' }),
    save: async () => { saves++; return { version: 2 }; }
  }, new Map(), async message => { prompts.push(message); return false; });
  page._id = '7';
  await page.load();
  page.edit({ detail: { value: '# Local edits' } });
  server = { version: 1, markdown: '# Regenerated on another client' };
  await page.save();
  assert.equal(saves, 0);
  assert.equal(prompts.length, 1);
  assert.equal(page.data.markdown, '# Local edits');
  assert.equal(page.data.dirty, true);
  assert.equal(storage.get('draft:7').markdown, '# Local edits');
});

test('same-version regeneration is loaded and confirmed before exporting a clean editor', async () => {
  let server = { version: 1, markdown: '# Original' }, exports = 0;
  const prompts = [];
  const { page } = loadPage('outline', {
    outline: async () => server, pages: async () => ({ pages: [] }), meta: async () => ({ model_provider: 'mock' }),
    exportWord: async () => { exports++; }
  }, new Map(), async message => { prompts.push(message); return false; });
  page._id = '7';
  await page.load();
  server = { version: 1, markdown: '# Regenerated on another client' };
  await page.exportWord();
  assert.equal(exports, 0);
  assert.equal(prompts.length, 1);
  assert.equal(page.data.markdown, server.markdown);
  assert.equal(page._savedMarkdown, server.markdown);
  assert.equal(page.data.dirty, false);
});
