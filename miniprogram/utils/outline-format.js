// Parse the backend's study-outline dialect into native text blocks.
// Content is always text, never HTML or executable rich-text input.
function formatOutline(markdown) {
  const logical = [];
  for (const line of markdown.split('\n')) {
    if (/^  /u.test(line) && logical.length && /^- /u.test(logical[logical.length - 1])) logical[logical.length - 1] += '\n' + line.slice(2);
    else logical.push(line);
  }
  return logical.filter(line => line.trim()).map((line, index) => {
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) return { key: index, type: `h${heading[1].length}`, text: heading[2], sources: [], label: '' };
    const point = /^[-*]\s+/.test(line);
    let text = point ? line.replace(/^[-*]\s+/, '') : line;
    let label = '';
    if (point) {
      const type = /【(课堂原话|AI推断|AI生成|来源待核对)】$/.exec(text);
      if (type) { label = type[1]; text = text.slice(0, type.index); }
    }
    const source = point && /（(来源|关联课堂页)：(P[1-9]\d*(?:,P[1-9]\d*)*)）$/.exec(text);
    const sources = source ? source[2].split(',').map(page => Number(page.slice(1))) : [];
    const sourceKind = source ? (label === 'AI生成' ? '关联课堂页' : source[1]) : '';
    if (source) text = text.slice(0, source.index);
    return { key: index, type: point ? 'point' : 'paragraph', text, label, sources, sourceKind };
  });
}
module.exports = { formatOutline };
