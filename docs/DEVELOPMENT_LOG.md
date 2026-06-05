# 开发日志

## 2026-06-05 21:18 +08:00：补充 PDF Chunk、RAG 与 LLM 实景评测

### 这次做了什么

- 新增 `scripts/generate_eval_datasets.py`，可重复生成较大规模评测数据。
- 生成 `evals/pdf_chunk_cases.json`：30 个 PDF 简历案例、120 条 chunk 查询。
- 生成 `evals/rag_cases.json`：48 个 RAG 检索案例，每个案例 6 个候选证据 chunk。
- 新增 PDF Chunk 多策略评测：固定窗口、页内段落窗口、大窗口、section-aware。
- 新增 RAG 多策略评测：纯向量、纯词法、词法优先混合、不同混合权重和类型加权。
- 根据评测结果将生产检索权重调整为 `lexical_score * 0.80 + vector_score * 0.15 + type_boost * 0.05`。
- 新增 query alias expansion，例如 `retrieval augmented generation` -> `RAG`。
- 新增 `/evaluations/pdf-chunk-strategies`、`/evaluations/rag-strategies`、`/evaluations/llm-workflow`。
- 使用真实 LLM 接口运行岗位适配判断和 JD 定制简历流程。
- 更新 `docs/EVALUATION.md`、`docs/PDF_CHUNKING.md`、`docs/API.md` 和 README。

### 发现了什么问题

- 第一版 PDF Chunk 评测数据页文本太短，几个策略几乎打平，无法支撑策略选择。
- 第一版 RAG 数据过于精确关键词匹配，`lexical_only` 明显占优，不能体现同义表达和向量重排的价值。
- 第一轮 LLM 实景评测中，模型把 `LLM Evaluation Intern` 错判为 `strong_fit`，说明 strong/partial 边界不够清楚。

### 怎么修复的

- 扩大并加长 PDF 评测数据，在页面中加入噪声段落和上下文关键词要求。
- 在 RAG 数据中加入同义表达查询，测试 query expansion 能力。
- 增加 `lexical_80_vector_15_type_5` 策略，保留词法召回优势，同时加入向量重排和 chunk 类型加权。
- 收紧 LLM 岗位适配 prompt：只有直接需要 Agent/RAG/FastAPI/SQLite 实现的岗位才能标为 `strong_fit`。
- 重新运行 LLM 实景评测后，`fit_label_accuracy=1.0`、`tailor_pass_rate=1.0`。

### 未修复的问题及原因

- 当前评测数据仍是合成数据，不是真实用户 PDF 和真实招聘 JD；原因是需要人工标注真实数据才能可靠评估。
- 当前 embedding 仍是 hash embedding，不是真实语义 embedding；原因是项目需要保持离线可测和无外部依赖可运行。
- 当前没有 reranker；原因是现阶段先用轻量混合检索建立 baseline，后续再增加二阶段排序。

### 下一步怎么做

- 收集真实 PDF 简历和真实 JD，构建人工标注评测集。
- 接入真实 embedding 模型后重新跑 RAG 权重评测。
- 增加 reranker，对 Top20 chunk 做二阶段排序。
- 将 LLM 实景评测纳入可选 CI，设置最低准确率阈值。

## 2026-06-05 20:40 +08:00：开发日志补充时间精度

### 这次做了什么

- 将开发日志标题格式从“日期”升级为“日期 + 时间 + 时区”。
- 在开发说明中补充日志格式要求：`YYYY-MM-DD HH:mm +08:00：变更标题`。
- 将上一条开发日志标题补齐到分钟级时间，便于同一天多次开发时追踪顺序。

### 发现了什么问题

- 原日志只写 `2026-06-05`，如果一天内多次提交或调试，无法快速判断先后顺序。
- Git 提交时间可以定位到具体分钟，但日志标题没有承载这个信息。

### 怎么修复的

- 新增本条日志，并放在文件最上方。
- 将上一条日志标题改为 `2026-06-05 20:30 +08:00`。
- 更新开发文档中的日志规则，明确以后必须带时间和时区。

### 未修复的问题及原因

- 没有补更早历史记录的时间，因为当前项目只有一条历史开发日志；已用对应提交时间补齐。

### 下一步怎么做

- 后续每次改动都按同一格式新增日志。
- 如果引入自动化发布或 CI，可在提交时校验开发日志标题格式。

## 2026-06-05 20:30 +08:00：中文文档、JD Chunk、混合向量索引、LLM 调试与评测闭环

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
