const nodes = [[.13,.5,'课堂照片'],[.37,.22,'提取原句'],[.38,.78,'回溯来源'],[.65,.49,'知识脉络'],[.88,.18,'理解'],[.89,.78,'复习']];
const edges = [[0,1],[0,2],[1,3],[2,3],[3,4],[3,5],[4,5]];
const compact = [[.1,.53],[.37,.12],[.35,.81],[.68,.65],[.9,.13],[.92,.86]];
function sceneGeometry(width, height, time) {
  const points = nodes.map((node, index) => { const position = width < 400 ? compact[index] : node; return { x: position[0] * width, y: position[1] * height + Math.sin(time * .28 + index) * 4, label: node[2] }; });
  const links = edges.map(([from, to], index) => {
    const start = points[from], end = points[to], bend = (index % 2 ? 1 : -1) * height * .14;
    const c1 = { x: start.x + (end.x - start.x) * .4, y: start.y + bend };
    const c2 = { x: end.x - (end.x - start.x) * .4, y: end.y - bend };
    const u = (time * .055 + index * .21) % 1, v = 1 - u;
    return { from, to, start, end, c1, c2, dashed: index === 1, particle: {
      x: v*v*v*start.x + 3*v*v*u*c1.x + 3*v*u*u*c2.x + u*u*u*end.x,
      y: v*v*v*start.y + 3*v*v*u*c1.y + 3*v*u*u*c2.y + u*u*u*end.y
    } };
  });
  return { points, links };
}
function drawKnowledgeScene(ctx, width, height, time) {
  const scene = sceneGeometry(width, height, time);
  ctx.clearRect(0, 0, width, height);
  scene.links.forEach(link => {
    ctx.beginPath(); ctx.moveTo(link.start.x, link.start.y);
    ctx.bezierCurveTo(link.c1.x, link.c1.y, link.c2.x, link.c2.y, link.end.x, link.end.y);
    ctx.strokeStyle = 'rgba(88,119,80,.28)'; ctx.lineWidth = .75;
    ctx.setLineDash(link.dashed ? [2,4] : []); ctx.stroke(); ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(link.particle.x, link.particle.y, 1.6, 0, Math.PI*2); ctx.fillStyle = '#8eaa7e'; ctx.fill();
  });
  scene.points.forEach((point, index) => {
    ctx.beginPath(); ctx.arc(point.x, point.y, index === 3 ? 4 : 3, 0, Math.PI*2);
    ctx.fillStyle = index === 3 ? '#24483e' : '#f7f4eb'; ctx.fill(); ctx.strokeStyle = '#91a082'; ctx.stroke();
    if (index === 3) { ctx.beginPath(); ctx.arc(point.x, point.y, 10 + Math.sin(time*.6), 0, Math.PI*2); ctx.strokeStyle = '#a0ac8b55'; ctx.stroke(); }
    ctx.font = `${width < 400 ? 10 : 12}px "Songti SC",SimSun,serif`; ctx.fillStyle = '#829074'; ctx.textAlign = 'center';
    ctx.fillText(point.label, point.x, point.y - 16);
  });
}
module.exports = { sceneGeometry, drawKnowledgeScene };
