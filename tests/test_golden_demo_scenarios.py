import json
from pathlib import Path

from app.agents.skills import active_skill_names_for_task
from app.agents.tools import AgentPlanner
from app.models.entities import AgentRun, Job, Profile
from app.models.schemas import AgentRunRequest
from app.services.run_business_summary import RunBusinessSummaryService


def test_golden_demo_scenarios_cover_match_tailor_and_approval_paths():
    payload = json.loads(
        Path("evals/golden_demo_scenarios.json").read_text(encoding="utf-8")
    )
    scenarios = {item["id"]: item for item in payload["scenarios"]}

    assert set(scenarios) == {
        "job_match",
        "evidence_tailor",
        "approval_application",
    }
    assert "match_score" in scenarios["job_match"]["expected_outputs"]
    assert "guardrail_passed" in scenarios["evidence_tailor"]["expected_outputs"]
    assert "approval_bypass_detected" in scenarios["approval_application"]["expected_outputs"]
    assert all(item["forbidden_behaviors"] for item in scenarios.values())


def test_golden_demo_task_has_versioned_skills_and_valid_tool_permissions():
    task_type = "full_career_flow"
    skill_names = active_skill_names_for_task(task_type)
    plan = AgentPlanner().build_plan(
        AgentRunRequest(task_type=task_type, profile_id=1, job_id=1)
    )

    assert "fit_assessment" in skill_names
    assert "resume_tailoring" in skill_names
    assert "application_packet" in skill_names
    assert plan["tool_permission_validation"]["passed"] is True
    assert plan["skill_disclosure"]["instructions_in_plan"] is False
    assert all(contract["version"] for contract in plan["skill_contracts"])


def test_business_summary_preserves_zero_match_score(db_session):
    profile = Profile(
        name="Zero Score Candidate",
        source_type="guided",
        raw_resume_text="No Agent delivery evidence.",
        structured_profile_json={"skills": []},
    )
    job = Job(
        source="manual",
        external_id="zero-score-job",
        title="Agent 开发实习生",
        company="DemoAI",
        raw_jd_text="需要生产级 Agent 项目经验。",
        structured_jd_json={"required_skills": ["Agent"]},
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)
    run = AgentRun(
        task_type="find_jobs_for_profile",
        profile_id=profile.id,
        job_id=job.id,
        status="completed",
        input_json={},
        output_json={
            "selected_job": {
                "job_id": job.id,
                "overall_score": 0,
                "matched_skills": [],
                "missing_skills": ["Agent"],
            }
        },
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    summary = RunBusinessSummaryService().build(db_session, run=run)

    assert summary["metrics"]["match_score"] == 0.0


def test_business_summary_distinguishes_job_selection_from_application_confirmation(db_session):
    run = AgentRun(
        task_type="full_career_flow",
        status="waiting_for_confirmation",
        input_json={},
        output_json={
            "requires_confirmation": True,
            "confirmation_type": "job_selection",
            "interrupts": [
                {
                    "value": {
                        "kind": "job_selection",
                        "matches": [
                            {"job_id": 1, "title": "Agent 工程师"},
                            {"job_id": 2, "title": "RAG 开发实习生"},
                        ],
                    }
                }
            ],
        },
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    summary = RunBusinessSummaryService().build(db_session, run=run)

    assert summary["headline"] == "已找到 2 个候选岗位，等待你选择"
