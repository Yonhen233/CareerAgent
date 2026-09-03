from __future__ import annotations

import json
import re
from typing import Any


class DocumentSchemaBatcher:
    """Deterministically split documents and merge schema fields with source provenance."""

    def split(self, text: str, *, max_chars: int) -> list[dict[str, Any]]:
        if len(text) <= max_chars:
            return [{"chunk_id": "section-1", "text": text, "start": 0, "end": len(text)}]
        blocks = [
            block
            for block in re.split(
                r"(?=\n(?:第?\d+页|教育经历|项目经历|实习经历|工作经历|任职要求|岗位职责|加分项)[:：]?)",
                text,
            )
            if block
        ]
        if len(blocks) == 1:
            blocks = [text[index : index + max_chars] for index in range(0, len(text), max_chars)]
        output: list[dict[str, Any]] = []
        buffer = ""
        start = 0
        for block in blocks:
            if buffer and len(buffer) + len(block) > max_chars:
                output.append(self._chunk(len(output), buffer, start))
                start += len(buffer)
                buffer = ""
            if len(block) > max_chars:
                for index in range(0, len(block), max_chars):
                    part = block[index : index + max_chars]
                    output.append(self._chunk(len(output), part, start + index))
                start += len(block)
            else:
                buffer += block
        if buffer:
            output.append(self._chunk(len(output), buffer, start))
        return output

    def merge(
        self,
        rows: list[tuple[dict[str, Any], dict[str, Any]]],
        *,
        list_fields: set[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        merged: dict[str, Any] = {}
        sources: dict[str, list[str]] = {}
        conflicts: list[dict[str, Any]] = []
        for chunk, payload in rows:
            chunk_id = str(chunk["chunk_id"])
            for field, value in payload.items():
                if value in (None, "", [], {}):
                    continue
                sources.setdefault(field, []).append(chunk_id)
                if field in list_fields:
                    target = merged.setdefault(field, [])
                    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in target}
                    for item in value if isinstance(value, list) else [value]:
                        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                        if key not in seen:
                            target.append(item)
                            seen.add(key)
                elif field not in merged:
                    merged[field] = value
                elif merged[field] != value:
                    conflicts.append(
                        {
                            "field": field,
                            "kept_value": merged[field],
                            "conflicting_value": value,
                            "chunk_id": chunk_id,
                        }
                    )
        return merged, {
            "mode": "section_batch_python_schema_merge",
            "chunk_count": len(rows),
            "field_sources": sources,
            "conflicts": conflicts,
            "llm_conflict_resolution": False,
        }

    @staticmethod
    def _chunk(index: int, text: str, start: int) -> dict[str, Any]:
        return {
            "chunk_id": f"section-{index + 1}",
            "text": text,
            "start": start,
            "end": start + len(text),
        }
