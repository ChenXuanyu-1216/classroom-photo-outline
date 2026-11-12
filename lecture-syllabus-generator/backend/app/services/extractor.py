from dataclasses import dataclass
from app.services.recognizer import RecognitionResult
from app.services.model_output import RecognizedPage


@dataclass
class ExtractedKnowledgePoint:
    content: str
    kp_type: str
    source_type: str
    source_pages: str
    page_id: int
    dedup_key: str


class Extractor:
    def extract(self, page_id: int, page_no: int, recognition: RecognitionResult) -> list[ExtractedKnowledgePoint]:
        source_tag = f"P{page_no}"
        points = recognition.structured.get("points", [])

        # Legacy pages have plain points. Structured diagrams can also enter
        # through cached recognition; materialize without duplicate sentences.
        if (recognition.structured.get('relations') or recognition.structured.get('flows')
                or recognition.structured.get('unverified_relations')):
            body = {key: value for key, value in recognition.structured.items() if key != '_meta'}
            rendered = RecognizedPage.model_validate(body).visible_points()
            points = list({(p['content'], p['source_type']): p for p in rendered}.values())
        warnings = recognition.structured.get('uncertain_regions', [])

        if not points and recognition.text:
            lines = [l.strip().lstrip("- ").strip() for l in recognition.text.strip().split("\n") if l.strip()]
            if lines:
                title = lines[0]
                points = [{"content": l, "kp_type": "concept", "source_type": "来源待核对"} for l in lines[1:]]
                if not points:
                    points = [{"content": title, "kp_type": "concept", "source_type": "来源待核对"}]

        result = []
        points = list(points) + [{'content': f'待核对：{warning}', 'kp_type': 'concept',
                                'source_type': '来源待核对'} for warning in warnings]
        for p in points:
            content = p.get("content", "")
            if not content.strip():
                continue
            kp_type = p.get("kp_type", "concept")
            source_type = p.get("source_type") or "来源待核对"
            # Case, spaces and indentation can change formulas or code semantics.
            dedup_key = content
            result.append(ExtractedKnowledgePoint(
                content=content,
                kp_type=kp_type,
                source_type=source_type,
                source_pages=source_tag,
                page_id=page_id,
                dedup_key=dedup_key,
            ))
        return result

    def dedup_cross_page(self, all_points: list[ExtractedKnowledgePoint]) -> list[ExtractedKnowledgePoint]:
        merged: dict[tuple[str, str], ExtractedKnowledgePoint] = {}
        for kp in all_points:
            # Equal text does not make an inference an original quotation.
            key = (kp.dedup_key, kp.source_type)
            if key in merged:
                existing = merged[key]
                existing_pages = set(existing.source_pages.split(","))
                new_pages = set(kp.source_pages.split(","))
                combined = ",".join(sorted(existing_pages | new_pages, key=lambda x: int(x[1:])))
                merged[key] = ExtractedKnowledgePoint(
                    content=existing.content,
                    kp_type=existing.kp_type,
                    source_type=existing.source_type,
                    source_pages=combined,
                    page_id=existing.page_id,
                    dedup_key=existing.dedup_key,
                )
            else:
                merged[key] = kp
        return list(merged.values())
