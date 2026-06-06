# 架构设计

CareerAgent 的目标不是“一个 Prompt 生成简历”，而是一个可观测、可评测、可扩展的求职 Agent 工程。

## 分层

- `app/api`：FastAPI 路由层，负责请求校验、DB Session 注入和服务编排。
- `app/frontend`：Jinja 页面路由，提供本地工作台。
- `app/agents`：Agent 工作流编排、Tool 注册表、Skill 注册表和 SubAgent 注册表。
- `app/services`：领域服务，包括简历解析、JD 解析、岗位搜索、RAG 检索、匹配、简历定制、Guardrails、投递包、评测和 Trace。
- `app/models`：SQLAlchemy 数据模型和 Pydantic 响应模型。
- `app/core`：配置、数据库、LLM 客户端。

## 数据模型

- `profiles`：候选人档案。
- `resume_chunks`：简历 chunk，包含结构化字段 chunk 和 PDF 页级 chunk。
- `jobs`：岗位 JD 主表。
- `job_chunks`：岗位 JD chunk。
- `match_results`：岗位匹配结果。
- `resume_versions`：定制简历版本。
- `applications`：投递包和投递状态。
- `agent_runs`：Agent 工作流运行记录。
- `agent_steps`：Agent 步骤级 Trace。
- `agent_artifacts`：Agent 产物。
- `llm_call_logs`：LLM 调用调试日志。
- `evaluation_runs`：评测运行结果。

## Agent 能力注册

`app/agents/tools.py` 保存可执行 Tool 的输入输出、副作用和 MCP 化候选标记。

`app/agents/skills.py` 保存更高层的能力注册：

- `resume_intake_and_structuring`
- `jd_structuring`
- `evidence_retrieval`
- `fit_assessment`
- `resume_tailoring`
- `application_packet`

`app/agents/subagents.py` 保存工程责任边界，例如 `fit_judge`、`resume_writer`、`application_operator`。上下文压缩不再注册成独立 subagent，而是由 `ContextCompressor` 作为 LLM 调用前的 runtime policy 执行。

## Agent 工作流

### `find_jobs_for_profile`

1. `plan_task` 生成 Plan-Execute 执行计划，包含 tools、skills、subagents 和 context policy，并写入 `agent_artifacts`。
2. 加载 Profile。
3. 并发搜索岗位源。
4. 并发解析 JD。
5. 顺序写入岗位和 JD chunk。
6. 匹配 Profile 与岗位。
7. 输出排序后的岗位列表和 source error。

### `tailor_resume_for_job`

1. `plan_task` 生成 Plan-Execute 执行计划。
2. 加载 Profile 和 Job。
3. 生成匹配结果。
4. 检索简历 RAG 证据。
5. `ContextCompressor` 按 Profile 摘要、JD 摘要、Top evidence 和总 prompt packet 预算生成压缩上下文。
6. 调用 LLM 生成定制简历；默认失败直接报错并进入 Trace。
7. Guardrail 检查新增事实和关键词覆盖。
8. 保存简历版本、diff、证据和 verification。

这个流程适合引入 ReAct 的位置是“定制简历 -> 验证 -> 修复”：

- Observe：读取 JD 缺口、Top20 检索证据和当前简历草稿。
- Act：生成或修复定制简历。
- Observe：Guardrail 检查新增事实、关键词覆盖和风险等级。
- Act：高风险时回退到证据更强的表达，最多迭代 2 次。

### `quick_apply`

1. 加载 Profile 和 Job。
2. 复用或生成定制简历。
3. 生成求职信和外联文案。
4. 保存投递清单、投递链接和状态。

## 混合向量检索

当前实现采用“SQLite 权威存储 + 可选 Chroma 镜像”的设计。

SQLite 存储：

- chunk 文本。
- chunk 类型。
- source。
- token count。
- metadata。
- embedding JSON。
- embedding provider/model/dimension metadata。

Chroma 镜像：

- 当 `VECTOR_BACKEND=hybrid` 且 `chromadb` 可用时，chunk 会同步写入 Chroma。
- 如果 Chroma 不可用，系统自动回退 SQLite 检索，不影响主流程和测试。

Embedding：

- 默认 `EMBEDDING_PROVIDER=sentence_transformers`。
- 默认模型 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`。
- 模型不可用时默认直接报错，避免静默掩盖质量问题。
- pytest 默认设置 `EMBEDDING_PROVIDER=hash`，这是显式测试模式，不是生产兜底。

Reranker：

- 默认对一阶段 Top20 候选做 CrossEncoder 二阶段排序。
- 默认模型 `cross-encoder/ms-marco-MiniLM-L-6-v2`。
- 为避免 reranker 牺牲证据召回，Top5 作为 recall anchor 保持一阶段顺序，第 6 到第 20 个候选在分数带内重排。
- rerank metadata 会记录 first-stage score、rerank score、融合权重、promotion gap 和模型名。

为什么这样设计：

- SQLite 让项目易部署、易测试、易审计。
- Chroma 体现真实 Agent/RAG 项目常见的向量库组件。
- 开发态优先暴露错误；离线 hash/heuristic 只作为显式测试模式。

## Agent Tool 与 MCP

当前 Agent 工具注册表位于 `app/agents/tools.py`，并通过 `GET /agent/tools` 暴露。主要工具包括：

- `profile_repository.load_profile`
- `job_search.search_jobs`
- `jd_parser.parse_jd`
- `vector_index.upsert_job_chunks`
- `matcher.match_job`
- `vector_index.retrieve_resume_evidence`
- `resume_tailor.tailor_resume`
- `guardrail.verify_resume`
- `application.create_quick_apply_packet`

当前不强制引入 MCP。原因是这些工具都在同一 FastAPI 进程内，直接 Python 调用更简单、可测、可追踪。适合 MCP 化的边界是外部能力：

- 浏览器自动填写招聘表单。
- 邮箱发送外联消息。
- 日历安排面试。
- 需要登录态的招聘平台搜索。
- 云盘或本地文件系统授权访问。

## FastAPI 并发设计

已使用的并发点：

- 岗位源搜索：腾讯、Lever 等 source 使用 `asyncio.gather` 并发请求。
- JD 解析：多个岗位的 JD 解析使用 semaphore 控制并发。
- 外部 I/O：HTTP 请求、LLM 调用都走 async client。

刻意不并发的点：

- SQLite/SQLAlchemy Session 写入保持顺序执行。
- 原因是同步 Session 不是线程安全对象，盲目并发写入会造成不稳定错误。

后续可升级方向：

- 引入 async SQLAlchemy engine。
- 将岗位抓取、JD 解析、向量入库拆成后台任务队列。
- 使用 Celery、Arq 或 Dramatiq 承载长任务。

## LLM 调试

`LLMClient` 支持记录调用日志：

- `trace_name`
- `model`
- `base_url`
- `status`
- `prompt_preview_json`
- `response_preview`
- `error_message`
- `latency_ms`
- `prompt_chars`
- `response_chars`

日志用于调试 prompt、模型响应、JSON 解析失败、超时和延迟问题。系统不会记录 API key。

## 上下文治理

`app/services/context_compressor.py` 实现渐进式披露：

- 默认只向 LLM 暴露结构化 Profile、结构化 JD 和 Top evidence。
- 全量原始简历、全量 JD、非 Top evidence 默认作为 deferred context，不进入 prompt。
- 压缩元数据记录字符数、预算、压缩事件和保留证据数量。
- 简历定制结果的 `keyword_alignment_json.context_compression` 会保存压缩元数据，便于追溯质量问题。

这样做的目标不是单纯减少 token，而是让失败可定位：如果 LLM 判断错，可以区分是解析错、RAG 没召回、reranker 排错、压缩丢证据，还是模型本身判断错。

## Guardrails

简历定制后会做规则校验：

- 检测源简历中不存在的新增数字指标。
- 检测过多长 token 新增事实。
- 计算 JD required skill 覆盖率。
- 计算 RAG 证据覆盖率。

输出风险等级：

- `low`
- `medium`
- `high`

## 评测闭环

评测样例位于 `evals/sample_cases.json`，运行后写入 `evaluation_runs`。

指标包括：

- required skill precision。
- required skill recall。
- missing skill precision。
- evidence hit rate。
- pass rate。
- avg overall score。

这使项目可以用量化指标描述“匹配与检索质量”，而不是只说“看起来还行”。
