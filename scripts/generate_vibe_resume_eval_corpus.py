from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
VIBE_ROOT = Path(r"D:\实习\vibe-resume")
OUTPUT = ROOT / "evals" / "vibe_resume_corpus"
PDF_DIR = OUTPUT / "pdfs"
HTML_DIR = ROOT / "tmp" / "vibe_resume_corpus"


CASES = [
    {
        "id": "vibe_standard_agent",
        "template": "templates/internship-employment/standard-one-page/index.html",
        "candidate_name": "陈卓",
        "layout": "vibe_standard_one_page",
        "expected_sections": ["教育背景", "专业技能", "项目经历"],
        "critical_facts": ["CareerAgent", "LangGraph", "RAG", "Checkpoint"],
        "retrieval_expectations": [
            {"id": "agent_runtime", "query": "Agent runtime checkpoint recovery", "expected_text": "checkpoint", "expected_page_no": 1},
            {"id": "rag_retrieval", "query": "RAG multilingual retrieval", "expected_text": "RAG", "expected_page_no": 1},
        ],
        "expected_profile": {"has_research": False, "has_publications": False, "has_patents": False},
    },
    {
        "id": "vibe_dense_agent",
        "template": "templates/internship-employment/dense-two-page/index.html",
        "candidate_name": "沈知遥",
        "layout": "vibe_dense_two_page",
        "expected_sections": ["实习经历", "项目经历", "Session & Memory Runtime"],
        "critical_facts": ["Agent Harness", "Middleware Chain", "checkpoint", "Tool Registry"],
        "retrieval_expectations": [
            {"id": "harness", "query": "Agent Harness middleware tool registry", "expected_text": "Agent Harness", "expected_page_no": 1},
            {"id": "memory_runtime", "query": "session memory checkpoint recovery", "expected_text": "Session", "expected_page_no": 1},
        ],
        "expected_profile": {"has_research": False, "has_publications": False, "has_patents": False},
    },
    {
        "id": "vibe_research_agent",
        "template": "templates/research-application/research-classic/index.html",
        "candidate_name": "林望舒",
        "layout": "vibe_research_classic",
        "expected_sections": ["教育背景", "科研经历", "论文与成果", "专业技能"],
        "critical_facts": ["Block-wise KV Cache Compression", "MLSys 2026 Workshop", "128K 上下文", "显存峰值降低 41%"],
        "retrieval_expectations": [
            {"id": "research_method", "query": "long context KV cache research", "expected_text": "Block-wise KV Cache Compression", "expected_page_no": 1},
            {"id": "research_metric", "query": "显存峰值 long context", "expected_text": "显存峰值降低 41%", "expected_page_no": 1},
        ],
        "expected_profile": {"has_research": True, "has_publications": True, "has_patents": False},
    },
]


def _canonical_text(pdf_path: Path) -> tuple[str, int]:
    document = pymupdf.open(pdf_path)
    try:
        return "\n\n".join(page.get_text("text") for page in document), document.page_count
    finally:
        document.close()


def _render_source(case: dict, output: Path, vibe_root: Path) -> Path:
    template = vibe_root / case["template"]
    html_path = HTML_DIR / f"{case['id']}.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    source = template.read_text(encoding="utf-8")
    base_href = template.parent.as_uri().rstrip("/") + "/"
    source = source.replace(
        "<head>",
        f"<head>\n    <base href=\"{base_href}\" />\n    <meta name=\"eval-case\" content=\"{case['id']}\" />",
        1,
    )
    for template_name in ("陈卓", "沈知遥", "林望舒"):
        source = source.replace(template_name, case["candidate_name"])
    html_path.write_text(source, encoding="utf-8")
    command = ["node", str(vibe_root / "scripts" / "export-pdf.mjs"), str(output), str(html_path)]
    subprocess.run(command, cwd=vibe_root, check=True)
    return html_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export browser-rendered VibeResume templates as PDF evaluation controls.")
    parser.add_argument("--vibe-root", type=Path, default=VIBE_ROOT)
    args = parser.parse_args()
    if not args.vibe_root.exists():
        raise SystemExit(f"VibeResume repository not found: {args.vibe_root}")
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for case in CASES:
        output = PDF_DIR / f"{case['id']}.pdf"
        _render_source(case, output, args.vibe_root)
        text, page_count = _canonical_text(output)
        row = {
            **case,
            "source_repository": str(args.vibe_root),
            "source_template": case["template"],
            "synthetic": True,
            "expected_page_count": page_count,
            "canonical_text": text,
            "canonical_character_count": len("".join(text.split())),
            "pdf_path": str(output.relative_to(ROOT)).replace("\\", "/"),
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        }
        manifest.append(row)
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(manifest), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
