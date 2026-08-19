from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.services.text_splitter import PDFPageText


_OCR_ENGINE: Any | None = None


class PDFExtractionError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class PDFPageDiagnostic:
    page_no: int
    extraction_method: str
    character_count: int
    printable_ratio: float
    replacement_character_ratio: float
    alnum_ratio: float
    image_count: int
    layout_mode: str
    ocr_confidence: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class PDFExtractionResult:
    pages: list[PDFPageText]
    page_diagnostics: list[PDFPageDiagnostic]
    page_count: int
    text_page_count: int
    ocr_page_count: int
    blank_page_count: int
    repeated_margin_lines_removed: list[str]
    warnings: list[str]
    parser: str = "pymupdf+rapidocr"

    @property
    def raw_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text.strip()).strip()

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": "careeragent-pdf-extraction-v1",
            "parser": self.parser,
            "page_count": self.page_count,
            "text_page_count": self.text_page_count,
            "ocr_page_count": self.ocr_page_count,
            "blank_page_count": self.blank_page_count,
            "usable_text_page_count": self.text_page_count + self.ocr_page_count,
            "text_page_coverage": round(
                (self.text_page_count + self.ocr_page_count) / max(self.page_count, 1),
                4,
            ),
            "repeated_margin_lines_removed": self.repeated_margin_lines_removed,
            "warnings": self.warnings,
            "pages": [asdict(item) for item in self.page_diagnostics],
        }


class PDFExtractionService:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def extract(self, *, filename: str, file_bytes: bytes) -> PDFExtractionResult:
        self._validate_upload(filename, file_bytes)
        try:
            import pymupdf as fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency readiness failure
            raise PDFExtractionError(
                "pdf_layout_parser_unavailable",
                "PDF layout parser is unavailable. Install PyMuPDF before accepting resume PDFs.",
            ) from exc

        try:
            document = fitz.open(stream=file_bytes, filetype="pdf")
        except Exception as exc:
            raise PDFExtractionError("pdf_corrupt", f"PDF cannot be opened: {exc}") from exc
        try:
            if document.needs_pass:
                raise PDFExtractionError("pdf_encrypted", "Password-protected PDFs are not supported.")
            if document.page_count <= 0:
                raise PDFExtractionError("pdf_empty", "PDF contains no pages.")
            if document.page_count > self.settings.pdf_max_pages:
                raise PDFExtractionError(
                    "pdf_too_many_pages",
                    f"PDF has {document.page_count} pages; the limit is {self.settings.pdf_max_pages}.",
                    details={"page_count": document.page_count},
                )

            pages: list[PDFPageText] = []
            diagnostics: list[PDFPageDiagnostic] = []
            for index in range(document.page_count):
                page = document.load_page(index)
                text, layout_mode = self._layout_text(page)
                image_count = len(page.get_images(full=True))
                quality = self._text_quality(text)
                extraction_method = "text_layer"
                confidence: float | None = None
                warnings: list[str] = []
                needs_ocr = (
                    quality["character_count"] < self.settings.pdf_min_text_chars_per_page
                    or quality["printable_ratio"] < self.settings.pdf_min_printable_ratio
                    or quality["alnum_ratio"] < self.settings.pdf_min_alnum_ratio
                    or quality["replacement_character_ratio"] > self.settings.pdf_max_replacement_ratio
                )
                if needs_ocr and image_count:
                    if not self.settings.pdf_ocr_enabled:
                        raise PDFExtractionError(
                            "pdf_ocr_required",
                            f"Page {index + 1} is image-based and OCR is disabled.",
                            details={"page_no": index + 1},
                        )
                    text, confidence, layout_mode = self._ocr_page(page)
                    extraction_method = "ocr"
                    quality = self._text_quality(text)
                    if quality["character_count"] < self.settings.pdf_min_text_chars_per_page:
                        raise PDFExtractionError(
                            "pdf_ocr_empty",
                            f"OCR could not recover enough text from page {index + 1}.",
                            details={"page_no": index + 1, "character_count": quality["character_count"]},
                        )
                    if confidence is not None and confidence < self.settings.pdf_ocr_min_confidence:
                        raise PDFExtractionError(
                            "pdf_ocr_low_confidence",
                            f"OCR confidence is too low on page {index + 1}: {confidence:.3f}.",
                            details={"page_no": index + 1, "ocr_confidence": confidence},
                        )
                    warnings.append("page_text_recovered_by_ocr")
                elif needs_ocr and not image_count and quality["character_count"] == 0:
                    extraction_method = "blank"
                    text = ""
                    warnings.append("blank_page")
                elif needs_ocr:
                    raise PDFExtractionError(
                        "pdf_text_encoding_invalid",
                        f"Text extraction quality is too low on page {index + 1}, and no image is available for OCR.",
                        details={"page_no": index + 1, **quality},
                    )

                pages.append(PDFPageText(page_no=index + 1, text=text.strip()))
                diagnostics.append(
                    PDFPageDiagnostic(
                        page_no=index + 1,
                        extraction_method=extraction_method,
                        character_count=quality["character_count"],
                        printable_ratio=quality["printable_ratio"],
                        replacement_character_ratio=quality["replacement_character_ratio"],
                        alnum_ratio=quality["alnum_ratio"],
                        image_count=image_count,
                        layout_mode=layout_mode,
                        ocr_confidence=confidence,
                        warnings=warnings,
                    )
                )
        finally:
            document.close()

        pages, removed = self._remove_repeated_margin_lines(pages)
        text_pages = sum(item.extraction_method == "text_layer" for item in diagnostics)
        ocr_pages = sum(item.extraction_method == "ocr" for item in diagnostics)
        blank_pages = sum(item.extraction_method == "blank" for item in diagnostics)
        warnings = []
        if ocr_pages:
            warnings.append("ocr_used")
        if removed:
            warnings.append("repeated_page_margins_removed")
        result = PDFExtractionResult(
            pages=pages,
            page_diagnostics=diagnostics,
            page_count=len(pages),
            text_page_count=text_pages,
            ocr_page_count=ocr_pages,
            blank_page_count=blank_pages,
            repeated_margin_lines_removed=removed,
            warnings=warnings,
        )
        if not result.raw_text:
            raise PDFExtractionError("pdf_text_layer_missing", "No usable text could be extracted from the PDF.")
        return result

    def _validate_upload(self, filename: str, file_bytes: bytes) -> None:
        if not file_bytes:
            raise PDFExtractionError("pdf_empty_upload", "Uploaded file is empty.")
        if Path(filename).suffix.lower() != ".pdf":
            raise PDFExtractionError("pdf_extension_invalid", "Only .pdf resume files are accepted.")
        if not file_bytes.lstrip().startswith(b"%PDF-"):
            raise PDFExtractionError("pdf_signature_invalid", "The uploaded file is not a valid PDF.")
        maximum = self.settings.pdf_max_upload_mb * 1024 * 1024
        if len(file_bytes) > maximum:
            raise PDFExtractionError(
                "pdf_too_large",
                f"PDF is larger than the {self.settings.pdf_max_upload_mb} MB limit.",
                details={"size_bytes": len(file_bytes)},
            )

    def _layout_text(self, page: Any) -> tuple[str, str]:
        blocks = [
            block
            for block in page.get_text("blocks", sort=True)
            if len(block) >= 7 and int(block[6]) == 0 and str(block[4] or "").strip()
        ]
        if not blocks:
            return "", "empty_text_layer"
        width = max(float(page.rect.width), 1.0)
        midpoint = width / 2
        left = [block for block in blocks if float(block[0]) < midpoint and float(block[2]) <= width * 0.62]
        right = [block for block in blocks if float(block[0]) >= width * 0.38]
        is_two_column = len(left) >= 2 and len(right) >= 2
        if not is_two_column:
            ordered = sorted(blocks, key=lambda block: (round(float(block[1]), 1), float(block[0])))
            return "\n".join(str(block[4]).strip() for block in ordered), "block_order"

        full_width = [block for block in blocks if float(block[0]) < width * 0.25 and float(block[2]) > width * 0.75]
        full_ids = {id(block) for block in full_width}
        left_column = [block for block in blocks if id(block) not in full_ids and float(block[0]) < midpoint]
        right_column = [block for block in blocks if id(block) not in full_ids and float(block[0]) >= midpoint]
        ordered = sorted(full_width, key=lambda block: float(block[1]))
        ordered.extend(sorted(left_column, key=lambda block: (float(block[1]), float(block[0]))))
        ordered.extend(sorted(right_column, key=lambda block: (float(block[1]), float(block[0]))))
        return "\n".join(str(block[4]).strip() for block in ordered), "two_column_blocks"

    def _ocr_page(self, page: Any) -> tuple[str, float | None, str]:
        global _OCR_ENGINE
        try:
            import numpy as np
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:  # pragma: no cover - dependency readiness failure
            raise PDFExtractionError(
                "pdf_ocr_unavailable",
                "An image-based PDF was detected, but the local OCR runtime is unavailable.",
            ) from exc
        if _OCR_ENGINE is None:
            _OCR_ENGINE = RapidOCR()
        base_pixels = max(float(page.rect.width) * float(page.rect.height), 1.0)
        requested_dpi = self.settings.pdf_ocr_dpi
        render_pixels = base_pixels * (requested_dpi / 72.0) ** 2
        dpi = requested_dpi
        if render_pixels > self.settings.pdf_max_render_pixels:
            dpi = int(72.0 * math.sqrt(self.settings.pdf_max_render_pixels / base_pixels))
        if dpi < 96:
            raise PDFExtractionError(
                "pdf_page_dimensions_unsafe",
                "A PDF page is too large to render safely for OCR.",
                details={"page_width": page.rect.width, "page_height": page.rect.height},
            )
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        channels = pixmap.n
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, channels)
        result, _ = _OCR_ENGINE(image)
        if not result:
            return "", None, "ocr_empty"
        rows, layout_mode = self._order_ocr_rows(result, image_width=pixmap.width)
        texts = [str(item[1]).strip() for item in rows if str(item[1]).strip()]
        confidences = [float(item[2]) for item in rows if len(item) > 2]
        confidence = sum(confidences) / len(confidences) if confidences else None
        return "\n".join(texts), confidence, layout_mode

    def _order_ocr_rows(self, rows: list[Any], *, image_width: int) -> tuple[list[Any], str]:
        midpoint = max(float(image_width), 1.0) / 2
        metrics = [
            (
                row,
                min(float(point[0]) for point in row[0]),
                max(float(point[0]) for point in row[0]),
                self._ocr_box_top(row[0]),
            )
            for row in rows
        ]
        full_width = [item for item in metrics if item[1] < image_width * 0.2 and item[2] > image_width * 0.8]
        full_ids = {id(item[0]) for item in full_width}
        left = [item for item in metrics if id(item[0]) not in full_ids and (item[1] + item[2]) / 2 < midpoint]
        right = [item for item in metrics if id(item[0]) not in full_ids and (item[1] + item[2]) / 2 >= midpoint]
        if len(left) < 2 or len(right) < 2:
            ordered = sorted(metrics, key=lambda item: (item[3], item[1]))
            return [item[0] for item in ordered], "ocr_reading_order"

        first_column_y = min(item[3] for item in [*left, *right])
        top_full = [item for item in full_width if item[3] <= first_column_y]
        remaining_full = [item for item in full_width if item[3] > first_column_y]
        ordered = sorted(top_full, key=lambda item: (item[3], item[1]))
        ordered.extend(sorted(left, key=lambda item: (item[3], item[1])))
        ordered.extend(sorted(right, key=lambda item: (item[3], item[1])))
        ordered.extend(sorted(remaining_full, key=lambda item: (item[3], item[1])))
        return [item[0] for item in ordered], "ocr_two_column"

    @staticmethod
    def _ocr_box_top(box: Any) -> float:
        return min(float(point[1]) for point in box)

    @staticmethod
    def _ocr_box_left(box: Any) -> float:
        return min(float(point[0]) for point in box)

    def _text_quality(self, text: str) -> dict[str, Any]:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return {
                "character_count": 0,
                "printable_ratio": 1.0,
                "replacement_character_ratio": 0.0,
                "alnum_ratio": 1.0,
            }
        printable = sum(character.isprintable() for character in compact)
        replacements = compact.count("\ufffd")
        alnum = sum(character.isalnum() for character in compact)
        return {
            "character_count": len(compact),
            "printable_ratio": round(printable / len(compact), 4),
            "replacement_character_ratio": round(replacements / len(compact), 4),
            "alnum_ratio": round(alnum / len(compact), 4),
        }

    def _remove_repeated_margin_lines(self, pages: list[PDFPageText]) -> tuple[list[PDFPageText], list[str]]:
        if len(pages) < 2:
            return pages, []
        margin_lines: list[str] = []
        page_line_sets: list[set[str]] = []
        for page in pages:
            lines = [line.strip() for line in page.text.splitlines() if line.strip()]
            candidates = [*lines[:2], *lines[-2:]] if lines else []
            normalized = {self._normalize_margin_line(line) for line in candidates if len(line) <= 120}
            page_line_sets.append({line for line in normalized if line})
        counts = Counter(line for page_lines in page_line_sets for line in page_lines)
        threshold = max(2, math.ceil(len(pages) * 0.6))
        repeated = {line for line, count in counts.items() if count >= threshold}
        if not repeated:
            return pages, []
        cleaned_pages: list[PDFPageText] = []
        for page in pages:
            kept = []
            for line in page.text.splitlines():
                if self._normalize_margin_line(line) in repeated:
                    margin_lines.append(line.strip())
                    continue
                kept.append(line)
            cleaned_pages.append(PDFPageText(page_no=page.page_no, text="\n".join(kept).strip()))
        return cleaned_pages, list(dict.fromkeys(margin_lines))

    @staticmethod
    def _normalize_margin_line(line: str) -> str:
        value = " ".join(str(line or "").lower().split())
        value = re.sub(r"(?:第\s*)?\d+\s*(?:页|/\s*\d+|of\s*\d+)", "<page>", value)
        return value
