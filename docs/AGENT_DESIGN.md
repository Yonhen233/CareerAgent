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
- `context_manager`：执行渐进式披露和分级上下文压缩。
- `fit_judge`：判断岗位适配度。
- `resume_writer`：生成定制简历并接受 Guardrail。
- `application_operator`：生成投递包。

这里的“SubAgent”先实现为工程上的责任边界和上下文边界，而不是多个自由聊天 Agent。这样可以避免为了形式感引入大量 prompt，却仍然能在执行计划、trace 和评测中清楚展示每个能力模块。

## 渐进式披露与分级压缩

LLM 不再直接读取全量 Profile、全量 JD 和全部证据，而是由 `ContextCompressor` 生成任务包：

- L1 `profile_facts`：结构化候选人事实、项目、经历、技能和简历信号句。
- L2 `job_requirements`：岗位硬要求、职责、资格条件、关键词和 JD 信号句。
- L3 `ranked_evidence`：Top20 检索证据、retrieval/rerank metadata 和预算内片段。
- L4 `prompt_packet`：最终传给 LLM 的任务包。
- L5/L6：如果 L4 仍超过预算，继续压缩为摘要包或最小决策包。

每次压缩都会写入 `context_compression` 元数据：

- `raw_chars`
- `initial_packet_chars`
- `compressed_chars`
- `reduction_ratio`
- `retained_evidence_count`
- 每层 `budget_chars`、`dropped_chars`、`within_budget` 和 shrink event

渐进式披露规则是：默认只给 LLM 看结构化摘要和 Top evidence；只有 Guardrail repair 需要具体引用时，后续才暴露更细粒度原文。证据不足时直接报告缺口，不让模型编造。

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

当前代码已经具备 ReAct 所需的工具边界和验证器。开发默认不做静默 fallback：LLM、embedding 或 reranker 失败会直接报错，Agent step 与 LLM log 用于追溯。下一步可以把高风险简历改写成最多 2 轮的 repair loop。

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
