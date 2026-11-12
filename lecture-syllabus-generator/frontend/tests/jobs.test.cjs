const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../js/api.js'), 'utf8');
const ok = body => ({ ok: true, status: 200, json: async () => body });
function api(fetch) {
    return vm.runInNewContext(source + '\nAPI;', { fetch, Response, setTimeout: fn => fn(), encodeURIComponent });
}
const accepted = () => ({ ok: true, status: 202, json: async () => ({ job_id: 'j1', task_id: 7, operation: 'pipeline', status: 'queued' }) });

test('model mutations choose async and polling sends GET only until terminal', async () => {
    const calls = [];
    let polls = 0;
    const API = api(async (url, options) => {
        calls.push([url, options]);
        if (options?.method === 'POST') return accepted();
        return ok({ job_id: 'j1', task_id: 7, operation: 'pipeline', status: ++polls === 1 ? 'running' : 'success', result: { module_count: 2 } });
    });
    const response = await API.runPipeline(7);
    assert.equal(calls[0][1].headers.Prefer, 'respond-async');
    const result = await API.waitForJob(response);
    assert.equal((await result.json()).module_count, 2);
    assert.equal(calls.length, 3);
    assert.ok(calls.slice(1).every(([url, opts]) => url === '/api/tasks/7/jobs/j1' && (!opts?.method || opts.method === 'GET')));
});

test('polling failure preserves retryability and never starts another job', async () => {
    let calls = 0;
    const API = api(async () => { calls++; return ok({ job_id: 'j1', task_id: 7, status: 'failed', error: {
        code: 'MODEL_AUTH_ERROR', message: '检查密钥', retryable: false } }); });
    const result = await API.waitForJob(accepted());
    assert.equal(result.ok, false);
    assert.equal((await result.json()).retryable, false);
    assert.equal(calls, 1);
});

test('polling network failure stops without repeating mutations', async () => {
    let calls = 0;
    const API = api(async () => { calls++; throw new Error('offline'); });
    await assert.rejects(API.waitForJob(accepted()), /offline/);
    assert.equal(calls, 1);
});

test('synchronous legacy response passes through', async () => {
    const API = api(() => { throw new Error('must not fetch'); });
    const response = ok({ status: 'success' });
    assert.equal(await API.waitForJob(response), response);
});
