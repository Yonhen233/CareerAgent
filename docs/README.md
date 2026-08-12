# CareerAgent 文档导航

## 先读这些

1. [完整系统设计、评测与 Bad Case 治理](CAREER_AGENT_SYSTEM_DESIGN_AND_EVALUATION.md)：当前实现的权威总览，覆盖用户流程、架构、各模块、指标、问题处理和成熟度边界。
2. [项目目录说明](PROJECT_STRUCTURE.md)：真实文件树、模块职责、依赖方向和新文件放置规则。
3. [架构设计](ARCHITECTURE.md)：运行时组件、LangGraph、RAG、队列与并发设计。
4. [Agent 设计说明](AGENT_DESIGN.md)：Skill、Tool Policy、Plan-Execute、ReAct、上下文和 Guardrail。
5. [黄金演示](GOLDEN_DEMOS.md)：岗位匹配、证据约束定制和审批式投递。

## 接口与开发

- [API 说明](API.md)
- [开发说明](DEVELOPMENT.md)
- [开发日志](DEVELOPMENT_LOG.md)

## RAG 与评测

- [PDF Chunk 方案](PDF_CHUNKING.md)
- [量化评测方案](EVALUATION.md)
- [国内真实岗位源](REAL_JOB_SOURCES.md)

## 生产与安全

- [Redis + SQLite 架构](CAREER_AGENT_REDIS_SQLITE_ARCHITECTURE.md)
- [Production Hardening Notes](CAREER_AGENT_HARDENING_NOTES.md)
- [面试 Hardening Q&A](CAREER_AGENT_INTERVIEW_HARDENING_QA.md)

## 面试资料

- [面试资料总导航](interview/README.md)
- [项目总览与架构](interview/PROJECT_OVERVIEW_AND_ARCHITECTURE.md)
- [核心链路实现详解](interview/IMPLEMENTATION_DEEP_DIVE.md)
- [生产工程与安全设计](interview/PRODUCTION_ENGINEERING.md)
- [评测体系与当前指标](interview/EVALUATION_AND_METRICS.md)
- [Bad Case 与设计决策](interview/BAD_CASES_AND_DECISIONS.md)
- [高频面试问答](interview/INTERVIEW_QA.md)
- [讲述稿与简历写法](interview/PRESENTATION_SCRIPTS.md)

文档状态以 README、开发日志和当前代码为准。归档材料只保留历史分析过程，不代表当前实现边界。
