const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { documentFixture } = require('./helpers/dom.cjs');
const Exp05 = require('../js/evaluation-model.js');
const source = fs.readFileSync(path.join(__dirname, '../js/evaluation.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../evaluation.html'), 'utf8');

function page() {
    const fixture = documentFixture();
    for (const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)) {
        const node = fixture.add(fixture.body, match[1], match[2]);
        node.dataset = {}; node.value = ''; node.disabled = false;
    }
    const byId = id => fixture.document.getElementById(id);
    const form = byId('review-form');
    form.values = new Map(); form.reset = () => form.values.clear();
    const callbacks = {}, requests = [];
    let finishSave, confirmCount = 0, failDetail = false;
    const snapshot = id => ({ id, sha256: 'audit', created_at: '2026-11-03T00:00:00Z', reviews: [], modules: [],
        evidence: { task: {id: 1, status: 'success'}, pages: [], configuration: {recognizer: 'mock'}, limits: [], prompt_hashes: {} } });
    const ok = data => ({ok: true, json: async () => data});
    const request = async (url, options) => {
        if (options.method === 'POST' && url.endsWith('/reviews')) {
            const body = JSON.parse(options.body); requests.push(body);
            return new Promise(resolve => { finishSave = (success = true) => resolve(success
                ? ok({id: 'r', created_at: 'now', ...body, metrics: null, module_pass: null})
                : {ok: false, status: 503, json: async () => ({message: '保存失败'})}); });
        }
        if (url.endsWith('/evaluations')) return ok({snapshots: ['a', 'b'].map(id => ({id, status: 'success', created_at: '2026-11-03T00:00:00Z'}))});
        if (failDetail) { failDetail = false; return {ok: false, status: 503, json: async () => ({message: '详情暂时不可用'})}; }
        return ok(snapshot(url.split('/').pop()));
    };
    const context = vm.createContext({document: fixture.document, location: {search: '?task_id=1'},
        window: {addEventListener: (name, callback) => {callbacks[name] = callback;}}, WorkspaceAuth: {fetch: request},
        Exp05, URL, URLSearchParams, confirm: () => {confirmCount++; return false;},
        FormData: class {constructor(f) {this.values = new Map(f.values);} get(key) {return this.values.get(key);}}});
    const ready = vm.runInContext(source, context);
    function warns() { let warned = false; callbacks.beforeunload({preventDefault() {warned = true;}}); return warned; }
    async function beginSave() {
        for (const [key, value] of Object.entries({reviewer: 'audit', review_kind: 'self', scope_type: 'page', scope_name: 'P1', expected: 'evidence', observed: 'observation', notes: 'before-save'})) form.values.set(key, value);
        await form.emit('input');
        const pending = form.emit('submit', {preventDefault() {}});
        for (let i = 0; i < 10 && !finishSave; i++) await Promise.resolve();
        assert.ok(finishSave);
        return {pending, finish: finishSave};
    }
    return {byId, form, ready, warns, beginSave, requests, finishLatestSave: () => finishSave(), confirmations: () => confirmCount, failNextDetail: () => {failDetail = true;}};
}

test('editing during save remains unsaved and switching snapshots requires confirmation', async () => {
    const env = page(); await env.ready;
    const saving = await env.beginSave();
    env.form.values.set('notes', 'edit-during-save'); await env.form.emit('input');
    saving.finish(); await saving.pending;
    assert.equal(env.requests[0].notes, 'before-save');
    assert.equal(env.warns(), true);
    assert.match(env.byId('evaluation-notice').textContent, /尚未保存/);
    env.byId('snapshot-select').value = 'b'; await env.byId('snapshot-select').emit('change');
    assert.equal(env.confirmations(), 1);
    assert.equal(env.form.values.get('notes'), 'edit-during-save');
    assert.equal(env.byId('snapshot-select').value, 'a');
    const resaving = env.form.emit('submit', {preventDefault() {}});
    env.finishLatestSave(); await resaving;
    assert.equal(env.requests[1].notes, 'edit-during-save');
    assert.equal(env.warns(), false);
});

test('unchanged successful save clears dirty state; failed save preserves it', async () => {
    const env = page(); await env.ready;
    let saving = await env.beginSave(); saving.finish(); await saving.pending;
    assert.equal(env.warns(), false);
    saving = await env.beginSave(); saving.finish(false); await saving.pending;
    assert.equal(env.warns(), true);
    assert.equal(env.form.values.get('notes'), 'before-save');
});

test('retrying a failed refresh stays on the bound snapshot and preserves its draft', async () => {
    const env = page(); await env.ready;
    env.byId('snapshot-select').value = 'b'; await env.byId('snapshot-select').emit('change');
    env.form.values.set('notes', 'older-snapshot-draft'); await env.form.emit('input');
    env.failNextDetail(); await env.byId('refresh-evidence').emit('click');
    assert.match(env.byId('evaluation-notice').textContent, /详情暂时不可用/);
    await env.byId('refresh-evidence').emit('click');
    assert.equal(env.byId('snapshot-select').value, 'b');
    assert.equal(env.form.values.get('notes'), 'older-snapshot-draft');
    assert.equal(env.warns(), true);
});
