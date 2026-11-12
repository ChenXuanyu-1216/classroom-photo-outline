const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { documentFixture } = require('./helpers/dom.cjs');
const source = fs.readFileSync(path.join(__dirname, '../js/upload.js'), 'utf8');

function page(createTask) {
    const env = documentFixture();
    const elements = {};
    for (const id of ['drop-zone', 'file-input', 'file-list', 'file-count', 'generate-btn', 'upload-message']) elements[id] = env.add(env.body, 'div', id);
    const context = vm.createContext({ document: env.document, API: { createTask }, alert() {}, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init?.detail; } } });
    context.window = context; context.matchMedia = () => ({ matches: true }); context.location = { href: '/' };
    vm.runInContext(source, context);
    return { ...env, elements, context, choose: files => elements['file-input'].emit('change', { target: { files } }) };
}

test('adding another selection retains the original file objects and submits both in display order', async () => {
    let submitted;
    const env = page(async (files, order) => { submitted = { files: [...files], order }; return { status: 400, json: async () => ({ message: '测试拒绝' }) }; });
    const first = { name: '<img src=x>.jpg', size: 8, lastModified: 1 };
    const second = { name: '第二张.png', size: 10, lastModified: 2 };
    await env.choose([first]); await env.choose([second]);
    assert.equal(env.elements['file-count'].textContent, '2 张');
    await env.elements['generate-btn'].emit('click');
    assert.equal(submitted.files[0], first); assert.equal(submitted.files[1], second); assert.equal(submitted.order, '1,2');
    assert.equal(env.elements['file-list'].querySelector('img'), null);
    assert.match(env.elements['upload-message'].textContent, /测试拒绝/);
});

test('remounting the retained upload view preserves ordering and does not submit twice', async () => {
    let finish, calls = 0, submitted;
    const env = page((files) => { calls++; submitted = [...files]; return new Promise(resolve => { finish = resolve; }); });
    const first = { name: 'a.jpg' }, second = { name: 'b.jpg' };
    await env.choose([first, second]);
    const up = env.elements['file-list'].querySelectorAll('button').find(button => button.getAttribute('aria-label') === '上移第 2 张照片');
    await up.emit('click');
    assert.ok(env.context.ClassroomUpload, 'the upload view needs an idempotent mount for workspace navigation');
    env.context.ClassroomUpload.init();
    const pending = env.elements['generate-btn'].emit('click');
    await env.elements['generate-btn'].emit('click');
    assert.equal(calls, 1); assert.equal(submitted[0], second); assert.equal(submitted[1], first);
    finish({ status: 400, json: async () => ({ message: '重试' }) }); await pending;
    assert.equal(env.elements['generate-btn'].disabled, false);
    assert.equal(env.elements['file-count'].textContent, '2 张');
});

test('the added-file announcement survives the native input clearing its live FileList', async () => {
    const env = page();
    const files = [{ name: '课堂.png' }];
    Object.defineProperty(env.elements['file-input'], 'value', { set() { files.length = 0; } });
    await env.choose(files);
    assert.match(env.elements['upload-message'].textContent, /已添加 1 张/);
    assert.equal(env.elements['file-count'].textContent, '1 张');
});

test('keyboard reordering retains the Down action on the moved photo', async () => {
    const env = page();
    await env.choose([{ name: 'a.jpg' }, { name: 'b.jpg' }, { name: 'c.jpg' }]);
    const down = env.elements['file-list'].querySelectorAll('button').find(button => button.getAttribute('aria-label') === '下移第 1 张照片');
    down.focus(); await down.emit('click');
    assert.equal(env.document.activeElement.getAttribute('aria-label'), '下移第 2 张照片');
    await env.document.activeElement.emit('click');
    assert.deepEqual(env.elements['file-list'].querySelectorAll('.file-name').map(node => node.textContent), ['b.jpg', 'c.jpg', 'a.jpg']);
});
