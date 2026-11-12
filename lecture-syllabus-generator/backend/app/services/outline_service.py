import re

from app.services.clusterer import ClusterModule


def logical_markdown_lines(markdown: str) -> list[str]:
    lines = []
    for line in markdown.split('\n'):
        if line.startswith('  ') and lines and lines[-1].startswith('- '):
            lines[-1] += '\n' + line[2:]
        else:
            lines.append(line)
    return lines


class OutlineService:
    def render_markdown(self, modules: list[ClusterModule]) -> str:
        lines = ["# 课程大纲", ""]
        for module in modules:
            lines.append(f"## {module.title}")
            if any(kp.source_type == 'AI生成' for kp in module.knowledge_points):
                lines.append('以下为 AI 生成的拓展知识，供学习参考，请自行核对；关联课堂页仅表示主题依据，不表示这些内容出现在原课件中。')
            for kp in module.knowledge_points:
                page_label = '关联课堂页' if kp.source_type == 'AI生成' else '来源'
                source_tag = f"（{page_label}：{kp.source_pages}）"
                type_tag = f"【{kp.source_type}】"
                content = kp.content.replace('\n', '\n  ')
                lines.append(f"- {content}{source_tag}{type_tag}")
            lines.append("")
        return "\n".join(lines).strip()

    def parse_markdown(self, markdown: str) -> list[dict]:
        modules = []
        current_module = None
        for line in logical_markdown_lines(markdown.strip()):
            if line.startswith("## "):
                if current_module:
                    modules.append(current_module)
                current_module = {"title": line[3:].strip(), "points": []}
            elif line.startswith("- ") and current_module is not None:
                content = line[2:]
                source_pages = ""
                source_type = ""
                if "【" in content and "】" in content:
                    s = content.rfind("【")
                    e = content.rfind("】")
                    if e == len(content) - 1 and content[s + 1:e] in {"课堂原话", "AI推断", "AI生成", "来源待核对"}:
                        source_type = content[s + 1:e]
                        content = content[:s] + content[e + 1:]
                # Only the terminal annotation is metadata; source mentions in
                # the knowledge body must survive parsing unchanged.
                page_tag = re.search(r'（(?:来源|关联课堂页)：(P[1-9][0-9]*(?:,P[1-9][0-9]*)*)）$', content)
                if page_tag:
                    source_pages = page_tag[1]
                    content = content[:page_tag.start()]
                current_module["points"].append({
                    "content": content,
                    "source_pages": source_pages,
                    "source_type": source_type,
                })
        if current_module:
            modules.append(current_module)
        return modules


outline_service = OutlineService()
