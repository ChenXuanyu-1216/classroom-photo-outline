const { test } = require('node:test');
const assert = require('node:assert/strict');
const { multipart } = require('../utils/multipart');
const { createApi, readResponse, networkError } = require('../utils/api');
const { inspectTask } = require('../utils/workflow');
const testSession = { authorization: async () => 'Bearer explicit-test-session', invalidate() {}, currentUser: () => ({ id: 1 }) };

test('multipart preserves binary image bytes and selected page order', async () => {
  const files = [{ path: 'b', name: '第二张.png' }, { path: 'a', name: 'a.jpg' }];
  const bytes = { b: new Uint8Array([0, 255, 13, 10]).buffer, a: new Uint8Array([128, 1]).buffer };
  const result = await multipart(files, path => Promise.resolve(bytes[path]), 'test-boundary');
  const response = new Response(result.body, { headers: { 'Content-Type': result.contentType } });
  const form = await response.formData();
  const uploaded = form.getAll('files');
  assert.equal(uploaded.length, 2);
  assert.equal(uploaded[0].name, 'page_1.png');
  assert.deepEqual([...new Uint8Array(await uploaded[0].arrayBuffer())], [0, 255, 13, 10]);
  assert.deepEqual([...new Uint8Array(await uploaded[1].arrayBuffer())], [128, 1]);
  assert.equal(form.get('order'), '1,2');
});

test('oversize batch is rejected before network upload', async () => {
  await assert.rejects(multipart([{ path: 'a', name: 'a.png' }], async () => new ArrayBuffer(8 * 1024 * 1024 + 1)), /8MB/);
});

test('API errors retain backend code and retryability; non-JSON is not success', () => {
  assert.throws(() => readResponse({ statusCode: 409, data: { code: 'TASK_BUSY', message: '处理中', retryable: true } }), e => e.code === 'TASK_BUSY' && e.retryable);
  assert.throws(() => readResponse({ statusCode: 200, data: '<html>error</html>' }), /数据/);
});

test('pipeline writes are async and never automatically retried', async () => {
  let calls = 0;
  const api = createApi({ request(options) {
    calls++;
    assert.equal(options.header.Prefer, 'respond-async');
    assert.equal(options.method, 'POST');
    assert.equal(options.url, 'http://localhost:8000/api/tasks/7/pipeline');
    options.fail({ errMsg: 'network lost' });
  } }, () => 'http://localhost:8000', testSession);
  await assert.rejects(api.pipeline(7), /network lost/);
  assert.equal(calls, 1);
});

test('resume only reads active job state and retains an older saved outline', async () => {
  const api = {
    task: async () => ({ status: 'running', active_job: { status: 'running', operation: 'regenerate' } }),
    pages: async () => ({ pages: [] }),
    outline: async () => ({ markdown: '旧大纲', version: 2 })
  };
  const state = await inspectTask(api, 7);
  assert.equal(state.running, true);
  assert.equal(state.hasOutline, true);
  assert.equal(state.title, '后台处理中');
});

test('partial results expose failed pages; only explicit missing outline is suppressed', async () => {
  const api = {
    task: async () => ({ status: 'partial' }),
    pages: async () => ({ pages: [{ page_no: 2, failed_flag: true }] }),
    outline: async () => ({ markdown: '已生成', version: 1 })
  };
  const state = await inspectTask(api, 1);
  assert.equal(state.hasOutline, true);
  assert.equal(state.failedPages.length, 1);
  api.outline = async () => { throw Object.assign(new Error('网关故障'), { code: 'NETWORK_ERROR' }); };
  await assert.rejects(inspectTask(api, 1), /网关故障/);
});

test('Word export refuses HTTP failures and opens a real docx only after download', async () => {
  let opened = false;
  const platform = {
    downloadFile(options) { options.success({ statusCode: 502, tempFilePath: '/error' }); },
    openDocument() { opened = true; }
  };
  const api = createApi(platform, () => 'https://example.com', testSession);
  await assert.rejects(api.exportWord(1), /下载/);
  assert.equal(opened, false);
  platform.downloadFile = options => options.success({ statusCode: 200, tempFilePath: '/word' });
  platform.openDocument = options => { opened = options.fileType === 'docx' && options.showMenu; options.success({}); };
  await api.exportWord(1);
  assert.equal(opened, true);
});

test('network diagnostics distinguish WeChat domain checks from stopped backend', () => {
  const blocked = networkError({ errMsg: 'request:fail url not in domain list' });
  assert.equal(blocked.code, 'DOMAIN_NOT_ALLOWED');
  assert.match(blocked.message, /开发者工具/);
  assert.match(blocked.message, /合法域名/);
  const refused = networkError({ errMsg: 'request:fail net::ERR_CONNECTION_REFUSED' });
  assert.equal(refused.code, 'BACKEND_UNREACHABLE');
  assert.match(refused.message, /启动/);
});


test('generic request fail explains backend reachability and preserves diagnostics', () => {
  const error = networkError({ errMsg: 'request:fail', errno: -1 });
  assert.equal(error.code, 'NETWORK_ERROR');
  assert.match(error.message, /后端/);
  assert.match(error.message, /局域网/);
  assert.equal(error.rawMessage, 'request:fail');
  assert.equal(error.errno, -1);
});

test('address unreachable explains listener, changed IP and local network permission', () => {
  const error = networkError({ errMsg: 'request:fail net::ERR_ADDRESS_UNREACHABLE' });
  assert.equal(error.code, 'BACKEND_UNREACHABLE');
  assert.match(error.message, /0\.0\.0\.0/);
  assert.match(error.message, /本地网络/);
});

test('failed Word transfer retains the same network diagnosis without retrying', async () => {
  let requests = 0;
  const api = createApi({ downloadFile(options) { requests++; options.fail({ errMsg: 'downloadFile:fail net::ERR_ADDRESS_UNREACHABLE' }); } }, () => 'http://192.0.2.25:8000', testSession);
  await assert.rejects(api.exportWord(1), error => error.code === 'BACKEND_UNREACHABLE' && /本地网络/.test(error.message));
  assert.equal(requests, 1);
});
