# RAG 质量诊断与处理记录（2026-09-11）

## 结论

当前 RAG 的主要问题不是把向量权重从 0.45 调成别的数，而是索引一致性和证据去重没有覆盖历史数据。新建 profile 使用的 `fact_id` 关联可以把结构化事实与 PDF 细节合并；历史 profile 仍可能只有结构化 chunk、页面 chunk 和旧向量，导致同一项目的多个视图重复占据 Top-K。

因此处理顺序是：先修索引复用条件并重建历史 profile，再用真实脱敏简历/JD 做 query-conditioned 标注校准。暂时不把更多合成 qrel 当作主要解决方案。

## 证据

### 现有 180-case RAG 集

该集通过了一致性校验，但 180 个 case 只有 12 个不同 query、115 段不同文本；每个 case 标注 4 个正向 chunk。因此 Recall@3 的理论上限是 0.75。当前质量先验策略的结果为：

| 指标 | 当前结果 |
| --- | ---: |
| Top1 | 1.0000 |
| Recall@3 | 0.6667（达到理论上限的 88.89%） |
| Recall@5 | 0.8333 |
| MRR | 1.0000 |
| nDCG@5 | 0.8732 |

失败样本集中在 `target_metric`、相邻经历、通用工具和同语言 hard negative。典型情况是目标项目已经进入候选，但 metric 细节排在同一项目的泛化描述或噪声后面。这说明当前评测能发现排序问题，但不能证明对真实简历和真实 JD 的泛化能力。

### 策略消融

在同一批 180 case 上使用真实多语言 embedding 和 Cross-Encoder：

| 质量先验 | reranker 权重 | Recall@3 | Recall@5 | nDCG@5 |
| --- | ---: | ---: | ---: | ---: |
| 开启 | 0.30 | 0.6667 | 0.8333 | 0.8732 |
| 开启 | 0.50 | 0.6250 | 0.7917 | 0.8378 |
| 开启 | 0.70 | 0.5208 | 0.7292 | 0.7817 |
| 关闭 | 0.30 | 0.6125 | 0.7292 | 0.7862 |

把 reranker 权重调大反而退化，说明它当前更适合做受约束的增强；质量先验是这套数据上的主要增益来源。调整策略可以做局部优化，但不能替代真实标注数据。

### 真实简历索引检查

当前 SQLite 中有 3,833 个 resume chunk，其中 3,555 个使用当前 SentenceTransformer、233 个仍是 hash provider、45 个缺少 embedding 元数据；2,449 个 job chunk 中还有 120 个 hash、8 个缺少元数据。对提供的简历对应历史 profile 查询时，Top-5 可能同时出现同一项目的结构化 chunk 和多个 PDF 页面 chunk，而且这些旧页面没有 `fact_id`，无法进行事实级去重。

一次真实查询的冷启动延迟约 12.3 秒，模型加载完成后的 warm P50 约 7.8 ms、P95 约 11.9 ms。冷启动是模型加载成本，不能当成单次检索稳定 SLO；报告中应分别记录 cold 和 warm。

## 已修复

`SQLiteVectorIndex._row_vectors` 现在同时校验向量维度、embedding provider、model 和 retrieval text version。即使新旧模型输出维度相同，也会自动重算旧向量，并回写新的 embedding 元数据。查询旧 profile 时会自动执行一次 `resume_facts_v2` 重建；多事实 PDF 页面会作为已选结构化事实的证据视图合并，不再单独占用 Top-K。新增回归测试覆盖“同维度换模型”“历史 profile 迁移”和“多事实页面去重”。

## 仍需处理

1. 对历史 profile 执行一次可审计的 reindex。可以从当前结构化 profile 和保留的 PDF page text 重建 chunk；重建后确认 `fact_id`、`fact_links` 和 embedding 元数据完整。
2. 为真实脱敏简历/JD 建立 query-conditioned 标注：相关性、是否支持该 claim、证据类型、否定/计划/课程语气、同一事实的多视图关系。每个 query 至少保留一个 hard negative 和一个相邻能力样本。
3. 按语言和 query 类型拆开报告：中文查中文、中文查英文、英文查中文、多技能组合 query；同时记录 Top1、Recall@3/5、MRR、nDCG、证据支持 precision 和 cold/warm latency。
4. 在真实标注集稳定后，再评估是否需要 multilingual cross-encoder。当前中文路由使用多语言 embedding 作为 rerank 分数；在没有足够语言标注和延迟预算前，直接替换模型风险较高。

## 判断

短期修复以索引重建和代码策略为主，不需要先大量增加标注数据。标注数据仍然需要补，但用途是验证真实泛化、校准 query-conditioned entailment 和训练/选择更好的 reranker，而不是用来掩盖当前历史索引污染。
