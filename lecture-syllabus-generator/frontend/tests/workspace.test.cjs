const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { documentFixture } = require('./helpers/dom.cjs');
const filename = path.join(__dirname, '../js/workspace.js');
const source = fs.existsSync(filename) ? fs.readFileSync(filename, 'utf8') : '';

function page({ fail = false, uploading = false, reduced = true, pendingResponse } = {}) {
    const env = documentFixture();
    const nav = env.add(env.body, 'nav', '', 'kf-nav');
    const uploadLink = env.add(nav, 'a', '', 'kf-nav-item kf-current'); uploadLink.href = '/';
    const historyLink = env.add(nav, 'a', '', 'kf-nav-item'); historyLink.href = '/history.html';
    const main = env.add(env.body, 'main', '', 'kf-main');
    const uploadSection = env.add(main, 'section', 'upload-section'); env.add(uploadSection, 'h1', '').textContent = '整理今天的课堂';
    env.add(uploadSection, 'input', 'file-input'); env.add(uploadSection, 'p', 'workspace-status');
    const remote = documentFixture(); remote.document.title = '整理记录 · 课堂大纲';
    const remoteMain = remote.add(remote.body, 'main', '', 'kf-main');
    const historySection = remote.add(remoteMain, 'section', 'history-section');
    remote.add(historySection, 'div', 'task-list'); remote.add(historySection, 'p', 'workspace-status');
    const location = new URL('http://localhost/');
    const history = { state: null, pushState(state, _, url) { this.state = state; location.href = new URL(url, location).href; }, replaceState(state, _, url) { this.state = state; location.href = new URL(url, location).href; } };
    const windowEvents = {}; let reads = 0;
    const uploadState = { count: 2, isUploading: uploading };
    const context = vm.createContext({
        document: env.document, location, history, URL, AbortController, setTimeout, clearTimeout,
        matchMedia: () => ({ matches: reduced }), scrollTo() {},
        addEventListener(name, handler) { windowEvents[name] = handler; },
        fetch: async () => { reads++; if (fail) throw new Error('offline'); if (pendingResponse) await pendingResponse; return { ok: true, text: async () => '<history fixture>' }; },
        DOMParser: class { parseFromString() { return remote.document; } },
        ClassroomUpload: { init() {}, getState: () => uploadState },
        ClassroomHistory: { init() { return Promise.resolve(); } },
    });
    context.window = context;
    vm.runInContext(source, context);
    return { ...env, main, uploadSection, historySection, uploadLink, historyLink, context, location, windowEvents, uploadState, reads: () => reads };
}

test('visiting history and returning keeps the original upload DOM and browser route', async () => {
    const env = page();
    assert.ok(env.context.ClassroomWorkspace, 'workspace navigation must preserve the live upload view');
    await env.context.ClassroomWorkspace.navigate('history');
    assert.equal(env.main.children[0], env.historySection); assert.equal(env.location.pathname, '/history.html');
    await env.context.ClassroomWorkspace.navigate('upload');
    assert.ok(env.main.children[0] === env.uploadSection); assert.equal(env.location.pathname, '/');
    await env.context.ClassroomWorkspace.navigate('history');
    assert.equal(env.reads(), 1, 'cached views should not refetch or replace the original file selection');
    assert.equal(env.historyLink.getAttribute('aria-current'), 'page');
});

test('a failed page read keeps the current upload view and offers an inline error', async () => {
    const env = page({ fail: true });
    assert.ok(env.context.ClassroomWorkspace, 'workspace must handle failed navigation without dropping files');
    await env.context.ClassroomWorkspace.navigate('history');
    assert.ok(env.main.children[0] === env.uploadSection); assert.equal(env.location.pathname, '/');
    assert.match(env.document.getElementById('workspace-status').textContent, /无法切换/);
    assert.equal(env.main.getAttribute('aria-busy'), 'false');
});

test('uploading blocks a local route change, and modified links keep browser behavior', async () => {
    const env = page({ uploading: true });
    assert.ok(env.context.ClassroomWorkspace, 'navigation must not detach an active upload');
    await env.context.ClassroomWorkspace.navigate('history');
    assert.equal(env.main.children[0], env.uploadSection); assert.equal(env.reads(), 0);
    let prevented = false;
    await env.document.dispatchEvent({ type: 'click', target: env.historyLink, button: 0, ctrlKey: true, preventDefault() { prevented = true; } });
    assert.equal(prevented, false);
});

test('browser back restores the retained upload view without pushing another route', async () => {
    const env = page();
    assert.ok(env.context.ClassroomWorkspace, 'browser history must restore workspace views');
    await env.context.ClassroomWorkspace.navigate('history');
    env.location.pathname = '/';
    await env.windowEvents.popstate();
    assert.equal(env.main.children[0], env.uploadSection); assert.equal(env.location.pathname, '/');
});

test('Back during an entry animation waits for the transition and preserves the Back entry', async () => {
    const env = page({ reduced: false });
    let finish, animations = 0;
    env.historySection.animate = () => ({ finished: ++animations === 1 ? new Promise(resolve => { finish = resolve; }) : Promise.resolve() });
    const entering = env.context.ClassroomWorkspace.navigate('history');
    for (let step = 0; step < 12; step++) await Promise.resolve();
    assert.equal(env.location.pathname, '/history.html');
    env.location.pathname = '/';
    const back = env.windowEvents.popstate();
    assert.equal(env.location.pathname, '/', 'a running animation must never overwrite the destination entry');
    finish(); await entering; await back;
    assert.ok(env.main.children[0] === env.uploadSection); assert.equal(env.location.pathname, '/');
});

test('an upload started during a slow page fetch keeps its live view mounted', async () => {
    let finish;
    const env = page({ pendingResponse: new Promise(resolve => { finish = resolve; }) });
    const navigating = env.context.ClassroomWorkspace.navigate('history');
    await Promise.resolve();
    env.uploadState.isUploading = true;
    finish(); await navigating;
    assert.ok(env.main.children[0] === env.uploadSection); assert.equal(env.location.pathname, '/');
    assert.match(env.document.getElementById('workspace-status').textContent, /正在上传/);
});
