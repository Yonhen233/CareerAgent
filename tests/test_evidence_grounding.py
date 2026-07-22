from app.services.evidence_grounding import EvidenceGroundingService


def test_resume_grounding_rejects_skill_absent_from_source():
    result = EvidenceGroundingService().evaluate_resume(
        "李明，使用 Python 和 FastAPI 构建 CareerAgent。",
        {
            "name": "李明",
            "skills": ["Python", "FastAPI", "Kubernetes"],
            "projects": [],
            "work_experience": [],
            "campus_experience": [],
            "education": [],
        },
    )

    assert result["passed"] is False
    assert [item["value"] for item in result["unsupported_skills"]] == ["Kubernetes"]


def test_jd_grounding_accepts_canonical_aliases_from_chinese_source():
    result = EvidenceGroundingService().evaluate_jd(
        "岗位要求：理解模型评测、A/B 实验、推荐系统、排序模型和提示词注入。",
        {
            "required_skills": [
                "Model Evaluation",
                "A/B Testing",
                "Recommendation",
                "Ranking",
                "Prompt Injection",
            ],
            "preferred_skills": [],
            "responsibilities": [],
            "qualifications": [],
        },
    )

    assert result["passed"] is True
    assert result["unsupported_skills"] == []


def test_generated_claim_grounding_rejects_new_achievement():
    result = EvidenceGroundingService().evaluate_generated_claims(
        "我主导跨地域容灾系统并完成生产切换。",
        ["使用 Python 和 FastAPI 构建求职助手。"],
        threshold=0.12,
    )

    assert result["passed"] is False
    assert result["unsupported_claims"]


def test_resume_grounding_rejects_fabricated_project_impact_even_when_skills_are_supported():
    result = EvidenceGroundingService().evaluate_resume(
        "李明使用 Python 和 FastAPI 开发 CareerAgent，完成简历解析。",
        {
            "name": "李明",
            "skills": ["Python", "FastAPI"],
            "projects": [
                {
                    "name": "CareerAgent",
                    "description": "使用 Python 和 FastAPI 开发 CareerAgent，完成简历解析。",
                    "impact": "将生产故障率降低 80%",
                    "tech_stack": ["Python", "FastAPI"],
                }
            ],
            "work_experience": [],
            "campus_experience": [],
            "education": [],
        },
    )

    assert result["passed"] is False
    assert result["unsupported_claim_fields"][0]["field"] == "projects[0].impact"
