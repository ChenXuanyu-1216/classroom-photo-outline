const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'));

for (const page of app.pages) {
  test(`${page}: page files and WXML expressions are bound`, () => {
    for (const extension of ['js', 'json', 'wxml', 'wxss']) assert.ok(fs.existsSync(path.join(root, `${page}.${extension}`)));
    JSON.parse(fs.readFileSync(path.join(root, `${page}.json`), 'utf8'));
    const template = fs.readFileSync(path.join(root, `${page}.wxml`), 'utf8');
    const directives = [...template.matchAll(/wx:(?:if|elif|for)="([^"]*)"/g)];
    assert.ok(directives.length > 0);
    for (const [, expression] of directives) assert.match(expression, /^{{[\s\S]+}}$/, `unbound directive: ${expression}`);
    for (const [, expression] of template.matchAll(/{{([\s\S]*?)}}/g)) new vm.Script(`(${expression})`);
    const source = fs.readFileSync(path.join(root, `${page}.js`), 'utf8');
    for (const [, handler] of template.matchAll(/bind(?:tap|input)="([^"]+)"/g)) assert.match(source, new RegExp(`\\b${handler}\\s*\\(`), `missing handler ${handler}`);
    assert.doesNotMatch(template, /<(?:div|span|br|img|html)\b/);
  });
}
