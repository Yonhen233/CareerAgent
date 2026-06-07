# 开发日志

## 2026-06-07 11:12 +08:00：新增真实 JD Ingest Smoke 并收敛模型缓存边界

### 这次做了什么
- 新增 `run_real_job_ingest_smoke`，从真实岗位源获取 posting 后继续验证 JD parser、SQLite upsert、JD chunk、embedding/reranker provider 和 retrieval probe。
- 新增 `POST /evaluations/real-job-ingest-smoke`，支持 `query`、`location`、`limit` 和重复 `sources` 参数。
- 每条真实岗位结果记录 `parse_success`、`ingest_success`、`chunk_index_success`、`retrieval_probe_hit`、`chunk_count`、`chunk_types`、`required_skill_count` 和 `retrieved_chunk_preview`。
- Summary 新增 `parse_success_rate`、`ingest_success_rate`、`chunk_index_success_rate`、`retrieval_probe_success_rate`、`embedding_provider_counts`、`retrieval_query_embedding_provider_counts`、`reranker_provider_counts`、`embedding_fallback_job_count` 和 `retrieval_fallback_job_count`。
- `EmbeddingService` 和 `RerankerService` 默认将 `HF_HOME`、`SENTENCE_TRANSFORMERS_HOME` 指向项目内 `data/models`，并默认设置 `HF_HUB_DISABLE_SYMLINKS_WARNING=1`。
- 新增单元测试覆盖真实 JD ingest 成功链路和 parser 失败链路，确保 parser 不可用时记录 `parse_error`，不会静默兜底为成功。
- 更新 README、API 和评测文档，说明 source smoke 与 ingest smoke 的边界。

### 发现了什么问题
- 只有 source smoke 还不够：岗位源可达并不代表 JD parser、SQLite 入库、chunk、embedding 和 retrieval probe 都能工作，需要单独的 ingest 层指标。
- 如果复用完整 `JobSearchService.search` 作为 smoke，source error、parser error、embedding error 和 SQLite error 会混在一起，生产排障时很难定位。
- 真实腾讯 JD 解析运行成功，但首次运行暴露出 HuggingFace 依赖会尝试访问/写入默认用户缓存目录；在当前 Windows 环境下，用户目录缓存会出现权限 warning。
- 将缓存迁到项目目录后，权限 warning 消失，但 Windows 不支持 symlink 时仍会出现 HuggingFace symlink 降级 warning；这是缓存策略噪声，不是业务失败。
- 关闭 symlink warning 后，真实运行仍会出现一条 `Transformer cache_dir argument is deprecated` 依赖告警；它来自第三方模型加载链路，不影响本次 ingest 指标。
- 当前真实 JD parser 对腾讯 Agent 实习 JD 只抽出 `Python`、`SQL` 两个 required skill，说明 parser 的技能抽取还需要在更多真实 JD 上校准。

### 怎么修复的
- `real-job-ingest-smoke` 逐 posting 捕获失败阶段：`parse_error` 表示 JD parser 失败，`ingest_error` 表示 SQLite upsert、chunk 或索引失败。
- 成功写入后立刻用 `query_job_chunks` 执行 retrieval probe，证明新写入的 JD chunk 可检索，而不是只看数据库行数。
- 从 `job_chunks.metadata_json.embedding` 和 retrieval metadata 中提取 provider 与 fallback reason，区分 `sentence_transformers/cross_encoder` 真模型路径和 `hash/heuristic` 降级路径。
- 模型缓存默认写入 `data/models/huggingface`，并关闭 symlink warning；用户已经设置 `HF_HOME` 时仍尊重用户配置。
- 真实 ingest smoke 已运行：`query=Agent Development Intern`、`sources=tencent`、`limit=1`、`parse_success_rate=1.0000`、`ingest_success_rate=1.0000`、`chunk_index_success_rate=1.0000`、`retrieval_probe_success_rate=1.0000`、`avg_chunks_per_job=8.0000`、`embedding_provider_counts=sentence_transformers:8`、`reranker_provider_counts=cross_encoder:3`、fallback job count 为 0。

### 未修复的问题及原因
- 真实 ingest smoke 现在默认最多跑少量岗位；原因是每条真实 JD 都会消耗 LLM 和 embedding/reranker 时间，当前先作为发布前 smoke，不做大规模批量评测。
- JD parser 的真实 required skills 抽取还不够细；原因是当前 parser prompt 与 schema 偏通用，下一步需要用真实 JD 标注集评估 parser recall。
- UI 还没有展示 source/ingest smoke 的历史趋势；原因是本轮先把 EvaluationRun 数据写完整，后续再做可视化。
- `Transformer cache_dir argument is deprecated` 告警未处理；原因是当前 CrossEncoder 已优先使用 `model_kwargs` 传递缓存目录，剩余告警可能来自第三方内部兼容层，本轮不为了隐藏 warning 改动模型加载参数。

### 下一步怎么做
- 基于真实腾讯/Lever JD 构建小规模 parser 标注集，评估 required skill recall、responsibility coverage 和 chunk coverage。
- 增加真实 JD parser 回归阈值，避免 parser 把核心技能抽漏却仍然显示 ingest 成功。
- 在评测页面展示 source smoke 与 ingest smoke 的最近状态、失败阶段和 provider/fallback 分布。

## 2026-06-06 21:56 +08:00：新增真实岗位源 Smoke 评测

### 这次做了什么
- 新增 `run_real_job_source_smoke`，并发探测真实岗位源，只记录 source 层健康度，不写入主岗位库，也不调用 LLM 解析 JD。
- 新增 `POST /evaluations/real-job-source-smoke`，支持 `query`、`location`、`limit` 和重复 `sources` 参数。
- 按 source 输出 `status`、`source_reachable`、`has_results`、`result_count`、`latency_ms`、`error` 和 `sample_jobs`。
- 汇总指标新增 `reachable_source_rate`、`result_source_rate`、`total_result_count`、`non_empty_jd_rate`、`apply_url_rate`、`internship_like_rate`、`query_relevance_rate`、`agent_related_rate` 和 `source_errors`。
- 保留 `agent-full-flow` 的可控岗位源回归，不把真实招聘站网络波动计入核心 `pass_rate`。
- 增加 fake source 单元测试，覆盖一个健康 source 和一个异常 source 同时存在时的 source 层指标，并断言 query relevance 与 Agent/AI relevance 指标。
- 更新 README、API 和评测文档，说明真实岗位源 smoke 的定位、接口和指标含义。

### 发现了什么问题
- 之前虽然有腾讯招聘和 Lever 的真实岗位源，但没有独立评测入口；如果直接塞进 Agent full-flow，会让外部网络波动影响核心链路回归。
- `JobSearchService.search` 会进入 JD parse 和入库链路，真实岗位源 smoke 如果复用它，会把 source 可达性、LLM 解析、embedding 和 SQLite 写入混在一起，定位问题不够清楚。
- 招聘源的“可访问”和“有结果”是两件事：source 可能正常返回空结果，也可能网络失败；需要分别记录 `reachable_source_rate` 和 `result_source_rate`。
- pytest 在当前 Windows 环境下会提示 `.pytest_cache` 写入权限警告，单测仍能通过；这属于测试缓存写入问题，不影响业务结果。

### 怎么修复的
- `EvaluationService` 直接通过 `JobSourceRegistry` 并发调用 source `search`，只做轻量岗位质量统计，不进入 JD parse 或职位入库。
- 对每个 source 单独 catch 异常并写入 `case_results_json`，把失败显式记录为 `source_error`，不是静默吞掉。
- Summary 增加 `core_regression_independent=true`，明确该评测不参与核心 Agent full-flow 回归门禁。
- Summary 状态细分为 `completed`、`completed_with_empty_sources`、`completed_with_source_errors` 和 `source_unavailable`，避免把空结果源误看成完全成功。
- 新增测试验证：一个 source 成功、一个 source 报错时，summary 为 `completed_with_source_errors`，且错误、样例岗位和质量指标都保留。
- 新增测试验证：所有 source 可达但部分 source 为空时，summary 为 `completed_with_empty_sources`。
- 真实网络 smoke 已运行：`query=Agent Development Intern`、`sources=tencent,lever`、`status=completed_with_empty_sources`、`reachable_source_rate=1.0000`、`result_source_rate=0.5000`、`total_result_count=8`、`non_empty_jd_rate=1.0000`、`apply_url_rate=1.0000`、`internship_like_rate=1.0000`、`query_relevance_rate=1.0000`、`agent_related_rate=1.0000`、`source_error_count=0`。腾讯返回 8 个岗位，Lever 当前 query 为空。

### 未修复的问题及原因
- 该 smoke 当前只检查 source 层，不验证真实岗位 JD 入库后的 parser/RAG/matcher 质量；原因是本轮先把外部源波动从核心回归中隔离出来。
- 还没有为真实 source 建立长期趋势看板；当前 EvaluationRun 已保存指标，后续可以在 UI 中展示历史 source 稳定性。
- Lever 当前配置 slug 对 `Agent Development Intern` 为空；原因可能是公司 slug 覆盖不足或岗位关键词不匹配，下一步需要扩展更多公司自有招聘源或为不同 source 配置查询策略。

### 下一步怎么做
- 在真实 source smoke 稳定后，增加一个可选的“真实 JD 解析与入库 smoke”，单独评估 parser/RAG，不和 source 可达性混淆。
- 扩展 Lever slug 和更多互联网/AI 大厂自有招聘源，并为中文/英文岗位源配置不同 query。
- 在 UI 评测页展示 source 层指标和最近失败原因。

## 2026-06-06 20:56 +08:00：补齐 Tailor ReAct Repair、Evidence Classifier 与 LLM 断点续跑

### 这次做了什么
- 为 `resume_tailor` 增加 1 轮 ReAct repair loop：初稿先过 Guardrail；如果 `risk_level=high` 或 `passed=false`，自动读取 Guardrail issues、当前草稿和压缩上下文，修复后再次验证。
- 真实 LLM 修复路径新增 `resume_tailor.repair_resume` 调用，要求 strict JSON，只能删除或改写无证据 claim、缺口披露和 `eager to learn` 类表达，不能新增事实。
- 离线测试路径新增 `resume_tailor.heuristic_repair`，用于在无 LLM fallback 测试中验证同一套 Guardrail repair 行为。
- 新增 `EvidenceClassifier`，区分 `shipped_project`、`metric_evidence`、`coursework`、`planned_learning`、`missing_skill_disclosure`、`adjacent_experience`、`generic_skill` 和 `unknown`。
- `MatcherService.retrieve_evidence` 接入 evidence type classification，并在相关性评分里提升交付/量化证据，降低课程、计划学习和缺口披露证据。
- LLM workflow 的 RAG stage trace 增加 `evidence_type` 和 `polarity`，方便检查 Top evidence 是真实交付证据还是噪声。
- `run_llm_workflow_evaluation` 增加 `resume_from_last_completed`，可以从 JSONL trace 中读取连续完成的 case 前缀，再从第一个缺失 case 继续运行。
- `POST /evaluations/llm-workflow` 增加 `resume_from_last_completed` 查询参数；当没有传 `trace_path` 时默认使用 `data/runtime/llm_workflow_trace_latest.jsonl`。
- JSONL trace 事件新增完整 `case_result`，恢复运行后不只知道最终状态，也能保留每个 case 的中间 `stage_trace`；`tailor_resume` stage 会展开 `react_repair` 元数据。
- 补充 evidence classifier、ReAct repair 和 LLM workflow resume 的单元测试；同步更新 README、API、Agent 设计和评测文档。

### 发现了什么问题
- 证据类型如果只按整段句子判断，`Analyzed A/B tests but did not implement ranking models` 这类混合证据会被整体判成负面，导致规则定制简历丢失真实的 `A/B tests` 和 `experiment analysis` 证据。
- 完整 Agent full-flow 评测首次回归时，推荐算法 case 的简历定制关键词覆盖失败，根因就是混合证据被 evidence type 过滤掉。
- Windows 下 pytest 的临时目录在当前环境触发权限问题，`tmp_path` 用例会在 fixture 阶段失败，无法真正测试断点续跑逻辑。
- 旧 JSONL trace 只保留 case 状态和 stage 摘要，恢复运行时不能完整还原 `case_results_json`，会影响后续 summary 和问题排查。
- 真实岗位源 smoke 仍然会受外部网络、接口变化和招聘站波动影响，不适合作为这一步内部链路修复的阻塞项。

### 怎么修复的
- `resume_tailor` 不再按 evidence type 直接丢弃 project/experience 证据，而是统一经过 `_safe_evidence_text`：保留 `but/did not/no` 前面的正向片段，删除负面披露。
- ReAct repair 的修复结果写入 `keyword_alignment.react_repair`，记录是否触发、触发风险、问题类型、修复工具、修复前后风险和二次 Guardrail 是否通过。
- `_load_resumable_llm_results` 只读取 selected cases 的连续完成前缀，遇到第一个缺失 case 就停止，避免跳跑导致评测顺序错乱。
- `_append_llm_trace` 写入完整 `case_result`，同时兼容旧 trace 的简化事件格式。
- 断点续跑测试改用 `data/runtime/test_llm_resume_*.jsonl` 并在 finally 中清理，避开当前 Windows pytest temp 权限问题。
- 完整回归已通过：`pytest -q` 为 `32 passed`；`python -m compileall app tests` 通过。
- 真实 LLM workflow smoke 已用新 key 跑通 3 个 case：先跑 1 个 case 写入 trace，再用 `resume_from_last_completed=true` 跳过已完成前缀补跑到 3 个 case；`resumed_case_count=1`、`completed_rate=1.0000`、`end_to_end_pass_rate=1.0000`、`fit_label_accuracy=1.0000`、`tailor_pass_rate=1.0000`、`guardrail_pass_rate=1.0000`。
- 真实 `resume_tailor.repair_resume` smoke 已触发：故意构造 `Eager to learn MLflow` 高风险初稿，LLM repair 后正文不再包含 `MLflow`，二次 Guardrail 从 `high` 降为 `low` 并通过。

### 未修复的问题及原因
- 还没有加入真实岗位源 smoke；原因是本轮按要求先修复不依赖外部岗位源的三个问题，真实岗位源会在内部链路稳定后作为 source 层指标接入。
- Evidence classifier 目前是规则版，不是训练模型；原因是当前还缺真实人工标注数据，先用规则分类让 RAG 排序和 trace 具备可解释性。
- LLM workflow 的断点续跑目前基于 JSONL trace，不支持直接从历史 `EvaluationRun` 自动恢复；原因是 JSONL 更适合长跑中断的即时恢复，数据库级恢复需要额外设计 checkpoint 选择和冲突处理。
- ReAct repair 当前限制为 1 轮；原因是简历改写场景更需要可控、可审计，下一步如果真实 case 证明 1 轮不足，再扩展到有限状态机或 LangGraph 节点。

### 下一步怎么做
- 在 Agent full-flow 评测里加入真实岗位源 smoke，并把网络失败、空结果、解析失败归入 source 层指标，不影响核心链路回归。
- 用真实 PDF 简历和真实 JD 标注数据校准 evidence type 权重，补充 abandoned prototype、research prototype、internship delivery 等证据类型。
- 把真实 LLM workflow 从 3-case smoke 扩展到 18-case 长跑，重点观察 repair 触发率、长 prompt 耗时和不同难度桶的稳定性。
- 评估是否把 LLM workflow resume 扩展到 `EvaluationRun` checkpoint，并在 UI/API 中展示可恢复进度。

## 2026-06-06 11:25 +08:00：重定义适配标注并补齐 Agent 全流程评测

### 这次做了什么
- 重新定义 `strong_fit`、`partial_fit`、`weak_fit` 标注标准，明确目标岗位、headline、求职意向不算匹配证据，负面证据优先级高于关键词命中。
- 新增 `evals/agent_full_flow_cases.json`，覆盖岗位搜索、匹配排序、简历定制、`quick_apply`、`fit_gate`、Trace 和 Artifact。
- 新增 `POST /evaluations/agent-full-flow`，并在评测服务中使用可控岗位源写入真实 `jobs`、`job_chunks` 和匹配结果。
- `quick_apply` 前新增 `fit_gate`：低于 55 分直接失败，并在 Agent step trace 中记录缺失技能和阻断原因。
- 匹配器改为只用事实 support text 做技能覆盖判断，过滤 guided raw text 中的目标岗位、headline、邮箱等元信息。
- Profile chunk 构建同样过滤目标意向类元信息，避免 RAG 证据被“想做某岗位”污染。
- 增强匹配器的负面证据识别，覆盖 `no/not/without/lacks/missing/did not build/coursework/read articles` 等表达。
- 简历定制 prompt 新增硬规则：缺失 JD 要求只能写进 `keyword_alignment.missing/notes`，不能以 “eager to learn” 等形式写进简历正文。
- Guardrail 增加“缺失技能正文披露”和技能别名识别，能区分 `A/B testing` 与 `A/B tests/experiment analysis`、`model evaluation` 与 `evaluation dashboards`。
- forbidden claim 检查改为否定上下文感知，避免把 “did not implement ranking models” 误判成编造 ranking model。
- 规则 fallback 简历定制在写入 `Selected Evidence` 前会清洗负面证据句，保留 “A/B tests” 这类正向片段，丢弃 “did not implement ranking” 和 “No MLflow”。
- 匹配器负面词从裸 `learning` 收紧为 `learning about/currently learning`，避免误伤 `Machine Learning`。
- 更新 README、API、架构、Agent 设计和评测文档。

### 发现了什么问题
- 完整 Agent 评测第一次暴露出目标意向污染：候选人写了 `Target roles: Agent Development Intern`，旧匹配逻辑会把 `Agent` 当成事实技能。
- `No MLflow or feature store experience` 这类句子会被旧关键词匹配误判成覆盖 MLflow/Feature Store。
- 推荐算法和 ML 平台弱匹配 case 不应允许一键投递；更合理的产品行为是允许分析或定制，但 `quick_apply` 必须被门禁拦住。
- 重复运行 Agent full-flow 评测时，评测岗位 external_id 会撞 SQLite 唯一约束。
- 真实 LLM trace 发现，旧 forbidden claim 检查只做 substring，会把否定披露误判成违规。
- 真实 LLM trace 还发现，Guardrail 如果没有技能别名，会把真实证据里的 `A/B tests`、`evaluation dashboards` 误判为不支持 `A/B testing`、`model evaluation`。
- 完整 pytest 首次回归时，增强 Guardrail 把离线 fallback 生成的负面证据原文判为高风险，说明 fallback 也必须遵守与 LLM prompt 相同的简历正文约束。
- 检查规则时发现裸 `learning` 会误伤 `Machine Learning`，这是求职场景里非常常见的技术词边界。

### 怎么修复的
- `MatcherService._support_text` 改为事实字段优先，raw text 只保留非元信息行；`ResumeTextSplitter.build_resume_chunks` 也做同样清洗。
- 在句子级别判断技能是否被正向或中性证据支持；如果技能只出现在负面句中，就进入 missing。
- Agent full-flow evaluation 每次运行生成唯一 namespace，原始岗位 ID 保存在 `eval_external_id`，既可重复运行又可稳定断言。
- 把推荐算法和 ML 平台边界 case 重标为弱匹配投递阻断样例，测试要求 `fit_gate_block_count >= 3`。
- Guardrail 新增缺失技能正文披露检查和技能别名表；`eager to learn MLflow` 不再算覆盖 MLflow，`Machine Learning` 也不会被误判为“正在学习”。
- `_heuristic_tailor` 新增安全证据清洗，避免在离线测试模式下把 RAG 原文中的负面缺口直接贴入简历正文。
- 增加 `Machine Learning` 边界测试，保证它不会被当作负面证据；`currently learning RAG` 仍会被识别为负面。
- 真实 LLM 5-case smoke 用新 key 重跑通过：`completed_rate=1.0000`、`end_to_end_pass_rate=1.0000`、`fit_label_accuracy=1.0000`、`tailor_pass_rate=1.0000`、`guardrail_pass_rate=1.0000`。
- 离线 Agent full-flow 评测通过：`pass_rate=1.0000`、`top_job_accuracy=1.0000`、`quick_apply_pass_rate=1.0000`、`fit_gate_block_count=3`、`trace_pass_rate=1.0000`。
- 完整回归测试通过：`28 passed`。

### 未修复的问题及原因
- 还没有把真实招聘网站抓取纳入 Agent full-flow 评测；原因是外部岗位源会波动，当前全链路回归先用可控岗位源保证可重复。
- 简历定制还没有实现 ReAct repair loop；原因是本轮先把生成约束和 Guardrail 规则补齐，下一步再把高风险失败自动修复成 1-2 轮可追踪循环。
- Guardrail 的技能别名仍是规则表，不是训练过的 evidence classifier；原因是当前样例规模还不足以支撑领域分类器训练，但已经把真实 trace 暴露的别名补入回归。

### 下一步怎么做
- 为 `resume_tailor` 增加 ReAct repair loop：Guardrail 高风险时自动读取问题、收缩上下文并重写一次。
- 在 Agent full-flow 评测里加入真实岗位源 smoke，只把网络波动作为 source 层指标，不影响核心链路回归。
- 增加 evidence type classifier，区分 shipped project、metric evidence、coursework、planned learning 和 missing-skill disclosure。
- 给 LLM workflow 增加 resume-from-last-completed，支持长跑中断后继续。

## 2026-06-06 09:25 +08:00：收敛上下文治理并补 LLM 评测逐 Case Trace

### 这次做了什么

- 将上下文治理从独立 `context_manager` subagent 改回 LLM 调用前的 runtime policy。
- 从 Skill 注册表中移除 `progressive_disclosure`，保留 `fit_assessment`、`resume_tailoring` 这类真正的任务能力。
- 将 `ContextCompressor` 从过重的 L4/L5/L6 多阶段压缩，收敛为 Profile 摘要、JD 摘要、Top evidence 和一次 prompt packet 总预算检查。
- `AgentPlanner.context_policy` 保留渐进式披露和预算策略，但明确它不是独立 subagent 或 skill。
- LLM workflow 评测改为启动时先创建 `EvaluationRun`，每完成一个 case 就更新 `summary_json` 和 `case_results_json`。
- 每个 LLM workflow case 新增 `stage_trace`，记录 resume parse、JD parse、RAG、fit judge、tailor 和 Guardrail 的中间摘要。
- `run_llm_workflow_evaluation` 新增 `case_limit`、`case_indexes` 和 `trace_path`，API 支持 `POST /evaluations/llm-workflow?case_limit=3`。
- 更新 README、API、架构、Agent 设计、开发说明和评测文档。

### 发现了什么问题

- 6 级上下文压缩确实偏过度，容易显得像为了架构复杂度而复杂。
- 单独用一个 `context_manager` subagent 管压缩也不够主流；上下文管理更适合作为 agent runtime/memory/prompt assembly policy，而不是一个会独立推理的 subagent。
- 之前真实 18-case 测试超时后没有结果，是因为评测服务把 case result 放在内存 list 中，最后才创建 `EvaluationRun`；命令被杀时自然没有 summary。
- 新增逐 case trace 后，真实测试暴露出 strong case 的 tailor `prompt_packet` 曾超过 9000 字符预算，说明只看最终 pass 会漏掉中间质量问题。
- 最新真实 3-case 测试中，`ml_candidate_partial_agent_role` 仍被模型判为 `weak_fit`，但 trace 显示 RAG 已检到 “did not build an agent system”，所以这是 partial/weak 标注边界问题，不是上下文丢失。

### 怎么修复的

- 移除 `context_manager` subagent 和 `progressive_disclosure` skill，把渐进式披露放到执行计划的 `context_policy`。
- 将压缩策略改为 `progressive_disclosure_budgeted_packet`，元数据只保留 Profile、JD、Evidence 和 Prompt Packet 四个层面。
- 压缩 evidence metadata，只保留 rank/score/rerank provider/final score 等排序调试必要字段，避免 metadata 把 prompt 撑大。
- 真实 LLM workflow 每跑完一个 case 就落库，并可写入 `data/runtime/llm_workflow_trace_latest.jsonl`。
- 重新跑真实 3-case LLM 测试：`completed_rate=1.0000`、`end_to_end_pass_rate=0.6667`、`fit_label_accuracy=0.6667`、`tailor_pass_rate=1.0000`、`guardrail_pass_rate=1.0000`。
- trace 确认两个 tailor case 的 prompt packet 都在预算内：strong case 6071 chars，hard partial case 5516 chars。

### 未修复的问题及原因

- hard partial/weak 边界仍未修复；原因是当前样例把“有 Python/Transformers/Model Evaluation，但明确没有 Agent/RAG 交付”的候选人标为 `partial_fit`，而模型按严格岗位要求判 `weak_fit` 也有合理性，需要重新定义标注标准。
- LLM workflow 还没有真正的断点续跑；现在已经逐 case 落库和写 JSONL，但如果要从某个 case 继续，还需要增加 resume-from-last-completed 参数。
- API 目前只暴露 `case_limit`，没有暴露 `case_indexes`；原因是公开 API 先保持简单，开发脚本可直接调用 service 跑指定 case。

### 下一步怎么做

- 为 LLM workflow 增加 resume mode，从 trace 或 `EvaluationRun` 中找到最后完成 case 后继续。
- 重新审视 partial/weak 人工标注，增加“相邻能力但无交付”的边界样例。
- 在 summary 中加入 prompt packet `within_budget_rate`，让预算问题变成量化指标。
- 后续 ReAct repair loop 只在 Guardrail 高风险或证据不足时启用，并按需请求 deferred context。

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
