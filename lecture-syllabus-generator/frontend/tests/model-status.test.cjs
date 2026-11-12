const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { documentFixture } = require('./helpers/dom.cjs');
const source = fs.readFileSync(path.join(__dirname, '../js/upload.js'), 'utf8');
async function page(meta, options = {}) {
    const env = documentFixture();
    for (const id of ['drop-zone','file-input','file-list','file-count','generate-btn','upload-message','model-status','model-budget-note','file-hint','file-formats','model-retry']) env.add(env.body, 'div', id);
    env.document.getElementById('model-status').textContent = '正在检查识别服务…';
    const timers = [];
    const context = vm.createContext({ document: env.document, API: options.api ?? {getMeta: async () => ({ok:true,json:async()=>meta})},
        AbortController, setTimeout: callback => { timers.push(callback); return timers.length; }, clearTimeout() {},
        CustomEvent: class {constructor(type){this.type=type}}, window:{location:{}}, matchMedia:()=>({matches:true}) });
    vm.runInContext(source, context);
    await new Promise(resolve=>setImmediate(resolve));
    env.expireCheck = async () => { timers.at(-1)?.(); await new Promise(resolve=>setImmediate(resolve)); };
    return env;
}
test('live recognition states cloud processing and blocks exhausted budget', async () => {
    const env = await page({model_provider:'deepseek',cluster_provider:'model',model_ready:true,allowed_ext:['.jpg','.png'],max_files_per_batch:20,max_file_mb:10,model_budget:{cap_cny:5,available:false}});
    assert.match(env.document.getElementById('model-status').textContent, /额度/);
    await env.document.getElementById('file-input').emit('change', {target:{files:[{name:'photo.jpg'}]}});
    assert.equal(env.document.getElementById('generate-btn').disabled, true);
    assert.match(env.document.getElementById('model-budget-note').textContent, /云端/);
});

test('disabled local cap permits upload after the old allowance was exhausted', async () => {
    const env = await page({model_provider:'deepseek',model_ready:true,
        model_budget:{enforced:false,cap_cny:null,reserved_cny:5.1,remaining_cny:null,available:true}});
    await env.document.getElementById('file-input').emit('change', {target:{files:[{name:'photo.jpg'}]}});
    assert.equal(env.document.getElementById('generate-btn').disabled, false);
    const note = env.document.getElementById('model-budget-note').textContent;
    assert.match(note, /未设置本地额度上限/);
    assert.match(note, /实际费用/);
    assert.doesNotMatch(note, /null|累计保守调用额度/);
});

test('a missing metadata method fails closed instead of keeping the initial checking message', async () => {
    const env = await page(null, {api:{}});
    assert.doesNotMatch(env.document.getElementById('model-status').textContent, /正在检查/);
    await env.document.getElementById('file-input').emit('change', {target:{files:[{name:'photo.jpg'}]}});
    assert.equal(env.document.getElementById('generate-btn').disabled, true);
    assert.equal(env.document.getElementById('model-retry').hidden, false);
});

test('a stalled service check times out and retry recovers without losing selected photos', async () => {
    let stalled = true;
    const env = await page(null, {api:{getMeta: async () => stalled ? new Promise(()=>{}) : ({ok:true,json:async()=>({model_provider:'mock',model_ready:true})})}});
    await env.document.getElementById('file-input').emit('change', {target:{files:[{name:'keep.jpg'}]}});
    await env.expireCheck();
    assert.match(env.document.getElementById('model-status').textContent, /超时|无法连接/);
    assert.equal(env.document.getElementById('generate-btn').disabled, true);
    assert.equal(env.document.getElementById('model-retry').hidden, false);
    stalled = false;
    await env.document.getElementById('model-retry').emit('click');
    assert.match(env.document.getElementById('model-status').textContent, /模拟演示/);
    assert.equal(env.document.getElementById('generate-btn').disabled, false);
    assert.match(env.document.getElementById('file-list').textContent, /keep.jpg/);
    assert.equal(env.document.getElementById('model-retry').hidden, true);
});

test('metadata requests bypass the browser cache and can be cancelled', async () => {
    let request;
    const context = vm.createContext({ fetch: async (url, options) => { request={url,options}; return {ok:true}; } });
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../js/api.js'),'utf8')+'\nglobalThis.client = API;', context);
    const controller = new AbortController();
    await context.client.getMeta({signal:controller.signal});
    assert.equal(request.url, '/api/meta');
    assert.equal(request.options?.cache, 'no-store');
    assert.equal(request.options?.signal, controller.signal);
});

for (const blockedBy of ['configuration', 'budget']) {
    test(`a ${blockedBy} block can recover after backend restart while retaining photos`, async () => {
        const meta = {model_provider:'deepseek',model_ready:blockedBy !== 'configuration',model_budget:{cap_cny:5,reserved_cny:0,available:blockedBy !== 'budget'}};
        const env = await page(meta);
        await env.document.getElementById('file-input').emit('change', {target:{files:[{name:'retained.png'}]}});
        assert.equal(env.document.getElementById('model-retry').hidden, false);
        assert.equal(env.document.getElementById('generate-btn').disabled, true);
        meta.model_ready = true;
        meta.model_budget.available = true;
        await env.document.getElementById('model-retry').emit('click');
        assert.equal(env.document.getElementById('generate-btn').disabled, false);
        assert.match(env.document.getElementById('file-list').textContent, /retained.png/);
    });
}
