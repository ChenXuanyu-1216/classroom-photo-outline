from dataclasses import dataclass, field
from app.services.extractor import ExtractedKnowledgePoint
from app.config import settings


@dataclass
class ClusterModule:
    title: str
    knowledge_points: list[ExtractedKnowledgePoint] = field(default_factory=list)
    source_pages: str = ""


def numbered_title(title: str, index: int) -> str:
    cn_nums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
    if title.startswith(tuple(f"{n}、" for n in cn_nums)):
        return title
    prefix = cn_nums[index - 1] if index <= len(cn_nums) else str(index)
    return f"{prefix}、{title}"


class Clusterer:
    def cluster(self, points: list[ExtractedKnowledgePoint], page_titles: dict[int, str]) -> list[ClusterModule]:
        if not points:
            return []
        if settings.cluster_provider == "model":
            # Lazy import keeps ClusterModule shared without an import cycle.
            from app.services.model_clusterer import ModelClusterer
            return ModelClusterer().cluster(points, page_titles)
        return self._cluster_by_page(points, page_titles)

    def _cluster_by_page(self, points: list[ExtractedKnowledgePoint], page_titles: dict[int, str]) -> list[ClusterModule]:
        page_to_points: dict[int, list[ExtractedKnowledgePoint]] = {}
        for kp in points:
            primary_page = int(kp.source_pages.split(",")[0][1:])
            page_to_points.setdefault(primary_page, []).append(kp)

        modules = []
        sorted_pages = sorted(page_to_points.keys())
        for idx, page_no in enumerate(sorted_pages, 1):
            title = page_titles.get(page_no, f"模块{idx}")
            title = numbered_title(title, idx)

            kps = page_to_points[page_no]
            all_pages = set()
            for kp in kps:
                for p in kp.source_pages.split(","):
                    all_pages.add(p)
            source_pages = ",".join(sorted(all_pages, key=lambda x: int(x[1:])))

            modules.append(ClusterModule(
                title=title,
                knowledge_points=kps,
                source_pages=source_pages,
            ))

        return modules
