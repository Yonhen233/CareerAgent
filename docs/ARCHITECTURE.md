# 架构设计

CareerAgent 的目标不是“一个 Prompt 生成简历”，而是一个可观测、可评测、可扩展的求职 Agent 工程。

## 分层

- `app/api`：FastAPI 路由层，负责请求校验、DB Session 注入和服务编排。
- `app/frontend`：Jinja 页面路由，提供本地工作台。
- `app/agents`：Agent 工作流编排。
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

## Agent 工作流

### `find_jobs_for_profile`

1. 加载 Profile。
2. 并发搜索岗位源。
3. 并发解析 JD。
4. 顺序写入岗位和 JD chunk。
5. 匹配 Profile 与岗位。
6. 输出排序后的岗位列表和 source error。

### `tailor_resume_for_job`

1. 加载 Profile 和 Job。
2. 生成匹配结果。
3. 检索简历 RAG 证据。
4. 调用 LLM 或 fallback 生成定制简历。
5. Guardrail 检查新增事实和关键词覆盖。
6. 保存简历版本、diff、证据和 verification。

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
- deterministic hash embedding。

Chroma 镜像：

- 当 `VECTOR_BACKEND=hybrid` 且 `chromadb` 可用时，chunk 会同步写入 Chroma。
- 如果 Chroma 不可用，系统自动回退 SQLite 检索，不影响主流程和测试。

为什么这样设计：

- SQLite 让项目易部署、易测试、易审计。
- Chroma 体现真实 Agent/RAG 项目常见的向量库组件。
- 离线 fallback 避免简历项目演示时被外部依赖卡住。

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
