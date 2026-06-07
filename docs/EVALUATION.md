# 量化评测方案

CareerAgent 的评测分为八类：

- 基础匹配评测：Profile/JD 匹配质量。
- PDF Chunk 策略评测：不同 PDF 切分方案对证据召回的影响。
- RAG 策略评测：不同检索排序策略对证据召回的影响。
- JD Parser 评测：衡量真实 JD 结构化解析质量，避免核心技能漏抽或把可选技能误写成 required。
- LLM 实景流程评测：真实调用 LLM 判断岗位适配度并按 JD 改写简历。
- Agent 全流程评测：覆盖岗位搜索、匹配排序、简历定制、一键投递门禁、Trace 和 Artifact。
- 真实岗位源 Smoke：只评测招聘源可达性、结果数量和岗位质量，不参与核心 Agent 回归 pass rate。
- 真实 JD Ingest Smoke：只评测真实 JD 解析、SQLite 入库、JD chunk、embedding/reranker 和检索 probe，不参与核心 Agent 回归 pass rate。

## 数据集

### 基础匹配数据

```text
evals/sample_cases.json
```

当前 3 个样例，用于快速回归。

### PDF Chunk 策略数据

```text
evals/pdf_chunk_cases.json
```

规模：

- 96 个合成 PDF 简历案例。
- 每个案例 5 页。
- 每个案例 6 个查询。
- 共 576 条 PDF chunk 查询。

数据设计：

- 覆盖 Agent/RAG、LLM Eval、后端平台、前端工具、ML 平台、数据工程等候选人类型。
- 每页包含目标证据、相邻岗位项目、课程噪声、计划学习、废弃 prototype 和重复技术词。
- 查询要求同时命中关键词、页码和上下文关键词。
- 查询按 `easy`、`medium`、`hard`、`adversarial` 分桶。
- 噪声类型包括 `coursework_vs_shipped`、`hard_negative_project_same_page`、`planned_learning_negative`、`cross_page_distractor`、`late_page_appendix` 等。

### RAG 策略数据

```text
evals/rag_cases.json
```

规模：

- 180 个 RAG 检索案例。
- 每个案例 12 个候选证据 chunk。
- 每个案例 4 个期望命中的 evidence chunk。
- 共 2160 个候选 chunk。

数据设计：

- 覆盖 12 类技术岗位：Agent/RAG、LLM Eval、后端平台、前端工具、ML 平台、数据工程、DevOps、AI 安全、移动 AI、推荐算法、产品分析、计算机视觉。
- 一部分查询使用精确技术关键词，一部分使用同义表达，例如 `retrieval augmented generation` -> `RAG`。
- 每个 case 包含 hard negative、planned learning、coursework、adjacent domain、generic tools、rejected prototype、long noise 等噪声 chunk。
- 按 `easy`、`medium`、`hard`、`adversarial` 分桶统计。

### JD Parser 评测数据

```text
evals/jd_parser_cases.json
```

规模：

- 30 个 JD 解析 case。
- 覆盖 `easy`、`medium`、`hard`、`adversarial` 四类难度。
- 覆盖 `preferred_skill_noise`、`negative_requirement`、`synonym_alias`、`chinese_jd`、`rag_stack`、`platform_stack`、`metric_evidence`、`agent_framework_stack` 等噪声画像。

数据设计：

- 每个 case 都包含原始 JD、岗位标题、公司/地点、期望 job_type、期望 required skills、期望关键词、期望不应进入 required 的技能。
- 中英混合覆盖 Agent/RAG、LLM Eval、Prompt Security、ML Platform、Backend、Frontend、Data Engineering、Recommendation、MLOps、Computer Vision 等真实岗位类型。
- 刻意加入 `Preferred`、`Nice to have`、`加分项`、`No prior X required`、`不要求 X`、同义词和相邻领域噪声。
- 指标同时检查 required skill recall、keyword hit rate、job_type accuracy、responsibility/qualification 最小覆盖和 absent required skill violation。

### LLM 实景流程数据

```text
evals/llm_workflow_cases.json
```

规模：

- 18 个端到端 LLM 流程案例，不再只评测 3 条岗位适配标签。
- 13 个案例会进入简历定制流程。
- 覆盖 `strong_fit`、`partial_fit`、`weak_fit` 三类标签。
- 覆盖 `easy`、`medium`、`hard`、`adversarial` 四类难度。

数据设计：

- 覆盖 Agent/RAG、LLM Eval、后端、前端、数据工程、ML、AI 安全、移动 AI、推荐、分析、DevOps、CV 等岗位。
- 每个 case 包含原始简历文本、期望 Profile 技能、期望 Profile 关键词、JD、期望 JD 技能、期望 fit label、期望 fit score 区间、定制简历关键词和禁止编造 claim。
- hard/adversarial case 明确加入 `did not build`、`No shipped project`、相邻岗位经验等反例，测试模型是否把“读过/计划学习/课程提到”误判成真实交付经验。

### Agent 全流程数据

```text
evals/agent_full_flow_cases.json
```

规模：

- 6 个端到端 Agent 流程案例。
- 覆盖 Agent、前端、数据工程、推荐算法、ML 平台和弱匹配 Agent 候选人。
- 每个 case 都使用可控岗位源写入真实 `jobs`、`job_chunks` 和匹配结果，避免外部招聘站波动影响回归。
- 强匹配 case 会跑通 `find_jobs_for_profile`、`tailor_resume_for_job` 和 `quick_apply`。
- 弱匹配 case 会允许定制简历或检索分析，但 `quick_apply` 必须被 `fit_gate` 阻断。

数据设计：

- 每个 case 包含 guided profile、候选岗位列表、期望 Top1 岗位、期望分数区间、是否运行 tailor、是否运行 quick_apply、是否期望投递门禁拦截。
- 评测会检查 Top1 岗位准确率、分数门禁、tailor Guardrail、quick apply 行为、Agent step trace 和 execution plan artifact。
- 岗位 external_id 每次评测运行都会带唯一 namespace，重复运行不会撞 SQLite 唯一约束；原始岗位 ID 保存在 `eval_external_id` 里用于断言。

## 标注标准

### `strong_fit`

- 分数区间：85-100。
- 候选人已经在项目、实习或工作中交付过岗位核心能力的大部分要求。
- 允许少量工具名缺失，但必须有可追溯证据，例如 shipped project、服务/API、评测指标、部署或可量化结果。
- 目标岗位、headline、求职意向不能作为匹配证据。

### `partial_fit`

- 分数区间：55-84。
- 候选人至少交付过一个与岗位核心任务直接相关或高度相邻的完整产物，但仍缺少部分核心工具、平台经验或业务场景。
- 可以进入简历定制和人工评估；是否一键投递由 `fit_gate` 分数、缺口和风险共同决定。
- 单纯“学过/读过/计划学习/课程提到”不能标为 `partial_fit`。

### `weak_fit`

- 分数区间：0-54。
- 出现以下任一情况即归入弱匹配：只有目标意向或 headline；只有课程、阅读、计划学习；明确写了 `did not build`、`No shipped project`、`No MLflow` 等核心否定证据；只有相邻岗位经验但缺少岗位核心交付。
- `weak_fit` 不代表候选人完全没有潜力，而是当前证据不足以直接投递。`quick_apply` 必须被 `fit_gate` 阻断，并在 Agent trace 中保留阻断原因。
- 负面证据优先级高于关键词重合，不能因为同一句话里出现技术词就算作已掌握。

## 运行方式

```bash
pytest -q
```

API：

```http
POST /evaluations/run
POST /evaluations/pdf-chunk-strategies
POST /evaluations/rag-strategies
POST /evaluations/agent-full-flow
POST /evaluations/jd-parser
POST /evaluations/real-job-source-smoke
POST /evaluations/real-job-ingest-smoke
POST /evaluations/llm-workflow
GET /evaluations/results
```

## JD Parser 评测

运行：

```http
POST /evaluations/jd-parser
```

最近一次离线回归结果：

| 指标 | 结果 |
| --- | ---: |
| case_count | 30 |
| completed_rate | 1.0000 |
| pass_rate | 1.0000 |
| avg_required_skill_recall | 0.9972 |
| avg_keyword_hit_rate | 1.0000 |
| job_type_accuracy | 1.0000 |
| responsibility_min_pass_rate | 1.0000 |
| qualification_min_pass_rate | 1.0000 |
| absent_required_skill_violation_count | 0 |

说明：

- 这次结果运行在测试环境的 `heuristic_fallback` parser mode，用于离线、可重复地验证规则路径；生产配置中 LLM 可用时仍会调用真实 JD parser LLM 链路。
- 本轮评测先暴露了两个问题：`Tool Calling`、`A/B Testing` 被后续负向句误判为不要求；`internal tools` 被 `intern` 子串误判成实习岗位。
- 修复后，负向语境只在当前行/句内判断，preferred 技能单独抽取；job_type 使用词边界匹配 `intern`，并把 `location=Remote` 和常规工程岗位标题纳入推断。
- 该评测不会替代真实 JD ingest smoke；它负责 parser 质量，ingest smoke 负责 source posting 进入 SQLite、chunk、embedding/reranker 和 retrieval probe 的链路健康。

## PDF Chunk 策略评测

对比策略：

- `fixed_window_450_overlap80`
- `paragraph_page_900_overlap160`
- `paragraph_page_1200_overlap200`
- `section_aware_700_overlap120`

最近一次评测结果：

| 策略 | Top3 关键词 | Top3 页码 | Top3 上下文 | Top1 平均字符 | 平均 Chunk 数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| fixed_window_450_overlap80 | 0.8472 | 0.7951 | 0.6771 | 449.89 | 20.96 |
| paragraph_page_900_overlap160 | 0.9479 | 0.8299 | 0.7760 | 772.77 | 10.00 |
| paragraph_page_1200_overlap200 | 0.9358 | 0.8403 | 0.8316 | 1054.09 | 9.00 |
| section_aware_700_overlap120 | 0.9479 | 0.8281 | 0.7865 | 534.33 | 16.57 |

选择：

```text
paragraph_page_900_overlap160
```

理由：

- 900 窗口与 section-aware 的 Top3 关键词命中率并列最高，为 0.9479。
- 900 窗口 Top3 页码命中率 0.8299，略高于 section-aware 的 0.8281。
- 1200 窗口上下文命中率最高，但平均 Top1 长度超过 1054 字符，更容易把 hard negative 和课程噪声一起带入上下文。
- section-aware 上下文表现略高于 900，但平均 chunk 数 16.57，检索和 rerank 成本更高。
- 因此当前选择 `paragraph_page_900_overlap160`，作为上下文保留、噪声控制和检索成本之间的折中。

暴露的问题：

- `coursework_vs_shipped` 噪声最难。900 窗口在这个噪声下 Top3 context hit 只有 0.0521。
- 说明仅靠 chunk 切分和向量/词法检索，仍难区分“课程里提到某技术”和“真实项目里交付某技术”。
- 当前已经在 RAG ranking 中加入 `EvidenceClassifier`：`shipped_project`、`metric_evidence` 会被加权，`coursework`、`planned_learning`、`missing_skill_disclosure` 会被降权。它解决的是“检索到了关键词，但证据性质不可靠”的排序问题，而不是替代 PDF chunk 策略本身。

## RAG 策略评测

对比策略：

- `hash_vector_only`
- `hash_lexical_only`
- `hash_lexical_80_vector_15_type_5`
- `real_embedding_vector_only`
- `real_embedding_70_vector_30_lexical`
- `real_embedding_55_vector_40_lexical_5_type`
- `real_embedding_45_vector_50_lexical_5_type`
- `real_embedding_top20_rerank`

真实模型：

- Embedding：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Reranker：`cross-encoder/ms-marco-MiniLM-L-6-v2`
- Rerank 候选：一阶段 Top20。
- Rerank 保护策略：Top5 作为召回锚点保持一阶段顺序，第 6 到第 20 个候选在分数带内二阶段排序。

最近一次评测结果：

| 策略 | Embedding | Reranker | Top1 Acc | Top3 Recall | Top5 Recall | MRR | nDCG@5 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| hash_vector_only | hash | none | 0.7500 | 0.4792 | 0.6875 | 0.8750 | 0.6734 |
| hash_lexical_only | hash | none | 0.7500 | 0.4792 | 0.7500 | 0.8333 | 0.7296 |
| hash_lexical_80_vector_15_type_5 | hash | none | 1.0000 | 0.5625 | 0.7292 | 1.0000 | 0.7748 |
| real_embedding_vector_only | sentence-transformers | none | 0.8333 | 0.4681 | 0.6014 | 0.9167 | 0.6415 |
| real_embedding_70_vector_30_lexical | sentence-transformers | none | 1.0000 | 0.4694 | 0.6403 | 1.0000 | 0.6968 |
| real_embedding_55_vector_40_lexical_5_type | sentence-transformers | none | 1.0000 | 0.5958 | 0.7292 | 1.0000 | 0.7830 |
| real_embedding_45_vector_50_lexical_5_type | sentence-transformers | none | 1.0000 | 0.6125 | 0.7292 | 1.0000 | 0.7862 |
| real_embedding_top20_rerank | sentence-transformers | cross-encoder | 1.0000 | 0.6125 | 0.7292 | 1.0000 | 0.7862 |

选择：

```text
real_embedding_top20_rerank
```

理由：

- 强噪声评测后，真实 embedding 策略中 `vector=0.45 / lexical=0.50 / type=0.05` 达到最高 Top3 Recall。
- `real_embedding_top20_rerank` 在 Top5 anchor 保护下与最佳一阶段真实 embedding 策略持平，同时保留 CrossEncoder 对 Top20 尾部证据的二阶段排序能力。
- hash baseline 的表现不再稳定：`hash_lexical_80_vector_15_type_5` Top3 Recall=0.5625，低于真实 embedding + rerank 的 0.6125。
- 选择真实 embedding 主路径更贴近真实 JD 和简历语义表达，例如中英文混写、同义表达、职责描述不直接出现技术名的情况。

分桶结果：

| 难度 | Top1 Acc | Top3 Recall | Top5 Recall | MRR | nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| easy | 1.0000 | 0.5000 | 0.7500 | 1.0000 | 0.7650 |
| medium | 1.0000 | 0.7500 | 0.7500 | 1.0000 | 0.8319 |
| hard | 1.0000 | 0.5833 | 0.7500 | 1.0000 | 0.7968 |
| adversarial | 1.0000 | 0.6167 | 0.6667 | 1.0000 | 0.7512 |

调试发现：

- 第一轮真实评测中，裸 CrossEncoder 权重过高，会把强关键词证据推出 Top3，Top3 Recall 从 0.9444 降到 0.8889。
- 修复方式是采用保守融合：一阶段分数为主，rerank 分数为辅，并设置 Top5 recall anchor。
- 依赖调试中发现 `transformers 5.x` 与当前 SentenceTransformers 加载不稳定，已在 `requirements.txt` 中约束 `transformers<5.0.0`、`huggingface-hub<1.0`。
- 强噪声数据集把 Top3 Recall 从原来的 0.9444 拉低到 0.6125，这是有意为之：新数据更接近真实简历里的课程噪声、计划学习和相邻项目干扰。
- 后续优化重点不再是继续调 embedding 权重。本轮已经加入规则版 evidence classifier 来区分“真实交付证据”和“仅提及/计划学习/缺口披露”，下一步更适合用真实人工标注数据校准规则权重，或增加 LLM verifier 做抽检。

## RAG 向量库选型

当前选择：

```text
SQLite 权威存储 + Chroma 可选向量库镜像
```

理由：

- SQLite 保存 Profile、JD、chunk、metadata、embedding 和评测结果，是可审计的权威存储。
- Chroma 是常见本地向量库，API 简单，适合展示真实 RAG 工程组件。
- Chroma 作为镜像而不是唯一存储，避免业务元数据被锁死在向量库里，也避免向量库不可用时主流程崩溃。
- 与 FAISS 相比，Chroma 更方便持久化和按 collection 管理 Profile/JD chunk。
- 与 Qdrant、Milvus 相比，Chroma 不需要额外服务，更适合个人简历项目和本地面试演示。
- 后续如果需要规模化，可替换为 Qdrant、Milvus、pgvector 或云向量库。

## Agent 全流程评测

接口：

```http
POST /evaluations/agent-full-flow
```

评测内容：

- 通过 guided profile 创建候选人档案和简历 chunk。
- 使用可控岗位源写入岗位、JD chunk 和向量索引。
- 运行 `find_jobs_for_profile`，检查 Top1 岗位和匹配分数区间。
- 对需要定制的 case 运行 `tailor_resume_for_job`，检查 Guardrail 和关键词覆盖。
- 对需要投递的 case 运行 `quick_apply`；弱匹配 case 期望被 `fit_gate` 阻断。
- 检查每个 Agent run 是否生成 `execution_plan` artifact，并记录完整 step trace。

最新离线全流程结果：

| 指标 | 结果 |
| --- | ---: |
| case_count | 6 |
| pass_rate | 1.0000 |
| completed_rate | 1.0000 |
| top_job_accuracy | 1.0000 |
| score_gate_accuracy | 1.0000 |
| tailor_pass_rate | 1.0000 |
| quick_apply_pass_rate | 1.0000 |
| fit_gate_block_count | 3 |
| trace_pass_rate | 1.0000 |
| artifact_pass_rate | 1.0000 |
| avg_top_job_score | 57.3650 |
| avg_ranking_margin | 29.8817 |

本轮暴露并修复的问题：

- Guided profile 的 `raw_text` 会包含 headline 和 `Target roles`，如果直接参与匹配，会把“想做某岗位”误判成“做过某岗位”。已改为 support text 和 profile chunk 都过滤目标意向、headline、邮箱等元信息。
- `No MLflow or feature store experience` 这类否定证据必须覆盖关键词命中。匹配器现在在句子级识别 `no/not/without/lacks/missing/did not build/coursework/read articles` 等负面证据。
- 重复运行评测时，评测岗位 external_id 曾经撞 SQLite 唯一约束。现在每次 Agent full-flow evaluation 都会生成唯一 namespace，原始 ID 仍保存在 `eval_external_id`。
- 推荐算法和 ML 平台两个弱匹配 case 被重新标注为“可分析/可定制，但不可一键投递”，更符合真实求职风险控制。

## 真实岗位源 Smoke

接口：

```http
POST /evaluations/real-job-source-smoke
POST /evaluations/real-job-source-smoke?query=Agent%20Development%20Intern&limit=8&sources=tencent&sources=lever
```

评测内容：

- 并发访问真实岗位源，例如腾讯招聘公开接口和 Lever 公开岗位 API。
- 对每个 source 单独记录 `status`、`source_reachable`、`result_count`、`latency_ms`、`error` 和 `sample_jobs`。
- 不调用 LLM 解析 JD，不写入主岗位库，只评估 source 层健康度，避免 LLM、embedding 或数据库状态掩盖招聘源问题。
- 网络失败、招聘站接口变化、空结果都会进入 `source_errors` 或 `source_unavailable`，不污染 `agent_full_flow` 的核心 pass rate。
- 如果所有 source 可达但部分 source 对当前 query 为空，summary 状态为 `completed_with_empty_sources`，并通过 `result_source_rate` 暴露空源比例。

核心指标：

| 指标 | 含义 |
| --- | --- |
| `reachable_source_rate` | 可访问的岗位源比例。 |
| `result_source_rate` | 返回至少一个岗位的岗位源比例。 |
| `total_result_count` | 本次 smoke 返回的岗位总数。 |
| `non_empty_jd_rate` | 返回岗位里 JD 文本非空的比例。 |
| `apply_url_rate` | 返回岗位里包含投递链接的比例。 |
| `internship_like_rate` | 返回岗位里标题、类型或 JD 命中 intern/实习/校招等信号的比例。 |
| `query_relevance_rate` | 返回岗位里标题、类型或 JD 命中当前 query token 的比例。 |
| `agent_related_rate` | 返回岗位里命中 Agent/RAG/LLM/AI/大模型/智能体等信号的比例。 |
| `source_error_count` | 发生异常的岗位源数量。 |

该评测的定位是 source 层真实环境探针：它可以暴露外部招聘站波动，但不会替代可控岗位源下的 Agent full-flow 回归。

最新真实 smoke：

| 指标 | 结果 |
| --- | ---: |
| query | `Agent Development Intern` |
| sources | `tencent, lever` |
| status | `completed_with_empty_sources` |
| reachable_source_rate | 1.0000 |
| result_source_rate | 0.5000 |
| total_result_count | 8 |
| non_empty_jd_rate | 1.0000 |
| apply_url_rate | 1.0000 |
| internship_like_rate | 1.0000 |
| query_relevance_rate | 1.0000 |
| agent_related_rate | 1.0000 |
| source_error_count | 0 |

本次结果说明腾讯招聘公开接口当前可达并返回 8 个实习相关岗位，Lever API 可达但当前配置的公司 slug 对该 query 没有返回岗位。后续优化应优先扩展 Lever slug 或加入更多大厂自有招聘源，而不是把空结果当作 Agent 核心失败。

## 真实 JD Ingest Smoke

接口：

```http
POST /evaluations/real-job-ingest-smoke
POST /evaluations/real-job-ingest-smoke?query=Agent%20Development%20Intern&limit=1&sources=tencent
```

评测内容：

- 先访问真实岗位源获取 posting，再对每条 posting 跑 JD parser。
- 将解析后的 JD upsert 到 `jobs`，切分并写入 `job_chunks`。
- 对写入后的 JD chunk 执行一次 retrieval probe，确认索引可检索。
- 每条岗位单独记录 `parse_error`、`ingest_error`、`chunk_count`、`chunk_types`、`required_skill_count`、`retrieved_chunk_preview`。
- 记录 `embedding_provider_counts`、`retrieval_query_embedding_provider_counts`、`reranker_provider_counts` 和 fallback job count，用于区分真实模型链路与 hash/heuristic 降级链路。

核心指标：

| 指标 | 含义 |
| --- | --- |
| `parse_success_rate` | JD 结构化解析成功比例。 |
| `ingest_success_rate` | 解析后写入 `jobs` 成功比例。 |
| `chunk_index_success_rate` | 写入 `job_chunks` 且 chunk 数量非零的比例。 |
| `retrieval_probe_success_rate` | 对新写入 JD 执行检索 probe 能返回结果的比例。 |
| `avg_chunks_per_job` | 每个真实 JD 平均切分出的 chunk 数。 |
| `avg_required_skill_count` | 每个真实 JD 解析出的 required skills 平均数量。 |
| `embedding_provider_counts` | JD chunk 写入时使用的 embedding provider 分布。 |
| `reranker_provider_counts` | retrieval probe 使用的 reranker provider 分布。 |
| `embedding_fallback_job_count` | 写入阶段出现 embedding fallback 的岗位数。 |
| `retrieval_fallback_job_count` | 检索阶段出现 embedding/reranker fallback 的岗位数。 |

最新真实 ingest smoke：

| 指标 | 结果 |
| --- | ---: |
| query | `Agent Development Intern` |
| sources | `tencent` |
| limit | 1 |
| status | `completed` |
| parse_success_rate | 1.0000 |
| ingest_success_rate | 1.0000 |
| chunk_index_success_rate | 1.0000 |
| retrieval_probe_success_rate | 1.0000 |
| avg_chunks_per_job | 8.0000 |
| avg_required_skill_count | 2.0000 |
| avg_keyword_count | 12.0000 |
| embedding_provider_counts | `sentence_transformers: 8` |
| retrieval_query_embedding_provider_counts | `sentence_transformers: 3` |
| reranker_provider_counts | `cross_encoder: 3` |
| embedding_fallback_job_count | 0 |
| retrieval_fallback_job_count | 0 |

本次真实运行说明：腾讯真实 JD 可以被 LLM parser 解析并成功写入 SQLite/`job_chunks`，检索 probe 可以召回新写入 chunk。运行时发现 HuggingFace 在 Windows 上会因为默认缓存目录和 symlink 能力产生噪声 warning；代码已将 `HF_HOME` 与 `SENTENCE_TRANSFORMERS_HOME` 默认指向项目内 `data/models`，并默认关闭 symlink warning，避免用户目录权限影响发布环境。当前仍可能出现 `Transformer cache_dir argument is deprecated` 的第三方兼容层告警，不影响本次 ingest 指标。

## LLM 实景流程评测

接口：

```http
POST /evaluations/llm-workflow
```

评测内容：

- 真实调用 LLM 解析简历。
- 真实调用 LLM 解析 JD。
- 基于 SQLite chunk 和 RAG 证据做岗位匹配与 evidence retrieval。
- 真实调用 LLM 判断岗位适配度，要求返回 strict JSON。
- 对标记为 `run_tailor=true` 的案例真实调用简历定制流程。
- 使用 Guardrail 验证是否引入未支持数字、过多新 claim、禁止 claim。
- 不做静默 fallback；失败 case 记录 `failed_stage` 和异常类型，LLM 调用日志记录 prompt/response/error trace。
- `EvaluationRun` 会在评测开始时创建，之后每完成一个 case 就更新 `summary_json` 和 `case_results_json`。
- 每个 case 带 `stage_trace`，记录 resume parse、JD parse、match/RAG、fit judge、tailor 的中间摘要。
- 开发脚本可传 `trace_path` 写 JSONL，即使长跑被中断，也能看到已经完成 case 的中间结果。
- `resume_from_last_completed=true` 时，评测会从 JSONL trace 中读取连续完成的 case 前缀，并从第一个缺失 case 继续跑；新 trace 事件会写入完整 `case_result`，因此恢复后仍能保留每个阶段的中间结果。
- `tailor_resume` stage 会记录 `react_repair` 元数据；如果触发修复，可以看到触发风险、问题类型、使用工具、修复后风险和二次 Guardrail 是否通过。

量化指标：

| 指标 | 含义 |
| --- | --- |
| `completed_rate` | 端到端流程完成率。 |
| `end_to_end_pass_rate` | 全流程验收通过率。 |
| `resume_parse_success_rate` | 简历结构化解析成功率。 |
| `avg_profile_skill_recall` | 结构化 Profile 对期望技能的召回。 |
| `jd_parse_success_rate` | JD 结构化解析成功率。 |
| `avg_jd_skill_recall` | 结构化 JD 对期望技能的召回。 |
| `fit_label_accuracy` | LLM 适配度标签准确率。 |
| `fit_score_in_range_rate` | LLM 分数是否落入人工期望区间。 |
| `avg_fit_score_range_error` | 分数超出期望区间时的平均偏差。 |
| `avg_matcher_evidence_hit_rate` | 匹配/RAG 证据是否覆盖期望关键词。 |
| `tailor_success_rate` | 简历定制调用成功率。 |
| `tailor_pass_rate` | 定制简历同时通过 Guardrail、关键词覆盖和禁止 claim 检查的比例。 |
| `guardrail_pass_rate` | Guardrail 通过率。 |
| `forbidden_claim_free_rate` | 没有出现禁止 claim 的比例。 |
| `context_compression` | fit judge 与 tailor 阶段的压缩上下文数量、平均压缩率和保留证据数。 |
| `difficulty_breakdown` | 按 easy/medium/hard/adversarial 分桶的指标。 |

历史 18-case 全量基线结果：

| 指标 | 结果 |
| --- | ---: |
| case_count | 18 |
| completed_rate | 0.9444 |
| end_to_end_pass_rate | 0.8889 |
| resume_parse_success_rate | 1.0000 |
| jd_parse_success_rate | 1.0000 |
| fit_judge_success_rate | 1.0000 |
| fit_label_accuracy | 0.9444 |
| fit_score_in_range_rate | 0.9444 |
| avg_fit_score_range_error | 0.8333 |
| avg_matcher_evidence_hit_rate | 1.0000 |
| tailor_case_count | 14 |
| tailor_success_rate | 0.9286 |
| tailor_pass_rate | 0.9286 |
| guardrail_pass_rate | 0.9286 |
| forbidden_claim_free_rate | 0.9286 |
| avg_hallucination_count | 0.0000 |

这组结果来自引入分级上下文压缩前的全量真实评测，用于保留长期对照。

历史全量分桶结果：

| 难度 | Case 数 | 完成率 | 端到端通过率 | Fit 标签准确率 | Fit 分数区间命中 | Tailor 通过率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| easy | 9 | 0.8889 | 0.8889 | 1.0000 | 1.0000 | 0.8750 |
| medium | 6 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| hard | 2 | 1.0000 | 0.5000 | 0.5000 | 0.5000 | 1.0000 |
| adversarial | 1 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |

上一轮较重压缩后的真实 smoke 结果：

| 评测 | Case | 覆盖 | 结果 |
| --- | ---: | --- | --- |
| 5-case smoke | 5 | strong/partial/weak/hard/adversarial，3 个 tailor case | `completed_rate=1.0000`，`end_to_end_pass_rate=0.8000`，`fit_label_accuracy=0.8000`，`tailor_pass_rate=1.0000`，`guardrail_pass_rate=1.0000` |
| 2-case context smoke | 2 | strong + hard partial 边界，2 个 tailor case | `completed_rate=1.0000`，`end_to_end_pass_rate=0.5000`，`tailor_pass_rate=1.0000`，`avg_tailor_reduction_ratio=0.3614`，`avg_tailor_retained_evidence_count=5.5` |

上一轮曾尝试重跑 18-case 全量真实评测，但 20 分钟命令超时，没有拿到 summary。根因是当时评测服务先把所有 case 放在内存 list 中，最后才创建 `EvaluationRun`。现在已经改为逐 case 落库，并可写 `trace_path`，不再只依赖最终 summary。

最新轻量上下文策略后的真实 trace smoke：

| Case | 难度 | 期望标签 | 预测标签 | 分数 | Case 通过 | Tailor 通过 |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `agent_candidate_strong_agent_role` | easy | `strong_fit` | `strong_fit` | 95 | 是 | 是 |
| `ml_candidate_weak_agent_role` | hard | `weak_fit` | `weak_fit` | 30 | 是 | 未运行 |
| `analytics_candidate_partial_recommendation_role` | hard | `partial_fit` | `partial_fit` | 60 | 是 | 是 |
| `beginner_candidate_weak_agent_role` | adversarial | `weak_fit` | `weak_fit` | 15 | 是 | 未运行 |
| `cv_candidate_partial_ml_platform_role` | medium | `partial_fit` | `partial_fit` | 60 | 是 | 是 |

汇总：

- `completed_rate=1.0000`
- `end_to_end_pass_rate=1.0000`
- `fit_label_accuracy=1.0000`
- `fit_score_in_range_rate=1.0000`
- `tailor_pass_rate=1.0000`
- `guardrail_pass_rate=1.0000`
- `forbidden_claim_free_rate=1.0000`
- `avg_tailor_reduction_ratio=0.4938`
- `avg_tailor_retained_evidence_count=6.3333`
- trace 文件：`data/runtime/llm_workflow_trace_latest.jsonl`

这次 trace 直接显示每个 case 的中间返回：简历解析出的技能、JD 解析出的 required skills、RAG Top evidence、fit judge 标签和分数、tailor guardrail 结果。`ml_candidate_weak_agent_role` 的 RAG Top evidence 明确包含 “did not build an agent system”，模型判 `weak_fit` 是符合新标注标准的结果。

本轮 ReAct repair 和断点续跑新增验证：

- 先跑 1 个真实 LLM case 写入 `data/runtime/llm_workflow_trace_latest.jsonl`，再用 `resume_from_last_completed=true` 跑 `case_limit=3`，服务正确跳过 1 个已完成 case，`resumed_case_count=1`。
- 3-case resume smoke 结果：`completed_rate=1.0000`、`end_to_end_pass_rate=1.0000`、`fit_label_accuracy=1.0000`、`fit_score_in_range_rate=1.0000`、`tailor_pass_rate=1.0000`、`guardrail_pass_rate=1.0000`。
- trace 中 `match_and_retrieve.details.top_evidence` 已能看到 `metric_evidence`、`missing_skill_disclosure`、`shipped_project`、`generic_skill` 等证据类型和 `positive/negative/neutral` polarity。
- 专门构造的真实 repair smoke 中，初稿包含 `Eager to learn MLflow` 并被 Guardrail 判为 high risk；`resume_tailor.repair_resume` 调用后删除正文缺口披露，二次 Guardrail 变为 `risk_level=low` 且 `passed=true`。

调试发现：

- 第一轮真实评测中，简历解析会因为 LLM 把 `impact`、`duration` 等叶子字段返回为 `null` 而失败。
- 修复方式是在 Pydantic schema 层把“应为字符串但缺失”的字段归一为空字符串，把列表字段的 `null` 归一为空列表；这不是兜底生成内容，只是接受真实 LLM 常见的缺失表达。
- 修复后，`resume_parse_success_rate` 从 0.7778 提升到 1.0000，`end_to_end_pass_rate` 从 0.6667 提升到 0.8889。
- 剩余 1 个失败是 `agent_candidate_strong_agent_role` 的 `tailor_resume` 阶段 `httpx.ReadTimeout`，说明长 prompt 的简历定制仍需要更好的超时预算或 prompt 压缩。
- 旧标注中 `ml_candidate_partial_agent_role` 被模型判成 `weak_fit`，trace 证明模型依据的是 “did not build an agent system” 这类核心否定证据；该 case 已重标为 `ml_candidate_weak_agent_role`，并作为 partial/weak 边界回归样例。
- 原异常记录使用 `str(exc)`，`ReadTimeout` 会显示为空字符串；已改为记录异常类型和 `repr(exc)`，保证 trace 可追溯。
- 上下文压缩已从过重的多阶段收缩改成 Profile 摘要、JD 摘要、Top evidence 和一次总 prompt packet 预算检查；短小 fit judge 上下文如果因为结构化元数据变大，会用 `expansion_ratio` 单独记录。
- 轻量策略第一轮真实 trace 发现 strong case 的 tailor packet 曾超过 9000 字符预算；修复方式是压缩 evidence metadata，只保留排序调试必要字段，并将预算 trim 调整为更明确的 Top evidence 片段。
- 本轮 5-case 真实 trace 发现 `ranking model` 出现在 “did not implement ranking models” 否定句中时，旧 forbidden claim 检查会误判；已改为否定上下文感知。
- 本轮 5-case 真实 trace 还发现 `A/B testing`、`model evaluation` 与源简历里的 `A/B tests`、`experiment analysis`、`evaluation dashboards` 属于同义证据；Guardrail 已增加技能别名，避免误伤真实证据。
- 简历定制 prompt 已明确要求：缺失 JD 要求只能写入 `keyword_alignment.missing/notes`，不能以 “eager to learn” 等形式写进简历正文。
- 本轮补充了 `resume_tailor` 的 1 轮 ReAct repair loop：Guardrail 高风险时读取 issues、压缩上下文和当前草稿，修复后再次验证，并把 `react_repair` 元数据写入简历版本。
- `match_and_retrieve.details.top_evidence` 已增加 `evidence_type` 和 `polarity`，用于排查 RAG 命中的到底是交付证据、课程噪声还是缺口披露。

## 后续优化

- 增加真实 PDF 简历和真实岗位 JD 的人工标注评测集。
- 用真实招聘 JD 和真实候选人简历重新验证 Top5 anchor 是否仍然合理。
- 用真实标注数据校准 evidence type classifier，补充 abandoned prototype、research prototype、internship delivery 等更细类型，必要时增加 LLM verifier 做二次核验。
- 对 LLM fit judge 增加 partial/weak 边界样例，特别是“相邻 ML/LLM 技能但缺少 Agent/RAG 交付”的情况。
- 将 `resume_from_last_completed` 从 JSONL trace 恢复扩展到基于 `EvaluationRun` 的恢复，并在 UI/API 中展示可恢复 checkpoint。
- 继续评估不同 evidence budget 对 Guardrail 和关键词覆盖的影响。
- 增加 LLM-as-judge，但保留人工抽检。
- 在 CI 中设置最低 `fit_label_accuracy`、`top3_recall`、`guardrail_pass_rate` 和 `end_to_end_pass_rate` 阈值。
