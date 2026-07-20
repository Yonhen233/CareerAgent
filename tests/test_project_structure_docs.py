from pathlib import Path


def test_project_structure_document_tracks_core_runtime_boundaries():
    document = Path("docs/PROJECT_STRUCTURE.md").read_text(encoding="utf-8")
    required_paths = [
        "app/main.py",
        "app/agents/langgraph_orchestrator.py",
        "app/agents/tools.py",
        "app/services/run_business_summary.py",
        "skills/resume_tailoring/SKILL.md",
        "evals/golden_demo_scenarios.json",
        "scripts/run_agent_worker_supervisor.py",
        "docker-compose.smtp.yml",
    ]

    for relative_path in required_paths:
        assert Path(relative_path).exists(), relative_path
        assert Path(relative_path).name in document

    for section in ["当前架构", "分层职责", "常见需求去哪里改", "新文件放置规则"]:
        assert section in document


def test_interview_snapshots_are_archived_outside_repository_root():
    archive = Path("docs/interview/archive-2026-07-06")
    names = [
        "CAREER_AGENT_INTERVIEW_QA.md",
        "CAREER_AGENT_INTERVIEW_REPORT.md",
        "CAREER_AGENT_WORKFLOW_DIAGRAMS.md",
    ]

    for name in names:
        assert (archive / name).is_file()
        assert not Path(name).exists()
