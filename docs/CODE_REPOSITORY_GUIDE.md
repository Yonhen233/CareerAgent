# CareerAgent 代码仓库说明

本文档是面向开发、排障和面试讲解的代码导航，不是 API 参数手册。阅读顺序建议是：先看启动入口，再看 Agent 编排和 Runtime，最后看业务服务与评测脚本。

## 1. 从请求到结果的主链路

```text
HTTP 请求
  -> app/main.py
  -> app/frontend/routes.py 或 app/api/*
  -> app/services/task_runner.py / task_queue.py
  -> app/agents/langgraph_orchestrator.py
  -> Planner / Task Graph / Skill Contract
  -> 业务工具 app/agents/tools.py
  -> Parser / Retrieval / Guardrail / Artifact
  -> Checkpoint、Trace、Completion Gate
  -> 页面或 API 返回结果
```

运行级状态由 SQLite 持久化，Redis 用于可选的队列、锁和短期缓存。LangGraph负责图状态和节点转移；项目自己的 Runtime 负责任务合同、上下文预算、证据边界、重试、恢复和完成判定。

## 2. 目录总览

```text
current_project/
├── app/
│   ├── agents/       Agent 图、Planner、局部 ReAct、工具注册
│   ├── api/          JSON API 路由
│   ├── core/         配置、数据库、LLM 客户端、安全与基础设施
│   ├── frontend/     页面路由
│   ├── models/       SQLAlchemy 实体和 Pydantic 数据契约
│   ├── services/     业务服务、Runtime、RAG、评测和可靠性控制面
│   ├── static/       前端 CSS 和 JavaScript
│   └── templates/    Jinja 页面模板
├── scripts/          数据集生成、离线评测、真实 API 评测和启动辅助脚本
├── skills/           面向 Agent 的 Skill 定义和提示约束
├── evals/            评测数据集、PDF样本和评测输出
├── docs/             架构、运行、评测、Bad Case 与面试材料
└── tests/            单元测试、集成测试、契约测试和回归测试
```

## 3. Agent 编排层

| 文件 | 职责 | 面试讲解重点 |
|---|---|---|
| `app/main.py` | 创建 FastAPI 应用、注册路由、启动基础设施 | 应用生命周期和依赖注入 |
| `app/agents/langgraph_orchestrator.py` | 构建主 LangGraph、执行任务图、恢复运行、调用完成门禁 | Plan-Execute、动态 Todo、Checkpoint、Completion Gate |
| `app/agents/orchestrator.py` | 旧版或轻量编排入口 | 与主编排器的边界和迁移关系 |
| `app/agents/task_graph.py` | Todo DAG、依赖判断、节点就绪和 Replan 数据结构 | 为什么不能只用线性步骤 |
| `app/agents/task_router.py` | 根据任务意图选择任务图和能力范围 | 路由不是自由规划，而是受契约限制的选择 |
| `app/agents/natural_language.py` | 将自然语言请求解析为任务意图和输入 | 结构化输出、缺失信息和澄清 |
| `app/agents/local_react.py` | 局部、有界的检索补检和生成修复循环 | 为什么不让整个 Agent 无限 ReAct |
| `app/agents/prompt_registry.py` | 统一管理系统提示词和版本 | Prompt 版本化、可追踪和评测 |
| `app/agents/skills.py` | Skill 定义、加载和能力说明 | Skill 是职责契约，不是独立对话 Agent |
| `app/agents/subagents.py` | 专项职责适配和历史兼容入口 | 与 Skill Contract 的关系 |
| `app/agents/tools.py` | 工具目录、注册、参数验证和业务工具适配 | 工具权限、幂等、审批和 Trace |
| `app/models/agent_plan_schemas.py` | Planner、Todo、Replan 的 Pydantic Schema | 防止模型返回任意文本驱动执行 |

## 4. API 与前端层

`app/api/` 中的文件按业务资源划分：

| 文件 | 主要资源 |
|---|---|
| `assistant.py` | 自然语言助手入口 |
| `agent_runs.py` | Agent Run 查询、恢复和历史详情 |
| `agent_tools.py` | 工具目录和工具调用相关接口 |
| `agent_skills.py` | Skill 展示和管理接口 |
| `tasks.py` | 任务创建、状态和重试 |
| `profiles.py` / `resumes.py` | 简历档案、PDF解析和简历版本 |
| `jobs.py` / `job_discovery.py` | 岗位池、岗位搜索和岗位来源 |
| `matches.py` | 岗位匹配、适配判断和差距分析 |
| `applications.py` | 投递材料和审批边界 |
| `interview_prep.py` | 面试题、证据、回答和练习状态 |
| `evaluations.py` / `ops.py` | 评测控制台、运行观测和 Token 用量 |
| `health.py` / `auth.py` | 健康检查和会话认证 |

`app/frontend/routes.py` 将页面路由到模板。页面模板位于 `app/templates/`，共享布局在 `base.html`，交互脚本在 `app/static/js/main.js`，统一样式在 `app/static/css/style.css`。

## 5. Core 基础设施

| 文件 | 作用 |
|---|---|
| `app/core/config.py` | 从环境变量加载模型、数据库、Redis、重试、RAG 和预算配置 |
| `app/core/database.py` | SQLAlchemy Engine、Session 和事务边界 |
| `app/core/llm.py` | OpenAI兼容 LLM 客户端、结构化输出、usage 记录和调用预算 |
| `app/core/redis_client.py` | Redis连接、健康检查、缓存和分布式能力适配 |
| `app/core/retry.py` | 按异常类型区分传输重试、结构修复和业务补偿 |
| `app/core/security.py` | 会话、权限和高风险操作相关安全逻辑 |
| `app/core/redaction.py` | 日志和 Trace 脱敏 |
| `app/core/telemetry.py` | 运行指标和基础观测 |

## 6. Runtime、可靠性与持久化

| 文件 | 作用 |
|---|---|
| `agent_runtime.py` | Agent Runtime 外观，连接任务执行、状态和控制策略 |
| `agent_harness.py` | Harness 级能力汇总：执行、观测、评测和治理 |
| `run_control.py` | Run 的暂停、恢复、取消和回溯控制 |
| `task_runner.py` / `task_queue.py` | 后台任务执行和可选队列调度 |
| `task_state.py` | 任务状态、阶段和终态模型 |
| `langgraph_checkpointer.py` | LangGraph Checkpoint 的持久化适配 |
| `context_runtime.py` | 上下文合同、预算、证据范围和最小上下文构建 |
| `context_compressor.py` / `conversation_compactor.py` | 上下文压缩和会话历史整理 |
| `context_recovery.py` | 从 Checkpoint 和索引恢复最小可执行上下文 |
| `execution_provenance.py` | 记录模型、Prompt、Feature Flag、节点合同和运行版本 |
| `trace_service.py` | 运行级事件、工具调用、产物和错误 Trace |
| `agent_reliability.py` | 轨迹、产物、调用顺序、政策阻断和完成门禁 |
| `stale_runs.py` | 发现心跳超时或长期未推进的运行 |

## 7. RAG、解析与匹配

| 文件 | 作用 |
|---|---|
| `pdf_extraction.py` | PDF文字层、版面、页码、扫描件和解析诊断 |
| `text_splitter.py` | 按章节、段落、页边界和跨页关系生成 Chunk |
| `resume_parser.py` | LLM简历结构化、字段合并和原文回指门禁 |
| `jd_parser.py` | LLM JD结构化、技能语义复核和原文回指门禁 |
| `embedding_service.py` | Embedding生成、批处理和模型路由 |
| `reranker.py` | Cross-Encoder重排、降级路径和延时控制 |
| `rerank_result_cache.py` | 重排结果缓存、键规范化和缓存命中审计 |
| `vector_index.py` | 向量索引、混合召回和RRF融合 |
| `retrieval_quality.py` | 证据数量、相关性、支持性和下游生成门禁 |
| `evidence_classifier.py` | 区分交付、弱证据、计划学习和明确缺失 |
| `evidence_grounding.py` | 字段、引用、数字和生成事实的原文支持校验 |
| `job_search.py` / `job_discovery.py` | 用户Query、岗位召回、来源融合和岗位详情 |
| `job_search_intent.py` | 自然语言求职偏好解析和检索意图 |
| `job_relevance.py` | 岗位相关性和候选人排序 |
| `matcher.py` / `semantic_match_analysis.py` | 基础匹配、LLM语义判断和双向引用 |

## 8. 生成、审批与面试

| 文件 | 作用 |
|---|---|
| `resume_tailor.py` | 面向JD生成定制简历、单次修复和重新验证 |
| `guardrails.py` | 简历生成后的事实、数字和字段风险校验 |
| `resume_delivery.py` | 简历版本、预览和导出 |
| `application_service.py` | 投递材料生成和业务状态 |
| `application_guardrails.py` | 投递文案的事实支持、跨语言回指和人工确认门禁 |
| `approval_service.py` | 高风险外部动作的人工审批 |
| `outbound_tools.py` / `high_risk_action_tools.py` | 外部动作、幂等键、审计和权限边界 |
| `interview_agentic_rag.py` | 面试问题的检索规划、混合检索、回答和Claim验证 |
| `interview_prep.py` | 面试包组织、题目质量和发布门禁 |
| `interview_answer_framework.py` | 回答框架和参考答案展示策略 |
| `interview_claim_evaluation.py` | Claim Verifier 离线评测 |
| `interview_sources.py` / `interview_references.py` | 面经、技术资料和来源权限 |
| `interview_experience.py` / `interview_delivery.py` | 面试经历导入和练习交互 |

## 9. 评测与运营服务

| 文件 | 作用 |
|---|---|
| `evaluation_service.py` | 统一评测编排、指标汇总和发布门禁 |
| `agent_system_evaluation.py` | Agent 全链路质量、轨迹和安全评测 |
| `agent_efficiency_evaluator.py` | Token、延时、缓存和上下文效率评测 |
| `capability_bad_case_evaluation.py` | 失败样例、能力边界和回归门禁 |
| `multilingual_rag_evaluation.py` | 中文、英文和跨语言检索校准 |
| `online_quality.py` / `slo_service.py` | 线上质量、SLO窗口和告警条件 |
| `llm_usage.py` | Provider usage、Token和成本统计 |
| `ops_audit.py` | 运行审计和控制台查询 |
| `memory_feedback.py` | 用户反馈和经验复用 |

## 10. 脚本和测试如何对应

`scripts/` 负责可复现的实验入口，不应把一次性命令写进业务服务。命名约定如下：

- `generate_*`：生成评测集或演示数据；
- `run_*_eval.py`：运行某项离线或真实 API 评测；
- `run_*_ab.py`：执行 A/B 或消融实验；
- `run_*_worker.py`：启动后台 Worker；
- `run_user_flow_smoke.py`：验证用户主流程。

`tests/` 按“一个失败模式一个回归测试”的原则组织。重要测试类别包括：

- Parser 和 PDF：字段回指、跨页、双栏、扫描件、表格；
- RAG：混合召回、跨语言、噪声、去重、证据门禁；
- Agent：Planner、Todo、Replan、局部 ReAct、工具合同；
- Reliability：Checkpoint、崩溃恢复、幂等、审批和Completion Gate；
- 生成：简历、投递材料、面试答案的事实与引用；
- Infrastructure：Redis、缓存、Token usage、模型路由和SLO。

## 11. 注释和模块头规范

核心代码文件开头应说明三个问题：

1. 文件在系统中的职责；
2. 它依赖或输出的主要数据；
3. 修改时最容易破坏的边界。

类注释说明“它代表什么状态或能力、由谁调用、保证什么不变量”。函数注释说明“输入、输出、副作用、失败语义和不能做什么”。对于简单的属性访问和明显的私有格式化函数，不写重复代码；对于重试、门禁、权限、状态迁移和上下文裁剪，必须写原因和边界。

推荐格式：

```python
"""岗位检索质量门禁。

该模块只决定检索证据是否足以支持下游生成，不负责生成回答。
阈值来自配置并由评测集校准；门禁失败时返回可审计原因。
"""


class RetrievalQualityService:
    """评估检索证据是否达到下游生成的最低质量。

    该服务不相信模型自报的“相关”，而是检查去重后的证据数量、
    语义/词法信号、证据类型和正向支持状态。
    """

    def assess(self, ...):
        """计算证据质量报告并给出是否允许下游生成的决定。

        Args:
            ...
        Returns:
            包含 passed、confidence、reasons 和 downstream_policy 的报告。
        Raises:
            不应因为证据不足抛出异常；由调用方根据 passed 选择阻断或补检。
        """
```

## 12. 面试推荐阅读路径

1. `app/main.py`：服务如何启动；
2. `app/api/assistant.py`：请求如何进入系统；
3. `app/agents/langgraph_orchestrator.py`：主任务图如何编排；
4. `app/agents/tools.py`：工具如何注册和受限调用；
5. `app/services/context_runtime.py`：上下文如何裁剪和恢复；
6. `app/services/retrieval_quality.py` 和 `evidence_grounding.py`：证据如何放行；
7. `app/services/agent_reliability.py`：任务如何避免早停和错误完成；
8. `scripts/run_agent_system_eval.py`：如何验证链路不是“能跑就算成功”。

完整设计背景、Bad Case 和指标定义见 `docs/CAREER_AGENT_SYSTEM_DESIGN_AND_EVALUATION.md`、`docs/EVALUATION.md` 和 `docs/DEVELOPMENT_LOG.md`。
