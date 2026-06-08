from app.models.schemas import AgentRunRequest, JobSearchRequest


def test_job_search_defaults_are_chinese_first():
    request = JobSearchRequest()

    assert request.query == "Agent 开发实习生"
    assert request.sources == ["tencent"]


def test_agent_run_defaults_are_chinese_first():
    request = AgentRunRequest(task_type="find_jobs_for_profile")

    assert request.query == "Agent 开发实习生"
