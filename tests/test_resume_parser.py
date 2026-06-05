import asyncio

from app.services.resume_parser import ResumeParserService


def test_heuristic_resume_parser_extracts_core_agent_skills():
    text = """
张三
zhangsan@example.com
Agent 开发实习生候选人

Projects
CareerAgent: Built a FastAPI RAG workflow with SQLite, tool calling, and evaluation.
"""
    parsed = asyncio.run(ResumeParserService().parse_structured_resume(text))
    assert parsed["email"] == "zhangsan@example.com"
    assert "FastAPI" in parsed["skills"]
    assert "RAG" in parsed["skills"]
    assert "SQLite" in parsed["skills"]
