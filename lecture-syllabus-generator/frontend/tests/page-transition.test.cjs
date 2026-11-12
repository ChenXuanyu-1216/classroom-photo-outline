const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { Element, documentFixture } = require('./helpers/dom.cjs');
const filename = path.join(__dirname, '../js/page-transition.js');
const source = fs.existsSync(filename) ? fs.readFileSync(filename, 'utf8') : '';

function page(pathname = '/history.html') {
    const env = documentFixture();
    env.document.documentElement = new Element('html');
    env.document.readyState = 'complete';
    const main = env.add(env.body, 'main', '', 'kf-main');
    main.animate = () => ({ finished: Promise.resolve(), cancel() {} });
    const link = env.add(main, 'a');
    link.href = '/outline.html?task_id=6';
    const location = new URL('http://localhost' + pathname);
    const destinations = [];
    location.assign = url => destinations.push(url);
    const windowEvents = {};
    const context = vm.createContext({
        document: env.document, location, URL,
        matchMedia: () => ({ matches: true }),
        addEventListener(name, handler) { windowEvents[name] = handler; },
    });
    context.window = context;
    vm.runInContext(source, context);
    function click(overrides = {}) {
        const event = { type: 'click', target: link, button: 0, defaultPrevented: false,
            preventDefault() { this.defaultPrevented = true; }, ...overrides };
        return { event, done: env.document.dispatchEvent(event) };
    }
    return { ...env, main, link, location, destinations, windowEvents, click };
}

test('opening an outline waits for the departing page to finish fading before navigating', async () => {
    const env = page();
    let finish;
    env.main.animate = () => ({ finished: new Promise(resolve => { finish = resolve; }), cancel() {} });
    const click = env.click();
    assert.equal(click.event.defaultPrevented, true, 'the outline link should wait for its fade');
    assert.deepEqual(env.destinations, []);
    finish();
    await click.done;
    assert.deepEqual(env.destinations, ['http://localhost/outline.html?task_id=6']);
});

test('modified, download, external and already handled links keep their browser behavior', async () => {
    for (const setup of [
        env => ({ ctrlKey: true }),
        env => ({ button: 1 }),
        env => { env.link.setAttribute('target', '_blank'); return {}; },
        env => { env.link.setAttribute('download', 'outline.docx'); return {}; },
        env => { env.link.href = 'https://example.com/outline.html?task_id=6'; return {}; },
        env => { env.link.href = '/api/tasks/6/export'; return {}; },
        env => ({ defaultPrevented: true }),
    ]) {
        const env = page();
        const overrides = setup(env);
        const click = env.click(overrides);
        await click.done;
        assert.equal(click.event.defaultPrevented, !!overrides.defaultPrevented);
        assert.deepEqual(env.destinations, []);
    }
});

test('returning from the browser cache clears the departure lock so another outline can open', async () => {
    const env = page();
    await env.click().done;
    await env.click().done;
    assert.equal(env.destinations.length, 1, 'repeated clicks must not navigate twice');
    assert.equal(typeof env.windowEvents.pageshow, 'function');
    env.windowEvents.pageshow({ persisted: true });
    env.link.href = '/outline.html?task_id=5';
    await env.click().done;
    assert.deepEqual(env.destinations, [
        'http://localhost/outline.html?task_id=6',
        'http://localhost/outline.html?task_id=5',
    ]);
});

test('an outline page fades before following its return to history link', async () => {
    const env = page('/outline.html?task_id=6');
    env.link.href = '/history.html';
    await env.click().done;
    assert.deepEqual(env.destinations, ['http://localhost/history.html']);
});

test('Back during a departure cancels its pending redirect and permits the next navigation', async () => {
    const env = page();
    let finish;
    env.main.animate = () => ({ finished: new Promise(resolve => { finish = resolve; }), cancel() {} });
    const opening = env.click();
    env.location.pathname = '/';
    env.windowEvents.popstate?.({});
    finish();
    await opening.done;
    assert.deepEqual(env.destinations, [], 'the old outline request must not override Back');
    env.main.animate = () => ({ finished: Promise.resolve(), cancel() {} });
    env.link.href = '/outline.html?task_id=5';
    await env.click().done;
    assert.deepEqual(env.destinations, ['http://localhost/outline.html?task_id=5']);
});

test('a workspace link already handled by its router cancels an older outline departure', async () => {
    const env = page();
    let finish;
    env.main.animate = () => ({ finished: new Promise(resolve => { finish = resolve; }), cancel() {} });
    const opening = env.click();
    env.link.href = '/';
    await env.click({ defaultPrevented: true }).done;
    finish();
    await opening.done;
    assert.deepEqual(env.destinations, [], 'the later workspace choice must win');
});
