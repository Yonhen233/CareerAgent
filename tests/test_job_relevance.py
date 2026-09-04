from app.services.job_relevance import (
    is_internship_like_posting,
    rank_postings_for_query,
    score_job_posting,
)
from app.services.job_sources import JobPosting


def _posting(title: str, job_type: str = "", jd: str = "") -> JobPosting:
    return JobPosting(
        source="fixture",
        external_id=title,
        title=title,
        company="Example",
        location="深圳",
        job_type=job_type,
        apply_url=f"https://example.com/jobs/{abs(hash(title))}",
        raw_jd_text=jd or f"{title}\n负责 AI Agent 能力建设。",
    )


def test_chinese_agent_intern_query_prioritizes_dev_interns():
    query = "Agent 开发实习生"
    postings = [
        _posting("QQ-Agent产品经理", "产品", "负责 Agent 产品规划和需求分析。"),
        _posting("AI Agent Sales Intern", "Internship", "Work with enterprise customers and sales teams."),
        _posting("元宝-Agent架构工程师", "技术", "负责大模型 Agent 架构和平台工程。"),
        _posting(
            "AI Agent Research & Application Intern",
            "Internship",
            "Build AI Agent applications with Python, RAG and evaluation.",
        ),
        _posting("游戏研发 Agent 产品策划", "产品", "负责游戏研发 Agent 产品策划。"),
    ]

    ranked = rank_postings_for_query(postings, query)
    titles = [posting.title for posting in ranked]

    assert titles[0] == "AI Agent Research & Application Intern"
    assert titles.index("元宝-Agent架构工程师") < titles.index("QQ-Agent产品经理")
    assert titles.index("元宝-Agent架构工程师") < titles.index("AI Agent Sales Intern")
    assert titles.index("元宝-Agent架构工程师") < titles.index("游戏研发 Agent 产品策划")


def test_relevance_score_exposes_ranking_reasons():
    intern = _posting("Agent Development Intern", "Internship", "Build Agent workflows with RAG.")
    product = _posting("QQ-Agent产品经理", "产品", "负责 Agent 产品规划。")

    intern_score = score_job_posting(intern, "Agent 开发实习生")
    product_score = score_job_posting(product, "Agent 开发实习生")

    assert intern_score.score > product_score.score
    assert "命中实习/校招" in intern_score.reasons
    assert "标题偏产品/运营" in product_score.reasons


def test_internship_signal_uses_word_boundary_for_internal_tools():
    internal_tools = _posting("Internal Tools Engineer", "Engineering", "Build internal tools for Agent teams.")
    chinese_campus = _posting("大模型智能体开发校招", "校招", "参与智能体应用开发。")
    full_campus_term = _posting("AI Agent 研发工程师", "2027年度秋季校园招聘", "参与智能体应用开发。")

    assert is_internship_like_posting(internal_tools) is False
    assert is_internship_like_posting(chinese_campus) is True
    assert is_internship_like_posting(full_campus_term) is True
