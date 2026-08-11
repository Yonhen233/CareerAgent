# 中文、英文与跨语言 RAG 校准报告

## 数据集

本轮新增 `rag_multilingual_calibration.json`：24 个 Agent 工程概念，每个概念覆盖 `zh_zh`、`en_en`、`zh_en`、`en_zh`、`mixed_zh`、`mixed_en` 六种语言组合，共 144 个 case、1,440 个 query-chunk 对。

每个 case 含 1 条正确证据和 9 条噪声，噪声包括：同主题但错误实现、只有学习计划、失败方案、相邻 Agent 工程概念和跨域干扰。样本由本次 ChatGPT/Codex 开发会话生成并人工定义概念与 hard negative，不调用 DeepSeek。与已有 180 条 RAG case 合计 324 个检索 case。

## 模型与策略

- Embedding：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- 一阶段生产混合分数：向量 0.45 + 词面 0.50 + 类型加分 0.05
- 二阶段：现有英文 `cross-encoder/ms-marco-MiniLM-L-6-v2`；中文路由使用质量保护的启发式排序
- 校准门禁：向量阈值，或“词面覆盖 + 一阶段分数”共同满足；简历事实生成还要求证据类型不是 negative/weak/coursework/planned learning

阈值不是跨模型通用常数。`0.50/0.10/0.45` 只对应当前真实多语言 embedding 的标注分布；离线回归使用的 hash embedding 采用独立 `vector=0.28/first-stage=0.30`，并在每份质量报告中输出实际 provider。生产 release gate 明确要求真实 embedding，hash 阈值不能作为线上质量结论。

## 排序结果

| 策略 | Top1 | Recall@5 | MRR |
| --- | ---: | ---: | ---: |
| 多语言纯向量 | 97.92% | 100% | 0.9896 |
| 生产混合一阶段 | 97.22% | 100% | 0.9850 |
| 混合 + Top20 reranker | 97.22% | 100% | 0.9850 |

修复前，中文启发式 reranker 把整体 Top1 从 97.22% 降到 93.06%，`zh_en` 从 91.67% 降到 79.17%。修复后它不得改写一阶段高置信 anchor，二阶段不再退化，但当前也没有产生可量化增益。纯向量在本数据集最好；生产仍保留混合检索以覆盖技术栈精确词和旧 180-case 场景，reranker 作为受离线门禁约束的增强，不因“架构上有二阶段”就假定有效。

多语言 cross-encoder 候选 `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` 在本机 CPU 对 1,440 对评分超过 10 分钟仍未完成，因此不进入默认路由。模型卡说明它面向 multilingual MS MARCO，但生产选型仍必须同时满足本项目质量与延迟门禁。

模型参考：[multilingual MS MARCO Cross-Encoder model card](https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1)。

## 语言差异

纯向量 Top1 分桶：

| 语言桶 | Top1 | Recall@5 |
| --- | ---: | ---: |
| 中文查中文 | 95.83% | 100% |
| 英文查英文 | 100% | 100% |
| 中文查英文 | 91.67% | 100% |
| 英文查中文 | 100% | 100% |
| 混写查中文 | 100% | 100% |
| 混写查英文 | 100% | 100% |

同一概念确实存在方向不对称：中文查询英文证据的 Top1 比英文查询中文低 8.33 个百分点。Recall@5 没有差异，说明目标证据能召回，但头部排序仍需更多真实中文 JD/英文项目描述校准。release gate 因此同时检查最弱语言桶和最大桶间差距，而不是只看总平均。

## 证据门禁校准

旧策略：`query_coverage >= 0.08 OR first_stage >= 0.08`。

| 门禁 | Recall | Precision | 错误证据误放率 |
| --- | ---: | ---: | ---: |
| 旧门禁 | 100% | 10.26% | 97.15% |
| v3：vector 0.50 / lexical 0.10 / first-stage 0.45 + evidence type | 95.83% | 85.19% | 1.85% |

最终阈值是 378 组组合扫描中，在整体 Recall >= 95%、FPR <= 10%、每个语言桶 Recall >= 90% 的候选中按 F1、低 FPR 和最弱桶排序得到；共有 83 组满足约束。

## 不能由阈值解决的问题

“投递前审批”与“邮件发送后才提示确认”在 embedding 空间高度相关。Embedding 衡量相关性，不等于蕴含或事实支持。系统因此把两层职责拆开：

1. 检索层判断 chunk 是否值得进入上下文。
2. EvidenceClassifier 区分 shipped/metric、coursework、planned learning、missing disclosure、mixed delivery/disclosure 与 polarity。
3. 简历定制只把正向或中性真实经历计为支持证据；负向证据仍保留用于差距分析。
4. Application Guardrail 再做 claim-source polarity、outcome 与结构化事实一致性检查。

本轮还发现 `without new artifacts` 被规则误判成缺失能力。修复为把“无新产物/无进展”这类运行触发条件与 `without auth` 等缺失声明分开，并保留回归样本。长期应继续引入真实人工标注的 query-conditioned entailment 数据，而不是无限追加关键词。

另一个对抗样本是 `Built experiment dashboards ... but did not implement ranking models`。把整个段落判为 negative 会丢失真实交付，把整个段落判为 positive 又会把缺失技能包装成能力。因此分类器输出 `mixed`，检索层允许它进入上下文，生成 Prompt 和最终 Guardrail 仍必须尊重其中的否定边界。中文“没有实现”还会命中“实现”子串，分类前必须先消除完整否定动作短语。

## 运行方式

```powershell
python -m scripts.generate_multilingual_rag_calibration
python -m scripts.run_multilingual_rag_calibration
```

评测不调用 LLM，结果写入 `evaluation_runs` 和本地 `artifacts/multilingual_rag_calibration_latest.json`。release policy 位于 `evals/rag_multilingual_release_policy.json`。
