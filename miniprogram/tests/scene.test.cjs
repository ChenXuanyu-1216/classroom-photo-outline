const { test } = require('node:test');
const assert = require('node:assert/strict');
const { sceneGeometry, drawKnowledgeScene } = require('../utils/knowledge-scene');
test('knowledge graph uses connected curves, a central node, and particles on the same curves', () => {
  const scene = sceneGeometry(700, 300, 0);
  assert.equal(scene.points.length, 6);
  assert.equal(scene.links.length, 7);
  assert.equal(scene.points[3].label, '知识脉络');
  assert.equal(scene.points[3].x, 700 * .65);
  for (const link of scene.links) {
    assert.equal(link.start, scene.points[link.from]);
    assert.equal(link.end, scene.points[link.to]);
    assert.notEqual(link.c1.y, link.start.y);
    assert.ok(Number.isFinite(link.particle.x) && Number.isFinite(link.particle.y));
  }
});
test('native canvas draws all graph edges as bezier curves rather than straight CSS strokes', () => {
  let curves = 0;
  const context = new Proxy({ bezierCurveTo() { curves++; } }, { get: (target, key) => key in target ? target[key] : () => {} });
  drawKnowledgeScene(context, 360, 160, 1);
  assert.equal(curves, 7);
});
test('small screens use the same compact node layout as the web diagram', () => {
  const center = sceneGeometry(360, 160, 0).points[3];
  assert.equal(center.x, 360 * .68);
  assert.equal(center.y, 160 * .65 + Math.sin(3) * 4);
});
