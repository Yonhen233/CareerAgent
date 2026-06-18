# 开发说明

## 本地启动

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

启动后常用入口：

- `/`：用户开始页，支持自然语言描述需求，也支持上传/输入简历后一键运行完整求职流程。
- `/ui/profiles`：用户简历档案页，手动建档按中文简历常见栏目填写，支持自定义栏目、多段教育/实习/项目/校园实践条目和可选照片；列表中的“预览简历”会打开 `/profiles/{profile_id}/html`。
- `/ui/resumes`：定制简历页，默认嵌入 `/resumes/{resume_version_id}/html` 进行排版预览，Markdown 下载只作为辅助出口。
- `/ui/ops`：右上角“控制台”，集中查看 readiness、metrics、脱敏配置、后台任务和 LLM trace。
- `/ui/quality`：评测和长跑任务入口，从控制台进入。

## 配置

核心环境变量：

```env
DATABASE_URL=sqlite:///./data/career_agent.db
LLM_API_KEY=
LLM_BASE_URL=https://llmapi.paratera.com
LLM_MODEL=DeepSeek-V4-Pro
LLM_FALLBACK_ENABLED=false
LLM_THINKING_MODE=auto
LLM_REASONING_EFFORT=high
LLM_RETRY_ATTEMPTS=1
LLM_RETRY_BACKOFF_SECONDS=0.75
LLM_CONTEXT_COMPRESSION_ENABLED=true
LLM_CONTEXT_MAX_CHARS=9000
LLM_EVIDENCE_MAX_CHARS=3600
VECTOR_BACKEND=hybrid
CHROMA_DIR=data/chroma
EMBEDDING_PROVIDER=sentence_transformers
EMBEDDING_MODEL_NAME=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING_PROVIDER_FALLBACK=error
RERANKER_ENABLED=true
RERANKER_PROVIDER=cross_encoder
RERANKER_MODEL_NAME=cross-encoder/ms-marco-MiniLM-L-6-v2
RERANKER_PROVIDER_FALLBACK=error
RERANKER_TOP_N=20
RERANKER_ANCHOR_TOP_N=5
JOB_INGEST_CONCURRENCY=6
```

说明：

- `VECTOR_BACKEND=sqlite`：只使用 SQLite。
- `VECTOR_BACKEND=hybrid`：SQLite + 可选 Chroma 镜像。
- `LLM_FALLBACK_ENABLED=false`：开发默认严格失败；设置为 `true` 才使用规则解析/生成路径。
- `LLM_THINKING_MODE=auto`：官方 DeepSeek V4 接口会自动发送 `thinking: disabled`，让 JD 解析、简历定制、面试包生成优先获得稳定最终 `content`；如果要调试思考模式，可显式设置为 `enabled`。
- `LLM_RETRY_ATTEMPTS=1`：只对网络断连、429 和 5xx 等瞬时错误做同请求短重试；每次失败都会进入 `llm_call_logs`，不是静默兜底。
- `LLM_CONTEXT_COMPRESSION_ENABLED=true`：真实 LLM 调用默认使用渐进式披露和分级上下文压缩。
- `LLM_CONTEXT_MAX_CHARS`：最终 prompt packet 的字符预算。
- `LLM_EVIDENCE_MAX_CHARS`：Top evidence 在压缩上下文中的字符预算。
- `EMBEDDING_PROVIDER=sentence_transformers`：使用真实 SentenceTransformer embedding。
- `EMBEDDING_PROVIDER=hash`：使用离线 hash embedding，适合快速测试。
- `EMBEDDING_PROVIDER_FALLBACK=error`：真实 embedding 加载失败时直接报错。
- `RERANKER_ENABLED=true`：对一阶段 Top20 chunk 做二阶段排序。
- `RERANKER_PROVIDER_FALLBACK=error`：真实 reranker 加载失败时直接报错。
- `RERANKER_ANCHOR_TOP_N=5`：保留前 5 条一阶段证据顺序，降低 reranker 牺牲召回的风险。
- `JOB_INGEST_CONCURRENCY`：并发解析 JD 的最大并发数。

## LangGraph 编排开发约定

- 主 Agent 编排位于 `app/agents/langgraph_orchestrator.py`，`app/agents/orchestrator.py` 只保留兼容类名。
- 所有新增 Agent task 都应先加入 LangGraph `StateGraph` 节点和条件边，再暴露到 API 或前端。
- Graph state 只能保存 JSON 友好的 ID、列表和字典，不要保存 SQLAlchemy ORM 对象、DB Session、文件句柄或模型实例。
- 节点内部继续使用 `TraceService.step()` 写入 `agent_steps`，这样前端、评测和排障仍可复用原 trace。
- 如果节点有数据库写入副作用，必须保证幂等或先检查已有记录；后续接入 checkpointer/interrupt 后，节点可能因为恢复而重新执行。
- 当前 LangGraph 已使用 `InMemorySaver` checkpointer；它适合本地运行和单进程调试，不提供跨进程恢复。
- 现阶段 LangGraph 使用运行期 `run_id -> Session` 映射接入现有 FastAPI DB Session；替换为持久化 checkpointer 时，需要把长流程恢复改成每个节点独立打开 Session。

## 数据库

默认使用 SQLite：

```env
DATABASE_URL=sqlite:///./data/career_agent.db
```

启动时会：

- 创建缺失表。
- 对少量新增列执行轻量 SQLite 迁移。

当前没有引入 Alembic。后续如果模型继续增长，应接入 Alembic 管理正式迁移。

## 新增岗位源

实现 `JobSource`：

```python
class MySource(JobSource):
    name = "my_source"

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        ...
```

然后在 `JobSourceRegistry` 注册。

注意：

- source 失败不能让整个搜索失败。
- 返回内容要统一映射到 `JobPosting`。
- 外部请求使用 async HTTP client。
- 中文主场景 source 应优先稳定返回中文 JD；海外 ATS 类 source 只能作为显式开启的英文辅助源。
- 不因为某个 ATS 接口容易访问就加入主路径；Greenhouse 这类中国求职场景较弱的源不作为默认能力，除非后续有明确英文岗位场景和单独评测。
- 新 source 返回后应复用 `job_relevance` 的中文岗位相关性排序和 trace 字段，避免各 source 各自定义不可比较的排序规则。

## 新增评测样例

编辑：

```text
evals/sample_cases.json
```

每个 case 包含：

- `profile`
- `job`
- `expected_matched_skills`
- `expected_missing_skills`
- `expected_evidence_keywords`
- `min_overall_score` 或 `max_overall_score`

运行：

```bash
pytest -q
```

## 演示 PDF 与真实用户链路 smoke

生成演示 PDF：

```bash
python scripts/generate_demo_resumes.py
```

默认输出到 `demo_resumes/`，用于首页上传或 `/profiles/upload` 测试。

使用真实 DeepSeek/OpenAI-compatible LLM 跑用户链路 smoke：

```powershell
$env:LLM_API_KEY='your_key_here'
$env:LLM_BASE_URL='https://api.deepseek.com'
$env:LLM_MODEL='deepseek-v4-pro'
$env:LLM_THINKING_MODE='auto'
$env:LLM_FALLBACK_ENABLED='false'
python scripts\run_user_flow_smoke.py --pdf demo_resumes\agent_intern_strong_resume.pdf
```

该 smoke 会真实调用 PDF 简历解析、JD 解析、简历定制、投递包和面试包。它不会写入或打印 API key；失败会直接抛错，详细 prompt/response 预览在 `llm_call_logs` 和 `/ui/ops` 可查。

自然语言入口可用同一套 LLM 配置验证：

```http
POST /assistant/natural-language
```

推荐先用包含 `jd_text` 的中文请求验证完整链路，因为外部岗位源会受实时岗位数量和网络波动影响。接口失败时仍会返回结构化 body，包含 `run_id`、`plan_json`、`repair_attempts` 和用户可读错误；前端会显示失败卡片并提供流程记录入口。

或通过 API：

```http
POST /evaluations/run
```

中文岗位排序评测使用：

```text
evals/job_relevance_cases.json
```

重新生成数据集：

```bash
python scripts/generate_job_relevance_eval.py
```

运行排序评测：

```http
POST /evaluations/job-relevance
```

核心指标包括 `top1_accuracy`、`avg_top3_recall`、`avg_mrr`、`avg_ndcg_at_5` 和 `low_grade_above_strong_count`。如果失败，优先查看 `case_results_json[].ranked_jobs[].reasons`，判断是 query intent 缺失、泛技术词过强，还是产品/销售/运营噪声没有被降权。

投递包 Guardrail 评测使用：

```text
evals/application_packet_cases.json
```

重新生成数据集：

```bash
python scripts/generate_application_packet_eval.py
```

运行投递包评测：

```http
POST /evaluations/application-packet
```

核心指标包括 `high_risk_recall`、`false_block_count`、`missed_high_risk_count` 和 `issue_code_hit_rate`。如果失败，优先查看 `case_results_json[].validation.issues`，判断是投递包编造事实、缺目标岗位、误拦截正常文案，还是人工确认边界缺失。

## LLM 调试

查看最近调用：

```http
GET /llm/debug/logs?limit=50
```

建议调试流程：

1. 先看 `status` 是否为 `completed`。
2. 再看 `latency_ms` 判断是否是模型慢。
3. 看 `prompt_chars` 判断上下文是否过长。
4. 看 `response_preview` 判断模型是否返回非 JSON。
5. 看 `error_message` 定位接口、解析或超时问题。

## 上下文压缩调试

简历定制和 LLM workflow 评测都会通过 `ContextCompressor` 生成压缩上下文。

可以在以下位置查看压缩元数据：

- `resume_versions.keyword_alignment_json.context_compression`
- LLM workflow case result 中的失败阶段和调用日志
- Agent run 的 `execution_plan.context_policy`

重点看：

- `raw_chars`：原始 Profile、JD 和证据总字符数。
- `compressed_chars`：传给 LLM 的任务包字符数。
- `retained_evidence_count`：保留了多少条证据。
- `levels[].events`：哪一层触发了收缩。

如果模型判断错误，先看 RAG 是否召回了正确证据，再看压缩层是否把证据裁掉，最后再调整 prompt 或评测期望。

## 真实 LLM 流程评测

运行简历解析、JD 解析、匹配/RAG、适配度判断、简历定制和 Guardrail 的端到端评测。开发调试时建议先跑 `case_limit` 或指定 `case_indexes`，并写入 trace 文件：

```powershell
$env:LLM_API_KEY='your_key_here'
$env:LLM_BASE_URL='https://llmapi.paratera.com'
$env:LLM_MODEL='DeepSeek-V4-Pro'
$env:LLM_THINKING_MODE='auto'
$env:LLM_FALLBACK_ENABLED='false'
$env:EMBEDDING_PROVIDER='sentence_transformers'
$env:EMBEDDING_PROVIDER_FALLBACK='error'
$env:RERANKER_ENABLED='true'
$env:RERANKER_PROVIDER='cross_encoder'
$env:RERANKER_PROVIDER_FALLBACK='error'

@'
import asyncio, json
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
from app.models import entities  # noqa: F401
from app.services.evaluation_service import EvaluationService

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, future=True)
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
db = SessionLocal()
try:
    run = asyncio.run(
        EvaluationService().run_llm_workflow_evaluation(
            db,
            case_limit=3,
            trace_path=Path("data/runtime/llm_workflow_trace.jsonl"),
        )
    )
    print(json.dumps(run.summary_json, ensure_ascii=False, indent=2))
finally:
    db.close()
'@ | python -
```

评测数据在 `evals/llm_workflow_cases.json`，结果指标说明见 `docs/EVALUATION.md`。真实调用失败不会自动兜底，失败阶段会写入 `failed_stage`。每个 case 的 `stage_trace` 会记录简历解析、JD 解析、RAG、fit judge、tailor 和 Guardrail 的中间摘要；如果命令超时，已经完成的 case 会写入数据库和 `trace_path`。
LLM 网络层短重试会以 `retryable_failed` 写入调用日志；如果重试后仍失败，case 仍按失败处理。

也可以使用开发期 CLI runner 执行长跑：

```powershell
$env:LLM_API_KEY='your_key_here'
$env:LLM_BASE_URL='https://api.deepseek.com'
$env:LLM_MODEL='deepseek-v4-pro'
$env:LLM_THINKING_MODE='auto'
$env:LLM_FALLBACK_ENABLED='false'
python scripts\run_llm_workflow_eval.py --trace-path data\runtime\llm_workflow_trace_latest.jsonl
```

Runner 会输出 UTF-8 JSON summary；默认质量门禁失败时返回非 0 退出码。若长跑中断，可加 `--resume` 从 trace 中第一个缺失 case 继续。

也可以通过后台任务 API 运行：

```http
POST /tasks/llm-workflow?case_limit=18
GET /tasks
GET /tasks/{task_id}
```

后台任务使用进程内 FastAPI BackgroundTasks 和 SQLite `task_runs` 表，适合开发期演示并发、轮询进度和失败追踪；生产多实例部署时应替换为 Redis/Celery/Arq 等外部队列。前端推荐使用 `/ui/quality` 的后台任务面板，或在 `/ui/ops` 查看最近任务和输出摘要。

## 权限与监控

开发默认不要求登录，便于本地调试。需要演示权限隔离时设置：

```env
ADMIN_API_KEY=your_admin_token
REQUIRE_ADMIN_FOR_MUTATIONS=true
```

开启后所有写操作都需要 `X-Admin-Token`。运维入口：

- `GET /ops/readiness`：数据库、LLM、embedding、reranker readiness。
- `GET /ops/metrics`：请求数、平均延迟、状态码分布、Agent run/task/LLM call 状态分布。
- `GET /ops/config`：脱敏配置摘要。

前端入口 `/ui/ops` 会聚合 readiness、metrics、脱敏配置、后台任务和最近 LLM 调用日志。页面上的 Admin Token 只保存到本机浏览器 localStorage；保存后所有前端 API 请求会自动携带 `X-Admin-Token`。面试准备和评测页面推荐使用 `/ui/prep`、`/ui/quality`，旧路径 `/ui/interview-prep`、`/ui/evaluations` 保持兼容。

API 也支持 smoke mode：

```http
POST /evaluations/llm-workflow?case_limit=3
```

## 真实 RAG 评测

运行真实 embedding + reranker 评测：

```powershell
$env:EMBEDDING_PROVIDER='sentence_transformers'
$env:EMBEDDING_MODEL_NAME='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
$env:EMBEDDING_PROVIDER_FALLBACK='error'
$env:RERANKER_ENABLED='true'
$env:RERANKER_PROVIDER='cross_encoder'
$env:RERANKER_MODEL_NAME='cross-encoder/ms-marco-MiniLM-L-6-v2'
$env:RERANKER_PROVIDER_FALLBACK='error'
pytest tests/test_evaluation_service.py -q
```

单元测试默认在 `tests/conftest.py` 中设置：

```env
EMBEDDING_PROVIDER=hash
RERANKER_ENABLED=false
LLM_FALLBACK_ENABLED=true
```

这样可以保证普通回归测试不依赖模型下载和外部 LLM；真实评测需要显式打开环境变量。

## 测试

```bash
pytest -q
```

测试覆盖：

- API 健康检查。
- 前端页面渲染。
- 简历解析。
- 简历 chunk 检索。
- JD chunk 检索。
- 岗位匹配。
- Agent 工作流。
- LLM 调用日志。
- 量化评测。

## 开发日志要求

之后每次改动都必须更新：

```text
docs/DEVELOPMENT_LOG.md
```

规则：

- 最新日志写在最上面。
- 标题必须包含时间和时区，格式为 `YYYY-MM-DD HH:mm +08:00：变更标题`。
- 说明这次做了什么。
- 说明发现了什么问题。
- 说明怎么修复。
- 说明未修复问题和原因。
- 说明下一步怎么做。
