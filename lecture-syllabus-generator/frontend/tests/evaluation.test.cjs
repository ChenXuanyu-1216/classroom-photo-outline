const assert = require('node:assert/strict');
const test = require('node:test');
const {reviewInput, report} = require('../js/evaluation-model.js');

test('blank scoring stays unscored and inconsistent counts cannot become metrics', () => {
    const input = new Map([['scope_type', 'page'], ['scope_name', 'P1'], ['structure', '4']]);
    assert.equal(reviewInput(input).counts, null);
    assert.equal(reviewInput(input).structure, null);
    input.set('expected_count', '3');
    assert.throws(() => reviewInput(input), /六项/);
    for (const name of ['matched','output','supported','source_correct','source_total']) input.set(name, '0');
    input.set('matched', '1');
    assert.throws(() => reviewInput(input), /正确数/);
    input.set('matched', '0'); input.set('error_category', 'formula');
    assert.throws(() => reviewInput(input), /四项/);
});

test('report keeps failed execution, all raters and critical evidence without claiming overall acceptance', () => {
    const review = {id:'r1', reviewer:'a', review_kind:'self', scope:'P1', created_at:'2026-11-03T00:00:00Z',
        expected:'原图 x > 0', observed:'输出 x >= 0', metrics:{coverage:0, fidelity:null, source:null}, counts:null,
        structure:null, usability:null, module_pass:null, critical_errors:[{category:'formula', expected:'x > 0', actual:'x >= 0', impact:'改变适用范围'}], core_flow:{word:'not_run'}};
    const output = report({id:'s1', sha256:'hash1', created_at:'2026-11-03T00:00:00Z',
        evidence:{task:{id:1, status:'failed'}, capture_kind:'job_terminal', job:{status:'failed', elapsed_seconds:2},
            pages:[{page_no:1, failed:true, error:{code:'MODEL_TIMEOUT'}}], outline:null, limits:['需人工核对']},
        reviews:[review, {...review, id:'r2', reviewer:'b', review_kind:'independent'}]});
    for (const text of ['MODEL_TIMEOUT','改变适用范围','a / self','b / independent','未评分／无比率','Word 导出：未执行','不自动汇总','hash1']) assert.ok(output.includes(text), text);
});

test('module review uses the chosen snapshot module and rejects invented names or IDs', () => {
    const input = new Map([['scope_type', 'module'], ['scope_name', '不存在的模块'], ['module_id', 'bad'], ['structure', '3'], ['usability', '3']]);
    const modules = [{id: 'module_1_0123456789abcdef', heading: '真实模块'}];
    assert.throws(() => reviewInput(input, modules), /实际存在/);
    input.set('module_id', modules[0].id);
    const body = reviewInput(input, modules);
    assert.equal(body.scope, 'module:真实模块');
    assert.equal(body.module_id, modules[0].id);
    assert.equal(body.structure, 3);
    input.set('scope_type', 'page');
    assert.equal(reviewInput(input, modules).module_id, null);
});
