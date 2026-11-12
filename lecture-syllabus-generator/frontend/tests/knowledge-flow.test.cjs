const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../js/knowledge-flow.js'), 'utf8');
function page({ saved, reduced = false, storageFails = false } = {}) {
    const storage = new Map(saved === undefined ? [] : [['knowledge-flow-speed', saved]]);
    const classes = new Set();
    const elements = {};
    for (const selector of ['.kf-motion', '.kf-speed-toggle', '.kf-speed-panel', '.kf-speed-input', '.kf-speed-value', '.kf-motion-controls']) {
        elements[selector] = { textContent: '', hidden: true, value: '1', attributes: {}, events: {},
            setAttribute(name, value) { this.attributes[name] = value; },
            addEventListener(name, callback) { this.events[name] = callback; },
            focus() { this.focused = true; }, contains(target) { return Object.values(elements).includes(target); } };
    }
    const animations = [{ playbackRate: 1, updatePlaybackRate(rate) { this.playbackRate = rate; } }];
    const root = {
        querySelector: selector => elements[selector],
        querySelectorAll: () => [{ getAnimations: () => animations }],
        classList: { toggle: (name, on) => on ? classes.add(name) : classes.delete(name) },
        style: { setProperty() {} },
    };
    const media = { matches: reduced, addEventListener(name, handler) { this.change = handler; } };
    const document = { getElementById: () => root, events: {}, addEventListener(name, callback) { this.events[name] = callback; } };
    vm.runInNewContext(source, { document, window: { matchMedia: () => media }, localStorage: {
        getItem(key) { if (storageFails) throw new Error('blocked'); return storage.get(key) ?? null; },
        setItem(key, value) { if (storageFails) throw new Error('blocked'); storage.set(key, value); },
    } });
    return { elements, classes, media, storage, animations, document };
}

test('speed selection changes actual animation rate and survives a new page load', () => {
    const first = page();
    const slider = first.elements['.kf-speed-input'];
    slider.value = '2';
    slider.events.input();
    assert.equal(first.animations[0].playbackRate, 2);
    assert.match(first.elements['.kf-speed-toggle'].textContent, /2×/);
    const next = page({ saved: first.storage.get('knowledge-flow-speed') });
    assert.equal(next.animations[0].playbackRate, 2);
    assert.equal(next.elements['.kf-speed-input'].value, '2');
});

test('changing speed while paused preserves pause and the selected rate on resume', () => {
    const env = page();
    env.elements['.kf-motion'].events.click();
    const slider = env.elements['.kf-speed-input'];
    slider.value = '0.5';
    slider.events.input();
    assert.equal(env.classes.has('kf-paused'), true);
    assert.equal(env.animations[0].playbackRate, 0.5);
    env.elements['.kf-motion'].events.click();
    assert.equal(env.classes.has('kf-playing'), true);
    assert.equal(env.animations[0].playbackRate, 0.5);
});

test('reduced motion remains paused until explicit play, including after speed adjustment', () => {
    const env = page({ reduced: true });
    assert.equal(env.classes.has('kf-paused'), true);
    env.elements['.kf-speed-input'].value = '3';
    env.elements['.kf-speed-input'].events.input();
    assert.equal(env.classes.has('kf-paused'), true);
    env.elements['.kf-motion'].events.click();
    assert.equal(env.classes.has('kf-playing'), true);
    env.media.change();
    assert.equal(env.classes.has('kf-playing'), true);
});

test('speed panel supports toggle, outside click and Escape with focus restoration', () => {
    const env = page();
    const toggle = env.elements['.kf-speed-toggle'];
    const panel = env.elements['.kf-speed-panel'];
    toggle.events.click();
    assert.equal(panel.hidden, false);
    assert.equal(toggle.attributes['aria-expanded'], 'true');
    env.document.events.keydown({ key: 'Escape' });
    assert.equal(panel.hidden, true);
    assert.equal(toggle.focused, true);
    toggle.events.click();
    env.document.events.click({ target: {} });
    assert.equal(panel.hidden, true);
});

test('invalid stored speed and unavailable storage do not break play or speed controls', () => {
    for (const saved of ['NaN', '', '-2', '0', '100', '0.25', '3']) {
        const env = page({ saved });
        assert.ok(env.animations[0].playbackRate >= 0.25 && env.animations[0].playbackRate <= 3);
    }
    const env = page({ storageFails: true });
    env.elements['.kf-speed-input'].value = '1.5';
    env.elements['.kf-speed-input'].events.input();
    assert.equal(env.animations[0].playbackRate, 1.5);
    env.elements['.kf-motion'].events.click();
    assert.equal(env.classes.has('kf-paused'), true);
});
