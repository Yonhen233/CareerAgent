from app.models.entities import Job, Profile
from app.services.guardrails import ResumeGuardrailService


def test_guardrail_rejects_missing_skill_learning_intent_in_resume_body():
    profile = Profile(
        name="Ma Chen",
        source_type="guided",
        raw_resume_text=(
            "Built object-detection experiments and evaluation dashboards with Python and PyTorch. "
            "No MLflow or feature store experience."
        ),
        structured_profile_json={
            "skills": ["Python", "PyTorch", "OpenCV", "Evaluation"],
            "projects": [
                {
                    "name": "VisionBench",
                    "description": "Built object-detection experiments and evaluation dashboards.",
                    "tech_stack": ["Python", "PyTorch"],
                }
            ],
        },
    )
    job = Job(
        source="manual",
        external_id="ml-platform",
        title="Machine Learning Platform Intern",
        raw_jd_text="Maintain MLflow, feature store pipelines, PyTorch baselines and model evaluation dashboards.",
        structured_jd_json={
            "required_skills": ["Python", "PyTorch", "MLflow", "Feature Store", "Model Evaluation"],
        },
    )
    resume = (
        "Built object-detection experiments and evaluation dashboards using Python and PyTorch. "
        "Eager to learn MLflow and feature store pipelines."
    )

    result = ResumeGuardrailService().verify(profile=profile, job=job, resume_markdown=resume, evidence=[{}] * 6)

    assert result["passed"] is False
    assert result["risk_level"] == "high"
    assert "model evaluation" in result["covered_required_skills"]
    assert "mlflow" not in result["covered_required_skills"]
    assert "feature store" not in result["covered_required_skills"]
    assert any(issue["type"] == "missing_skill_in_resume_body" for issue in result["issues"])


def test_guardrail_keeps_machine_learning_as_positive_context():
    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Built machine learning workflows with Python.",
        structured_profile_json={"skills": ["Python", "Machine Learning"]},
    )
    job = Job(
        source="manual",
        external_id="ml",
        title="ML Intern",
        raw_jd_text="Build machine learning workflows with Python.",
        structured_jd_json={"required_skills": ["Machine Learning", "Python"]},
    )

    result = ResumeGuardrailService().verify(
        profile=profile,
        job=job,
        resume_markdown="Built machine learning workflows with Python.",
        evidence=[{}] * 6,
    )

    assert result["passed"] is True
    assert "machine learning" in result["covered_required_skills"]


def test_guardrail_accepts_common_skill_aliases_from_source():
    profile = Profile(
        name="Tang Wei",
        source_type="guided",
        raw_resume_text="Built experiment analysis notebooks and analyzed A/B tests with product metrics.",
        structured_profile_json={"skills": ["Experiment Analysis", "Metrics"]},
    )
    job = Job(
        source="manual",
        external_id="recommendation",
        title="Recommendation Algorithm Intern",
        raw_jd_text="Analyze A/B testing and metrics for recommender systems.",
        structured_jd_json={"required_skills": ["A/B Testing", "Metrics"]},
    )

    result = ResumeGuardrailService().verify(
        profile=profile,
        job=job,
        resume_markdown="Hands-on experience in A/B Testing, experiment analysis, and Metrics.",
        evidence=[{}] * 6,
    )

    assert result["passed"] is True
    assert "a/b testing" in result["covered_required_skills"]


def test_guardrail_rejects_unsupported_semantic_achievement():
    profile = Profile(
        name="Li Ming",
        source_type="guided",
        raw_resume_text="使用 Python 和 FastAPI 构建 CareerAgent。",
        structured_profile_json={"skills": ["Python", "FastAPI"]},
    )
    job = Job(
        source="manual",
        external_id="agent-platform",
        title="Agent 开发实习生",
        raw_jd_text="负责 Agent 平台开发。",
        structured_jd_json={"required_skills": ["Python", "FastAPI"]},
    )

    result = ResumeGuardrailService().verify(
        profile=profile,
        job=job,
        resume_markdown="使用 Python 和 FastAPI。主导跨地域容灾系统并完成生产切换。",
        evidence=[],
    )

    assert result["passed"] is False
    assert any(issue["type"] == "unsupported_semantic_claim" for issue in result["issues"])
