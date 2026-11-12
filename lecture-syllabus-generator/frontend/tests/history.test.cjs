const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Only the DOM operations used by this page; network responses stay at the API boundary.
class Element {
    constructor(tag = 'div') {
        this.tagName = tag.toLowerCase();
        this.children = [];
        this.attributes = {};
        this.events = {};
        this.className = '';
        this.ownText = '';
        this.htmlWrites = [];
    }
    set textContent(value) { this.ownText = String(value); this.children = []; }
    get textContent() { return this.ownText + this.children.map(child => child.textContent).join(''); }
    set innerHTML(value) { this.htmlWrites.push(value); this.children = []; this.ownText = ''; }
    append(...children) { for (const child of children) { child.parentNode = this; this.children.push(child); } }
    appendChild(child) { this.append(child); return child; }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    replaceChildren(...children) {
        if (this.contains(this.ownerDocument?.activeElement)) this.ownerDocument.activeElement = this.ownerDocument.body;
        for (const child of this.children) child.parentNode = null;
        this.children = [];
        this.append(...children);
        this.ownText = '';
    }
    remove() {
        if (this.contains(this.ownerDocument?.activeElement)) this.ownerDocument.activeElement = this.ownerDocument.body;
        if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this);
        this.parentNode = null;
    }
    focus() { this.ownerDocument.activeElement = this; }
    click() { if (this.download) (this.ownerDocument.downloads ||= []).push({ href: this.href, filename: this.download }); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    addEventListener(name, handler) { this.events[name] = handler; }
    querySelectorAll(selector) {
        const matches = node => selector.startsWith('.')
            ? node.className.split(/\s+/).includes(selector.slice(1))
            : node.tagName === selector;
        return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0]; }
}

const source = fs.readFileSync(path.join(__dirname, '../js/history.js'), 'utf8');
function page(listTasks, getOutline = async () => ({ ok: false }), options = {}) {
    const elements = { 'task-list': new Element(), 'task-count': new Element('span') };
    const document = {
        body: new Element('body'),
        getElementById: id => elements[id],
        createElement(tag) { const element = new Element(tag); element.ownerDocument = this; return element; },
    };
    document.activeElement = document.body;
    Object.values(elements).forEach(element => { element.ownerDocument = document; });
    const context = vm.createContext({
        document,
        API: { listTasks, getOutline, exportWord: options.exportWord, deleteTask: options.deleteTask || (async () => ({ ok: true, status: 204 })) },
        URL: { createObjectURL: () => 'blob:authenticated-docx', revokeObjectURL() {} },
        setTimeout: fn => fn(),
        window: { confirm: options.confirm || (() => true), alert() {} },
    });
    const ready = Promise.resolve(vm.runInContext(source, context));
    return { list: elements['task-list'], count: elements['task-count'], document, ready };
}
const response = tasks => ({ ok: true, json: async () => ({ tasks }) });
const task = overrides => ({ task_id: 12, title: '高等数学课堂', status: 'success', page_count: 8, created_at: '2026-10-16T08:30:00Z', ...overrides });

test('history exports through authenticated API instead of navigating to a bearerless URL', async () => {
    let requested = null, prevented = false;
    const env = page(async () => response([task()]), undefined, { exportWord: async id => { requested = id; return { ok: true, blob: async () => ({}) }; } });
    await env.ready;
    await env.list.querySelector('.kf-record-export').events.click({ preventDefault() { prevented = true; } });
    assert.equal(requested, 12);
    assert.equal(prevented, true);
    assert.deepEqual(env.document.downloads, [{ href: 'blob:authenticated-docx', filename: 'outline_12.docx' }]);
});

test('records display the saved title safely and retain open/export destinations', async () => {
    const title = '<img src=x onerror=alert(1)>课堂记录';
    const env = page(async () => response([task({ title })]));
    await env.ready;
    assert.equal(env.list.querySelector('h3')?.textContent, title);
    assert.equal(env.list.querySelector('img'), undefined);
    assert.equal(env.list.querySelector('.kf-status')?.textContent, '已完成');
    assert.match(env.list.textContent, /8 页/);
    assert.equal(env.count.textContent, '1 条记录');
    assert.deepEqual(env.list.querySelectorAll('a').map(link => link.href), [
        '/outline.html?task_id=12', '/api/tasks/12/export',
    ]);
    assert.equal(env.list.getAttribute('aria-busy'), 'false');
});

test('empty history provides a working route back to upload', async () => {
    const env = page(async () => response([]));
    await env.ready;
    assert.match(env.list.textContent, /还没有整理记录/);
    assert.equal(env.list.querySelector('a')?.href, '/');
    assert.equal(env.count.textContent, '0 条记录');
});

test('failed request offers a retry that replaces the error with fresh records', async () => {
    let attempts = 0;
    const env = page(async () => ++attempts === 1 ? { ok: false } : response([task({ title: '重试后的课堂' })]));
    await env.ready;
    assert.match(env.list.textContent, /无法加载/);
    const retry = env.list.querySelector('button');
    assert.ok(retry, 'the error must have a retry action');
    await retry.events.click();
    assert.equal(env.list.querySelector('h3')?.textContent, '重试后的课堂');
    assert.doesNotMatch(env.list.textContent, /无法加载/);
    assert.equal(env.count.textContent, '1 条记录');
});

for (const [name, failure] of [
    ['network rejection', async () => { throw new Error('offline'); }],
    ['malformed response', async () => ({ ok: true, json: async () => ({ tasks: null }) })],
]) {
    test(`${name} becomes a recoverable error rather than a stuck loading screen`, async () => {
        const env = page(failure);
        await env.ready.catch(() => {});
        assert.match(env.list.textContent, /无法加载/);
        assert.ok(env.list.querySelector('button'));
        assert.equal(env.list.getAttribute('aria-busy'), 'false');
    });
}

test('loading is announced until the pending response is rendered', async () => {
    let finish;
    const env = page(() => new Promise(resolve => { finish = resolve; }));
    assert.equal(env.list.getAttribute('aria-busy'), 'true');
    assert.match(env.list.textContent, /正在读取/);
    finish(response([task()]));
    await env.ready;
    assert.equal(env.list.getAttribute('aria-busy'), 'false');
    assert.equal(env.count.textContent, '1 条记录');
});

test('all known statuses and unknown values remain readable without becoming markup', async () => {
    const statuses = ['pending', 'running', 'success', 'failed', 'partial', '<img src=x>'];
    const env = page(async () => response(statuses.map((status, index) => task({ status, task_id: index + 1 }))));
    await env.ready;
    assert.deepEqual(env.list.querySelectorAll('.kf-status').map(status => status.textContent), [
        '待处理', '处理中', '已完成', '处理失败', '部分完成', '状态未知',
    ]);
    assert.equal(env.list.querySelectorAll('article').length, 6);
    assert.equal(env.list.querySelector('img'), undefined);
});

test('UTC timestamps without an offset from the API retain their instant in a Shanghai browser', async () => {
    const previousTimezone = process.env.TZ;
    process.env.TZ = 'Asia/Shanghai';
    try {
        const env = page(async () => response([task({ created_at: '2026-10-16T08:30:00' })]));
        await env.ready;
        assert.equal(env.list.querySelector('time')?.dateTime, '2026-10-16T08:30:00.000Z');
        assert.match(env.list.querySelector('time')?.textContent, /16:30/);
    } finally {
        if (previousTimezone === undefined) delete process.env.TZ;
        else process.env.TZ = previousTimezone;
    }
});

test('keyboard retry preserves focus on the next available action after repeated failure', async () => {
    const env = page(async () => ({ ok: false }));
    await env.ready;
    const retry = env.list.querySelector('button');
    retry.focus();
    await retry.events.click();
    assert.equal(env.document.activeElement, env.list.querySelector('button'));
});

test('a slow retry does not steal focus after the visitor moves to another control', async () => {
    let attempts = 0;
    let finish;
    const env = page(() => ++attempts === 1 ? Promise.resolve({ ok: false }) : new Promise(resolve => { finish = resolve; }));
    await env.ready;
    const retry = env.list.querySelector('button');
    retry.focus();
    const pending = retry.events.click();
    const outside = env.document.createElement('button');
    outside.focus();
    finish({ ok: false });
    await pending;
    assert.equal(env.document.activeElement, outside);
});

test('expanding a record lazily reads its saved outline as text without changing open/export destinations', async () => {
    let reads = 0;
    const env = page(async () => response([task()]), async id => {
        assert.equal(id, 12); reads++;
        return { ok: true, json: async () => ({ task_id: 12, markdown: '## 索引\n- <img src=x onerror=alert(1)>\n- 来源：第 2 页', version: 3 }) };
    });
    await env.ready; assert.equal(reads, 0);
    const toggle = env.list.querySelector('.kf-record-preview');
    assert.ok(toggle, 'records need an in-place outline preview');
    await toggle.events.click();
    assert.equal(reads, 1); assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    assert.match(env.list.querySelector('.kf-outline-preview').textContent, /索引.*<img src=x onerror=alert\(1\)>.*第 2 页/s);
    assert.equal(env.list.querySelector('img'), undefined);
    await toggle.events.click();
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(env.list.querySelector('.kf-outline-preview').hidden, true);
    assert.deepEqual(env.list.querySelectorAll('a').map(link => link.href), ['/outline.html?task_id=12', '/api/tasks/12/export']);
});

test('outline preview can retry a failed read and collapsing during a slow read does not reopen it', async () => {
    let reads = 0, finish;
    const env = page(async () => response([task()]), () => ++reads === 1 ? Promise.resolve({ ok: false }) : new Promise(resolve => { finish = resolve; }));
    await env.ready;
    const toggle = env.list.querySelector('.kf-record-preview');
    assert.ok(toggle, 'a failed outline read must stay recoverable inside the record');
    await toggle.events.click();
    const panel = env.list.querySelector('.kf-outline-preview');
    assert.match(panel.textContent, /无法预览/);
    const pending = panel.querySelector('button').events.click();
    await toggle.events.click();
    finish({ ok: true, json: async () => ({ task_id: 12, markdown: '## 恢复后的大纲', version: 1 }) });
    await pending;
    assert.equal(panel.hidden, true);
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    assert.match(panel.textContent, /恢复后的大纲/);
});

test('repeated outline preview failure preserves keyboard focus on the replacement retry', async () => {
    const env = page(async () => response([task()]));
    await env.ready;
    await env.list.querySelector('.kf-record-preview').events.click();
    const panel = env.list.querySelector('.kf-outline-preview');
    const retry = panel.querySelector('button'); retry.focus();
    await retry.events.click();
    assert.equal(env.document.activeElement, panel.querySelector('button'));
});

test('deleting a failed record removes only that row and updates the count', async () => {
    const deleted = [];
    let confirmed;
    const env = page(async () => response([task({status:'failed'}), task({task_id:13,title:'另一条记录'})]), undefined, {
        confirm: text => { confirmed=text; return true; },
        deleteTask: async id => { deleted.push(id); return {ok:true,status:204}; },
    });
    await env.ready;
    const remove = env.list.querySelector('.kf-record-delete');
    assert.ok(remove, 'history rows need a delete action');
    remove.focus();
    await remove.events.click();
    assert.deepEqual(deleted, [12]);
    assert.match(confirmed, /12/);
    assert.equal(env.count.textContent, '1 条记录');
    assert.equal(env.list.querySelectorAll('article').length, 1);
    assert.match(env.list.textContent, /另一条记录/);
    assert.equal(env.document.activeElement, env.list.querySelector('.kf-record-delete'));
});

test('cancelling deletion never calls the API or changes the record', async () => {
    let calls = 0;
    const env = page(async () => response([task()]), undefined, {
        confirm: () => false, deleteTask: async () => { calls++; },
    });
    await env.ready;
    const remove = env.list.querySelector('.kf-record-delete');
    assert.ok(remove);
    await remove.events.click();
    assert.equal(calls, 0);
    assert.equal(env.count.textContent, '1 条记录');
    assert.equal(env.list.querySelectorAll('article').length, 1);
});

for (const failure of ['network', 'server']) {
    test(`${failure} delete failure keeps the row and allows retry`, async () => {
        let calls = 0;
        const env = page(async () => response([task()]), undefined, {deleteTask: async () => {
            if (++calls > 1) return {ok:true,status:204};
            if (failure === 'network') throw new Error('offline');
            return {ok:false,status:500,json:async()=>({message:'<img src=x>删除失败，请重试'})};
        }});
        await env.ready;
        const remove = env.list.querySelector('.kf-record-delete');
        assert.ok(remove);
        remove.focus();
        await remove.events.click();
        assert.equal(env.count.textContent, '1 条记录');
        assert.equal(remove.disabled, false);
        assert.match(env.list.querySelector('.kf-delete-message').textContent, /删除失败/);
        assert.equal(env.list.querySelector('img'), undefined);
        await remove.events.click();
        assert.equal(env.count.textContent, '0 条记录');
        assert.match(env.list.textContent, /还没有整理记录/);
        assert.equal(env.document.activeElement, env.list.querySelector('a'));
    });
}

test('a pending delete cannot submit twice or steal focus after the visitor moves away', async () => {
    let calls=0, finish;
    const env = page(async () => response([task(),task({task_id:13})]), undefined, {deleteTask: () => {
        calls++; return new Promise(resolve=>{finish=resolve;});
    }});
    await env.ready;
    const remove = env.list.querySelector('.kf-record-delete');
    assert.ok(remove);
    remove.focus();
    const pending = remove.events.click();
    assert.equal(remove.disabled, true);
    await remove.events.click();
    assert.equal(calls, 1);
    const outside = env.document.createElement('button'); outside.focus();
    finish({ok:true,status:204}); await pending;
    assert.equal(env.document.activeElement, outside);
    assert.equal(env.count.textContent, '1 条记录');
});

test('running tasks keep a disabled delete button and never send a delete request', async () => {
    let calls=0;
    const env = page(async () => response([task({status:'running'})]), undefined, {deleteTask: async () => {calls++;}});
    await env.ready;
    const remove = env.list.querySelector('.kf-record-delete');
    assert.ok(remove); assert.equal(remove.disabled, true);
    await remove.events.click();
    assert.equal(calls, 0);
});

test('API delete targets the selected record using the DELETE method', async () => {
    let request;
    const context = vm.createContext({fetch:async(url,options)=>{request={url,options};return {ok:true,status:204};}});
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../js/api.js'),'utf8')+'\nglobalThis.client = API;', context);
    assert.equal(typeof context.client.deleteTask, 'function');
    await context.client.deleteTask(12);
    assert.equal(request.url, '/api/tasks/12');
    assert.equal(request.options.method, 'DELETE');
});
