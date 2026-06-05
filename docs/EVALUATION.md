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

- 30 个合成 PDF 简历案例。
- 每个案例 3 页。
- 每个案例 4 个查询。
- 共 120 条 PDF chunk 查询。

数据设计：

- 覆盖 Agent/RAG、LLM Eval、后端平台、前端工具、ML 平台、数据工程等候选人类型。
- 每页包含目标证据和噪声段落。
- 查询要求同时命中关键词、页码和上下文关键词。

### RAG 策略数据

```text
evals/rag_cases.json
```

规模：

- 48 个 RAG 检索案例。
- 每个案例 6 个候选证据 chunk。
- 每个案例 3 个期望命中的 evidence chunk。

数据设计：

- 一半查询使用精确技术关键词。
- 一半查询使用同义表达，例如 `retrieval augmented generation` -> `RAG`。

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
| fixed_window_450_overlap80 | 1.0000 | 1.0000 | 0.9583 | 438.49 | 7.90 |
| paragraph_page_900_overlap160 | 1.0000 | 1.0000 | 1.0000 | 755.93 | 3.90 |
| paragraph_page_1200_overlap200 | 1.0000 | 1.0000 | 1.0000 | 808.98 | 3.00 |
| section_aware_700_overlap120 | 0.9417 | 0.9667 | 0.9417 | 507.02 | 10.70 |

选择：

```text
paragraph_page_900_overlap160
```

理由：

- Top3 关键词命中率和页码命中率均为 1.0。
- Top3 上下文命中率为 1.0，优于固定窗口。
- 相比 1200 大窗口，900 字符 chunk 更少引入无关噪声。
- 平均 Top1 长度约 756 字符，能保留项目/经历上下文，又不会过长。
- 平均 chunk 数 3.9，检索成本低于 section-aware 的 10.7。

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
| hash_vector_only | hash | none | 1.0000 | 0.8333 | 0.9444 | 1.0000 | 0.9501 |
| hash_lexical_only | hash | none | 1.0000 | 0.9444 | 1.0000 | 1.0000 | 0.9912 |
| hash_lexical_80_vector_15_type_5 | hash | none | 1.0000 | 0.9444 | 1.0000 | 1.0000 | 0.9912 |
| real_embedding_vector_only | sentence-transformers | none | 1.0000 | 0.7778 | 1.0000 | 1.0000 | 0.9664 |
| real_embedding_70_vector_30_lexical | sentence-transformers | none | 1.0000 | 0.8611 | 1.0000 | 1.0000 | 0.9745 |
| real_embedding_55_vector_40_lexical_5_type | sentence-transformers | none | 1.0000 | 0.9444 | 1.0000 | 1.0000 | 0.9843 |
| real_embedding_45_vector_50_lexical_5_type | sentence-transformers | none | 1.0000 | 0.9444 | 1.0000 | 1.0000 | 0.9843 |
| real_embedding_top20_rerank | sentence-transformers | cross-encoder | 1.0000 | 0.9444 | 1.0000 | 1.0000 | 0.9843 |

选择：

```text
real_embedding_top20_rerank
```

理由：

- 真实 embedding 策略中，`vector=0.55 / lexical=0.40 / type=0.05` 达到最高 Top3 Recall。
- `real_embedding_top20_rerank` 在 Top5 anchor 保护下与最佳一阶段真实 embedding 策略持平，同时保留 CrossEncoder 对 Top20 尾部证据的二阶段排序能力。
- hash baseline 的 nDCG@5 更高，说明当前合成数据仍偏精确技术词；它保留为离线基线，不作为生产主路径。
- 选择真实 embedding 主路径更贴近真实 JD 和简历语义表达，例如中英文混写、同义表达、职责描述不直接出现技术名的情况。

调试发现：

- 第一轮真实评测中，裸 CrossEncoder 权重过高，会把强关键词证据推出 Top3，Top3 Recall 从 0.9444 降到 0.8889。
- 修复方式是采用保守融合：一阶段分数为主，rerank 分数为辅，并设置 Top5 recall anchor。
- 依赖调试中发现 `transformers 5.x` 与当前 SentenceTransformers 加载不稳定，已在 `requirements.txt` 中约束 `transformers<5.0.0`、`huggingface-hub<1.0`。

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

- 真实调用 LLM 判断岗位是否适合候选人。
- 要求模型返回 strict JSON。
- 对强匹配岗位调用简历定制流程。
- 用 required skill coverage 和 guardrail risk level 验收改写结果。

最近一次实测结果：

```text
case_count = 3
fit_label_accuracy = 1.0000
tailor_pass_rate = 1.0000
```

三个岗位：

- `strong_agent_fit`：预期 `strong_fit`，模型返回 `strong_fit`。
- `partial_llm_eval_fit`：预期 `partial_fit`，模型返回 `partial_fit`。
- `weak_frontend_fit`：预期 `weak_fit`，模型返回 `weak_fit`。

简历定制结果：

- required skill coverage = 1.0000
- guardrail risk level = `low`
- LLM 调用日志均为 `completed`

调试发现：

- 第一轮 prompt 中，模型把 `LLM Evaluation Intern` 错判为 `strong_fit`。
- 原因是 strong/partial 边界不够硬。
- 修复方式是在 prompt 中明确：只有直接需要 Agent/RAG/FastAPI/SQLite 实现的岗位才能标为 `strong_fit`；LLM eval、dashboard、frontend 等相邻方向应标为 `partial_fit` 或 `weak_fit`。

## 后续优化

- 增加真实 PDF 简历和真实岗位 JD 的人工标注评测集。
- 用真实招聘 JD 和真实候选人简历重新验证 Top5 anchor 是否仍然合理。
- 增加中文/英文混合 JD 的人工标注 RAG 数据。
- 增加 LLM-as-judge，但保留人工抽检。
- 在 CI 中设置最低 `fit_label_accuracy`、`top3_recall` 和 `guardrail_pass_rate` 阈值。
