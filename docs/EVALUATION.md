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

- `vector_only`
- `lexical_only`
- `lexical_80_vector_15_type_5`
- `hybrid_70_vector_30_lexical`
- `hybrid_58_vector_34_lexical_8_type_boost`
- `hybrid_alias_62_vector_33_lexical_5_type_boost`

最近一次评测结果：

| 策略 | Top1 Acc | Top3 Recall | Top5 Recall | MRR | nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| vector_only | 1.0000 | 0.8333 | 0.9444 | 1.0000 | 0.9501 |
| lexical_only | 1.0000 | 0.9444 | 1.0000 | 1.0000 | 0.9912 |
| lexical_80_vector_15_type_5 | 1.0000 | 0.9444 | 1.0000 | 1.0000 | 0.9912 |
| hybrid_70_vector_30_lexical | 1.0000 | 0.8889 | 0.9444 | 1.0000 | 0.9555 |
| hybrid_58_vector_34_lexical_8_type_boost | 1.0000 | 0.8889 | 1.0000 | 1.0000 | 0.9857 |
| hybrid_alias_62_vector_33_lexical_5_type_boost | 1.0000 | 0.8889 | 1.0000 | 1.0000 | 0.9857 |

选择：

```text
lexical_80_vector_15_type_5
```

理由：

- 当前技术岗位数据里，JD 与简历证据包含大量精确技能词，词法召回非常重要。
- 该策略与 `lexical_only` 指标持平，但额外保留 15% 向量重排和 5% chunk 类型加权。
- query alias expansion 可将 `retrieval augmented generation`、`Python API service` 等表达映射到 `RAG`、`FastAPI` 等技术词。
- 比纯向量更稳，因为当前本地 hash embedding 不是语义 embedding。

## RAG 向量库选型

当前选择：

```text
SQLite 权威存储 + Chroma 可选向量库镜像
```

理由：

- SQLite 保存所有 chunk、metadata、embedding、职位 JD 和评测结果，便于审计、测试和本地演示。
- SQLite 不需要外部服务，适合简历项目、单机部署和面试演示。
- Chroma 是常见本地向量库，API 简单，能体现真实 RAG 工程组件。
- Chroma 作为镜像而不是唯一存储，避免向量库不可用时主流程崩溃。
- 后续如果需要规模化，可替换为 Milvus、Qdrant、pgvector 或云向量库。

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
- 接入真实 embedding 模型后重新评估 RAG 权重。
- 增加 reranker，对 Top20 chunk 做二阶段排序。
- 增加 LLM-as-judge，但保留人工抽检。
- 在 CI 中设置最低 `fit_label_accuracy`、`top3_recall` 和 `guardrail_pass_rate` 阈值。
