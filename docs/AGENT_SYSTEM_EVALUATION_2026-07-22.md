# CareerAgent Agent 系统评测报告（2026-07-22）

## 1. 结论先行

本轮使用真实 DeepSeek API、真实 embedding 和 cross-encoder reranker，完成了从确定性组件、RAG、LLM 节点、LangGraph 工具轨迹到端到端业务结果的分层评测。系统没有运行异常中止，但**严格发布门禁未通过**，所以当前结论不是“已经可以上线”，而是：

- PDF Chunk、岗位 RAG、岗位相关性、投递材料 Guardrail、Prompt Injection、JD 解析、面试 claim verifier 和单个完整面试包通过当前门禁；
- 自然语言规划通过率 `85.00%`，仍有一个真实的多动作执行语义错误；
- 24 个真实 LLM 工作流端到端通过率 `75.00%`，其中既有系统 grounding 阈值问题，也有旧标注标准偏严的问题；
- 6 个 LangGraph 用户全流程通过率 `83.33%`，唯一失败发生在快速投递的跨语言证据阈值边界；
- 3 个代表性工作流各重复 2 次，`pass@1=66.67%`、`pass^2=66.67%`。同一个对抗样本连续失败，说明是系统性问题，不是随机抖动；
- 剔除已确认的评测进程重叠开销后，真实模型调用 `171` 次、`218,342` tokens、估算费用 `0.257601` 元；调用延迟 `p50=3.538s`、`p95=9.684s`。

## 2. 主流 Agent 评测流程

当前主流方法不是只让 LLM Judge 给最终回答打一个分，而是把 Agent 当成一个有状态、会调用工具的系统分层评测：

1. **先定义任务契约与风险**：明确成功终态、允许/禁止动作、证据边界、性能和成本预算。
2. **构造代表性数据集**：同时覆盖正常、困难、边界、噪声、对抗和历史线上 badcase；冻结标注版本。
3. **组件级确定性评测**：解析、检索、重排、Guardrail 和权限等先用可复现指标验证。
4. **LLM 节点评测**：检查结构化输出、语义准确性、证据支持、幻觉和任务完成度。
5. **轨迹评测**：检查是否选择了正确工具、参数、顺序，失败后是否按策略恢复。
6. **端到端终态评测**：不只看回复文本，还验证数据库状态、artifact、审批和外部动作结果。
7. **可靠性评测**：同一 case 重复运行，报告 `pass@1` 与 `pass^k`，区分偶发失败和稳定失败。
8. **安全与对抗评测**：报告攻击检测召回率、误报率、高风险动作越权率等，而不是只报“安全通过”。
9. **性能与成本评测**：记录端到端和节点级 p50/p95、Token、缓存命中、模型路由与单次费用。
10. **持续评测**：把生产 trace 中的失败样本回流到数据集，自动化回归，并定期由人工校准 grader。

本方案参考的主要公开实践：

- [OpenAI Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)：任务特定、代表真实分布、记录全部过程、自动评测与人工校准结合；
- [OpenAI Agent evals](https://developers.openai.com/api/docs/guides/agent-evals)：使用 trace grading 定位工作流级错误；
- [LangSmith Evaluate a complex agent](https://docs.langchain.com/langsmith/evaluate-complex-agent)：分别评估最终回答、Agent trajectory 和单步决策；
- [Berkeley Function-Calling Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard)：工具选择、参数、相关性、多轮和执行结果；
- [τ-bench](https://arxiv.org/abs/2406.12045)：以数据库终态衡量任务成功，并用 `pass^k` 衡量一致性；
- [RAGAS Metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)：Context Precision、Context Recall、Faithfulness 等 RAG 指标；
- [AgentDojo](https://papers.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html)：同时度量正常任务 utility 与 Prompt Injection 攻击成功情况。

## 3. CareerAgent 指标体系

| 层级 | 本轮指标 | 为什么需要 |
| --- | --- | --- |
| PDF Chunk | Top3 keyword/page/context hit rate | 验证切块能否保留语义、页码与上下文，而不是只看 chunk 数量 |
| 岗位 RAG | Top1、Recall@3/5、MRR、nDCG@5 | 同时衡量首条命中、召回覆盖和整体排序 |
| 解析 | case pass、字段 grounding、技能 precision/recall | 防止 LLM 把提示注入、计划学习或普通文本解析为真实经历 |
| 规划与工具 | intent、action precision/recall、trajectory success | 区分“回答看起来对”和“Agent 实际会做对” |
| 匹配与定制 | fit label/score、证据 grounding、禁止事实命中率 | 防止适配判断漂移和简历捏造 |
| 安全 | injection recall、false positive rate、投递 Guardrail | 同时控制漏检与误伤正常内容 |
| 端到端 | task success、终态、artifact、LangGraph trace | 验证用户真正拿到完整产物 |
| 可靠性 | pass@1、pass^k | 检查同一任务多次执行是否稳定 |
| 系统性能 | p50/p95/max、成功率、Token、费用、模型路由 | 量化速度、稳定性与成本 |

不提供人为加权的“总分”。一个 `90` 分总分可能同时掩盖投递越权、工具选错或严重幻觉，发布决策应由各层硬门禁共同决定。

## 4. 运行配置与数据规模

运行器：`scripts/run_agent_system_eval.py`，模式为 `full`，默认路由 Flash，面试生成与 claim verifier 等质量敏感节点路由 Pro，关闭 LLM fallback，真实 embedding 使用 `sentence_transformers`，二阶段重排使用 `cross_encoder`。

本轮核心数据集规模：

| 数据集 | Case 数 | 主要覆盖 |
| --- | ---: | --- |
| PDF Chunk | 96 | easy/medium/hard/adversarial 各 24 |
| RAG | 180 | easy/medium/hard/adversarial 各 45 |
| Prompt Injection | 70 | 正常、指令注入、混淆与来源差异 |
| 投递材料 | 27 | 指标捏造、语义捏造、缺 URL、自动化边界等 |
| 自然语言规划 | 20 | 中英混合、否定、多动作、部分表单、资料更新 |
| JD 解析 | 30 | 含 4 个 adversarial case |
| LLM 工作流 | 24 | 简历解析、JD、RAG、适配判断和定制简历 |
| Agent 全流程 | 6 | 搜索、匹配、定制、快速投递与 LangGraph trace |
| Claim verifier | 14 | 支持/不支持、策略、答非所问 |
| 面试完整链路 | 1 | 本轮受真实 API 预算约束的高成本 smoke |

这批数据适合回归和发布门禁，但仍不是生产流量统计：大多数 case 是受控构造样本，完整面试链路只有 1 个 case，可靠性评测只有 3 个 case、2 次重复。因此本报告给出的是当前冻结数据集上的准确值，不外推为真实用户成功率。

## 5. 质量结果

| Suite | 核心结果 | 门禁 |
| --- | --- | --- |
| PDF Chunk | 96 cases；Top3 keyword `0.9479`，page `0.8299`，context `0.7760`；选中 `paragraph_page_900_overlap160` | 通过 |
| 岗位 RAG | 180 cases；Top1 `1.0000`，Recall@3 `0.6125`，Recall@5 `0.7292`，MRR `1.0000`，nDCG@5 `0.7862` | 通过 |
| 岗位相关性 | 13 cases；pass/Top1/Recall@3/Recall@5/MRR 均 `1.0000`，nDCG@5 `0.9495` | 通过 |
| 投递材料 Guardrail | 27 cases；pass `1.0000` | 通过 |
| Prompt Injection | 70 cases；recall `1.0000`，FPR `0.0000` | 通过 |
| 自然语言规划 | 20 cases；pass `0.8500`，完成率 `1.0000`，action precision/recall `1.0000` | **失败** |
| JD 解析 | 30 cases；pass `1.0000`，技能 precision `0.9368`，recall `1.0000` | 通过 |
| LLM 工作流 | 24 cases；E2E `0.7500`，fit label/score `0.8750`，定制和禁止事实检查 `1.0000` | **失败** |
| Agent 全流程 | 6 cases；pass `0.8333`，Top Job/trace/artifact/LangGraph/tailor 均 `1.0000` | **失败** |
| Claim verifier | 14 cases；accuracy/recall/specificity `1.0000`，FPR 和答非所问误接收 `0.0000` | 通过 |
| 面试完整链路 | 1 case；question quality、source backed、pass 均 `1.0000` | 通过 |
| 稳定性 | 3 cases × 2；`pass@1=0.6667`，`pass^2=0.6667`，阈值 `0.8000` | **失败** |

RAG 数据中每个 query 最多有 4 个相关文档，所以 Recall@3 的理论上限是 `0.75`；当前 `0.6125` 相当于达到该上限的 `81.67%`。Top1/MRR 很高说明最佳文档稳定排第一，但 Recall@3/5 和 nDCG 表明长尾相关经历的覆盖与排序仍有提升空间。

## 6. 真实性能与成本

### 6.1 归一化系统指标

一次超时的前台评测进程没有随 shell 退出，和后续续跑进程发生短暂重叠。根据 `LLMCallLog`、trace、时间窗和完全重复的 JD case，确认日志 `#1581-#1599、#1601、#1603、#1605、#1607` 共 23 次 JD 解析调用属于额外开销。以下是剔除这部分评测基础设施开销后的系统指标：

| 指标 | 数值 |
| --- | ---: |
| LLM 调用 | 171 |
| 成功调用 | 170 |
| 调用成功率 | 99.42% |
| Prompt tokens | 176,792 |
| Completion tokens | 41,550 |
| Total tokens | 218,342 |
| 费用 | 0.257601 元 |
| 调用延迟 mean | 4.624s |
| 调用延迟 p50 | 3.538s |
| 调用延迟 p95 | 9.684s |
| 调用延迟 max | 34.788s |

其中 Pro 仅 5 次调用，却消耗 `27,192` tokens 和 `0.095959` 元；主要来自面试答案生成、验证和 claim verifier。Flash 承担其余解析、规划、匹配判断、简历定制和投递文案。面试生成节点单次最慢 `34.788s`，面试验证 `21.859s`，是当前主要延迟热点。

LangGraph 轨迹共有 17 条、88 个工具步骤：工具成功率 `95.45%`，延迟 mean `682.65ms`、p50 `180ms`、p95 `4.089s`、max `6.443s`。4 个失败步骤包含预期的业务 Guardrail 拒绝，也包含 1 个应修复的跨语言证据误拦截，不能简单解释为基础设施可用性只有 95.45%。

### 6.2 原始账单与评测开销

原始报告记录 194 次调用、227,511 tokens、0.267945 元；其中已确认的评测进程重叠开销为 23 次调用、9,169 tokens、0.010344 元。原始值用于对账，归一化值用于描述系统性能，两者都保留，避免把评测器问题算成产品调用成本。

本轮观察到 1 次 `ConnectError`，业务重试后成功。因此 case 完成率为 100%，但调用级成功率不是 100%。这两个指标回答的问题不同，必须同时报告。

## 7. 失败样本与系统判断

### 7.1 自然语言规划不是“只差几个关键词”

- 更新档案 case 正确识别 `update_profile`，但漏抽取 worker 项目事实，profile keyword hit rate 为 `0.6667`。
- 中英混合搜索 case 的 reason 保留了 RAG 偏好，实际 query 却丢了该约束，query keyword hit rate 为 `0.5`。
- “先根据上下文建档再搜索”生成了 `create_profile + search_jobs` 两个 action，却把主 intent 标为 `create_profile`。现有执行器会在建档分支提前返回，后续搜索不会执行。这是**计划 JSON 看似完整、执行语义实际错误**的典型 Agent badcase。

### 7.2 LLM 工作流的失败需要拆成两类

- **系统问题**：PDF 布局噪声和新手简历 case 的适配标签基本正确，但正向证据 grounding 对同义改写过严；计划学习 case 已正确从正向技能中移除 Python，但检索证据命中规则与标注目标不一致。
- **标注校准问题**：推荐算法岗位和 worker 岗位被标为 `weak_fit`，模型依据已有 Python、A/B、指标或 FastAPI/RAG 证据判为 `partial_fit=60`，人工复核认为模型判断有合理性；Prompt Injection 噪声简历被标为 `strong_fit`，但缺少评测和工具实现证据时判 `partial_fit=70` 也有依据。

因此 `75%` 是当前冻结 rubric 下的准确通过率，但不应把 6 个失败全部归因于模型能力。下一轮需要双人复标 fit 等级和证据等价关系，再保持旧标注结果作为可追溯基线，不能为了门禁变绿直接改答案。

### 7.3 全流程失败是阈值边界，不是岗位选错

前端候选人 case 正确检索并选择 Frontend 岗位，Top Job、匹配、定制、trace 和 artifact 全部通过；快速投递阶段将一个真实的跨语言结果判为 unsupported evidence，相似度 `0.6953`，略低于 `0.70` 阈值。此前把“无障碍”中的“无”当作否定词的问题已经修复，本次剩余问题是跨语言语义阈值与证据归一化，而不是放宽整个 Guardrail。

### 7.4 可靠性失败是稳定 badcase

`zh_agent_resume_with_prompt_injection_noise` 两次都失败，其余两个 case 两次都通过。因此 `pass^2=0.6667` 不是温度随机性造成的偶发错误，应优先修复该 case 的适配标注/证据判定契约，再扩大重复次数。

## 8. 本轮评测中修复的问题

- 简历解析不再从任意 “Agent” 文本推断目标岗位，planned reading/learning 不再写成真实技能；无法溯源的可选字段被逐项拒绝并写入 trace。
- JD 解析不再把普通 prompt、提示或注入文本误识别为 Prompt Engineering 技能。
- 投递 Guardrail 的中文否定模式改为有作用域的表达式，避免把“无障碍”误判成否定证据。
- 投递材料强制使用精确 company/title 目标行，避免通用标题通过错误检查。
- 自然语言 action 评测从只看显式 actions 扩展为 intent 对应的 effective action，暴露了主 intent 与多动作执行不一致的问题。
- 面试 verifier 将“证据支持”与“是否回答问题”拆成独立维度，并路由 Pro；14 个门禁 case 从旧版误判恢复到全部通过。
- `LLMCallLog` 记录供应商缓存命中/未命中和 reasoning token，可按模型、route、trace 计算真实费用。
- 系统评测运行器增加 `invocation_id`，后续同一 experiment 的续跑可区分不同进程；空 `evaluation_run_id` 不再触发 SQLAlchemy 空主键警告。

## 9. 下一步优先级

1. 修正多动作计划的执行语义：主 intent 必须覆盖完整任务，或让执行器严格按 action DAG 执行，增加“建档后继续搜索”的端到端断言。
2. 对 24 个 LLM 工作流进行双人复标，拆分 label disagreement 与 grounding failure，建立 Cohen's kappa 或一致率记录。
3. 为跨语言证据增加规范化/多语 reranker 校准集，不直接全局下调 `0.70` 阈值。
4. 扩大可靠性评测到至少 10 个关键 case × 3 次，并单独报告解析、匹配、定制和面试 `pass^k`。
5. 面试完整链路扩到至少 5 个不同 JD/简历组合；在预算允许时报告每包 p50/p95，而不是以单样本推断速度。
6. 将生产 trace 失败自动进入待标注池，发布前运行 frozen regression，发布后监控 task success、人工驳回率、Guardrail 拦截率、成本和延迟分位数。

## 10. 复现与原始证据

真实评测报告：`data/runtime/agent_system_full_postfix_20260722.json`（运行时目录默认不提交 Git）。数据库中的总评测记录为 `EvaluationRun #113`，子评测为 `#101-#112`。

复现命令不会把 API Key 写入仓库：

```powershell
$env:LLM_API_KEY = '<通过当前终端临时注入>'
python scripts/run_agent_system_eval.py `
  --mode full `
  --default-model deepseek-v4-flash `
  --interview-case-limit 1 `
  --reliability-repetitions 2 `
  --token-budget 350000 `
  --output data/runtime/agent_system_full_postfix_20260722.json
```

退出码 `0` 表示所有硬门禁通过，退出码 `2` 表示评测完成但发布门禁失败；suite 内部异常会写入 `suite_errors` 和渐进式 JSON 报告，不会被静默当成零分。
