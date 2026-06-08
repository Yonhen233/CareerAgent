from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JobRelevance:
    score: float
    reasons: list[str]


AGENT_CN_SIGNALS = ("智能体", "大模型", "大语言模型", "生成式", "检索增强", "工具调用")
AGENT_EN_PATTERNS = (
    r"(?<![a-z0-9])ai[-\s]?agents?(?![a-z0-9])",
    r"(?<![a-z0-9])agents?(?![a-z0-9])",
    r"(?<![a-z0-9])agentic(?![a-z0-9])",
    r"(?<![a-z0-9])llms?(?![a-z0-9])",
    r"(?<![a-z0-9])rag(?![a-z0-9])",
    r"(?<![a-z0-9])ai(?![a-z0-9])",
    r"large language models?",
)
DEVELOPMENT_CN_SIGNALS = ("开发", "研发", "工程师", "架构", "算法", "后端", "平台", "技术", "应用")
DEVELOPMENT_EN_PATTERNS = (
    r"(?<![a-z0-9])development(?![a-z0-9])",
    r"(?<![a-z0-9])developers?(?![a-z0-9])",
    r"(?<![a-z0-9])engineers?(?![a-z0-9])",
    r"(?<![a-z0-9])engineering(?![a-z0-9])",
    r"(?<![a-z0-9])research(?![a-z0-9])",
    r"(?<![a-z0-9])application(?![a-z0-9])",
    r"(?<![a-z0-9])backend(?![a-z0-9])",
    r"(?<![a-z0-9])platform(?![a-z0-9])",
)
PRODUCT_CN_SIGNALS = ("产品经理", "产品策划", "产品", "策划", "运营", "增长", "商业化", "销售", "商务", "市场")
BUSINESS_EN_PATTERNS = (
    r"(?<![a-z0-9])sales(?![a-z0-9])",
    r"account executive",
    r"business development",
    r"customer success",
    r"(?<![a-z0-9])marketing(?![a-z0-9])",
)
INTERNSHIP_PATTERN = re.compile(r"(?<![a-z0-9])intern(ship)?s?(?![a-z0-9])")


def rank_postings_for_query(postings: list[Any], query: str) -> list[Any]:
    scored = [(index, posting, score_job_posting(posting, query)) for index, posting in enumerate(postings)]
    scored.sort(key=lambda item: (-item[2].score, item[0]))
    return [posting for _, posting, _ in scored]


def score_job_posting(posting: Any, query: str) -> JobRelevance:
    title = _posting_title(posting).lower()
    job_type = _posting_job_type(posting).lower()
    jd_preview = _posting_jd(posting)[:1600].lower()
    haystack = source_posting_haystack(posting)
    query_text = (query or "").lower()
    score = 0.0
    reasons: list[str] = []

    wants_agent = _has_agent_signal(query_text)
    wants_internship = _contains_internship_signal(query_text)
    wants_development = _has_development_signal(query_text) or "开发实习" in query_text
    wants_product = _has_product_signal(query_text)

    for term in _query_terms(query):
        if _term_present(title, term):
            score += 1.8
        elif _term_present(haystack, term):
            score += 0.5

    if wants_agent:
        if _has_agent_signal(title):
            score += 7.0
            reasons.append("标题命中 Agent/LLM/RAG")
        elif _has_agent_signal(haystack):
            score += 3.0
            reasons.append("JD 命中 Agent/LLM/RAG")

    if wants_internship:
        if is_internship_like_posting(posting):
            score += 8.0
            reasons.append("命中实习/校招")
        else:
            score -= 4.0
            reasons.append("缺少实习/校招信号")

    if wants_development:
        if _has_development_signal(title) or _has_development_signal(job_type):
            score += 5.0
            reasons.append("标题命中开发/工程")
        elif _has_development_signal(jd_preview):
            score += 1.5
            reasons.append("JD 命中开发/工程")
        if _has_product_signal(title) or _has_product_signal(job_type):
            score -= 7.0
            reasons.append("标题偏产品/运营")
        if _has_business_signal(title) or _has_business_signal(job_type):
            score -= 8.0
            reasons.append("标题偏销售/商务")

    if not wants_product and (_has_product_signal(title) or _has_product_signal(job_type)):
        score -= 3.0
    if not wants_product and (_has_business_signal(title) or _has_business_signal(job_type)):
        score -= 4.0

    if _posting_jd(posting).strip():
        score += 0.4
        reasons.append("JD 非空")
    if _posting_apply_url(posting):
        score += 0.4
        reasons.append("有投递链接")

    return JobRelevance(score=round(score, 4), reasons=reasons[:6])


def is_internship_like_posting(posting: Any) -> bool:
    haystack = " ".join(
        [
            _posting_title(posting),
            _posting_job_type(posting),
            _posting_jd(posting)[:800],
        ]
    ).lower()
    return _contains_internship_signal(haystack)


def is_query_relevant_posting(posting: Any, query: str) -> bool:
    haystack = source_posting_haystack(posting)
    terms = _query_terms(query)
    if not terms and query.strip():
        terms = [query.strip().lower()]
    return any(_term_present(haystack, term) for term in terms)


def is_agent_related_posting(posting: Any) -> bool:
    return _has_agent_signal(source_posting_haystack(posting))


def source_posting_haystack(posting: Any) -> str:
    return " ".join(
        [
            _posting_title(posting),
            _posting_company(posting),
            _posting_location(posting),
            _posting_job_type(posting),
            _posting_jd(posting),
        ]
    ).lower()


def _query_terms(query: str) -> list[str]:
    terms = [
        token.strip().lower()
        for token in re.split(r"[\s,/|;:()（）\-]+", query or "")
        if len(token.strip()) >= 2
    ]
    text = (query or "").lower()
    extras: list[str] = []
    if "开发" in text:
        extras.extend(["开发", "研发", "development", "engineer", "engineering"])
    if "实习" in text or INTERNSHIP_PATTERN.search(text):
        extras.extend(["实习", "实习生", "校招", "intern", "internship"])
    if _has_agent_signal(text):
        extras.extend(["agent", "智能体", "llm", "rag", "大模型"])
    output: list[str] = []
    seen: set[str] = set()
    for term in [*terms, *extras]:
        if term and term not in seen:
            seen.add(term)
            output.append(term)
    return output


def _term_present(text: str, term: str) -> bool:
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9]+", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}s?(?![a-z0-9])", text) is not None
    return term in text


def _contains_internship_signal(text: str) -> bool:
    return "实习" in text or "校招" in text or INTERNSHIP_PATTERN.search(text) is not None


def _has_agent_signal(text: str) -> bool:
    return _contains_any(text, AGENT_CN_SIGNALS) or _matches_any(text, AGENT_EN_PATTERNS)


def _has_development_signal(text: str) -> bool:
    return _contains_any(text, DEVELOPMENT_CN_SIGNALS) or _matches_any(text, DEVELOPMENT_EN_PATTERNS)


def _has_product_signal(text: str) -> bool:
    return _contains_any(text, PRODUCT_CN_SIGNALS)


def _has_business_signal(text: str) -> bool:
    return _matches_any(text, BUSINESS_EN_PATTERNS)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) is not None for pattern in patterns)


def _posting_title(posting: Any) -> str:
    return str(getattr(posting, "title", "") or "")


def _posting_company(posting: Any) -> str:
    return str(getattr(posting, "company", "") or "")


def _posting_location(posting: Any) -> str:
    return str(getattr(posting, "location", "") or "")


def _posting_job_type(posting: Any) -> str:
    return str(getattr(posting, "job_type", "") or "")


def _posting_apply_url(posting: Any) -> str:
    return str(getattr(posting, "apply_url", "") or "")


def _posting_jd(posting: Any) -> str:
    return str(getattr(posting, "raw_jd_text", "") or "")
