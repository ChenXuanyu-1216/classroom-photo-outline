const { test } = require('node:test');
const assert = require('node:assert/strict');
const { formatOutline } = require('../utils/outline-format');

test('native reading blocks retain multiline knowledge and clickable source pages', () => {
  const blocks = formatOutline('# 课程大纲\n\n## 知识结构\n- 树的定义\n  保留正文中的 P99（来源：P1,P2）【课堂原话】\n\n- AI补充（关联课堂页：P3）【AI生成】');
  assert.equal(blocks[0].type, 'h1');
  assert.equal(blocks[1].type, 'h2');
  assert.equal(blocks[2].text, '树的定义\n保留正文中的 P99');
  assert.deepEqual(blocks[2].sources, [1, 2]);
  assert.equal(blocks[2].label, '课堂原话');
  assert.equal(blocks[3].label, 'AI生成');
  assert.deepEqual(blocks[3].sources, [3]);
});

test('literal markup is kept as text rather than executable HTML', () => {
  const blocks = formatOutline('## 标题\n<script>bad()</script>\n- 正文 P99');
  assert.equal(blocks[1].text, '<script>bad()</script>');
  assert.deepEqual(blocks[2].sources, []);
});

test('associated classroom pages remain associations without an AI label', () => {
  const blocks = formatOutline('- 补充判断（关联课堂页：P2）\n- 推断（关联课堂页：P3）【AI推断】');
  assert.equal(blocks[0].sourceKind, '关联课堂页');
  assert.equal(blocks[1].sourceKind, '关联课堂页');
});
