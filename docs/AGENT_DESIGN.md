# Agent 设计说明

## LLM 调用点

当前 LLM 不是一个“全能 Prompt”，而是被放在需要语义理解或自然语言生成的边界上：

- `ResumeParserService.parse_structured_resume`：从 PDF 原文抽取结构化 Profile。
- `JDParserService.parse_jd`：从真实 JD 抽取 required skills、responsibilities、qualifications。
- `MatcherService`：主匹配逻辑仍是可解释规则 + RAG evidence，不把最终匹配分数完全交给 LLM。
- `ResumeTailorService._llm_tailor`：根据 JD 和检索证据生成定制简历。
- `ApplicationService`：生成求职信和外联文案。
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

当前已经引入显式 Skill 注册表和 SubAgent 注册表：

- `GET /agent/skills`：查看 Agent 能力、触发条件、上下文策略和输出契约。
- `GET /agent/subagents`：查看各 SubAgent 的职责、读写边界和上下文策略。

核心 SubAgent：

- `profile_analyst`：解析候选人 Profile。
- `job_analyst`：解析岗位 JD。
- `evidence_curator`：检索和整理证据。
- `fit_judge`：判断岗位适配度。
- `resume_writer`：生成定制简历并接受 Guardrail。
- `application_operator`：生成投递包。

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

这个能力不会出现在 Skill 注册表里，因为它不是用户意图层的能力，而是 LLM 调用链路的上下文策略。Skill 仍保留给 `fit_assessment`、`resume_tailoring` 这类可执行任务能力。

## Plan-Execute

`AgentOrchestrator.run` 会先执行 `plan_task`，由 `AgentPlanner` 生成计划并写入 `agent_artifacts`。

适合 Plan-Execute 的原因：

- 求职流程天然是多步骤：加载 Profile、搜索岗位、解析 JD、匹配、检索证据、改写简历、验证、生成投递包。
- 每一步都可以对应一个明确 Tool，便于 trace、重试和测试。
- 计划本身是可展示产物，适合作为简历项目亮点。

当前计划是确定性 planner。后续可以升级为 LLM planner，但执行仍应限制在注册工具内，避免模型自由调用不可控能力。

执行计划现在会包含：

- `skills`
- `subagents`
- `context_policy`
- `react_loops`
- `mcp_recommendation`
- `langgraph_decision`

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

## 当前 Tool

`GET /agent/tools` 可以查看工具注册表。当前工具包括：

| Tool | 作用 | 是否适合 MCP |
| --- | --- | --- |
| `profile_repository.load_profile` | 加载候选人档案 | 否 |
| `job_repository.load_job` | 加载目标岗位 | 否 |
| `job_search.search_jobs` | 并发搜索岗位源 | 是 |
| `jd_parser.parse_jd` | 解析 JD | 否 |
| `vector_index.upsert_job_chunks` | 写入 JD chunk 和 embedding | 否 |
| `matcher.match_job` | 生成匹配分数和证据 | 否 |
| `vector_index.retrieve_resume_evidence` | 检索简历证据并 rerank | 否 |
| `resume_tailor.tailor_resume` | 定制简历 | 否 |
| `guardrail.verify_resume` | 检查幻觉和证据覆盖 | 否 |
| `application.create_quick_apply_packet` | 生成投递包 | 是 |

## 是否需要 MCP

当前阶段不强制引入 MCP。

理由：

- 工具都在同一 FastAPI 进程内，直接 Python 调用更简单。
- Agent trace 已经能记录每一步 input/output/latency/error。
- 本地 SQLite 是权威存储，工具边界还没有跨进程或跨授权域。

适合 MCP 的下一阶段：

- 浏览器：打开招聘网站、辅助填写表单、等待用户确认提交。
- 邮箱：发送外联邮件或保存草稿。
- 日历：根据邮件或聊天记录安排面试。
- 云盘/本地文件系统：管理不同岗位的简历版本。
- 需要登录态的招聘平台：把账号授权和抓取能力隔离在 MCP server 中。

推荐路线：

1. 先保持当前 Python Tool registry。
2. 当浏览器/邮箱/日历接入后，把这些外部工具封装为 MCP。
3. Orchestrator 只面向统一 Tool Spec，不直接依赖某个 MCP server 的实现。

## 是否需要 LangGraph

当前不把整个项目迁移到 LangGraph。

理由：

- 现有 Orchestrator 已经有显式 Plan-Execute、step trace、artifact、失败状态和工具边界。
- 目前真正缺的是上下文治理、能力注册、真实评测和失败追踪，而不是图框架本身。
- 直接迁移会增加依赖和重构成本，但对当前简历项目的可展示能力提升有限。

适合迁移到 LangGraph 的触发条件：

- 出现多分支状态机，例如多个岗位并行推进、不同状态恢复。
- 需要人工审批节点，例如投递前确认、表单提交前确认。
- 需要后台长任务恢复，例如持续抓取岗位、定时投递提醒。
- 接入多个 MCP server，需要统一工具调用、重试和权限隔离。
- ReAct repair loop 从 1-2 轮扩展成复杂策略搜索。

所以当前选择是“先实现 LangGraph 关注的工程能力，再决定是否换框架”：状态、节点、工具、trace、上下文预算和评测都已经显式化。
