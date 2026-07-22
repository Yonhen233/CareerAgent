# CareerAgent 评测体系与当前指标

## 1. 先说明结论口径

截至 2026-07-22，CareerAgent 有三组不能混用的结果：

1. **专项评测**：PDF、RAG、岗位排序、投递和注入等固定数据集，适合比较策略和做回归。
2. **整轮系统基线**：EvaluationRun `#113` 使用真实 DeepSeek、真实 embedding 和 reranker 跑 12 个 suite。它完成了运行，但严格发布门禁失败。
3. **修复后定向回归**：`#114/#118/#119/#120` 只重跑已知失败 case，全部通过。它证明这些 bad case 已修复，不代表 24-case 全量已经重新认证。

面试时最稳妥的结论是：**专项能力大多已过门禁，整轮系统评测暴露了规划、fit 和 quick apply 问题；这些已知问题已经定向修复，但出于真实 API 成本控制，尚未重新跑完整发布套件，所以不能宣称当前全系统通过率 100%。**

## 2. 评测为什么分层

Agent 的最终答案可能正确，但过程有问题；也可能工具轨迹正确，最终业务状态却错。CareerAgent 采用分层门禁，不计算一个会掩盖短板的加权总分：

| 层级 | 主要问题 | 典型指标 |
| --- | --- | --- |
| 数据与解析 | PDF/JD 是否保留事实和结构 | 字段 grounding、skill precision/recall、页码命中 |
| 检索与排序 | 正确证据/岗位是否被召回并排前 | Recall@K、MRR、nDCG@K、Top1 accuracy |
| LLM 节点 | 模型是否遵循 schema、标签和证据 | parse success、fit accuracy、claim grounding |
| 工具轨迹 | Agent 是否选对工具并按依赖执行 | action precision/recall、step success、LangGraph 标记 |
| 业务终态 | 用户最终得到的岗位、简历和投递是否正确 | E2E pass、top-job、fit gate、artifact 完整性 |
| 安全 | 是否阻止编造、注入和越权动作 | high-risk recall、FPR、forbidden claim free |
| 稳定与性能 | 重复运行是否稳定，成本是否可控 | pass^k、p50/p95、Token、费用、调用成功率 |

核心安全项采用硬门禁：例如 prompt injection recall 未达标时，不能用 RAG nDCG 高分抵消。

## 3. 当前数据集规模

| 数据集 | Case 数 | 主要覆盖 |
| --- | ---: | --- |
| `pdf_chunk_cases.json` | 96 | 分页、双栏、附录、课程/交付混淆、计划学习、同页和跨页干扰 |
| `rag_cases.json` | 180 | easy/medium/hard/adversarial、多正例、负向证据、同义词和硬负例 |
| `jd_parser_cases.json` | 30 | 中英混合、required/preferred、否定要求、技能别名、相邻领域 |
| `job_relevance_cases.json` | 13 | 13 个意图、130 个 0-4 级人工相关性候选岗位 |
| `natural_language_plan_cases.json` | 20 | 单/多动作、否定、部分表单、ID、内联 JD、中英混合、越权动作 |
| `llm_workflow_cases.json` | 24 | 简历解析、JD 解析、RAG、fit、定制和 Guardrail 的真实 LLM 链路 |
| `agent_full_flow_cases.json` | 6 | 搜索、排序、定制、fit gate、投递、Trace、Artifact 和 LangGraph |
| `application_packet_cases.json` | 27 | 事实声明、跨语言表达、数字、目标岗位、人工确认和误拦截 |
| `prompt_injection_cases.json` | 70 | 40 攻击 + 30 良性；JD/PDF/RAG/面经四来源 |
| `interview_claim_verifier_cases.json` | 14 | 支持/不支持 claim、伪装经历、未来方案和答非所问 |
| `interview_prep_cases.json` | 9 | 三类准备角度、技能覆盖、面经链接、缺口 drill、Markdown 交付 |
| `real_job_source_cases.json` | 8 | 真实招聘源的可达性和数据质量 smoke |

这些数据主要是人工设计和可控构造，适合回归，不等同于真实用户分布。fit 标签目前仍以单人标注为主，后续需要双人复标和 Cohen's kappa。

## 4. 指标怎么理解

### 4.1 Recall@K

正确证据集合为 `G`，TopK 召回为 `R_K`：

```text
Recall@K = |G ∩ R_K| / |G|
```

一个 query 可能有多个正确 chunk，因此即使 Top1 总能命中一个主证据，Top3/Top5 recall 也可能不到 1。

### 4.2 MRR

只看第一个相关结果的排名：

```text
MRR = mean(1 / first_relevant_rank)
```

MRR=1 表示每个 case 的第一个相关结果都排第一，但不能证明所有相关证据都被召回。

### 4.3 nDCG@K

nDCG 同时考虑多级相关性和排序位置。岗位数据用 0-4 级标注，强相关岗位排在前面比“只召回但排在后面”得分更高。

### 4.4 Grounding 与 Citation

- **grounding rate**：发布的字段或 claim 能否由原文/证据直接支持；
- **citation integrity**：引用的 ID 是否真实存在、是否属于当前题；
- **question answering accuracy**：事实正确之外，是否回答了问题；
- **forbidden claim free**：是否没有把缺口、计划或无证据数字写成经历。

### 4.5 pass^k

同一 case 连续运行 `k` 次，全部通过才算成功：

```text
pass^k = 全部 k 次通过的 case 数 / case 总数
```

它衡量一致性，不是“至少成功一次”的 pass@k。Agent 做投递这类高风险流程时，偶尔成功不够。

## 5. 专项评测结果

### 5.1 PDF Chunk：EvaluationRun #101

| 指标 | 结果 | 门槛 |
| --- | ---: | ---: |
| 简历 / Query | 96 / 576 | - |
| 选中策略 | `paragraph_page_900_overlap160` | - |
| Top3 关键词命中 | 0.9479 | >= 0.90 |
| Top3 页码命中 | 0.8299 | >= 0.80 |
| Top3 上下文命中 | 0.7760 | >= 0.75 |
| 平均 Top1 长度 | 772.77 字符 | <= 950 |
| 平均 chunk 数 | 10.0 | <= 14 |

发布门禁通过。需要特别保留的坏指标是 `coursework_vs_shipped` 上下文命中仅 0.0521，说明句子级混合极性仍是明确短板。

### 5.2 RAG 策略：EvaluationRun #102

选中 `real_embedding_top20_rerank`，真实 provider 为 `sentence_transformers` 和 `cross_encoder`，无 fallback：

| 指标 | 结果 | 门槛 |
| --- | ---: | ---: |
| Case | 180 | - |
| Top1 accuracy | 1.0000 | >= 0.80 |
| 平均 Recall@3 | 0.6125 | >= 0.60 |
| 平均 Recall@5 | 0.7292 | >= 0.70 |
| MRR | 1.0000 | >= 0.85 |
| nDCG@5 | 0.7862 | >= 0.75 |

Top1=1 与 Recall@5=0.7292 并不矛盾：每个 query 的首个主证据都正确，但一个 case 可能有多个 gold evidence，Top5 未覆盖全部。

### 5.3 岗位相关性：EvaluationRun #103

13 个 query、130 个候选岗位：pass=1.0、Top1=1.0、Recall@3/5=1.0、MRR=1.0、nDCG@5=0.9495，且没有低等级岗位排在强相关岗位之前。固定集门禁通过，但样本规模较小，不能当线上推荐 CTR。

### 5.4 投递包 Guardrail：EvaluationRun #104

27 个 case：pass=1.0、high-risk recall=1.0、false block rate=0、missed high-risk rate=0、issue-code hit rate=1.0。风险分布为 11 low、4 medium、12 high。

### 5.5 Prompt Injection：EvaluationRun #105

70 个 case中 40 攻击、30 良性：detection recall=1.0、false positive rate=0、true negative rate=1.0、severity accuracy=1.0。JD、PDF、RAG、面经四来源与 instruction override、tool escalation、data exfiltration、RAG pollution 四类别均过各自门槛。

### 5.6 真实岗位源快照：EvaluationRun #47

2026-07-20 对腾讯、百度、美团、字节、阿里搜索“Prompt Agent 安全 评测实习生”：5/5 来源可达并返回结果，40 个岗位的 JD 非空率和投递链接率均为 1.0，实习类比例 0.85，query/Agent 相关率均为 1.0，耗时 6.611 秒。

这是 point-in-time source smoke。招聘站接口、批次和网络会变化，所以它独立报告，不参与核心 Agent 质量门禁。

## 6. 整轮真实系统基线：EvaluationRun #113

### 6.1 质量结果

| Suite | Run | 结果 | 门禁 |
| --- | --- | --- | --- |
| PDF Chunk | #101 | 96 cases，策略门禁通过 | 通过 |
| RAG | #102 | 180 cases，检索门禁通过 | 通过 |
| 岗位排序 | #103 | 13/13 | 通过 |
| 投递 Guardrail | #104 | 27/27 | 通过 |
| Prompt Injection | #105 | 70/70 | 通过 |
| 自然语言规划 | #106 | pass 0.85，intent 0.95，action P/R 1.0 | **失败** |
| 真实 JD Parser | #107 | 30/30；skill P/R/F1=0.9368/1/0.9652 | 通过 |
| 真实 LLM Workflow | #108 | E2E 18/24=0.75；fit label=0.875 | **失败** |
| Agent Full Flow | #110 | 5/6=0.8333；Trace/Artifact/LangGraph=1.0 | **失败** |
| Claim Verifier | #111 | 14/14；FPR=0；答非所问误接收=0 | 通过 |
| 完整面试包 | #112 | 1/1，10 题、三视角和导出门禁通过 | 通过但样本不足 |
| Reliability | #109 重复切片 | 3 cases x 2，pass^2=0.6667 | **失败** |

系统 gate 因任何核心 suite 失败而失败。这里没有用 PDF/RAG 满分抵消 LLM 或全流程错误。

### 6.2 性能和成本

评测器曾因前台超时后子进程未结束而产生重叠调用。报告保留两套数字：

| 口径 | 调用 | Token | 费用 |
| --- | ---: | ---: | ---: |
| 供应商原始对账 | 194 | 227,511 | 0.267945 元 |
| 剔除已确认的重叠评测开销 | 171 | 218,342 | 0.257601 元 |
| 重叠开销 | 23 | 9,169 | 0.010344 元 |

归一化调用延迟：mean 4.624 秒、p50 3.538 秒、p95 9.684 秒、max 34.788 秒。

原始记录中 Flash 189 次、200,319 tokens、0.171986 元；Pro 5 次、27,192 tokens、0.095959 元。Pro 调用很少，但因长上下文面试生成/验证占了约 35.8% 的费用。

LangGraph 轨迹共 17 条、88 个工具步骤，工具成功率 95.45%，p50 180ms、p95 4.089s。失败步骤既包含预期 Guardrail 拒绝，也包含当时尚未修复的跨语言证据误拦截，不能直接解释成基础设施可用率。

## 7. 修复后定向回归

| Run | 切片 | 结果 | 能证明什么 |
| --- | --- | --- | --- |
| #114 | 3 个规划历史失败 case | 3/3；intent/action/禁止动作均通过 | 多动作终态、增量 Profile 和显式约束已修复 |
| #118 | 2 个 fit 历史失败 case | 2/2；fit、定制、Guardrail 全通过 | fit-rubric、`AgentTrace` 子串污染和双边证据验证已修复 |
| #119 | 1 个前端 full-flow case | 1/1；top job、quick apply、Trace、Artifact、LangGraph 全通过 | 0.698 跨语言证据边界恢复与完整产物可用 |
| #120 | 1 个 injection 噪声 workflow | 1/1；最终 grounding 与 forbidden claim 均通过 | raw-vs-published verifier 能剔除不严谨 gap |

`#118/#119/#120` 合计 15 次 Flash 调用、22,004 tokens；没有调用 Pro，也没有重跑昂贵面试包。

注意 `#120` 的原始 gap grounding 仍为 0.6667，Verifier 拒绝 1 条，但最终发布层为 1.0。这不是隐藏错误，而是说明“模型草稿会错，发布门禁把错的内容挡掉”。

## 8. 最新离线 JD 回归为什么仍有红灯

EvaluationRun `#122` 使用显式 `heuristic_fallback` 跑 30 个 JD case：pass=0.9333、required skill precision/recall/F1=0.9817/1.0/0.9901，但 grounding gate=0.9333，低于 0.95 门槛，release gate 失败。两个 unsupported skill 来自收紧英文词边界后暴露的 taxonomy/grounding 边缘。

这和 `#107` 的真实 LLM 30/30 不矛盾：模式和代码时间点不同。最新状态应该报告为“真实 LLM 基线通过；修复后离线严格 grounding 仍差 2 个 case，需要继续处理”，而不是挑一个更好看的 run 覆盖另一个。

## 9. Flash 与 Pro 如何分工

同样本对照结果：

| 分层 | Flash | Pro |
| --- | ---: | ---: |
| Canary | 1/1，7,629 tokens，40.0s | 1/1，7,469 tokens，57.1s |
| Planner | 4/4 | 4/4 |
| JD Parser | 4/4 | 4/4 |
| Core hard/adversarial | 0/3 | 0/3 |
| Core Token / wall time | 22,175 / 79.1s | 15,737 / 128.8s |
| Interview release gate | 失败 | 通过 |
| Interview Token / wall time | 29,135 / 87.1s | 30,615 / 129.2s |

结论不是“便宜模型全部替换贵模型”。Flash 适合短结构化节点；复杂面试生成和验证保留 Pro。Flash 输出上限只放宽 15%，不增加 repair 轮数。模型选择仍由同一套门禁判断，不能给便宜模型更低的事实标准。

## 10. 当前发布判断

### 已经可以有把握地说

- 主要业务链路和生产机制已经实现，完整本地测试为 264 项；
- PDF/RAG、岗位排序、投递 Guardrail 和注入专项固定集已过门禁；
- 真实 LLM 整轮评测能够跑完并产出节点、Token、费用和业务终态；
- 整轮基线暴露的已知规划、fit 和跨语言 grounding bad case 已定向修复。

### 还不能说

- 不能说最新代码的 24-case LLM workflow 已经 100% 通过；
- 不能把 1 个完整面试包外推成稳定成功率或 p95；
- 不能把固定 70-case injection 的 100% 当未知攻击的 100%；
- 不能把一次五源可达性当招聘站 SLA；
- 不能说系统已经达到大规模多租户 SaaS 的数据库和身份能力。

### 下一轮最有价值的评测

1. 双人复标 fit-rubric-v2，报告 Cohen's kappa 和 disagreement set；
2. 修复 `#122` 两个 JD grounding case，再跑全量 24-case workflow；
3. 对关键 3-5 case 做 pass^3，而不是只做一次成功 smoke；
4. 完整面试包扩大到至少 5 个不同 JD/Profile，报告每包调用数、Token、p50/p95 和 repair 率；
5. Prompt Injection 增加真实失败、编码混淆和多轮间接注入；
6. 中文 reranker 做独立对照，报告检索质量与延迟变化。
