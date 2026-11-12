const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../js/outline.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../outline.html'), 'utf8');
class Element {
    constructor() {
        this.events = {}; this.hidden = false; this.disabled = false; this.value = ''; this.textContent = '';
        const classes = new Set();
        this.classList = { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) };
    }
    get innerHTML() { return this.html ?? this.textContent.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }
    set innerHTML(value) { this.html = value; }
    addEventListener(name, callback) { this.events[name] = callback; }
    setAttribute(name, value) { this[name] = value; }
    click() {}
}
const ok = body => ({ ok: true, json: async () => body });
function page(api = {}, confirm = () => true) {
    const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
    const calls = [];
    const API = {
        getTask: async () => ok({ task_id: 7, status: 'success' }),
        getOutline: async () => ok({ markdown: '# 原大纲\n## 模块\n- 原文（来源：P1）【课堂原话】', version: 1 }),
        updateOutline: async (id, markdown) => { calls.push(['save', markdown]); return ok({ version: 2 }); },
        regenerate: async () => { calls.push(['regenerate']); return ok({ status: 'success' }); },
        exportWord: async () => { calls.push(['export']); return { ok: true, blob: async () => ({}) }; },
        ...api,
    };
    const context = vm.createContext({ document: { getElementById: id => elements[id], createElement: () => new Element() },
        window: { location: { search: '?task_id=7' } }, API, confirm, alert() {}, URLSearchParams,
        URL: { createObjectURL: () => 'blob:word', revokeObjectURL() {} } });
    const ready = Promise.resolve(vm.runInContext(source, context));
    return { elements, calls, API, ready };
}

test('multiline knowledge remains one escaped item with indentation and source', async () => {
    const env = page({ getOutline: async () => ok({ markdown: '# 大纲\n## 代码\n- if Value:\n      print("<input>")\n      # keep\n      return Value（来源：P1）【课堂原话】', warnings: ['P2：右下角看不清'] }) });
    await env.ready;
    const rendered = env.elements['outline-view'].innerHTML;
    assert.equal((rendered.match(/<li>/g) || []).length, 1);
    assert.match(rendered, /if Value:\n    print/);
    assert.match(rendered, /&lt;input&gt;/);
    assert.doesNotMatch(rendered, /<input>/);
    assert.match(env.elements['outline-quality'].textContent, /P2：右下角看不清/);
});

test('AI expansion is visibly labeled and keeps related pages through edit and save', async () => {
    const markdown = '# 大纲\n## AI 知识拓展\n- 监督学习 & <input>（关联课堂页：P1,P3）【AI生成】';
    const env = page({ getOutline: async () => ok({ markdown }) });
    await env.ready;
    assert.match(env.elements['outline-view'].innerHTML, /class="type-ai">【AI生成】/);
    assert.match(env.elements['outline-view'].innerHTML, /class="source-tag">（关联课堂页：P1,P3）/);
    assert.match(env.elements['outline-view'].innerHTML, /&lt;input&gt;/);
    env.elements['edit-btn'].events.click();
    await env.elements['save-btn'].events.click();
    assert.deepEqual(env.calls, [['save', markdown]]);
    assert.match(env.elements['outline-view'].innerHTML, /class="type-ai">【AI生成】/);
});

test('load network failure offers retry and never rejects initialization', async () => {
    const env = page({ getOutline: async () => { throw new Error('offline'); } });
    await env.ready;
    assert.equal(env.elements['outline-status'].classList.contains('hidden'), false);
    assert.equal(env.elements['outline-retry-btn'].classList.contains('hidden'), false);
    assert.equal(env.elements['export-btn'].disabled, true);
    env.API.getOutline = async () => ok({ markdown: '# 恢复后的大纲', version: 1 });
    await env.elements['outline-retry-btn'].events.click();
    assert.match(env.elements['outline-view'].innerHTML, /恢复后的大纲/);
    assert.equal(env.elements['export-btn'].disabled, false);
});

test('save failure retains the editable draft and permits another save', async () => {
    const env = page({ updateOutline: async () => { throw new Error('offline'); } });
    await env.ready;
    env.elements['edit-btn'].events.click();
    env.elements['outline-editor'].value = '# 尚未保存的修改';
    await env.elements['save-btn'].events.click();
    assert.equal(env.elements['outline-editor'].value, '# 尚未保存的修改');
    assert.equal(env.elements['outline-editor'].classList.contains('hidden'), false);
    assert.equal(env.elements['save-btn'].disabled, false);
    env.API.updateOutline = async () => ok({ version: 2 });
    await env.elements['save-btn'].events.click();
    assert.match(env.elements['outline-view'].innerHTML, /尚未保存的修改/);
});

test('unsaved editing cannot export stale content', async () => {
    const env = page();
    await env.ready;
    env.elements['edit-btn'].events.click();
    env.elements['outline-editor'].value = '# 未保存';
    await env.elements['export-btn'].events.click();
    assert.deepEqual(env.calls, []);
    assert.equal(env.elements['export-btn'].disabled, true);
});

test('server error text is displayed as text and cannot become markup', async () => {
    const env = page({ getOutline: async () => ({ ok: false, json: async () => ({ message: '<img src=x onerror=alert(1)>' }) }) });
    await env.ready;
    assert.doesNotMatch(env.elements['outline-view'].innerHTML, /<img/);
    assert.match(env.elements['outline-status'].textContent, /<img/);
});

test('cancel regeneration sends no mutation and preserves the displayed result', async () => {
    const env = page({}, () => false);
    await env.ready;
    const previous = env.elements['outline-view'].innerHTML;
    await env.elements['regenerate-btn'].events.click();
    assert.deepEqual(env.calls, []);
    assert.equal(env.elements['outline-view'].innerHTML, previous);
});

test('pending save prevents duplicate submissions and reenables controls afterward', async () => {
    let finish;
    let requests = 0;
    const env = page({ updateOutline: () => { requests++; return new Promise(resolve => { finish = resolve; }); } });
    await env.ready;
    env.elements['edit-btn'].events.click();
    env.elements['outline-editor'].value = '# 保存一次';
    const first = env.elements['save-btn'].events.click();
    const second = env.elements['save-btn'].events.click();
    assert.equal(env.elements['save-btn'].disabled, true);
    finish(ok({ version: 2 }));
    await Promise.all([first, second]);
    assert.equal(requests, 1);
    assert.equal(env.elements['export-btn'].disabled, false);
});

test('regeneration followed by failed reload blocks stale preview export and offers reload', async () => {
    const env = page();
    await env.ready;
    env.API.getOutline = async () => { throw new Error('offline after regenerate'); };
    await env.elements['regenerate-btn'].events.click();
    assert.equal(env.elements['export-btn'].disabled, true);
    assert.equal(env.elements['outline-retry-btn'].classList.contains('hidden'), false);
    env.API.getOutline = async () => ok({ markdown: '# 最新大纲', version: 1 });
    await env.elements['outline-retry-btn'].events.click();
    assert.match(env.elements['outline-view'].innerHTML, /最新大纲/);
    assert.equal(env.elements['export-btn'].disabled, false);
});

test('lost regeneration response reconciles with GET only and waits for running task', async () => {
    const env = page({ regenerate: async () => { throw new Error('lost response'); } });
    await env.ready;
    await env.elements['regenerate-btn'].events.click();
    env.API.getTask = async () => ok({ status: 'running' });
    await env.elements['outline-retry-btn'].events.click();
    assert.equal(env.elements['export-btn'].disabled, true);
    assert.equal(env.elements['regenerate-btn'].disabled, true);
    env.API.getTask = async () => ok({ status: 'success' });
    env.API.getOutline = async () => ok({ markdown: '# 服务端已保存的新大纲', version: 1 });
    await env.elements['outline-retry-btn'].events.click();
    assert.match(env.elements['outline-view'].innerHTML, /服务端已保存的新大纲/);
    assert.equal(env.elements['export-btn'].disabled, false);
    assert.deepEqual(env.calls, []);
});
