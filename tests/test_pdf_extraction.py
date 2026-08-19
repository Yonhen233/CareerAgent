from __future__ import annotations

import asyncio
import threading
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.core.config import Settings
from app.main import app
from app.services.pdf_extraction import PDFExtractionError, PDFExtractionService
from app.services.resume_parser import ResumeParserService
from app.services.text_splitter import PDFPageText, ResumeTextSplitter


def _text_pdf(pages: list[list[tuple[float, float, str]]]) -> bytes:
    output = BytesIO()
    writer = canvas.Canvas(output)
    for rows in pages:
        for x, y, text in rows:
            writer.drawString(x, y, text)
        writer.showPage()
    writer.save()
    return output.getvalue()


def _scan_image(lines: list[str]) -> bytes:
    image = Image.new("RGB", (1500, 620), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 38)
    for index, line in enumerate(lines):
        draw.text((50, 50 + index * 76), line, fill="black", font=font)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _mixed_pdf() -> bytes:
    output = BytesIO()
    writer = canvas.Canvas(output, pagesize=(1500, 620))
    writer.drawString(60, 550, "Li Ming Agent Development Intern")
    writer.drawString(60, 500, "Page one: Python FastAPI project experience")
    writer.showPage()
    scan = _scan_image(["项目经历", "CareerAgent 使用 LangGraph Redis RAG", "实现 checkpoint 与任务恢复"])
    writer.drawImage(ImageReader(BytesIO(scan)), 0, 0, width=1500, height=620)
    writer.save()
    return output.getvalue()


def test_pdf_rejects_invalid_signature_and_extension():
    service = PDFExtractionService()
    with pytest.raises(PDFExtractionError) as signature_error:
        service.extract(filename="resume.pdf", file_bytes=b"not a pdf")
    assert signature_error.value.code == "pdf_signature_invalid"

    valid = _text_pdf([[(72, 750, "Li Ming Agent Developer Resume")]])
    with pytest.raises(PDFExtractionError) as extension_error:
        service.extract(filename="resume.txt", file_bytes=valid)
    assert extension_error.value.code == "pdf_extension_invalid"


def test_pdf_upload_api_preserves_validation_status_codes():
    client = TestClient(app)

    invalid = client.post(
        "/profiles/upload",
        files={"file": ("resume.pdf", b"not a pdf", "application/pdf")},
    )
    oversized = client.post(
        "/profiles/upload",
        files={"file": ("resume.pdf", b"%PDF-" + b"0" * (16 * 1024 * 1024), "application/pdf")},
    )

    assert invalid.status_code == 400
    assert oversized.status_code == 413


def test_pdf_enforces_page_limit():
    settings = Settings(pdf_max_pages=1)
    service = PDFExtractionService(settings=settings)
    payload = _text_pdf(
        [
            [(72, 750, "Li Ming Agent Developer Resume")],
            [(72, 750, "CareerAgent project details")],
        ]
    )
    with pytest.raises(PDFExtractionError) as error:
        service.extract(filename="resume.pdf", file_bytes=payload)
    assert error.value.code == "pdf_too_many_pages"


def test_pdf_rejects_corrupt_and_password_protected_documents():
    service = PDFExtractionService()
    with pytest.raises(PDFExtractionError) as corrupt_error:
        service.extract(filename="resume.pdf", file_bytes=b"%PDF-1.7\ncorrupt")
    assert corrupt_error.value.code == "pdf_corrupt"

    source = PdfReader(BytesIO(_text_pdf([[(72, 750, "Private Resume Content")]])))
    encrypted = PdfWriter()
    encrypted.append_pages_from_reader(source)
    encrypted.encrypt("secret")
    output = BytesIO()
    encrypted.write(output)
    with pytest.raises(PDFExtractionError) as encrypted_error:
        service.extract(filename="resume.pdf", file_bytes=output.getvalue())
    assert encrypted_error.value.code == "pdf_encrypted"


def test_multipage_pdf_removes_repeated_margins_and_builds_cross_page_bridge():
    payload = _text_pdf(
        [
            [
                (72, 800, "Li Ming Resume - Page 1 of 2"),
                (72, 750, "Designed the retrieval and worker architecture."),
                (72, 700, "CareerAgent Project"),
            ],
            [
                (72, 800, "Li Ming Resume - Page 2 of 2"),
                (72, 750, "Implemented LangGraph checkpoint and Redis recovery."),
                (72, 700, "Measured RAG Recall and latency."),
            ],
        ]
    )
    result = PDFExtractionService().extract(filename="resume.pdf", file_bytes=payload)

    assert result.page_count == 2
    assert result.text_page_count == 2
    assert all("Li Ming Resume" not in page.text for page in result.pages)
    assert result.repeated_margin_lines_removed

    chunks = ResumeTextSplitter().split_pdf_pages(result.pages)
    bridge = next(chunk for chunk in chunks if chunk.metadata.get("strategy") == "cross_page_semantic_bridge")
    assert "CareerAgent Project" in bridge.text
    assert "LangGraph checkpoint" in bridge.text
    assert bridge.metadata["page_start"] == 1
    assert bridge.metadata["page_end"] == 2


def test_mixed_text_and_scanned_pdf_uses_page_level_ocr():
    result = PDFExtractionService().extract(filename="mixed.pdf", file_bytes=_mixed_pdf())

    assert result.page_count == 2
    assert result.text_page_count == 1
    assert result.ocr_page_count == 1
    assert result.page_diagnostics[1].extraction_method == "ocr"
    assert result.page_diagnostics[1].ocr_confidence > 0.8
    assert "CareerAgent" in result.pages[1].text
    assert "Redis" in result.pages[1].text


def test_blank_page_is_reported_without_inflating_text_coverage():
    payload = _text_pdf(
        [
            [(72, 750, "Li Ming Agent Developer Resume")],
            [],
        ]
    )
    result = PDFExtractionService().extract(filename="blank-page.pdf", file_bytes=payload)
    diagnostics = result.as_dict()

    assert result.blank_page_count == 1
    assert diagnostics["usable_text_page_count"] == 1
    assert diagnostics["text_page_coverage"] == 0.5


def test_ocr_two_column_rows_are_not_interleaved():
    service = PDFExtractionService()

    def row(left: float, top: float, right: float, text: str):
        return ([[left, top], [right, top], [right, top + 20], [left, top + 20]], text, 0.99)

    rows = [
        row(50, 50, 280, "Left title"),
        row(800, 50, 1100, "Right title"),
        row(50, 100, 300, "Left detail"),
        row(800, 100, 1150, "Right detail"),
    ]
    ordered, mode = service._order_ocr_rows(rows, image_width=1500)

    assert mode == "ocr_two_column"
    assert [item[1] for item in ordered] == ["Left title", "Left detail", "Right title", "Right detail"]


def test_two_column_pdf_records_layout_route():
    payload = _text_pdf(
        [
            [
                (60, 760, "Skills: Python FastAPI"),
                (60, 700, "Education: Software Engineering"),
                (340, 760, "CareerAgent Project"),
                (340, 700, "Implemented LangGraph Redis RAG workflow"),
            ]
        ]
    )
    result = PDFExtractionService().extract(filename="columns.pdf", file_bytes=payload)
    assert result.page_diagnostics[0].layout_mode == "two_column_blocks"
    assert result.pages[0].text.index("Skills") < result.pages[0].text.index("CareerAgent")


def test_pdf_profile_persists_extraction_diagnostics_and_cross_page_chunks(db_session):
    service = ResumeParserService()
    service.llm = type("UnavailableLLM", (), {"available": False})()
    profile = asyncio.run(
        service.create_profile_from_pdf(
            db_session,
            filename="mixed.pdf",
            file_bytes=_mixed_pdf(),
        )
    )

    diagnostics = profile.structured_profile_json["source_diagnostics"]["pdf_extraction"]
    assert diagnostics["page_count"] == 2
    assert diagnostics["ocr_page_count"] == 1
    assert any(
        chunk.metadata_json.get("strategy") == "cross_page_semantic_bridge"
        for chunk in profile.chunks
    )


def test_pdf_profile_offloads_cpu_extraction_from_async_request_thread(db_session):
    service = ResumeParserService()
    service.llm = type("UnavailableLLM", (), {"available": False})()
    request_thread = threading.get_ident()
    extraction_threads = []
    original_extract = service.pdf_extraction.extract

    def observed_extract(**kwargs):
        extraction_threads.append(threading.get_ident())
        return original_extract(**kwargs)

    service.pdf_extraction.extract = observed_extract
    asyncio.run(
        service.create_profile_from_pdf(
            db_session,
            filename="text.pdf",
            file_bytes=_text_pdf([[(72, 750, "CareerAgent uses FastAPI LangGraph and Redis recovery")]]),
        )
    )

    assert extraction_threads
    assert extraction_threads[0] != request_thread


def test_unrelated_complete_pages_do_not_create_cross_page_duplicate_chunks():
    pages = [
        PDFPageText(page_no=1, text="Education\nSoftware Engineering degree completed."),
        PDFPageText(page_no=2, text="Project Experience\nBuilt CareerAgent with LangGraph."),
    ]

    chunks = ResumeTextSplitter().split_pdf_pages(pages)

    assert not any(chunk.metadata.get("strategy") == "cross_page_semantic_bridge" for chunk in chunks)
