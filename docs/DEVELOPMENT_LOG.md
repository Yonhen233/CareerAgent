# 开发日志

## 2026-06-06 08:43 +08:00：补强 LLM Skill、SubAgent 与渐进式上下文披露

### 这次做了什么

- 新增 Agent Skill 注册表和 SubAgent 注册表，通过 `GET /agent/skills`、`GET /agent/subagents` 暴露能力边界。
- 将误理解的“奖金税披露”纠正为“渐进式披露”，新增 `progressive_disclosure` skill，并由 `context_manager` subagent 负责。
- `AgentPlanner` 的执行计划新增 `skills`、`subagents`、`context_policy` 和 `langgraph_decision` 字段。
- 重写 `ContextCompressor`，从单层裁剪升级为分级压缩：L1 Profile、L2 JD、L3 ranked evidence、L4-L6 prompt packet。
- 简历定制和 LLM workflow fit judge 都接入分级压缩上下文，并把 `context_compression` 元数据写入评测结果。
- 更新 README、架构文档、Agent 设计文档、API 文档、开发说明和评测文档，说明 Skill/SubAgent、渐进式披露、分级压缩和 LangGraph 暂不迁移理由。
- 新增上下文压缩测试、Skill/SubAgent API 测试、执行计划能力边界测试，并扩展 LLM workflow summary 测试。

### 发现了什么问题

- LLM 部分不是缺一个更大的 prompt，而是缺明确的能力边界、上下文预算、分级披露和可评测的压缩元数据。
- `ResumeTailorService._llm_tailor` 的异常 fallback 分支引用了已经不在作用域内的 `profile/job/evidence`，真实 LLM 超时或坏 JSON 时会触发二次错误。
- 18-case 真实 LLM workflow 全量评测在 20 分钟命令超时后没有拿到 summary，说明当前评测执行器缺少分批、逐 case 落盘和断点恢复。
- 5-case 真实 smoke 评测中，`ml_candidate_partial_agent_role` 仍被模型判为 `weak_fit`，partial/weak 边界仍不稳定。
- 2-case context smoke 发现短小 fit judge 上下文因为结构化字段和 trace 元数据，可能比原始上下文略大，直接展示负数 `reduction_ratio` 容易误导。

### 怎么修复的

- 用 `progressive_disclosure` skill 明确“默认只披露结构化摘要和 Top evidence，证据不足直接报告缺口”的规则。
- 增加 `context_manager` subagent，把上下文压缩从 prompt 内约定提升为可注册、可测试、可展示的工程模块。
- 在 `ContextCompressor` 中记录每层 `input_chars`、`output_chars`、`budget_chars`、`dropped_chars`、`within_budget` 和 shrink events。
- 修复 `_llm_tailor` 的参数传递，保证 LLM 异常时如果显式开启测试 fallback，可以正常回到规则路径。
- LLM workflow summary 新增 `context_compression` 聚合指标，包括 fit/tailor 压缩上下文数量、平均压缩率和平均保留证据数。
- 将 `reduction_ratio` 最低值限制为 0，并新增 `expansion_ratio` 表示短上下文结构化开销。
- 跑通真实 LLM 连通性测试、5-case 全流程 smoke 和 2-case context smoke；普通测试保持 `21 passed`。

### 未修复的问题及原因

- 暂不把整个 Agent 改成 LangGraph；原因是当前 Orchestrator 已有 plan-execute、trace、artifact 和工具边界，现阶段迁移框架收益低于补齐上下文治理和评测闭环。
- 18-case 全量真实 LLM 评测仍未在本次改动后完成；原因是顺序真实调用耗时过长，命令超时会丢失中间结果，需要先改造评测执行器。
- `ml_candidate_partial_agent_role` 的 partial/weak 边界仍未修复；原因是这需要更多边界样例、prompt 标准或单独 verifier，不应靠一次 prompt 微调硬掰结果。
- L3 evidence 层的 JSON metadata 开销仍可能让层级预算显示 `within_budget=false`，但最终 L4-L6 prompt packet 会继续压缩到总预算内；后续需要区分“证据文本预算”和“JSON 包预算”。

### 下一步怎么做

- 给 LLM workflow 增加 smoke mode、case limit、逐 case 落盘和可恢复运行，避免全量真实评测超时后没有 summary。
- 增加 partial/weak 边界数据，尤其是“有 ML/LLM 相邻经验但没有 Agent/RAG 交付”的案例。
- 评估不同 evidence budget 对 fit label、tailor keyword hit、Guardrail 通过率的影响，选择更稳的压缩预算。
- 在 Guardrail 高风险时实现 1-2 轮 ReAct repair loop，并让 repair loop 按需请求 deferred context。
- 等浏览器投递、邮箱、日历或多 MCP server 接入后，再评估是否迁移到 LangGraph。

## 2026-06-06 01:15 +08:00：补强 LLM 端到端流程评测与真实调用指标

### 这次做了什么

- 新增 `evals/llm_workflow_cases.json`，把 LLM 评测从 3 条岗位匹配样例扩展为 18 个端到端流程案例。
- LLM 评测覆盖简历解析、JD 解析、RAG 证据检索、岗位适配判断、简历定制和 Guardrail 验证。
- LLM 评测新增量化指标：`completed_rate`、`end_to_end_pass_rate`、`resume_parse_success_rate`、`jd_parse_success_rate`、`fit_label_accuracy`、`fit_score_in_range_rate`、`tailor_pass_rate`、`guardrail_pass_rate`、`forbidden_claim_free_rate` 和 `difficulty_breakdown`。
- 将岗位适配判断 prompt 改成通用证据约束规则，不再写死为 Agent/RAG 岗位边界。
- 在 schema 层兼容真实 LLM 常见的 `null` 叶子字段，把字符串字段缺失归一为空字符串，把列表字段缺失归一为空列表。
- 改进异常记录，`ReadTimeout` 这类 `str(exc)` 为空的异常会记录异常类型和 `repr(exc)`，方便通过 trace 追溯。
- 更新 README、API 说明、开发说明和评测文档，补充真实 LLM workflow 评测运行方式、指标定义和实测结果。
- 新增 LLM workflow 数据集测试、summary 指标测试、schema 归一化测试和异常格式化测试。

### 发现了什么问题

- 之前的 LLM 评测只覆盖岗位匹配标签，没有真实评测简历解析、JD 解析、简历定制、Guardrail 和失败 trace。
- 第一轮真实 LLM workflow 评测中，`resume_parse_success_rate=0.7778`，失败原因主要是模型把 `projects.impact`、`work_experience.duration` 等字段返回为 `null`。
- schema 修复后重新跑真实评测，`resume_parse_success_rate=1.0000`、`fit_label_accuracy=0.9444`、`end_to_end_pass_rate=0.8889`。
- 仍有 1 个 case 在 `tailor_resume` 阶段触发 `httpx.ReadTimeout`，说明长 prompt 的简历定制仍有超时风险。
- hard 分桶中 `ml_candidate_partial_agent_role` 被模型从人工期望的 `partial_fit` 判为 `weak_fit`，说明 partial/weak 边界还需要更多反例和 prompt 约束。

### 怎么修复的

- 将 LLM 评测 case 设计为包含原始简历、期望 Profile 技能、期望 Profile 关键词、JD、期望 JD 技能、fit label、fit score 区间、定制简历关键词和禁止 claim 的完整样本。
- 在 `EvaluationService.run_llm_workflow_evaluation` 中按阶段执行真实流程，并把每个阶段的成功率和质量指标写入 summary。
- 新增 `_keyword_hit_rate`、`_score_range_error`、`_llm_case_passed`、`_summarize_llm_by_key` 等指标 helper。
- 删除旧的 3 条硬编码 LLM workflow 逻辑，避免评测退回 toy demo。
- 在 Pydantic schema 中增加字段归一化 validator，真实 LLM 返回 `null` 时不编造信息，只保留为空值。
- 在 `LLMClient` 和 LLM workflow case 捕获处使用统一异常格式，保证失败报告里能看到异常类型。

### 未修复的问题及原因

- `tailor_resume` 仍可能因为上游 LLM 长时间无响应而超时；原因是当前 prompt 同时包含 Profile JSON、原始简历、JD JSON、JD 文本和 Top10 evidence，长上下文生成耗时不可控。
- hard case 的 partial/weak 边界还不够稳定；原因是模型对“有相邻 ML/LLM 能力但缺少 Agent/RAG 交付”比人工标注更保守。
- LLM workflow 数据集仍是合成数据；原因是真实简历和真实 JD 需要脱敏、人工标注和版本管理。

### 下一步怎么做

- 压缩 `resume_tailor` prompt，只传最相关 evidence 和结构化摘要，降低超时概率。
- 增加 partial/weak 边界样例，尤其是相邻技能、课程经验、读过论文但没有交付的情况。
- 在真实脱敏简历和真实招聘 JD 上建立人工标注 LLM workflow 数据集。
- 为 LLM workflow 增加 CI 阈值，例如 `fit_label_accuracy`、`end_to_end_pass_rate`、`guardrail_pass_rate` 的最低标准。

## 2026-06-05 22:54 +08:00：扩充强噪声评测集并改为默认失败直报

### 这次做了什么

- 重写 `scripts/generate_eval_datasets.py`，把 PDF chunk 评测从 30 个 case / 120 条 query 扩到 96 个 case / 576 条 query。
- 把 RAG 评测从 48 个 case / 288 个候选 chunk 扩到 180 个 case / 2160 个候选 chunk。
- 新数据集加入 hard negative、课程噪声、计划学习、废弃 prototype、相邻岗位项目、跨页干扰、通用工具词等噪声。
- PDF 与 RAG 评测 summary 增加 `difficulty_breakdown` 和 `noise_breakdown`。
- PDF chunk 评测改用生产 embedding 与生产检索权重，不再只在 hash ranker 上选切分策略。
- 根据强噪声 RAG 评测，将生产检索权重从 `vector=0.55 / lexical=0.40 / type=0.05` 调整为 `vector=0.45 / lexical=0.50 / type=0.05`。
- 将 embedding、reranker、LLM 默认策略改为失败直接报错；只有测试环境显式开启 hash/heuristic/LLM fallback。
- 更新 README、架构文档、开发说明和评测文档，说明严格失败和强噪声评测结果。

### 发现了什么问题

- 原 PDF/RAG 数据集过小、过理想，不能暴露课程噪声和相邻岗位干扰。
- 强噪声 PDF 评测发现 `coursework_vs_shipped` 很难，`paragraph_page_900_overlap160` 在该噪声下 Top3 context hit 只有 0.0521。
- 强噪声 RAG 评测把 Top3 Recall 从原来的 0.9444 拉低到 0.6125，说明新数据更能暴露真实弱点。
- `vector=0.55 / lexical=0.40 / type=0.05` 在强噪声数据下不如 `vector=0.45 / lexical=0.50 / type=0.05`。
- pytest 里使用 `setdefault` 设置环境变量会被外部 shell 中残留的真实评测变量覆盖，导致测试误走真实模型和严格 LLM 路径。

### 怎么修复的

- 生成更大规模、更强噪声的数据集，并把难度、噪声类型写入 case/query。
- 新增分桶评测指标，直接暴露 easy/medium/hard/adversarial 和不同噪声 profile 的表现。
- 重新运行真实 embedding + CrossEncoder reranker 评测，选择 `real_embedding_top20_rerank`。
- 将测试环境变量改为直接赋值，强制 `EMBEDDING_PROVIDER=hash`、`RERANKER_ENABLED=false`、`LLM_FALLBACK_ENABLED=true`。
- 默认配置改为 `EMBEDDING_PROVIDER_FALLBACK=error`、`RERANKER_PROVIDER_FALLBACK=error`、`LLM_FALLBACK_ENABLED=false`。

### 未修复的问题及原因

- `coursework_vs_shipped` 仍然很弱；原因是当前 ranker 还没有 evidence type classifier，难以区分“真实交付”和“课程/计划中提到”。
- Reranker 目前通过 Top5 anchor 避免破坏召回，但对 Top3 Recall 没有新增收益；原因是通用 MS MARCO CrossEncoder 未针对简历/JD 证据排序微调。
- 评测数据仍是合成数据；原因是真实 PDF 简历和真实 JD 需要人工脱敏和标注。

### 下一步怎么做

- 增加 evidence type classifier 或 LLM verifier，给 shipped project、metric evidence、coursework、planned learning、abandoned prototype 不同权重。
- 收集真实脱敏简历和真实 JD 做人工标注评测集。
- 用失败 trace 继续调试 LLM parse/tailor 的 prompt，而不是用 fallback 掩盖错误。

## 2026-06-05 22:21 +08:00：接入真实 Embedding、Top20 Reranker 与 Agent Tool 规划

### 这次做了什么

- 新增 `EmbeddingService`，默认接入 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，并保留 hash fallback。
- 新增 `RerankerService`，支持 `cross-encoder/ms-marco-MiniLM-L-6-v2` 对一阶段 Top20 chunk 做二阶段排序。
- 将 `SQLiteVectorIndex` 的简历 chunk、JD chunk 写入和查询改为真实 embedding 主路径，并在 metadata 中记录 provider/model/dimension。
- 将生产检索权重调整为 `vector=0.55 / lexical=0.40 / type=0.05`。
- 为 reranker 增加 Top5 recall anchor：前 5 条证据保留一阶段顺序，第 6 到第 20 条在分数带内 rerank。
- 扩展 RAG 评测，加入 hash baseline、真实 embedding 多权重、真实 CrossEncoder Top20 rerank 对比。
- 新增 `AgentToolSpec` 和 `AgentPlanner`，每次 Agent run 会先生成 Plan-Execute artifact。
- 新增 `GET /agent/tools`，可查看当前 Agent 工具清单和 MCP 候选边界。
- 新增 `docs/AGENT_DESIGN.md`，说明 LLM 调用点、Plan-Execute、ReAct、Tool 和 MCP 取舍。
- 更新 README、架构文档、API 文档、开发说明和评测文档。
- 新增 embedding/reranker 与 agent tools 测试。

### 发现了什么问题

- 裸 `pip install` 安装到了系统 Python，而项目实际使用 `C:\Users\IC\.codex\python312\python.exe`，导致第一次真实评测显示 `No module named 'sentence_transformers'`。
- `sentence-transformers` 自动安装了 `transformers 5.x` 后，本地模型加载不稳定，出现 tokenizer/processor 识别问题。
- 裸 CrossEncoder rerank 权重过高时，会把强关键词证据推出 Top3，导致 Top3 Recall 从 0.9444 降到 0.8889。
- 当前合成 RAG 数据仍偏精确技术关键词，hash/lexical baseline 的 nDCG@5 高于真实 embedding 策略。

### 怎么修复的

- 改用 `python -m pip install` 安装依赖到当前解释器。
- 在 `requirements.txt` 中增加 `transformers<5.0.0`、`huggingface-hub<1.0`，真实模型可稳定加载。
- 对 RAG 策略重新评测，真实 embedding 最佳权重为 `0.55/0.40/0.05`。
- 将 reranker 改为保守融合，并加入 Top5 recall anchor，最终 `real_embedding_top20_rerank` 达到 Top3 Recall=0.9444、Top5 Recall=1.0、MRR=1.0、nDCG@5=0.9843。
- 保留 hash baseline 作为离线可测对照，但生产策略选择真实 embedding + Top20 rerank。
- pytest 默认设置 `EMBEDDING_PROVIDER=hash`、`RERANKER_ENABLED=false`，保证普通回归测试不依赖模型下载。

### 未修复的问题及原因

- 真实 RAG 评测数据仍是合成数据，不是真实求职者 PDF 和真实招聘 JD；原因是需要人工标注数据才能可靠衡量真实效果。
- Reranker 目前使用通用 MS MARCO CrossEncoder，不是招聘/简历领域模型；原因是领域 reranker 需要额外数据微调。
- ReAct repair loop 还没有真正执行多轮修复；原因是当前先补齐 Tool registry、Plan artifact 和 Guardrail 验证边界。
- MCP 暂未引入；原因是当前工具都在同一 FastAPI 进程内，直接调用更简单，浏览器/邮箱/日历等外部授权工具接入后更适合 MCP 化。

### 下一步怎么做

- 构建真实 PDF 简历和真实 JD 的人工标注 RAG 数据。
- 增加简历定制的 ReAct repair loop，高风险时最多自动修复 2 轮。
- 接入浏览器辅助填写投递表单，并评估是否以 MCP server 形式暴露。
- 增加领域 reranker 或用真实招聘数据微调 reranker。

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
