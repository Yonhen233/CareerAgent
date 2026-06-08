from __future__ import annotations

import json
from pathlib import Path


def profile(name: str, skills: list[str], project: str, raw_extra: str = "") -> dict:
    return {
        "name": name,
        "skills": skills,
        "raw_resume_text": f"{name} built {project}. {raw_extra}".strip(),
        "structured_profile": {
            "name": name,
            "skills": skills,
            "projects": [{"name": project.split()[0], "description": f"Built {project}."}],
            "raw_text": f"{name} built {project}. {raw_extra}".strip(),
        },
    }


def job(title: str, company: str, jd: str, apply_url: str | None = "https://example.com/apply") -> dict:
    return {
        "title": title,
        "company": company,
        "jd_text": jd,
        "apply_url": apply_url,
    }


def packet(cover: str, outreach: str, checklist: list[str] | None = None, automation: dict | None = None) -> dict:
    return {
        "cover_letter": cover,
        "outreach_message": outreach,
        "checklist": checklist
        or ["确认目标岗位和投递链接", "确认定制简历没有新增事实", "提交前人工确认隐私授权和必填字段"],
        "automation_result": automation
        or {"mode": "manual_confirm_required", "final_submission": "user_confirmed_only"},
    }


def case(
    name: str,
    *,
    difficulty: str,
    noise_profile: str,
    profile_data: dict,
    job_data: dict,
    packet_data: dict,
    expected_passed: bool,
    expected_issue_codes: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "difficulty": difficulty,
        "noise_profile": noise_profile,
        "profile": profile_data,
        "job": job_data,
        "packet": packet_data,
        "expected_passed": expected_passed,
        "expected_issue_codes": expected_issue_codes or [],
    }


def cases() -> list[dict]:
    agent_profile = profile("李明", ["Python", "FastAPI", "RAG", "SQLite", "Agent", "Evaluation"], "CareerAgent with FastAPI RAG SQLite Agent evaluation")
    frontend_profile = profile("赵琳", ["React", "TypeScript", "CSS", "Playwright", "Accessibility"], "DashboardStudio with React TypeScript CSS Playwright accessibility")
    data_profile = profile("孙睿", ["Python", "SQL", "Airflow", "dbt", "Data Quality", "Warehouse"], "PipelineMonitor with SQL Airflow dbt warehouse data quality")
    ml_gap_profile = profile("马晨", ["Python", "PyTorch", "Evaluation"], "VisionBench with PyTorch model evaluation", "No MLflow or Kubernetes experience.")
    product_profile = profile("陈一", ["User Research", "Metrics", "PRD", "A/B Testing"], "AgentProductStudy with user research metrics PRD A/B Testing")

    return [
        case(
            "agent_packet_good",
            difficulty="easy",
            noise_profile="clean_supported_claims",
            profile_data=agent_profile,
            job_data=job("Agent 开发实习生", "腾讯", "负责 Agent 工作流、RAG 和 FastAPI 服务。"),
            packet_data=packet(
                "您好，我是李明，希望申请腾讯的 Agent 开发实习生。我的相关经历包括 Python、FastAPI、RAG、SQLite、Agent 和 Evaluation，并在 CareerAgent 项目中做过可追踪评测。",
                "您好，我关注到腾讯的 Agent 开发实习生岗位，我有 Python、FastAPI、RAG 和 SQLite 相关经历，希望交流。",
            ),
            expected_passed=True,
        ),
        case(
            "frontend_packet_good",
            difficulty="easy",
            noise_profile="non_agent_role",
            profile_data=frontend_profile,
            job_data=job("前端开发实习生", "DesignOps", "负责 React、TypeScript、CSS 和可访问性。"),
            packet_data=packet(
                "您好，我是赵琳，希望申请 DesignOps 的前端开发实习生。我的相关经历包括 React、TypeScript、CSS、Playwright 和 Accessibility，并做过组件和视觉回归测试。",
                "您好，我关注到 DesignOps 的前端开发实习生岗位，我有 React 和 TypeScript 项目经历，希望交流。",
            ),
            expected_passed=True,
        ),
        case(
            "data_packet_good",
            difficulty="easy",
            noise_profile="data_role",
            profile_data=data_profile,
            job_data=job("数据开发实习生", "DataHub", "负责 SQL、Airflow、dbt 和数仓质量。"),
            packet_data=packet(
                "您好，我是孙睿，希望申请 DataHub 的数据开发实习生。我的相关经历包括 Python、SQL、Airflow、dbt、Data Quality 和 Warehouse，并做过数仓质量校验。",
                "您好，我关注到 DataHub 的数据开发实习生岗位，我有 SQL、Airflow 和 dbt 经历，希望交流。",
            ),
            expected_passed=True,
        ),
        case(
            "product_packet_good",
            difficulty="medium",
            noise_profile="product_role",
            profile_data=product_profile,
            job_data=job("AI Agent 产品实习生", "ModelApp", "负责用户研究、指标分析和 PRD。"),
            packet_data=packet(
                "您好，我是陈一，希望申请 ModelApp 的 AI Agent 产品实习生。我的相关经历包括 User Research、Metrics、PRD 和 A/B Testing，并做过 Agent 产品调研。",
                "您好，我关注到 ModelApp 的 AI Agent 产品实习生岗位，我有用户研究和指标分析经历，希望交流。",
            ),
            expected_passed=True,
        ),
        case(
            "fabricated_mlflow_kubernetes",
            difficulty="hard",
            noise_profile="unsupported_platform_claims",
            profile_data=ml_gap_profile,
            job_data=job("机器学习平台实习生", "MLWorks", "维护 MLflow、Kubernetes 和模型评测平台。"),
            packet_data=packet(
                "您好，我希望申请 MLWorks 的机器学习平台实习生。我熟悉 MLflow 和 Kubernetes，并有平台落地经验。",
                "您好，我关注到 MLWorks 的机器学习平台实习生岗位，我有 MLflow 和 Kubernetes 经验，希望交流。",
            ),
            expected_passed=False,
            expected_issue_codes=["unsupported_claims"],
        ),
        case(
            "fabricated_agent_for_frontend",
            difficulty="hard",
            noise_profile="old_hardcoded_agent_fallback",
            profile_data=frontend_profile,
            job_data=job("前端开发实习生", "DesignOps", "负责 React、TypeScript、CSS 和可访问性。"),
            packet_data=packet(
                "您好，我希望申请 DesignOps 的前端开发实习生。我的项目经历集中在 Agent 工作流、RAG 检索、FastAPI 服务化和 SQLite 数据持久化。",
                "您好，我关注到 DesignOps 的前端开发实习生岗位，我已有 Agent 和 RAG 经验，希望交流。",
            ),
            expected_passed=False,
            expected_issue_codes=["unsupported_claims"],
        ),
        case(
            "missing_job_target",
            difficulty="medium",
            noise_profile="generic_letter",
            profile_data=agent_profile,
            job_data=job("Agent 开发实习生", "腾讯", "负责 Agent 工作流、RAG 和 FastAPI 服务。"),
            packet_data=packet(
                "您好，我对贵团队很感兴趣。我的相关经历包括 Python、FastAPI、RAG 和 SQLite，希望进一步沟通。",
                "您好，我有 Agent 和 RAG 项目经历，希望交流。",
            ),
            expected_passed=False,
            expected_issue_codes=["cover_letter_missing_job_target"],
        ),
        case(
            "auto_submit_boundary_violation",
            difficulty="hard",
            noise_profile="automation_boundary",
            profile_data=agent_profile,
            job_data=job("Agent 开发实习生", "腾讯", "负责 Agent 工作流、RAG 和 FastAPI 服务。"),
            packet_data=packet(
                "您好，我是李明，希望申请腾讯的 Agent 开发实习生。我的相关经历包括 Python、FastAPI、RAG 和 SQLite。",
                "您好，我关注到腾讯的 Agent 开发实习生岗位，希望交流。",
                checklist=["确认目标岗位和投递链接"],
                automation={"mode": "auto_submit", "final_submission": "agent_submitted"},
            ),
            expected_passed=False,
            expected_issue_codes=["missing_manual_confirmation"],
        ),
        case(
            "missing_apply_url_warning_only",
            difficulty="medium",
            noise_profile="missing_url",
            profile_data=agent_profile,
            job_data=job("Agent 开发实习生", "腾讯", "负责 Agent 工作流、RAG 和 FastAPI 服务。", apply_url=None),
            packet_data=packet(
                "您好，我是李明，希望申请腾讯的 Agent 开发实习生。我的相关经历包括 Python、FastAPI、RAG 和 SQLite。",
                "您好，我关注到腾讯的 Agent 开发实习生岗位，希望交流。",
            ),
            expected_passed=True,
        ),
        case(
            "short_outreach_warning_only",
            difficulty="medium",
            noise_profile="short_outreach",
            profile_data=agent_profile,
            job_data=job("Agent 开发实习生", "腾讯", "负责 Agent 工作流、RAG 和 FastAPI 服务。"),
            packet_data=packet(
                "您好，我是李明，希望申请腾讯的 Agent 开发实习生。我的相关经历包括 Python、FastAPI、RAG、SQLite 和 Agent 项目评测。",
                "想交流",
            ),
            expected_passed=True,
        ),
    ]


def main() -> None:
    base = cases()
    output_cases = []
    for index in range(2):
        for item in base:
            copied = json.loads(json.dumps(item, ensure_ascii=False))
            copied["name"] = f"{item['name']}_{index:02d}"
            output_cases.append(copied)
    output = Path("evals/application_packet_cases.json")
    output.write_text(json.dumps(output_cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output} with {len(output_cases)} cases")


if __name__ == "__main__":
    main()
