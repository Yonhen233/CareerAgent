from app.models.schemas import AgentRunRequest, JobSearchRequest
from app.core.config import get_settings
from app.services.job_sources import JobSourceRegistry


def test_job_search_defaults_are_chinese_first():
    request = JobSearchRequest()

    assert request.query == "Agent 开发实习生"
    assert request.sources == [
        "tencent", "baidu", "meituan", "bytedance", "alibaba", "jd", "tcl",
        "midea", "wind", "moka_cn",
    ]


def test_agent_run_defaults_are_chinese_first():
    request = AgentRunRequest(task_type="find_jobs_for_profile")

    assert request.query == "Agent 开发实习生"


def test_job_source_registry_defaults_to_chinese_source_only(monkeypatch):
    monkeypatch.setenv("TENCENT_CAREERS_ENABLED", "true")
    monkeypatch.setenv("BAIDU_CAREERS_ENABLED", "true")
    monkeypatch.setenv("MEITUAN_CAREERS_ENABLED", "true")
    monkeypatch.setenv("BYTEDANCE_CAREERS_ENABLED", "true")
    monkeypatch.setenv("ALIBABA_CAREERS_ENABLED", "true")
    monkeypatch.setenv("JD_CAREERS_ENABLED", "true")
    monkeypatch.setenv("TCL_CAREERS_ENABLED", "true")
    monkeypatch.setenv("MIDEA_CAREERS_ENABLED", "true")
    monkeypatch.setenv("WIND_CAREERS_ENABLED", "true")
    monkeypatch.setenv("MOKA_CHINA_CAREERS_ENABLED", "true")
    monkeypatch.setenv("LEVER_CAREERS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        registry = JobSourceRegistry()
    finally:
        get_settings.cache_clear()

    assert list(registry.sources) == [
        "tencent", "baidu", "meituan", "bytedance", "alibaba", "jd", "tcl",
        "midea", "wind", "moka_cn",
    ]
    assert "lever" not in registry.sources
    assert "greenhouse" not in registry.sources
