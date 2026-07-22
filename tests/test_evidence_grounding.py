from app.services.evidence_grounding import EvidenceGroundingService


def test_support_score_ignores_terminal_period_tokenization_noise():
    service = EvidenceGroundingService()

    score = service.support_score(
        "Developed Terraform modules.",
        "Built Kubernetes templates, Terraform modules and Prometheus alert rules.",
    )

    assert score >= 0.6


def test_citation_grounding_accepts_positive_fact_spanning_adjacent_pdf_lines():
    service = EvidenceGroundingService()
    source = (
        "技能\nPython | FastAPI | SQLite | RAG\n项目经历\nDocRAG\n"
        "解析 PDF 列表、表格和跨页标题\n使用向量检索返回证据片段\n"
        "补充说明：仅在课程作业中使用过 LangGraph，没有 Redis 实践。"
    )

    report = service.evaluate_citations(
        ["技能包括 Python、FastAPI、RAG", "项目 DocRAG 涉及 PDF 解析和向量检索"],
        [source],
        threshold=0.5,
        require_positive=True,
    )

    assert report["passed"] is True


def test_citation_grounding_rejects_reading_as_positive_experience():
    service = EvidenceGroundingService()

    report = service.evaluate_citations(
        ["Read articles about RAG, Agent, FastAPI and SQLite"],
        ["Coursework: Read articles about RAG, Agent, FastAPI and SQLite."],
        threshold=0.5,
        require_positive=True,
    )

    assert report["passed"] is False


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


def test_resume_grounding_rejects_inferred_target_role_even_in_long_profile():
    result = EvidenceGroundingService().evaluate_resume(
        "许言，Agent 开发候选人。技能：Python、FastAPI、LangGraph。项目 AgentOps 实现事件流和健康探针。",
        {
            "name": "许言",
            "headline": "Agent 开发候选人",
            "target_roles": ["Agent 开发实习生"],
            "skills": ["Python", "FastAPI", "LangGraph"],
            "projects": [{"name": "AgentOps", "description": "实现事件流和健康探针"}],
            "work_experience": [],
            "campus_experience": [],
            "education": [],
        },
    )

    assert result["passed"] is False
    assert result["unsupported_target_roles"] == [
        {"field": "target_roles[0]", "value": "Agent 开发实习生"}
    ]


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


def test_jd_grounding_records_retrieval_keyword_expansion_without_treating_it_as_fact():
    result = EvidenceGroundingService().evaluate_jd(
        "岗位要求：使用 Python 开发 Agent 工作流。",
        {
            "required_skills": ["Python", "Agent"],
            "preferred_skills": [],
            "keywords": ["Python", "Agent", "大模型"],
            "responsibilities": ["使用 Python 开发 Agent 工作流"],
            "qualifications": [],
        },
    )

    assert result["passed"] is True
    assert result["unsupported_skills"] == []
    assert result["unsupported_keywords"] == [{"field": "keywords", "value": "大模型"}]


def test_generated_claim_grounding_rejects_new_achievement():
    result = EvidenceGroundingService().evaluate_generated_claims(
        "我主导跨地域容灾系统并完成生产切换。",
        ["使用 Python 和 FastAPI 构建求职助手。"],
        threshold=0.12,
    )

    assert result["passed"] is False
    assert result["unsupported_claims"]


def test_fit_gap_grounding_accepts_requirement_only_listed_as_profile_skill():
    result = EvidenceGroundingService().evaluate_fit_gaps(
        ["JD 要求熟悉 FastAPI，但候选人的 FastAPI 仅在技能列表中，未在项目中体现"],
        jd={"required_skills": ["Python", "FastAPI"], "preferred_skills": []},
        jd_sources=["任职要求：熟悉 Python、FastAPI。"],
        profile={
            "skills": ["Python", "FastAPI"],
            "projects": [{"name": "CareerAgent", "description": "使用 Python 构建求职助手"}],
            "work_experience": [
                {"company": "", "duration": "", "role": "技能：Python、FastAPI", "details": "技能：Python、FastAPI"}
            ],
        },
    )

    assert result["passed"] is True
    assert result["results"][0]["matched_requirements"] == ["FastAPI"]
    assert result["results"][0]["missing_delivery_evidence"] == ["FastAPI"]


def test_fit_gap_grounding_rejects_missing_claim_when_project_has_direct_evidence():
    result = EvidenceGroundingService().evaluate_fit_gaps(
        ["JD 要求熟悉 FastAPI，但候选人没有项目使用证据"],
        jd={"required_skills": ["FastAPI"], "preferred_skills": []},
        jd_sources=["任职要求：熟悉 FastAPI。"],
        profile={
            "skills": ["FastAPI"],
            "projects": [{"name": "CareerAgent", "description": "使用 FastAPI 构建异步 API 服务"}],
        },
    )

    assert result["passed"] is False
    assert result["results"][0]["candidate_absence_verified"] is False


def test_fit_gap_grounding_rejects_generic_history_gap_not_required_by_jd():
    result = EvidenceGroundingService().evaluate_fit_gaps(
        ["No work experience or internship history"],
        jd={"required_skills": ["distributed tracing"], "preferred_skills": []},
        jd_sources=["Requires production distributed tracing experience."],
        profile={"projects": [{"name": "AgentOps", "description": "Built health probes."}]},
    )

    assert result["passed"] is False
    assert result["results"][0]["matched_requirements"] == []


def test_positive_support_uses_english_sentence_boundary_before_negative_clause():
    source = (
        "Implemented LangGraph event streaming, structured logs and graceful worker drain. "
        "Did not implement distributed tracing across external model providers."
    )

    assert EvidenceGroundingService().has_positive_support(
        "Implemented LangGraph event streaming, structured logs and graceful worker drain",
        source,
    ) is True


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


def test_grounding_does_not_treat_agenttrace_as_agent_delivery():
    grounding = EvidenceGroundingService()
    source = "AgentTrace: 构建工具调用 trace 查看器和 RAG 引用检查。"

    assert grounding.value_supported("Agent", source) is False
    assert grounding.has_positive_support("Agent", source) is False
    assert grounding.has_positive_support("Agent", "Implemented an Agent workflow.") is True
