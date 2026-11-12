"""Build a self-contained, offline UI preview from the current frontend assets."""
from pathlib import Path
import re

APP_DIR = Path(__file__).resolve().parents[1]
FRONTEND = APP_DIR / "frontend"


def build_preview() -> Path:
    history = (FRONTEND / "history.html").read_text(encoding="utf-8")
    upload = (FRONTEND / "index.html").read_text(encoding="utf-8")
    upload_section = re.search(r"    <section\b[\s\S]*?</section>", upload).group()
    tools = '''<div class="kf-preview-tools">
      <p>样式预览 · 示例记录，打开与导出请使用运行中的系统。</p>
      <button id="preview-empty" type="button">查看空记录</button>
      <p id="preview-message" role="status"></p>
    </div>'''
    history = history.replace("</section>", tools + "</section>", 1)
    history = history.replace("</main>", '</main>\n<template data-workspace-page="upload" data-page-title="课堂大纲 · 离线预览">\n' + upload_section + "\n</template>", 1)
    history = re.sub(r'\s*<link rel="stylesheet"[^>]+>', "", history)
    history = re.sub(r'\s*<script src="[^"]+"></script>', "", history)
    history = history.replace("<title>整理记录 · 课堂大纲</title>", "<title>课堂大纲 · 离线预览</title>")
    css = "\n".join((FRONTEND / "css" / name).read_text(encoding="utf-8") for name in ["home.css", "history.css"])
    css += '''
    #knowledge-flow-home .kf-preview-tools { margin: 20px 0; text-align: center; color: var(--kf-muted); font-size: 12px; }
    #knowledge-flow-home .kf-preview-tools button { margin-top: 8px; border: 0; padding: 8px 14px; border-radius: 7px; background: var(--kf-wash); color: var(--kf-ink); }
    '''
    csp = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data:; connect-src \'none\'; base-uri \'none\'; form-action \'none\'">'
    history = history.replace("</head>", csp + "\n<style>" + css + "</style>\n</head>")
    scripts = "\n".join((FRONTEND / "js" / name).read_text(encoding="utf-8") for name in ["upload.js", "history.js", "knowledge-flow.js", "workspace.js", "page-transition.js"])
    behavior = '''
    let empty = false;
    const samples = [
      {task_id: 3, title: '高等数学：微积分', status: 'success', page_count: 12, created_at: '2026-10-17T03:20:00Z'},
      {task_id: 2, title: '数据结构：树与遍历', status: 'partial', page_count: 8, created_at: '2026-10-16T07:45:00Z'},
      {task_id: 1, title: '大学物理：电磁学', status: 'success', page_count: 16, created_at: '2026-10-15T01:10:00Z'}
    ];
    const outlines = {
      3: '# 高等数学：微积分\\n## 导数与微分\\n- 导数描述函数的局部变化率。来源：第 1–4 页\\n## 定积分\\n- 使用定积分计算区间上的累积量。来源：第 5–8 页\\n## 应用\\n- 面积与体积问题。来源：第 9–12 页',
      2: '# 数据结构：树与遍历\\n## 二叉树\\n- 二叉树的结构与存储。来源：第 1–3 页\\n## 树的遍历\\n- 前序、中序和后序遍历。来源：第 4–8 页',
      1: '# 大学物理：电磁学\\n## 电场\\n- 电场强度与电势。来源：第 1–8 页\\n## 磁场\\n- 磁感应强度与电磁感应。来源：第 9–16 页'
    };
    const API = {
      listTasks: async () => ({ok: true, json: async () => ({tasks: empty ? [] : samples})}),
      getOutline: async id => ({ok: true, json: async () => ({task_id: id, markdown: outlines[id], version: 1})}),
      createTask: async () => ({status: 400, json: async () => ({message: '离线预览不提交任务，请启动正式系统'})})
    };
    document.addEventListener('click', event => {
      const link = event.target.closest('a');
      if (!link) return;
      const target = link.getAttribute('href');
      if (target.startsWith('/outline.html') || target.startsWith('/api/')) {
        event.preventDefault();
        document.getElementById('preview-message').textContent = '这里是示例记录，请启动后端后使用打开与导出。';
      }
    });
    document.getElementById('preview-empty').addEventListener('click', event => {
      empty = !empty;
      event.target.textContent = empty ? '查看示例记录' : '查看空记录';
      const oldList = document.getElementById('task-list');
      oldList.replaceWith(oldList.cloneNode(false));
      ClassroomHistory.init();
    });
    '''
    history = history.replace("</body>", "<script>\n" + behavior + "\n" + scripts + "\n</script>\n</body>")
    destination = APP_DIR.parent / "frontend-preview.html"
    destination.write_text("\n".join(line.rstrip() for line in history.splitlines()) + "\n", encoding="utf-8")
    return destination


if __name__ == "__main__":
    print(build_preview())
