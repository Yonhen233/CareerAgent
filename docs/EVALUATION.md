# 量化评测方案

CareerAgent 的评测分为四层：

- 基础匹配评测：Profile/JD 匹配质量。
- PDF Chunk 策略评测：不同 PDF 切分方案对证据召回的影响。
- RAG 策略评测：不同检索排序策略对证据召回的影响。
- LLM 实景流程评测：真实调用 LLM 判断岗位适配度并按 JD 改写简历。

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

### LLM 实景流程数据

```text
evals/llm_workflow_cases.json
```

规模：

- 18 个端到端 LLM 流程案例，不再只评测 3 条岗位适配标签。
- 14 个案例会进入简历定制流程。
- 覆盖 `strong_fit`、`partial_fit`、`weak_fit` 三类标签。
- 覆盖 `easy`、`medium`、`hard`、`adversarial` 四类难度。

数据设计：

- 覆盖 Agent/RAG、LLM Eval、后端、前端、数据工程、ML、AI 安全、移动 AI、推荐、分析、DevOps、CV 等岗位。
- 每个 case 包含原始简历文本、期望 Profile 技能、期望 Profile 关键词、JD、期望 JD 技能、期望 fit label、期望 fit score 区间、定制简历关键词和禁止编造 claim。
- hard/adversarial case 明确加入 `did not build`、`No shipped project`、相邻岗位经验等反例，测试模型是否把“读过/计划学习/课程提到”误判成真实交付经验。

## 运行方式

```bash
pytest -q
```

API：

```http
POST /evaluations/run
POST /evaluations/pdf-chunk-strategies
POST /evaluations/rag-strategies
POST /evaluations/llm-workflow
GET /evaluations/results
```

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
- 下一步需要在 RAG ranking 中加入 evidence type 识别，例如 shipped/project/metric 比 coursework/planned learning 权重更高。

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
- 后续优化重点不再是继续调 embedding 权重，而是引入 evidence classifier 或 LLM verifier 区分“真实交付证据”和“仅提及/计划学习”。

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

| Case | 难度 | 期望标签 | 预测标签 | Case 通过 | Tailor 通过 | Prompt Packet |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `agent_candidate_strong_agent_role` | easy | `strong_fit` | `strong_fit` | 是 | 是 | 6071 chars，预算内 |
| `ml_candidate_partial_agent_role` | hard | `partial_fit` | `weak_fit` | 否 | 是 | 5516 chars，预算内 |
| `beginner_candidate_weak_agent_role` | adversarial | `weak_fit` | `weak_fit` | 是 | 未运行 | 无 tailor |

汇总：

- `completed_rate=1.0000`
- `end_to_end_pass_rate=0.6667`
- `fit_label_accuracy=0.6667`
- `tailor_pass_rate=1.0000`
- `guardrail_pass_rate=1.0000`
- `avg_tailor_reduction_ratio=0.4892`
- `avg_tailor_retained_evidence_count=6.5`
- trace 文件：`data/runtime/llm_workflow_trace_latest.jsonl`

这次 trace 直接显示每个 case 的中间返回：简历解析出的技能、JD 解析出的 required skills、RAG Top evidence、fit judge 标签和分数、tailor guardrail 结果。`ml_candidate_partial_agent_role` 失败不是因为没有 trace 或证据丢失，RAG Top evidence 明确包含 “did not build an agent system”，模型因此判 `weak_fit`，说明下一步应重新定义 partial/weak 标注边界或增加边界 prompt，而不是继续调上下文压缩。

调试发现：

- 第一轮真实评测中，简历解析会因为 LLM 把 `impact`、`duration` 等叶子字段返回为 `null` 而失败。
- 修复方式是在 Pydantic schema 层把“应为字符串但缺失”的字段归一为空字符串，把列表字段的 `null` 归一为空列表；这不是兜底生成内容，只是接受真实 LLM 常见的缺失表达。
- 修复后，`resume_parse_success_rate` 从 0.7778 提升到 1.0000，`end_to_end_pass_rate` 从 0.6667 提升到 0.8889。
- 剩余 1 个失败是 `agent_candidate_strong_agent_role` 的 `tailor_resume` 阶段 `httpx.ReadTimeout`，说明长 prompt 的简历定制仍需要更好的超时预算或 prompt 压缩。
- hard 分桶里 `ml_candidate_partial_agent_role` 被模型判成 `weak_fit`，暴露出 partial/weak 边界仍需更细：有 Python/Transformers/Evaluation 交集但明确没有 Agent/RAG 交付时，人工期望是 partial，模型更保守。
- 原异常记录使用 `str(exc)`，`ReadTimeout` 会显示为空字符串；已改为记录异常类型和 `repr(exc)`，保证 trace 可追溯。
- 上下文压缩已从过重的多阶段收缩改成 Profile 摘要、JD 摘要、Top evidence 和一次总 prompt packet 预算检查；短小 fit judge 上下文如果因为结构化元数据变大，会用 `expansion_ratio` 单独记录。
- 轻量策略第一轮真实 trace 发现 strong case 的 tailor packet 曾超过 9000 字符预算；修复方式是压缩 evidence metadata，只保留排序调试必要字段，并将预算 trim 调整为更明确的 Top evidence 片段。

## 后续优化

- 增加真实 PDF 简历和真实岗位 JD 的人工标注评测集。
- 用真实招聘 JD 和真实候选人简历重新验证 Top5 anchor 是否仍然合理。
- 增加 evidence type classifier，区分 shipped project、metric evidence、coursework、planned learning、abandoned prototype。
- 对 LLM fit judge 增加 partial/weak 边界样例，特别是“相邻 ML/LLM 技能但缺少 Agent/RAG 交付”的情况。
- 在逐 case trace 的基础上增加可恢复运行，支持从最后一个完成 case 继续。
- 继续评估不同 evidence budget 对 Guardrail 和关键词覆盖的影响。
- 增加 LLM-as-judge，但保留人工抽检。
- 在 CI 中设置最低 `fit_label_accuracy`、`top3_recall`、`guardrail_pass_rate` 和 `end_to_end_pass_rate` 阈值。
