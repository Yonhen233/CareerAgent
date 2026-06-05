# 开发日志

## 2026-06-05：中文文档、JD Chunk、混合向量索引、LLM 调试与评测闭环

### 这次做了什么

- 将 README 和 `docs/` 下已有文档改写为中文。
- 新增 `docs/PDF_CHUNKING.md`，详细说明 PDF 页级 chunk、结构化 chunk、metadata 和检索评分。
- 新增 `docs/EVALUATION.md`，说明评测样例、指标和运行方式。
- 新增 `docs/DEVELOPMENT_LOG.md`，并按“最新在最上面”的规则记录本次开发。
- 新增 `job_chunks` 表，岗位 JD 会和简历一样被切分、向量化并存储。
- 给 `resume_chunks` 增加 `metadata_json`，用于记录页码、字段、字符范围、切分策略。
- 增加 SQLite 轻量迁移，避免旧本地数据库因为新增列无法继续使用。
- 引入可选 Chroma 向量库镜像，SQLite 仍作为权威存储。
- 岗位搜索流程中，岗位源请求和 JD 解析使用 async 并发，数据库写入保持顺序。
- 新增 `llm_call_logs` 表和 `/llm/debug/logs` API，用于调试 LLM 调用。
- 新增 `evaluation_runs` 表、`/evaluations/run`、`/evaluations/results` 和 `evals/sample_cases.json`。
- 新增测试：JD chunk、LLM 日志、量化评测。

### 发现了什么问题

- 原项目只存储简历 chunk，没有职位 JD chunk，无法解释“岗位侧证据”。
- SQLite 检索虽然稳定，但缺少常见向量库组件，不够像真实 RAG 工程。
- PDF chunk 只有 raw text，没有页码和字符范围，证据回溯能力不足。
- LLM 调用失败时只能看到最终异常，缺少 prompt、response、延迟等调试信息。
- 测试只有功能是否跑通，缺少匹配质量的量化指标。
- 同步 SQLAlchemy Session 不适合直接并发写入。
- 使用 `TestClient(app)` 直接请求 DB 写入接口时，部分版本不会自动触发 lifespan，导致新表尚未创建。

### 怎么修复的

- 新增 `JobChunk` 模型和 `split_jd_text`，让 JD 也进入 chunk 检索体系。
- 新增 `metadata_json`，为简历 chunk 保存页码、字段和字符范围。
- 在 `SQLiteVectorIndex` 中增加 `upsert_job_chunks` 和 `query_job_chunks`。
- 增加 `ChromaVectorLibrary`，在可用时同步写入 Chroma，不可用时自动回退。
- 在 `JobSearchService` 中用 `asyncio.gather` 和 semaphore 并发解析 JD。
- 在 `LLMClient` 中记录调用日志，不记录 API key。
- 增加 `EvaluationService` 和样例集，输出 precision、recall、evidence hit rate、pass rate。
- 增加回归测试，保证新增能力可验证。
- API 级手动验证改用 `with TestClient(app) as client`，确保 startup/lifespan 执行后再请求。

### 未修复的问题及原因

- 还没有引入 Alembic：当前变更只需要轻量 SQLite 迁移，正式迁移系统会增加项目复杂度，适合下一阶段接入。
- Chroma 目前是镜像，不是主检索路径：为了保证无外部依赖时测试和演示稳定，SQLite 检索仍是主路径。
- PDF 多栏布局和表格恢复还没做：这需要更专业的 PDF layout parser，当前先保证页级证据和结构化证据可追踪。
- Agent 还没有后台任务队列：当前同步数据库写入较简单，后续长任务再引入队列更合理。

### 下一步怎么做

- 接入 Alembic 管理数据库迁移。
- 增加更多真实岗位评测样例，设置最低 pass rate 阈值。
- 将 Chroma 检索纳入主路径，并与 SQLite 检索做融合排序。
- 增加后台任务队列，让岗位搜索和简历定制支持异步任务状态轮询。
- 增加 PDF layout-aware 解析，处理多栏、表格和项目符号结构。
