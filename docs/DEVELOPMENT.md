# 开发说明

## 本地启动

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

## 配置

核心环境变量：

```env
DATABASE_URL=sqlite:///./data/career_agent.db
LLM_API_KEY=
LLM_BASE_URL=https://llmapi.paratera.com
LLM_MODEL=DeepSeek-V4-Pro
LLM_FALLBACK_ENABLED=false
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
- `EMBEDDING_PROVIDER=sentence_transformers`：使用真实 SentenceTransformer embedding。
- `EMBEDDING_PROVIDER=hash`：使用离线 hash embedding，适合快速测试。
- `EMBEDDING_PROVIDER_FALLBACK=error`：真实 embedding 加载失败时直接报错。
- `RERANKER_ENABLED=true`：对一阶段 Top20 chunk 做二阶段排序。
- `RERANKER_PROVIDER_FALLBACK=error`：真实 reranker 加载失败时直接报错。
- `RERANKER_ANCHOR_TOP_N=5`：保留前 5 条一阶段证据顺序，降低 reranker 牺牲召回的风险。
- `JOB_INGEST_CONCURRENCY`：并发解析 JD 的最大并发数。

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

或通过 API：

```http
POST /evaluations/run
```

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

## 真实 LLM 流程评测

运行简历解析、JD 解析、匹配/RAG、适配度判断、简历定制和 Guardrail 的端到端评测：

```powershell
$env:LLM_API_KEY='your_key_here'
$env:LLM_BASE_URL='https://llmapi.paratera.com'
$env:LLM_MODEL='DeepSeek-V4-Pro'
$env:LLM_FALLBACK_ENABLED='false'
$env:EMBEDDING_PROVIDER='sentence_transformers'
$env:EMBEDDING_PROVIDER_FALLBACK='error'
$env:RERANKER_ENABLED='true'
$env:RERANKER_PROVIDER='cross_encoder'
$env:RERANKER_PROVIDER_FALLBACK='error'

@'
import asyncio, json
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
    run = asyncio.run(EvaluationService().run_llm_workflow_evaluation(db))
    print(json.dumps(run.summary_json, ensure_ascii=False, indent=2))
finally:
    db.close()
'@ | python -
```

评测数据在 `evals/llm_workflow_cases.json`，结果指标说明见 `docs/EVALUATION.md`。真实调用失败不会自动兜底，失败阶段会写入 `failed_stage`，调用级问题可通过 `GET /llm/debug/logs?limit=50` 查看。

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
