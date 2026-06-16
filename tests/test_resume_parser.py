import asyncio
import json

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


def test_resume_parser_retries_transient_llm_error(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()

    class FakeLLM:
        available = True

        def __init__(self) -> None:
            self.trace_names: list[str] = []

        async def generate_text(self, **kwargs):
            self.trace_names.append(kwargs["trace_name"])
            if len(self.trace_names) == 1:
                raise RuntimeError("RemoteProtocolError: Server disconnected without sending a response.")
            return json.dumps(
                {
                    "name": "Tang Wei",
                    "email": "tangwei@example.com",
                    "phone": None,
                    "headline": "Product Analytics Intern Candidate",
                    "target_roles": [],
                    "education": [],
                    "skills": ["SQL", "Python", "Metrics"],
                    "projects": [
                        {
                            "name": "MetricStudio",
                            "description": "Built funnel dashboards and experiment analysis notebooks.",
                            "tech_stack": ["SQL", "Python"],
                            "impact": "Defined metrics.",
                        }
                    ],
                    "work_experience": [],
                    "awards": [],
                    "languages": [],
                    "raw_text": "ignored",
                },
                ensure_ascii=False,
            )

    service = ResumeParserService()
    fake_llm = FakeLLM()
    service.llm = fake_llm

    parsed = asyncio.run(
        service.parse_structured_resume(
            "Tang Wei\ntangwei@example.com\nSkills: SQL, Python, Metrics\nProjects\nMetricStudio"
        )
    )

    assert fake_llm.trace_names == [
        "resume_parser.parse_structured_resume",
        "resume_parser.parse_structured_resume.retry_1",
    ]
    assert parsed["email"] == "tangwei@example.com"
    assert {"Python", "SQL"} <= set(parsed["skills"])
    get_settings.cache_clear()
