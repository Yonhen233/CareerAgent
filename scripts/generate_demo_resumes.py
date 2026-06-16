from __future__ import annotations

from pathlib import Path
import textwrap


OUTPUT_DIR = Path("demo_resumes")


RESUMES = {
    "agent_intern_strong_resume.pdf": [
        "Li Ming - Agent Development Intern Candidate",
        "Email: liming@example.com | Phone: 13800000000 | Location: Shenzhen",
        "Target Roles: Agent Development Intern, AI Application Development Intern",
        "Skills: Python, FastAPI, SQLite, RAG, Chroma, LLM, prompt engineering, evaluation, guardrails, Playwright, pytest",
        "Project: CareerAgent",
        "Built a career assistant agent for Chinese internship scenarios.",
        "Implemented PDF resume chunking, SQLite metadata storage, vector retrieval, Top20 reranker, JD parsing, job matching, resume tailoring, application packet generation and interview preparation.",
        "Designed Plan-Execute workflow with traceable steps, artifacts, LLM call logs and guardrail repair loop for high risk resume drafts.",
        "Used FastAPI async endpoints, SQLite persistence, background task progress, RAG evaluation datasets and full-flow LLM workflow tests.",
        "Measured PDF chunk and RAG strategies with synthetic noisy samples, selected section-aware chunking plus overlap and reranked retrieval.",
        "Experience: AI Application Lab Intern",
        "Developed internal tools with Python APIs and dashboards. Wrote pytest cases and monitored latency, error traces and structured JSON failures.",
        "Education: B.S. Computer Science, 2027",
    ],
    "agent_intern_noisy_resume.pdf": [
        "Wang Yu - AI Product Engineering Intern Candidate",
        "Email: wangyu@example.com | Location: Shanghai",
        "Target: Agent Intern, LLM Application Intern",
        "Core Skills: Python, FastAPI, SQL, LangChain reading notes, RAG coursework, UI prototyping, data analysis",
        "Project: Campus Offer Helper",
        "Created a prototype for collecting job posts and comparing resumes. The first version used keyword rules and simple SQLite tables.",
        "Coursework: read papers about ReAct, Plan-Execute, vector search and reranking. Planned to add Chroma but did not ship the vector module.",
        "Project: Resume QA Demo",
        "Implemented a small PDF text extraction demo and Streamlit page. It could answer basic resume questions but had no production tracing.",
        "Notes: no deployed browser automation, no MCP integration, no real online application submission.",
        "Education: Software Engineering, 2026",
    ],
    "backend_platform_resume.pdf": [
        "Chen Hao - Backend Platform Intern Candidate",
        "Email: chenhao@example.com | Location: Beijing",
        "Target Roles: Backend Intern, AI Platform Intern",
        "Skills: Python, Java, FastAPI, MySQL, SQLite, Redis, Docker, pytest, REST API, basic LLM API integration",
        "Project: Operations Dashboard",
        "Built FastAPI services for task queue status, request metrics, configuration masking and admin token checks.",
        "Designed database models, pagination APIs, validation logic and deployment health endpoints.",
        "Project: Document Processing Service",
        "Implemented PDF text extraction, section tagging, chunk storage and asynchronous batch processing for internal documents.",
        "Related AI Work: integrated an LLM JSON extraction endpoint and added retry logs for invalid JSON responses.",
        "Gap Notes: limited hands-on reranker tuning and no large scale vector database deployment.",
    ],
    "ml_rag_partial_resume.pdf": [
        "Zhao Ran - Machine Learning Intern Candidate",
        "Email: zhaoran@example.com | Location: Hangzhou",
        "Target Roles: ML Intern, RAG Evaluation Intern",
        "Skills: Python, PyTorch, pandas, evaluation metrics, embedding experiments, FAISS tutorial, model analysis",
        "Project: VisionBench",
        "Built model evaluation dashboards with PyTorch and pandas. Compared accuracy, latency and failure categories across image classifiers.",
        "Project: Retrieval Study",
        "Ran offline experiments for BM25 and dense retrieval on course documents. Logged recall at k and manually labeled hard negative cases.",
        "Coursework: learned LangGraph and agent concepts from papers and blog posts, but did not ship a multi-agent production workflow.",
        "Gap Notes: no FastAPI production service, no online job source integration, no application packet generation.",
    ],
}


def escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_text_stream(lines: list[str]) -> bytes:
    commands: list[str] = ["BT", "/F1 10 Tf"]
    y = 780
    for raw in lines:
        wrapped = textwrap.wrap(raw, width=92) or [""]
        for line in wrapped:
            commands.append(f"1 0 0 1 50 {y} Tm ({escape_pdf_text(line)}) Tj")
            y -= 14
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", errors="replace")


def write_pdf(path: Path, lines: list[str]) -> None:
    stream = build_text_stream(lines)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(output)


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    for filename, lines in RESUMES.items():
        write_pdf(OUTPUT_DIR / filename, lines)
        print(OUTPUT_DIR / filename)


if __name__ == "__main__":
    main()
