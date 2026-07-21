# Agent 设计说明

## LLM 调用点

当前 LLM 不是一个“全能 Prompt”，而是被放在需要语义理解或自然语言生成的边界上：

- `ResumeParserService.parse_structured_resume`：从 PDF 原文抽取结构化 Profile。
- `JDParserService.parse_jd`：从真实 JD 抽取 required skills、responsibilities、qualifications。
- `NaturalLanguageAgentService._build_plan`：把用户自然语言需求解析为受控 intent 和 action plan，只允许落到已注册的求职工具链。
- `MatcherService`：主匹配逻辑仍是可解释规则 + RAG evidence，不把最终匹配分数完全交给 LLM。
- `ResumeTailorService._llm_tailor`：根据 JD 和检索证据生成定制简历。
- `ApplicationService`：生成求职信和外联文案。
- `InterviewPrepService`：当前使用结构化规则生成面试准备包，不强制调用 LLM；已支持引用用户导入的真实同岗面经。后续如果接入自动抓取或多篇面经聚合，可用 LLM 做去重、归因和风险标注。
- `EvaluationService._llm_judge_suitability`：真实调用 LLM 判断岗位是否适配，用于评测 prompt 边界。

所有 LLM 调用都通过 `LLMClient` 记录：

- trace name。
- model/base_url。
- prompt preview。
- response preview。
- latency。
- prompt/response 字符数。
- error message。

系统不会记录 API key。

## Skill 与 SubAgent

当前已经引入文件化 Skill 契约和 SubAgent 注册表：

- `skills/*/SKILL.md`：每个 Skill 使用 YAML front matter 声明版本、状态、owner、触发条件、输入、允许调用的 Tool、上下文、输出契约、禁止行为、成功标准和失败策略，正文保存执行指令。
- `GET /agent/skills`：只返回 Skill 目录 metadata，不返回完整执行指令。
- `GET /agent/skills/{skill_name}`：按需返回指定 Skill 的完整契约和正文指令。
- `GET /agent/subagents`：查看各 SubAgent 的职责、读写边界和上下文策略。

`AgentPlanner` 不再只把 Skill 名字写进计划。它会根据 `task_type` 选择 Skill，生成精简的 `skill_contracts`，并校验计划中每个 Tool 是否出现在至少一个当前 Skill 的 `allowed_tools` 中。权限校验失败时直接阻止计划执行，不把未授权工具调用交给 LLM 或 Orchestrator。

核心 SubAgent：

- `profile_analyst`：解析候选人 Profile。
- `job_analyst`：解析岗位 JD。
- `evidence_curator`：检索和整理证据。
- `fit_judge`：判断岗位适配度。
- `resume_writer`：生成定制简历并接受 Guardrail。
- `application_operator`：生成投递包。
- `interview_coach`：生成面试准备包、缺口 drill 和调研清单。

这里的“SubAgent”只表示任务责任边界，不把上下文压缩做成单独 subagent。上下文治理是 Orchestrator/LLM 调用前的 runtime policy，由 `ContextCompressor` 处理；这样更接近主流 Agent 工程实践，也避免为了形式感制造一个不会独立推理的 subagent。

## 渐进式披露与上下文预算

LLM 不再直接读取全量 Profile、全量 JD 和全部证据，而是由 `ContextCompressor` 生成任务包：

- `profile_summary`：结构化候选人事实、项目、经历、技能和简历信号句。
- `job_summary`：岗位硬要求、职责、资格条件、关键词和 JD 信号句。
- `evidence_snippets`：Top evidence、retrieval/rerank metadata 和预算内片段。
- `prompt_packet`：最终传给 LLM 的任务包，只做一次总预算检查。

每次压缩都会写入 `context_compression` 元数据：

- `raw_chars`
- `initial_packet_chars`
- `compressed_chars`
- `reduction_ratio`
- `retained_evidence_count`
- 每层 `budget_chars`、`dropped_chars`、`within_budget` 和 shrink event

渐进式披露规则是：默认只给 LLM 看结构化摘要和 Top evidence；只有 Guardrail repair 需要具体引用时，后续才暴露更细粒度原文。证据不足时直接报告缺口，不让模型编造。

这个能力不会作为独立 Skill 或 SubAgent 暴露，因为它不是用户意图层的能力，而是 LLM 调用链路的 runtime policy。Skill 的“渐进式披露”是另一层含义：目录查询只暴露 metadata，执行计划只带当前任务的精简契约，只有调试或执行器确实需要时才读取完整 `SKILL.md` 指令。两者共同减少无关上下文，但职责不同。

## LangGraph Plan-Execute

`AgentOrchestrator` 已经迁移为 LangGraph 编排器。旧的 `app.agents.orchestrator.AgentOrchestrator` 类名只作为兼容外壳，实际继承 `LangGraphAgentOrchestrator`，核心实现位于 `app/agents/langgraph_orchestrator.py`。

每次运行都会：

1. 创建 `agent_runs` 记录，并在 `input_json.orchestration_framework` 标记 `langgraph`。
2. 进入 LangGraph `StateGraph`。
3. 先执行 `plan_task` 节点，由 `AgentPlanner` 生成计划并写入 `agent_artifacts`。
4. 根据 `task_type` 走条件边，进入 `find_jobs_for_profile`、`tailor_resume_for_job`、`quick_apply`、`prepare_interview_for_job` 或 `full_career_flow` 对应子流程。
5. 每个业务节点继续写入 `agent_steps`，保留原有 step trace、artifact 和错误追踪。
6. LangGraph `astream_events` 产生的 graph/node/interrupt 事件会写入 `agent_events`，前端可通过 SSE 实时展示节点级进度。

Graph state 只保存 JSON 友好的 ID、状态和产物摘要，例如 `profile_id`、`job_id`、`resume_version_id`、`matches`、`selected_job`、`fit_gate` 和 `output`。SQLAlchemy Session 不进入 state，而是通过运行期 `run_id -> Session` 映射注入节点；当前 graph 已接入 LangGraph SQLite checkpointer，checkpoint 文件默认位于 `data/runtime/langgraph_checkpoints.sqlite`。

适合 Plan-Execute 的原因：

- 求职流程天然是多步骤：加载 Profile、搜索岗位、解析 JD、匹配、检索证据、改写简历、验证、生成投递包。
- 每一步都可以对应一个明确 Tool，便于 trace、重试和测试。
- 计划本身是可展示产物，适合作为简历项目亮点。

当前计划是确定性 planner。后续可以升级为 LLM planner，但执行仍应限制在注册工具内，避免模型自由调用不可控能力。

自然语言入口本身也是一个 LangGraph 图：`parse_user_request -> execute_user_plan -> repair_user_plan -> execute_repaired_user_plan -> finalize`。LLM planner 只负责意图解析和计划修复，不直接执行浏览器、文件或数据库写入。执行阶段仍由 `AgentOrchestrator`、`ResumeParserService`、`JDParserService` 等受控服务完成。首次执行失败会触发 1 轮 plan repair；repair 后仍失败则返回 `status=failed` 和 Run ID，不把失败包装成成功结果。

执行计划现在会包含：

- `skills`
- `skill_contracts`
- `skill_disclosure`
- `subagents`
- `tool_policies`
- `tool_permission_validation`
- `context_policy`
- `react_loops`
- `mcp_recommendation`
- `langgraph_decision`
- `orchestration_framework=langgraph`
- `graph_thread_id`

## ReAct

最适合引入 ReAct 的环节是简历定制：

1. Observe：读取 JD 缺口、Top20 RAG 证据、当前简历草稿。
2. Act：生成或修复定制简历。
3. Observe：运行 Guardrail，检查新增事实、关键词覆盖、risk level。
4. Act：如果风险高，回退到更保守、更有证据支持的表达。

当前 `resume_tailor` 已经实现 1 轮 ReAct repair loop：

- 初稿生成后先运行 `guardrail.verify_resume`。
- 如果 `risk_level=high` 或 `passed=false`，repair loop 会读取 Guardrail issues、当前简历草稿和压缩后的上下文。
- 真实 LLM 路径调用 `resume_tailor.repair_resume`，只允许删除或改写无证据、高风险、缺口披露类表达，不允许新增事实。
- 离线测试路径使用 `resume_tailor.heuristic_repair`，同样删除 `eager to learn`、缺失技能正文披露和高风险 claim。
- 修复后的简历会再次经过 Guardrail，修复元数据写入 `keyword_alignment.react_repair`，包含触发风险、问题类型、attempt 工具、修复前后风险和是否通过。

当前只做 1 轮 repair，是为了让行为可控、trace 易读，并避免把简历生成变成无限重写。开发默认不做静默 fallback：LLM、embedding 或 reranker 失败会直接报错，Agent step、LLM log、Guardrail issues 和 `react_repair` 元数据用于追溯。

## Evidence Type Classifier

RAG 证据不再只按向量分和关键词分排序，`MatcherService.retrieve_evidence` 会先通过 `EvidenceClassifier` 给每个 chunk 打上证据类型：

- `shipped_project`：真实交付项目或上线产物。
- `metric_evidence`：带评测指标、提升比例、吞吐、延迟、用户量等量化结果的证据。
- `coursework`：课程、作业、阅读材料或课程项目里的提及。
- `planned_learning`：计划学习、正在学习、准备补齐的表达。
- `missing_skill_disclosure`：明确写了 `No MLflow`、`did not build agent system`、`without production RAG` 等缺口披露。
- `adjacent_experience`、`generic_skill`、`unknown`：相邻经验、泛技能或无法判断的证据。

匹配器会提高 `metric_evidence` 和 `shipped_project` 的权重，降低 `coursework`、`planned_learning` 和 `missing_skill_disclosure` 的权重。LLM workflow 的 `match_and_retrieve.details.top_evidence` 也会记录 `evidence_type` 和 `polarity`，方便看到模型拿到的是交付证据、课程噪声还是缺口披露。

## 投递门禁

`quick_apply` 不再把所有岗位都推进到投递包生成。它会先运行 `fit_gate`：

- `overall_score >= 55` 才允许继续生成或复用定制简历。
- `overall_score < 55` 直接失败，错误信息包含缺失技能，Agent step trace 保留输入、耗时和异常。
- 目标岗位、headline 和求职意向不作为匹配证据；只有技能、项目、经历、教育等事实字段进入 support text。
- `did not build`、`No shipped project`、`No MLflow`、课程/阅读/计划学习等负面证据优先级高于关键词命中。

这个设计让开发期评测更接近真实求职场景：Agent 可以建议继续补项目或定制简历，但不能把证据不足的岗位静默投出去。

## 投递包 Guardrail

`quick_apply` 通过 `fit_gate` 后，还会对投递包执行 `ApplicationPacketGuardrail`：

- 求职信必须提到目标公司或岗位，避免生成泛化模板。
- 求职信和外联文案中如果出现“熟悉、掌握、负责、建设、落地、经验”等能力声明，声明的技能必须能在 Profile、项目、经历或定制简历中找到正向证据。
- `No MLflow`、`没有 Kubernetes 经验` 等缺口披露不会被当作支持证据。
- 投递包必须保留 `manual_confirm_required` 和 `user_confirmed_only`，系统只准备材料和链接，不自动提交最终申请。
- 缺少投递链接、外联文案过短等问题记录为 warning；编造事实或越过人工确认边界记录为 high-risk issue 并阻断投递包创建。

## Prompt Injection Guard

JD、PDF 简历、RAG chunk 和导入面经都被视为 untrusted content。`PromptInjectionGuard` 会检测覆盖系统指令、工具越权、数据外泄和 RAG 污染指令；进入 LLM context 前会过滤命中的恶意行，并把风险写入结构化 metadata。项目新增 `POST /evaluations/prompt-injection` 和 `evals/prompt_injection_cases.json`，用 adversarial/benign case 量化 detection recall、false positive rate、severity accuracy 和 source/category breakdown。

## 面试准备包

`prepare_interview_for_job` 补齐投递后的面试准备阶段。它读取结构化 JD、`match_result`、Top evidence 和缺口技能，生成：

- 同岗位面经与高频追问：优先引用已导入的牛客网、OfferShow、小红书等同岗面经文本；如果平台正文难以稳定获取，只在面试包附上参考标题、链接和搜索入口，不把抓正文作为核心依赖。
- 简历项目技术栈追问：从 Profile skills、项目 `tech_stack` 和 RAG 证据出发，追问架构位置、技术取舍、替代方案和指标。
- LLM 项目实现追问：真实 API 和 Agent 工作流会调用 LLM，基于 JD、项目、RAG 证据生成输入/处理/输出、日志指标、失败边界、本人贡献等连续追问。
- LLM 八股与基础追问：基于 JD 必备技能生成常见八股、底层原理、工程取舍和最小 demo 追问；缺口技能必须变成诚实披露和补齐计划。
- 缺口 drill：对 `missing_skills` 生成诚实披露话术和最小补齐任务。
- 通用面试与行为问题：覆盖动机、失败复盘、模糊需求拆解和协作推进。

每道题都有稳定 `question_id`、可追溯的 `source_perspective` 和用于产品验收的 `preparation_angle`。两层标签的职责不同：`source_perspective` 说明题目来自已导入面经、调研线索、项目证据、JD 深挖还是缺口 drill；`preparation_angle` 把这些来源归并成真实准备时更容易理解的三个角度：

- `same_role_interview_experience`：网上同岗位面经，包括用户导入的真实面经和牛客网、OfferShow、小红书等调研线索。
- `resume_project_tech_stack`：简历项目涉及的技术栈与交付证据，包括 `llm_project_implementation` 生成的项目实现追问。
- `other_possible_interview_questions`：其他可能面试问题，包括通用行为题、JD 技术追问、缺口追问和 `llm_foundation_drill` 生成的八股/基础追问。

`summary_json.preparation_angles` 会记录每个角度的输入来源、题目数和准备重点；`summary_json.interview_reference_links` 只保存面经标题、链接、搜索入口和边界说明。`InterviewReferenceService` 会过滤 `example/sample/demo` 等占位地址，区分用户导入原文、站内搜索、平台入口和普通搜索，并在读取旧面试包与导出 Markdown 时重新规范化。`summary_json.question_quality` 保存本地可解释 judge 的质量分、阈值、失败项和样例问题；`coverage_json.preparation_angle_counts`、`preparation_angles_passed`、`question_quality_score` 和 `question_quality_passed` 用来量化三视角覆盖与问题质量。`InterviewPrepDeliveryService` 负责把准备包渲染成 Markdown，并记录 `todo`、`practicing`、`ready`、`deferred` 等按题练习状态。评测会检查题号唯一性、来源视角覆盖、准备角度覆盖、LLM 追问组、质量 judge 和 Markdown 导出，而不只检查最终生成了一段文本。

面试题质量 judge 暂时不引入新的 LLM-as-judge 技术栈，而是使用本地可解释规则检查 JD 贴合、连续追问深度、缺口诚实边界、项目绑定、证据可追溯、行动性和重复率。理由是面试包生成本身已经调用 LLM，质量门禁需要低成本、可离线回归、失败原因可解释；后续如果要做人工抽检或发布前评审，可以在这个本地 judge 之后叠加 LLM-as-judge。

当前不把牛客网、OfferShow、小红书公开搜索结果直接写入面试包，原因是这些平台公开可达性、登录态、反爬、客户端渲染和内容真实性都不稳定。系统支持用户导入真实面经文本/链接，`InterviewExperienceService` 只从原文抽取问题、轮次、技术主题和可信度信号，不会在文本缺失时编造具体帖子；如果无法拿到正文，就只把标题和链接作为参考入口附在面试包里。`interview-source-smoke` 只保留 source 层健康探针职责，不再承担核心面试内容生成。核心面试包生成链路转向 JD + 简历项目 + RAG 证据 + LLM 追问链。否定证据优先级高于正向动作词，例如“没有 Kubernetes 集群维护经验”不能因为包含“维护”就被当成 Kubernetes 支持证据。

## 当前 Tool

`GET /agent/tools` 可以查看统一工具策略注册表。每个 Tool 除了输入、输出和副作用，还必须声明：

- `risk_level`：`low`、`medium` 或 `high`。
- `approval_requirement`：无审批或必须绑定的 `action_type`。
- `idempotency_policy`：只读、唯一键 upsert、run 级业务幂等或 approval 级单次执行。
- `timeout_seconds` 和 `retry_policy`。
- `audit_events`：必须留下的成功、失败或审批审计事件。
- `allowed_skills`：由 `SKILL.md` 反向计算，供 Planner 做权限校验。
- `mcp_candidate`：未来是否值得迁移到跨进程/跨授权域工具。

当前核心工具包括：

| Tool | 风险 | 审批要求 | 幂等策略 |
| --- | --- | --- | --- |
| `profile_repository.load_profile` | low | 无 | 只读 |
| `job_search.search_jobs` | medium | 无 | `source + external_id` upsert |
| `jd_parser.parse_jd` | medium | 无 | 每个岗位版本保存一份结构化结果 |
| `matcher.match_job` | low | 无 | run 保存被选中的 match result |
| `vector_index.retrieve_resume_evidence` | low | 无 | 只读 |
| `resume_tailor.tailor_resume` | medium | 无 | `run + profile + job` |
| `guardrail.verify_resume` | low | 无 | 只读 |
| `application.create_quick_apply_packet` | high | `application_packet` | `run + profile + job + resume` |
| `browser_apply` | high | `browser_apply` | 一个 approval 绑定一次执行 |
| `email_draft` | high | `email_draft` | 一个 approval 绑定一次执行 |
| `email_send` | high | `email_send` | 一个 approval 绑定一次执行 |

## 业务运行摘要

原始 Step/Event/LLM log 适合排障，但用户和面试官不应该依赖阅读几十段 JSON 才知道一次任务做了什么。`RunBusinessSummaryService` 会在 run 结束时生成 `business_summary` artifact，同时 `GET /agent/runs/{run_id}/summary` 可以基于最新审批和外发结果实时重建摘要：

- 路由层：选中的 Skill、SubAgent、Tool 和权限校验。
- 过程层：工具调用数、成功率、repair、幂等复用和总耗时。
- 结果层：目标岗位、匹配与缺口、证据覆盖、Guardrail、简历/投递/面试包 ID。
- 副作用层：高风险工具、审批状态、外发结果和审批绕过检测。

摘要只汇总已经落库的事实，不从最终文案反推“看起来成功”。当前也不会虚构简历定制前后的分数提升；这类 delta 必须由同一标注集上的前后对照评测产生。

## 是否需要 MCP

当前阶段不为了“技术栈完整”强制把所有 Python Tool 包装成 MCP。

理由：

- Profile、JD、RAG、匹配和 Guardrail 都在同一服务与数据权限域内，直接 Python Tool 能保留更清晰的事务和类型边界。
- Agent trace 已经能记录每一步 input/output/latency/error。
- 本地 SQLite 是权威存储，工具边界还没有跨进程或跨授权域。

适合 MCP 的下一阶段：

- 浏览器和邮箱已经通过高风险工具网关接入，并受 approval table 约束；当需要独立授权、远端部署或复用现成服务时，最适合优先 MCP 化。
- 日历：根据邮件或聊天记录安排面试。
- 云盘/本地文件系统：管理不同岗位的简历版本。
- 需要登录态的招聘平台：把账号授权和抓取能力隔离在 MCP server 中。

推荐路线：

1. 保持当前统一 Tool Policy 作为 Orchestrator 的稳定协议。
2. 优先将浏览器、邮箱、日历等跨授权域工具迁移为 MCP，核心数据库工具仍保留进程内调用。
3. MCP adapter 必须继承相同的审批、幂等、超时、重试和审计策略，不能绕过 HighRiskActionToolService。

## LangGraph 迁移状态

当前主 Agent 编排已经迁移到 LangGraph。

已完成：

- `find_jobs_for_profile`、`tailor_resume_for_job`、`quick_apply`、`prepare_interview_for_job` 和 `full_career_flow` 都通过同一个 `StateGraph` 运行。
- FastAPI `/agent/runs`、自然语言 Agent、Agent full-flow 评测和面试包评测都走 LangGraph 编排。
- 自然语言入口有独立 LangGraph 图，覆盖意图解析、计划执行、repair 和最终汇总。
- 计划、步骤、artifact、event 和错误仍写入 SQLite trace 表，前端无需访问 LangGraph 内部对象即可展示。
- `execution_plan.langgraph_decision.migrated=true`，运行输入输出都带有 `orchestration_framework=langgraph`。
- SQLite checkpointer 已持久化 LangGraph checkpoint；`POST /agent/runs/{run_id}/resume` 可以在新 Orchestrator 实例中按 `graph_thread_id` 恢复。
- `quick_apply` 和 `full_career_flow` 会在生成投递包前触发 LangGraph interrupt；确认前不会写入 `applications`。
- `POST /agent/runs/background` 支持后台启动 queued run，并通过 RedisTaskRunner 入队；`scripts/run_agent_worker.py` 独立消费执行 LangGraph。
- 每个后台 run 执行前会获取 Redis run lock；节点开始前检查 SQLite 状态和 Redis cancel flag。
- 简历版本、投递包、面试包写库节点都有业务幂等键，checkpoint 重放或重复 resume 会复用已有产物。
- 投递包 interrupt 前会创建 `agent_approvals` 审批记录，resume 确认/拒绝/取消都有审计状态。
- `agent_approvals` 的动作类型已经扩展到 `application_packet`、`browser_apply`、`email_draft`、`email_send`；浏览器辅助填写、邮件草稿和邮件发送必须通过高风险工具网关检查 approved approval 后才会执行真实 Playwright/SMTP/EML 工具。
- `ops_audit_events` 记录 DLQ 重放/丢弃和高风险工具放行等跨 run 运维审计事件。
- Redis worker 支持 high/normal/low 优先级队列、Sentinel HA 和 supervisor 多进程启动。
- 运维接口支持多租户 RBAC header，上线时可替换成 OIDC/SSO。
- `GET /agent/runs/{run_id}/events/stream` 支持 LangGraph SSE 事件流，`TraceService` 写 SQLite event 后也会发布 Redis pub/sub 通知。
- 首页一键流程已经改成单个后台 `full_career_flow` run，通过 SSE 推进阶段状态。

迁移中特别处理的问题：

- Graph state 不保存 ORM 对象，只保存 ID 和 JSON 产物，避免后续持久化和恢复失败。
- 搜索岗位后返回的 `job_ids` 必须在 `TypedDict` state schema 中声明；否则 LangGraph 会丢弃该字段，导致后续节点拿不到岗位。这已经通过回归测试固定。
- 保留依赖注入参数，测试和评测中的 fake `job_search`、`matcher`、`tailor`、`application`、`interview_prep` 仍可替换节点内部服务。

后续增强：

- 当浏览器、邮箱、日历等工具 MCP 化后，把对应节点改为跨进程工具调用，但入口仍必须走当前高风险工具网关和 approval table。
