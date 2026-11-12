const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class Element {
    constructor() {
        this.textContent = '';
        this.children = [];
        this.events = {};
        this.disabled = false;
        const classes = new Set(['hidden']);
        this.classList = { add: (...names) => names.forEach(n => classes.add(n)),
            remove: (...names) => names.forEach(n => classes.delete(n)),
            contains: name => classes.has(name) };
        this.status = { textContent: '' };
    }
    appendChild(child) { this.children.push(child); }
    replaceChildren(...children) { this.children = children; }
    querySelector() { return this.status; }
    addEventListener(name, callback) { this.events[name] = callback; }
    setAttribute(name, value) { this[name] = value; }
}
const source = fs.readFileSync(path.join(__dirname, '../js/progress.js'), 'utf8');
const ok = body => ({ ok: true, status: 200, json: async () => body });
const noOutline = () => ({ ok: false, status: 500, json: async () => ({ code: 'PIPELINE_ERROR', message: '大纲尚未生成，请先执行管道。', retryable: true }) });
const pageDetail = (page_no, extra = {}) => ({ page_no, filename: `p${page_no}.png`, confidence: 0.95,
    low_confidence_flag: false, failed_flag: false, error_msg: null, ...extra });

function page({ status = 'pending', pages = [pageDetail(1)], outline = null, api = {}, search = '?task_id=7', confirm = () => true } = {}) {
    const elements = {};
    const calls = [];
    const state = { status, pages, outline };
    const API = {
        getTask: async () => ok({ task_id: 7, status: state.status, page_count: state.pages.length }),
        getPages: async () => ok({ task_id: 7, pages: state.pages }),
        getOutline: async () => state.outline ? ok(state.outline) : noOutline(),
        recognize: async (id, pageNo) => {
            calls.push(['recognize', pageNo ?? 'ALL']);
            state.pages = state.pages.map(page => pageNo && page.page_no !== pageNo ? page : pageDetail(page.page_no));
            state.status = state.pages.some(page => page.failed_flag) ? 'partial' : 'success';
            return ok({ task_id: 7, status: state.status });
        },
        runPipeline: async () => { calls.push(['pipeline']); state.status = 'success'; state.outline = { markdown: '# 大纲', version: 1 }; return ok({ task_id: 7, status: 'success', knowledge_point_count: 1, module_count: 1 }); },
        ...api,
    };
    const document = { getElementById: id => elements[id] ||= new Element(), createElement: () => new Element() };
    const window = { location: { search, href: '' } };
    const context = vm.createContext({ document, window, URLSearchParams, API, confirm, alert: () => {} });
    const ready = Promise.resolve(vm.runInContext(source, context));
    return { ready, elements, state, calls, window, API };
}

test('fresh task runs once and reloading a saved result never repeats POST operations', async () => {
    const first = page();
    await first.ready;
    assert.deepEqual(first.calls, [['recognize', 'ALL'], ['pipeline']]);
    const reloaded = page({ status: first.state.status, outline: first.state.outline });
    await reloaded.ready;
    assert.deepEqual(reloaded.calls, []);
    assert.equal(reloaded.elements['view-outline'].href, '/outline.html?task_id=7');
    assert.equal(reloaded.elements['view-outline'].classList.contains('hidden'), false);
});

test('recognition already completed without an outline proceeds directly to generation', async () => {
    const env = page({ status: 'success' });
    await env.ready;
    assert.deepEqual(env.calls, [['pipeline']]);
});

test('single page retry never re-recognizes successful pages and clears stale page warnings', async () => {
    const env = page({ status: 'partial', outline: { markdown: '# 已有', version: 1 }, pages: [
        pageDetail(1), pageDetail(2, { failed_flag: true, confidence: 0, error_msg: '超时' }),
    ] });
    await env.ready;
    assert.deepEqual(env.calls, []);
    await env.elements['retry-btn'].events.click();
    assert.deepEqual(env.calls, [['recognize', 2], ['pipeline']]);
    assert.equal(env.elements['failed-pages'].classList.contains('hidden'), true);
    assert.equal(env.elements['failed-page-list'].children.length, 0);
});

test('failed pages without any saved result wait for explicit retry on reload', async () => {
    const env = page({ status: 'failed', pages: [pageDetail(1, { failed_flag: true, confidence: 0 })] });
    await env.ready;
    assert.deepEqual(env.calls, []);
    assert.equal(env.elements['retry-btn'].classList.contains('hidden'), false);
    assert.equal(env.elements['view-outline'].classList.contains('hidden'), true);
});

test('a running task is observed without starting another execution', async () => {
    const env = page({ status: 'running' });
    await env.ready;
    assert.deepEqual(env.calls, []);
    assert.match(env.elements['progress-message'].textContent, /处理中/);
    assert.equal(env.elements['continue-btn'].classList.contains('hidden'), false);
});

test('network failure becomes an inline recoverable error and never rejects page initialization', async () => {
    let failed = true;
    const env = page({ api: { getTask: async () => { if (failed) throw new Error('offline'); return ok({ status: 'pending' }); } } });
    await env.ready;
    assert.equal(env.elements['progress-error'].classList.contains('hidden'), false);
    assert.equal(env.elements['continue-btn'].classList.contains('hidden'), false);
    failed = false;
    await env.elements['continue-btn'].events.click();
    assert.deepEqual(env.calls, [['recognize', 'ALL'], ['pipeline']]);
    assert.equal(env.elements['progress-error'].classList.contains('hidden'), true);
});

test('generation failure can retry generation without repeating completed recognition', async () => {
    let attempts = 0;
    const env = page({ api: { runPipeline: async () => ++attempts === 1
        ? { ok: false, status: 500, json: async () => ({ message: '生成失败', retryable: true }) }
        : ok({ status: 'success' }) } });
    await env.ready;
    assert.equal(env.elements['progress-error'].classList.contains('hidden'), false);
    await env.elements['continue-btn'].events.click();
    assert.deepEqual(env.calls, [['recognize', 'ALL']]);
    assert.equal(attempts, 2);
    assert.equal(env.elements['view-outline'].classList.contains('hidden'), false);
});

test('double clicking retry while recognition is pending sends only one request', async () => {
    let finish;
    let requests = 0;
    const pending = new Promise(resolve => { finish = resolve; });
    const env = page({ status: 'failed', pages: [pageDetail(2, { failed_flag: true })] });
    await env.ready;
    env.API.recognize = async () => { requests++; await pending; env.state.pages = [pageDetail(2)]; return ok({ status: 'success' }); };
    const first = env.elements['retry-btn'].events.click();
    const second = env.elements['retry-btn'].events.click();
    assert.equal(env.elements['retry-btn'].disabled, true);
    finish();
    await Promise.all([first, second]);
    assert.deepEqual(env.calls, [['pipeline']]);
    assert.equal(requests, 1);
});

test('running tasks with failed pages cannot launch retries', async () => {
    const env = page({ status: 'running', pages: [pageDetail(1, { failed_flag: true })] });
    await env.ready;
    assert.equal(env.elements['retry-btn'].classList.contains('hidden'), true);
    await env.elements['retry-btn'].events.click();
    assert.deepEqual(env.calls, []);
});

test('retry rechecks latest failed pages after a lost response and skips recovered pages', async () => {
    const env = page({ status: 'failed', pages: [pageDetail(1, { failed_flag: true }), pageDetail(2, { failed_flag: true })] });
    await env.ready;
    const requests = [];
    let lost = true;
    env.API.recognize = async (id, pageNo) => {
        requests.push(pageNo);
        if (pageNo === 2 && lost) { lost = false; throw new Error('offline'); }
        env.state.pages = env.state.pages.map(p => p.page_no === pageNo ? pageDetail(pageNo) : p);
        return ok({ status: 'partial' });
    };
    await env.elements['retry-btn'].events.click();
    await env.elements['retry-btn'].events.click();
    assert.deepEqual(requests, [1, 2, 2]);
});

test('failed generation on reload waits for explicit continuation', async () => {
    const env = page({ status: 'failed' });
    await env.ready;
    assert.deepEqual(env.calls, []);
    await env.elements['continue-btn'].events.click();
    assert.deepEqual(env.calls, [['pipeline']]);
});

test('failed regeneration keeps old outline available and continuation retries generation', async () => {
    let attempts = 0;
    const env = page({ status: 'partial', outline: { markdown: '# 旧大纲', version: 2 },
        pages: [pageDetail(1), pageDetail(2, { failed_flag: true })],
        api: { runPipeline: async () => {
            if (++attempts === 1) { env.state.status = 'failed'; return { ok: false, status: 500, json: async () => ({ message: '生成失败', retryable: true }) }; }
            return ok({ status: 'success' });
        } } });
    await env.ready;
    await env.elements['retry-btn'].events.click();
    assert.equal(env.state.outline.markdown, '# 旧大纲');
    await env.elements['continue-btn'].events.click();
    assert.equal(attempts, 2);
});

test('saved outline plus failed task never hides the failed generation on reload', async () => {
    const env = page({ status: 'failed', outline: { markdown: '# 保留旧稿', version: 2 } });
    await env.ready;
    assert.deepEqual(env.calls, []);
    assert.match(env.elements['progress-title'].textContent, /未完成/);
    assert.equal(env.elements['continue-btn'].classList.contains('hidden'), false);
    assert.equal(env.elements['view-outline'].classList.contains('hidden'), false);
});

test('lost generation response restores server result without overwriting a newer edit', async () => {
    let attempts = 0;
    const env = page({ status: 'partial', outline: { markdown: '# 旧稿', version: 2 },
        pages: [pageDetail(1), pageDetail(2, { failed_flag: true })],
        api: { runPipeline: async () => {
            attempts++;
            env.state.status = 'success';
            env.state.outline = { markdown: '# 服务端新稿', version: 1 };
            throw new Error('lost acknowledgement');
        } } });
    await env.ready;
    await env.elements['retry-btn'].events.click();
    env.state.outline = { markdown: '# 之后校对的稿件', version: 2 };
    await env.elements['continue-btn'].events.click();
    assert.equal(attempts, 1);
    assert.equal(env.state.outline.markdown, '# 之后校对的稿件');
    assert.equal(env.elements['view-outline'].classList.contains('hidden'), false);
});

test('canceling overwrite before retry preserves the existing edited outline', async () => {
    const env = page({ status: 'partial', outline: { markdown: '# 我的修改', version: 2 },
        pages: [pageDetail(1, { failed_flag: true })], confirm: () => false });
    await env.ready;
    await env.elements['retry-btn'].events.click();
    assert.deepEqual(env.calls, []);
    assert.equal(env.state.outline.markdown, '# 我的修改');
});

test('invalid task identifiers redirect without sending any requests', async () => {
    for (const search of ['', '?task_id=<script>', '?task_id=-1']) {
        const env = page({ search });
        await env.ready;
        assert.deepEqual(env.calls, []);
        assert.equal(env.window.location.href, '/');
    }
});

test('all failed recognition stops the active step and never starts generation', async () => {
    const env = page({ api: { recognize: async () => {
        env.state.status = 'failed';
        env.state.pages = [pageDetail(1, { failed_flag: true, error_msg: '无法连接模型服务' })];
        return ok({ status: 'failed' });
    } } });
    await env.ready;
    assert.match(env.elements['progress-title'].textContent, /识别失败/);
    assert.equal(env.elements['step-recognize'].classList.contains('active'), false);
    assert.equal(env.elements['step-recognize'].status.textContent, '识别失败');
    assert.deepEqual(env.calls, []);
});

test('reopening failed recognition shows failure rather than a waiting or running step', async () => {
    const env = page({ status: 'failed', pages: [pageDetail(1, { failed_flag: true })] });
    await env.ready;
    assert.equal(env.elements['step-recognize'].status.textContent, '识别失败');
    assert.equal(env.elements['step-recognize'].classList.contains('active'), false);
    assert.equal(env.elements['retry-btn'].classList.contains('hidden'), false);
    assert.deepEqual(env.calls, []);
});

test('queued job is observed on reload without starting recognition or generation', async () => {
    const env = page({ status: 'queued', api: { getTask: async () => ok({ status: 'queued', active_job: {
        job_id: 'j1', task_id: 7, operation: 'pipeline', status: 'queued' } }) } });
    await env.ready;
    assert.deepEqual(env.calls, []);
    assert.match(env.elements['progress-title'].textContent, /排队/);
});

test('nonretryable failed page explains next action without sending a paid retry', async () => {
    const env = page({ status: 'failed', pages: [pageDetail(1, { failed_flag: true,
        error_code: 'IMAGE_UNREADABLE', error_retryable: false, error_msg: '图片不可辨认' })] });
    await env.ready;
    assert.equal(env.elements['retry-btn'].classList.contains('hidden'), true);
    assert.match(env.elements['failed-page-list'].children[0].textContent, /更换清晰图片/);
});

test('background capable fresh task queues the whole flow instead of stopping after recognition', async () => {
    const env = page({ api: { backgroundJobs: true } });
    await env.ready;
    assert.deepEqual(env.calls, [['pipeline']]);
});

test('configuration failure permits explicit retry after repair without automatic spending', async () => {
    const env = page({ status: 'failed', pages: [pageDetail(1, { failed_flag: true,
        error_code: 'MODEL_AUTH_ERROR', error_retryable: false, error_msg: '检查密钥' })] });
    await env.ready;
    assert.deepEqual(env.calls, []);
    assert.equal(env.elements['retry-btn'].classList.contains('hidden'), false);
    assert.match(env.elements['retry-btn'].textContent, /修正/);
    await env.elements['retry-btn'].events.click();
    assert.deepEqual(env.calls, [['recognize', 1], ['pipeline']]);
});
