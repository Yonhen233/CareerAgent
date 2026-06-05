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
VECTOR_BACKEND=hybrid
CHROMA_DIR=data/chroma
JOB_INGEST_CONCURRENCY=6
```

说明：

- `VECTOR_BACKEND=sqlite`：只使用 SQLite。
- `VECTOR_BACKEND=hybrid`：SQLite + 可选 Chroma 镜像。
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
