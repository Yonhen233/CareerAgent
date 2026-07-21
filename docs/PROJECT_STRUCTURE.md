# CareerAgent 项目目录说明

本文档描述当前仓库的真实文件结构、模块职责和依赖边界。目录树只列出源码、配置、评测和文档资产；数据库、日志、模型缓存等运行时文件不会提交到 Git。

## 当前架构

```text
CareerAgent/
├── app/                                      # FastAPI 产品代码
│   ├── main.py                               # 应用组合根：生命周期、中间件、静态资源和 Router 注册
│   │
│   ├── agents/                               # Agent 编排层：决定任务如何规划和执行
│   │   ├── langgraph_orchestrator.py         # LangGraph 主图、节点、条件路由、interrupt/resume
│   │   ├── natural_language.py               # 自然语言需求解析图和一次 plan repair
│   │   ├── orchestrator.py                   # 兼容入口，实际继承 LangGraphOrchestrator
│   │   ├── skills.py                         # 扫描/校验 SKILL.md，按任务渐进式披露
│   │   ├── tools.py                          # Tool Policy、Planner、权限/风险/审批/幂等策略
│   │   └── subagents.py                      # 职责边界定义，不代表独立进程或任意自治模型
│   │
│   ├── api/                                  # HTTP API 层：校验请求、鉴权、调用 Agent/领域服务
│   │   ├── agent_runs.py                     # Run 创建、后台入队、事件、摘要、取消和恢复
│   │   ├── agent_skills.py                   # Skill 目录/详情与 SubAgent 元数据
│   │   ├── agent_tools.py                    # Tool Policy 查询
│   │   ├── assistant.py                      # 自然语言 Agent 入口
│   │   ├── profiles.py                       # 简历上传、结构化建档、查询和预览
│   │   ├── jobs.py                           # 岗位搜索、JD 入库和预览
│   │   ├── job_discovery.py                  # 可选简历的岗位发现会话、结果恢复
│   │   ├── matches.py                        # 岗位匹配
│   │   ├── resumes.py                        # 简历评分、定制和交付
│   │   ├── applications.py                   # 投递材料、审批和高风险动作
│   │   ├── interview_prep.py                 # 面经导入、面试包和练习状态
│   │   ├── evaluations.py                    # PDF/RAG/Agent/Injection 等评测入口
│   │   ├── tasks.py                          # 后台任务与队列状态
│   │   ├── auth.py                           # Session 登录
│   │   ├── ops.py                            # 运维、队列、DLQ、stale run 与审计
│   │   ├── llm_debug.py                      # LLM 调用日志查询
│   │   └── health.py                         # liveness/readiness
│   │
│   ├── services/                             # 领域服务层：真实业务规则与外部能力适配
│   │   ├── resume_parser.py                  # PDF 文本提取与结构化简历解析
│   │   ├── text_splitter.py                  # PDF/Profile/JD 的结构感知 Chunk
│   │   ├── resume_review.py                  # 简历评分与针对性修改建议
│   │   ├── resume_tailor.py                  # RAG 证据约束简历定制与 ReAct repair
│   │   ├── resume_delivery.py                # HTML/Markdown 简历交付
│   │   │
│   │   ├── job_sources.py                    # 腾讯/百度/美团/字节/阿里岗位源适配器
│   │   ├── job_search.py                     # 真实招聘源并发搜索与入库
│   │   ├── job_discovery.py                  # 可选简历的跨岗位检索、匹配和会话持久化
│   │   ├── job_relevance.py                  # 中文岗位相关性排序
│   │   ├── jd_parser.py                      # JD 结构化解析
│   │   ├── matcher.py                        # 岗位匹配、缺口和证据汇总
│   │   │
│   │   ├── embedding_service.py              # Embedding provider
│   │   ├── vector_index.py                   # SQLite 权威向量索引与可选 Chroma 镜像
│   │   ├── reranker.py                       # Top20 二阶段重排
│   │   ├── evidence_classifier.py            # 交付/指标/课程/计划/缺口证据分类
│   │   ├── context_compressor.py             # Profile/JD/证据/Prompt Packet 分级预算
│   │   │
│   │   ├── guardrails.py                     # 定制简历事实与关键词 Guardrail
│   │   ├── application_guardrails.py         # 投递材料事实和人工确认边界
│   │   ├── prompt_injection_guard.py         # JD/PDF/RAG/面经注入检测
│   │   ├── approval_service.py               # 高风险动作审批表
│   │   ├── high_risk_action_tools.py         # 审批后执行浏览器/邮件工具的统一网关
│   │   ├── outbound_tools.py                 # Playwright、EML 和 SMTP 实际适配
│   │   ├── application_service.py            # 求职信、外联文案和投递清单
│   │   │
│   │   ├── interview_experience.py           # 用户导入面经的结构化处理
│   │   ├── interview_sources.py              # 牛客/OfferShow/小红书来源 smoke
│   │   ├── interview_prep.py                 # JD + 项目 + 缺口的面试问题生成
│   │   ├── interview_delivery.py             # 面试包 Markdown 和练习状态
│   │   ├── interview_answer_framework.py     # 面试题参考答案、回答思路、证据绑定和旧数据升级
│   │   │
│   │   ├── trace_service.py                  # Run/Step/Artifact/Event Trace
│   │   ├── run_business_summary.py           # 路由/过程/结果/副作用四层业务摘要
│   │   ├── task_queue.py                     # Redis 优先级队列、DLQ、HA 和 recovery
│   │   ├── task_runner.py                    # 队列任务执行入口
│   │   ├── stale_runs.py                     # heartbeat 与 stale run 管理
│   │   ├── ops_audit.py                      # 运维与高风险动作审计
│   │   ├── session_auth.py                   # Session 用户、租户和角色
│   │   └── evaluation_service.py             # 评测执行和指标聚合
│   │
│   ├── core/                                 # 基础设施配置，不承载求职业务规则
│   │   ├── config.py                         # .env 与 Settings
│   │   ├── database.py                       # SQLAlchemy Engine/Session/初始化
│   │   ├── llm.py                            # OpenAI-compatible LLM 客户端与调用日志
│   │   ├── redis_client.py                   # Redis/Sentinel 连接
│   │   ├── security.py                       # Admin token、RBAC 请求检查
│   │   └── telemetry.py                      # HTTP 指标
│   │
│   ├── models/
│   │   ├── entities.py                       # SQLite 业务表、审批、Trace、评测实体
│   │   └── schemas.py                        # FastAPI/Pydantic 请求响应契约
│   │
│   ├── frontend/
│   │   └── routes.py                         # 用户页面和控制台页面路由
│   ├── templates/                            # Jinja 页面
│   └── static/                               # 全站 CSS 与浏览器端交互
│
├── skills/                                   # 可版本化 Agent Skill 内容层
│   ├── resume_intake_and_structuring/SKILL.md
│   ├── jd_structuring/SKILL.md
│   ├── evidence_retrieval/SKILL.md
│   ├── fit_assessment/SKILL.md
│   ├── resume_tailoring/SKILL.md
│   ├── application_packet/SKILL.md
│   └── interview_preparation/SKILL.md
│
├── evals/                                    # 机器可读评测集和发布阈值
│   ├── golden_demo_scenarios.json            # 三条黄金业务路径
│   ├── pdf_chunk_cases.json                   # PDF Chunk 噪声与边界样本
│   ├── rag_cases.json                         # RAG 召回/排序样本
│   ├── jd_parser_cases.json                   # 中英 JD 结构化样本
│   ├── job_relevance_cases.json               # 中文岗位排序标注
│   ├── agent_full_flow_cases.json             # Agent 全流程组件覆盖
│   ├── llm_workflow_cases.json                # 真实 LLM 分阶段流程
│   ├── application_packet_cases.json          # 投递包事实边界
│   ├── interview_prep_cases.json              # 面试包三视角覆盖
│   ├── prompt_injection_cases.json            # 多来源 adversarial/benign 样本
│   └── prompt_injection_release_policy.json   # 总体和分桶 release gate
│
├── scripts/                                  # 运维、数据生成和真实 smoke 命令
│   ├── run_agent_worker.py                    # 单 Redis worker
│   ├── run_agent_worker_supervisor.py         # 多 worker、探针与优雅 drain
│   ├── run_user_flow_smoke.py                 # PDF 到面试包的真实用户链路
│   ├── run_llm_workflow_eval.py               # 可恢复的真实 LLM 评测
│   ├── generate_demo_resumes.py               # 演示 PDF
│   ├── generate_eval_datasets.py              # PDF/RAG 等评测数据构建
│   ├── generate_job_relevance_eval.py         # 中文岗位相关性数据构建
│   └── generate_application_packet_eval.py    # 投递包 Guardrail 数据构建
│
├── tests/                                    # API、Agent、RAG、安全、队列和前端回归
│   ├── conftest.py                           # 测试环境、临时 SQLite 和显式离线 provider
│   ├── test_agent_workflow.py                # LangGraph 任务与业务摘要
│   ├── test_agent_hardening.py               # interrupt、审批、幂等与恢复
│   ├── test_agent_runs_api.py                # Run API、队列失败和事件
│   ├── test_natural_language_agent.py        # 自然语言计划与 repair
│   ├── test_golden_demo_scenarios.py         # 三条黄金路径契约
│   ├── test_task_queue.py                    # Redis、DLQ、优先级与 recovery
│   ├── test_context_compressor.py            # 上下文预算
│   ├── test_embedding_reranker.py            # Embedding 与二阶段排序
│   ├── test_vector_index.py                  # SQLite/Chroma 向量索引
│   ├── test_matcher.py                       # 匹配分、证据和负向语境
│   ├── test_job_chunks.py                    # JD Chunk
│   ├── test_job_relevance.py                 # 中文岗位排序
│   ├── test_resume_parser.py                 # PDF 简历解析
│   ├── test_resume_review.py                 # 简历评分与建议
│   ├── test_resume_html_preview.py           # 可投递 HTML 正文边界
│   ├── test_application_guardrails.py        # 投递包事实边界
│   ├── test_guardrails.py                    # 简历 Guardrail
│   ├── test_evidence_classifier.py           # 证据类型分类
│   ├── test_interview_prep.py                # 面试包三视角
│   ├── test_evaluation_service.py            # 评测指标与 release gate
│   ├── test_frontend_pages.py                # 用户页和控制台 DOM 契约
│   ├── test_health.py                        # 健康检查和能力注册 API
│   ├── test_llm_debug.py                     # LLM 调用日志
│   ├── test_llm_error_formatting.py          # LLM 错误可追溯格式
│   ├── test_schema_normalization.py          # 真实 LLM null/类型归一
│   ├── test_guided_profile_schema.py         # 中文简历多段结构
│   ├── test_chinese_first_defaults.py        # 中文岗位主链路默认值
│   └── test_project_structure_docs.py        # 目录说明与归档位置防漂移
│
├── demo_resumes/                             # 可直接上传的四份演示 PDF（提交到 Git）
│   ├── agent_intern_strong_resume.pdf
│   ├── agent_intern_noisy_resume.pdf
│   ├── ml_rag_partial_resume.pdf
│   └── backend_platform_resume.pdf
│
├── docs/                                     # 架构、接口、评测、开发和面试资料
│   ├── README.md                             # 文档分组索引
│   ├── PROJECT_STRUCTURE.md                  # 本文件：真实目录树和放置规则
│   ├── ARCHITECTURE.md                       # 运行时架构
│   ├── AGENT_DESIGN.md                       # Agent/Skill/Tool/Context/Guardrail
│   ├── API.md                                # HTTP 接口
│   ├── DEVELOPMENT.md                        # 本地开发与运行
│   ├── DEVELOPMENT_LOG.md                    # 倒序带时间开发日志
│   ├── EVALUATION.md                         # 数据集、指标和真实结果
│   ├── PDF_CHUNKING.md                       # PDF Chunk 策略
│   ├── GOLDEN_DEMOS.md                       # 三条黄金演示
│   ├── CAREER_AGENT_REDIS_SQLITE_ARCHITECTURE.md
│   ├── CAREER_AGENT_HARDENING_NOTES.md
│   ├── CAREER_AGENT_INTERVIEW_HARDENING_QA.md
│   └── interview/
│       ├── README.md                         # 当前资料入口和归档警告
│       └── archive-2026-07-06/               # 带日期的旧面试材料快照
│
├── data/                                     # SQLite/Chroma/checkpoint/uploads（运行时生成，Git 忽略）
├── logs/                                     # LLM/worker/评测日志（运行时生成，Git 忽略）
├── docker-compose.smtp.yml                   # Mailpit 本地 SMTP smoke
├── .env.example                              # 无密钥配置模板
├── pyproject.toml                            # pytest/ruff 配置
├── requirements.txt                          # Python 依赖
└── README.md                                 # 产品主线、运行方式和文档入口
```

## 分层职责

| 层 | 可以做什么 | 不应该做什么 |
| --- | --- | --- |
| `api` | HTTP 校验、鉴权、Session 注入、调用应用服务 | 写复杂匹配/RAG/审批规则 |
| `agents` | 任务规划、LangGraph 路由、状态、interrupt/resume | 直接操作浏览器或拼 SQL |
| `skills` | 描述能力契约、允许工具、上下文和失败策略 | 保存运行时状态或执行代码 |
| `services` | 实现求职领域规则、RAG、Guardrail 和外部工具适配 | 依赖 FastAPI Request/Response |
| `models` | 持久化实体和 API schema | 编排工作流 |
| `core` | 配置、数据库、LLM、Redis、安全和遥测基础设施 | 承载岗位匹配等业务决策 |
| `evals/tests` | 量化质量与回归行为 | 被生产代码反向依赖 |

依赖方向保持为：

```text
Frontend/API
    -> Agents
        -> Services
            -> Models/Core

Skills -> Planner/Tool Policy metadata
Evals/Tests -> API/Agents/Services
```

## 常见需求去哪里改

| 需求 | 首要入口 | 通常同时修改 |
| --- | --- | --- |
| 增加 Agent 任务类型 | `app/agents/langgraph_orchestrator.py` | `tools.py`、`skills/`、API、测试 |
| 增加或修改 Skill | `skills/<name>/SKILL.md` | `app/agents/skills.py` 的任务映射 |
| 增加 Tool | `app/agents/tools.py` | 对应 service、Skill `allowed_tools`、审批和测试 |
| 修改 PDF Chunk | `app/services/text_splitter.py` | `evals/pdf_chunk_cases.json`、`docs/PDF_CHUNKING.md` |
| 修改 RAG | `vector_index.py`、`reranker.py` | RAG 评测、embedding 配置和 Trace |
| 修改简历定制 | `resume_tailor.py` | Guardrail、Skill、LLM workflow 评测 |
| 增加高风险外发动作 | `high_risk_action_tools.py` | Tool Policy、approval、audit、RBAC、smoke |
| 修改后台执行 | `task_queue.py`、worker scripts | heartbeat、recovery、DLQ、运维页面 |
| 增加用户页面 | `templates/`、`static/` | `frontend/routes.py`、对应 API、前端测试 |
| 增加评测 | `evals/*.json` | `evaluation_service.py`、API/UI、release gate |

## 新文件放置规则

1. 业务规则优先放进对应 `service`，API 只做协议层工作。
2. LangGraph 节点负责组织服务，不复制 service 内的领域规则。
3. 新 Skill 必须有独立 `SKILL.md`，并明确 `allowed_tools` 与失败策略。
4. 新 Tool 必须声明风险、审批、幂等、超时、重试和审计事件。
5. 新功能必须同时考虑 API、用户 UI、Trace、评测和测试，不只增加一个演示脚本。
6. `data/`、`logs/`、模型、checkpoint 和临时测试输出属于运行时资产，不提交 Git。
7. 历史面试材料放在 `docs/interview/archive-*`，不能作为当前实现状态的权威来源。
