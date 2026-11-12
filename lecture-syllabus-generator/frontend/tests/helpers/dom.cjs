class Element {
    constructor(tag = 'div', id = '') {
        this.tagName = tag.toLowerCase(); this.id = id; this.children = []; this.attributes = {};
        this.events = {}; this.className = ''; this.ownText = ''; this.hidden = false;
        this.style = { setProperty() {}, removeProperty() {} };
        this.classList = {
            contains: name => this.className.split(/\s+/).includes(name),
            add: name => this.classList.toggle(name, true),
            remove: name => this.classList.toggle(name, false),
            toggle: (name, on) => {
                const names = new Set(this.className.split(/\s+/).filter(Boolean));
                const next = on === undefined ? !names.has(name) : on;
                if (next) names.add(name); else names.delete(name);
                this.className = [...names].join(' '); return next;
            },
        };
    }
    set textContent(value) { this.ownText = String(value); this.replaceChildren(); }
    get textContent() { return this.ownText + this.children.map(child => child.textContent).join(''); }
    append(...children) {
        for (const child of children) {
            if (child.parentNode) child.parentNode.children = child.parentNode.children.filter(node => node !== child);
            child.parentNode = this; child.ownerDocument = this.ownerDocument; this.children.push(child);
        }
    }
    appendChild(child) { this.append(child); return child; }
    replaceChildren(...children) {
        this.children.forEach(child => { child.parentNode = null; }); this.children = []; this.append(...children);
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return name === 'href' ? this.href : this.attributes[name] ?? null; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, handler) { (this.events[name] ||= []).push(handler); }
    async emit(name, event = {}) { for (const handler of this.events[name] || []) await handler({ target: this, ...event }); }
    matches(selector) {
        if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
        if (selector.startsWith('#')) return this.id === selector.slice(1);
        return this.tagName === selector;
    }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    getBoundingClientRect() { return { top: 0, left: 0, width: 700, height: 400 }; }
    focus() { this.ownerDocument.activeElement = this; }
}

function documentFixture() {
    const body = new Element('body', 'knowledge-flow-home');
    const doc = {
        body, title: '上传', events: {}, activeElement: body,
        createElement(tag) { const node = new Element(tag); node.ownerDocument = doc; return node; },
        getElementById(id) { return body.id === id ? body : body.querySelector(`#${id}`); },
        querySelector: selector => body.querySelector(selector), querySelectorAll: selector => body.querySelectorAll(selector),
        addEventListener(name, handler) { (this.events[name] ||= []).push(handler); },
        async dispatchEvent(event) { for (const handler of this.events[event.type] || []) await handler(event); },
    };
    body.ownerDocument = doc;
    function add(parent, tag, id, className = '') { const node = doc.createElement(tag); node.id = id; node.className = className; parent.append(node); return node; }
    return { document: doc, body, add };
}
module.exports = { Element, documentFixture };
