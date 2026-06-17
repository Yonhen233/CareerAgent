from app.models.schemas import EducationItem, ExperienceItem, GuidedProfileRequest, ProjectItem
from app.services.resume_parser import ResumeParserService


def test_guided_profile_keeps_mainstream_chinese_resume_sections(db_session):
    payload = GuidedProfileRequest(
        name="王一",
        email="wangyi@example.com",
        phone="13900000000",
        photo_data_url="data:image/png;base64,iVBORw0KGgo=",
        location="深圳",
        availability="2026 年暑期可实习",
        headline="Agent 开发实习生候选人",
        self_summary="熟悉 FastAPI、RAG 和 Agent workflow，有真实项目交付经验。",
        enabled_sections=["intent", "summary", "photo", "education", "projects", "skills"],
        target_roles=["Agent 开发实习生"],
        education=[
            EducationItem(
                school="华南理工大学",
                degree="本科",
                major="软件工程",
                duration="2023.09 - 2027.06",
                details="GPA 3.7/4.0，核心课程：数据库、机器学习。",
            ),
            EducationItem(
                school="清华大学",
                degree="交换生",
                major="人工智能",
                duration="2025.02 - 2025.06",
                details="参与智能体系统课程项目。",
            )
        ],
        skills=["Python", "FastAPI", "RAG", "SQLite"],
        projects=[
            ProjectItem(
                name="CareerAgent",
                description="构建求职助手 Agent，支持简历解析、岗位匹配和定制简历。",
                tech_stack=["Python", "FastAPI", "RAG"],
                impact="跑通真实 LLM 全流程并记录 trace。",
            ),
            ProjectItem(
                name="EvalBoard",
                description="构建 LLM 评测看板。",
                tech_stack=["FastAPI", "SQLite"],
                impact="支持 case trace 查看。",
            )
        ],
        work_experience=[
            ExperienceItem(
                company="AI Lab",
                role="后端开发实习生",
                duration="2025.07 - 2025.10",
                details="维护 FastAPI 服务和 SQLite 数据链路。",
                tech_stack=["FastAPI", "SQLite"],
            ),
            ExperienceItem(
                company="开源社区",
                role="贡献者",
                duration="2025.11 - 2026.01",
                details="修复 Agent trace 展示问题。",
                tech_stack=["Python"],
            )
        ],
        campus_experience=[
            ExperienceItem(
                company="AI 社团",
                role="技术组成员",
                duration="2024.09 - 2025.01",
                details="组织 Agent 技术分享。",
            ),
            ExperienceItem(
                company="创新创业协会",
                role="项目负责人",
                duration="2024.03 - 2024.06",
                details="组织校内产品原型评审。",
            )
        ],
        certifications=["英语六级"],
        awards=["校级二等奖学金"],
        languages=["中文", "英语 CET-6"],
        portfolio_links=["https://github.com/example/CareerAgent"],
    )

    profile = ResumeParserService().create_profile_from_guided_answers(db_session, payload)
    structured = profile.structured_profile_json

    assert structured["location"] == "深圳"
    assert structured["photo_data_url"].startswith("data:image/png;base64")
    assert "photo" in structured["enabled_sections"]
    assert structured["availability"] == "2026 年暑期可实习"
    assert structured["self_summary"].startswith("熟悉 FastAPI")
    assert len(structured["education"]) == 2
    assert len(structured["projects"]) == 2
    assert len(structured["work_experience"]) == 2
    assert len(structured["campus_experience"]) == 2
    assert structured["education"][0]["school"] == "华南理工大学"
    assert structured["work_experience"][0]["company"] == "AI Lab"
    assert structured["campus_experience"][0]["company"] == "AI 社团"
    assert structured["certifications"] == ["英语六级"]
    assert structured["portfolio_links"] == ["https://github.com/example/CareerAgent"]
    assert "Campus experience" in profile.raw_resume_text
    assert "Certifications" in profile.raw_resume_text
    assert "data:image" not in profile.raw_resume_text
