# CareerAgent Agent 效率与可靠性评测

本文说明如何评测 CareerAgent 的真实 Agent 链路、运行时控制器和缓存，而不是只看某一个模型调用是否返回内容。
评测器是旁路工具，不改变正式业务流程、模型路由、工具权限或门禁策略。

## 1. 评测目标

评测回答四个问题：

1. Agent 能否完成岗位搜索、匹配、简历定制和投递材料等真实任务。
2. Agent 是否按正确的工具顺序执行，是否产生完成产物，是否在高风险动作前经过审批。
3. 检索失败、工具非法、重复无进展、预算耗尽和计划失败时，Runtime 是否能停止或恢复。
4. Cross-Encoder 缓存是否减少重复推理，同时保持结果等价、作用域隔离和并发 single-flight。

评测结果不使用一个加权总分掩盖短板。业务通过率、轨迹正确性、策略安全性、成本和延迟分别报告。

## 2. 评测入口

确定性评测不调用 LLM API：

```powershell
$env:PYTHONPATH='C:\Users\IC\.codex\python312\Lib\site-packages'
$env:REDIS_ENABLED='false'
python scripts/run_agent_efficiency_eval.py `
  --mode deterministic `
  --output evals/results/agent_efficiency_deterministic.json
```

真实评测使用正式的本地 Embedding、Cross-Encoder、Redis 和配置的 LLM：

```powershell
$env:LLM_API_KEY='...'
$env:PYTHONPATH='C:\Users\IC\.codex\python312\Lib\site-packages'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:REDIS_ENABLED='true'
python scripts/run_agent_efficiency_eval.py `
  --mode full `
  --base-url https://llmapi.paratera.com `
  --default-model deepseek-v4-flash `
  --repetitions 3 `
  --token-budget 60000 `
  --output evals/results/agent_efficiency_real.json `
  --trace-output evals/results/agent_efficiency_real.jsonl
```

`--repetitions 3` 用于计算同一批 case 的 Pass^3。评测器还支持 `--reuse-run-ids` 和 `--experiment-id`，用于在汇总代码出错时复用已完成的数据库评测运行，避免重复消耗 API。

输出包括：

- JSON：完整汇总、数据集版本、模型、token、成本、延迟、缓存、故障注入和门禁。
- JSONL：每个真实 case 一行，包含 repetition、EvaluationRun、AgentRun 轨迹和工具步骤；故障注入也单独写入。
- 数据库：真实 AgentRun、AgentStep、AgentArtifact 和 LLMCallLog，支持继续从 case 逐例复盘。

## 3. 业务端到端评测

真实业务复用 `evals/agent_full_flow_cases.json`，覆盖强匹配、跨语言证据、弱匹配门禁和初学者画像等场景。每个 case 至少检查：

- 岗位选择：预期岗位是否排在第一位，分数是否在合理范围。
- 任务完成：AgentRun 是否完成或按预期阻断，是否使用 LangGraph。
- 轨迹：必要步骤、工具名称、参数、顺序、重复调用、审批约束和 Completion Artifact。
- 简历定制：结构化输出是否通过事实门禁，是否达到该 case 的关键词覆盖要求。
- 投递材料：高风险 quick_apply 是否正确完成，或在低适配时正确阻断。

弱匹配被 Fit Gate 阻断不是失败，而是预期安全结果。因而 `quick_apply` 的“completed rate”不能单独解释为业务成功率；报告同时给出 `correct_outcome_rate`、预期阻断数和实际阻断数。

## 4. 指标定义

### 4.1 可靠性

- **Pass@1**：每个 case 第一次运行是否通过的比例。
- **Pass^K**：同一个 case 连续 K 次全部通过的比例。它衡量一致性，比一次通过更严格。
- **case pass rate**：所有 repetition 的 case 试验结果比例，便于核对原始数据。
- **correct outcome rate**：包含“成功完成”和“按预期阻断”两类正确结果，适合 quick_apply 等有安全门禁的任务。

### 4.2 轨迹和工具

从持久化 AgentStep 和 trajectory evaluator 统计：

- 工具调用数、工具成功率和工具延迟。
- 缺失步骤、失败步骤、未预期工具、顺序违反、参数违反。
- 重复调用、审批违反、策略阻断违反和缺少完成产物。

这能区分“答案看起来对”与“Agent 实际按合同完成”。

### 4.3 局部 ReAct 与 Replan

故障注入数据集覆盖：

- 首轮证据不足后换动作恢复。
- 未注册动作在执行前被阻断。
- 重复动作无进展时停止。
- 达到局部预算时停止。
- 计划节点失败后只恢复剩余 DAG。
- 新计划试图改写已完成节点时拒绝。
- 未经审批的高风险动作被策略边界阻断。

每个 case 记录触发率、恢复率、尝试次数、no-progress stop、budget stop、非法动作阻断和完成节点保护。

### 4.4 缓存 A/B

缓存 A/B 使用正式 `RerankResultCacheService`，只把模型评分回调替换为确定性回调，以隔离模型和网络噪声。检查：

- 缓存命中率和模型调用减少率。
- 缓存前后分数和排序是否完全等价。
- 并发请求是否 single-flight，只发生一次实际计算。
- scope、模型版本、语言路由、算法版本和内容变化是否导致正确失效。
- 是否出现跨用户或跨作用域命中。

这不是把一个本地 reranker 延迟当成全系统 SLO，而是验证缓存的正确性和并发行为。真实业务报告还会读取每个 AgentStep 中的缓存元数据。

### 4.5 Token、成本和延迟

LLM 用量来自 `LLMCallLog`，按模型、路由、trace group 以及总量统计：

- prompt、completion、total tokens。
- 成功/失败调用数和用量明细覆盖率。
- 根据 DeepSeek V4 Flash/Pro 价格给出成本下界和上界；供应商没有返回缓存命中拆分时不伪造精确价格。
- 每种 Agent task type 的 mean、P50、P95、max 延迟。

每个延迟维度少于 10 个样本时标记 `low_sample`。样本达到 10 只是最低稳定性门槛，不等于已经建立生产 SLO。

## 5. 门禁

本轮确定性门禁要求：

| 门禁 | 要求 |
| --- | --- |
| 高风险自动 Replan | 0 |
| 非法工具阻断 | 100% |
| 重复副作用 | 0 |
| 已完成节点被重写 | 0 |
| 缓存结果等价 | 100% |
| 缓存跨作用域命中 | 0 |
| 故障注入 case | 100% 通过 |

真实链路还要求：

- API 调用失败率为 0，或失败必须在 case 预期内并留下错误轨迹。
- 每个 case 有可追溯 AgentRun、工具步骤和 Completion Artifact。
- 低适配岗位只能被正确阻断，不能为了提高通过率放宽 Fit Gate。

## 6. 最近一轮真实结果

结果文件：`evals/results/agent_efficiency_real_v4_flash_20260907.json`。

- 模型：DeepSeek V4 Flash，路由 `flash_economy`。
- 3 轮、每轮 6 个全链路 case，共 18 个真实全链路试验；另补测 1 个面试准备 case。
- Pass@1：**1.0**。
- Pass^3：**1.0**。
- 全链路部分 27 次 LLM 调用全部成功，44,425 tokens；面试准备补测增加 5 次调用，合计 32 次、84,776 tokens。
- 总成本区间：**0.071912–0.200674 元**；由于供应商没有提供缓存拆分，报告标记为非精确成本。
- 所有真实 Agent 轨迹的 trajectory evaluator 通过率：**1.0**。
- 缺失步骤、顺序违反、参数违反、重复调用、审批违反、策略阻断违反和缺少完成产物：均为 **0**。
- `find_jobs_for_profile` 18 个样本完成率 **1.0**。
- `tailor_resume_for_job` 15 个样本完成率 **1.0**。
- `quick_apply` 18 个样本中，强匹配案例正确完成，弱匹配案例按预期被 Fit Gate 阻断；报告同时记录预期/实际阻断数量。
- `prepare_interview`：补测 1 个 case，质量通过率 **1.0**，生成 10 道题；但该 case 的 5 次调用额外消耗约 40,351 tokens，使整轮超过 60,000 token 预算。
- 真实运行延迟总体 P50 约 **6.57 秒**、P95 约 **42.63 秒**；岗位搜索、简历定制和投递任务都有至少 10 个样本，因此这些维度标记为 `stable`，但仍需真实用户流量建立线上 SLO。
- 缓存确定性 A/B：命中率 **0.9286**，模型调用减少 **0.9286**，结果等价 **100%**，single-flight 通过，跨作用域命中 **0**。

本轮总发布门禁因此为 **false**，原因只有 token budget breach，不是业务 case 或安全门禁失败。这个结果说明面试准备的生成、答案验证和修复链路需要做调用预算协调：质量通过后不应继续无条件触发多轮验证。下一步应在正式业务侧增加阶段预算、失败类型分级和只针对具体缺陷的验证批次，然后用同一面试 case 做前后消融评测。

### 6.1 扩充样本后的真实复测

为避免结论只依赖 Agent 开发这一类单一案例，评测集扩充为 10 个真实业务场景，并对每个场景连续运行 3 次，共 30 次全链路样本。新增覆盖：

- 前端候选人和数据工程候选人，验证跨岗位语义匹配不会只偏向 Agent 关键词。
- 科研/论文型 Agent 候选人，验证科研经历、实验和信息检索证据能够参与匹配。
- Agent 平台工程候选人，验证工具契约、Redis、幂等和 Checkpoint 等工程能力。
- GraphRAG/多模态候选人，验证 GraphRAG、Neo4j、OCR、视觉语言模型等复合语义。
- Agent 安全与评测候选人，验证 Prompt Injection、Guardrails、审计和对抗评测能力。
- 推荐算法、ML 平台和初学者等低适配候选人，验证检索可以返回相关岗位，但 Fit Gate 不会因为关键词相似而错误放行。

最新结果文件：`evals/results/agent_efficiency_real_v4_flash_extended_20260907.json`。

- 真实模型：DeepSeek V4 Flash；本地多语言 Embedding、Cross-Encoder 和 Redis 均启用。
- 10 个 case × 3 次，共 30 次全链路运行；Pass@1 **1.0**、Pass@K **1.0**、Pass^3 **1.0**。
- 业务正确性：岗位检索 30/30 完成，简历定制 27/27 完成；投递流程 30/30 的最终结果正确，其中 9 次按预期被 Fit Gate 阻断，未伪造投递材料。
- 工具可靠性：岗位检索和简历定制工具成功率 **1.0**；投递流程工具成功率 **0.9474**，9 个失败步骤均为预期的适配门禁阻断，不是异常放行。
- 全链路延迟 P50 **7.973 秒**、P95 **37.276 秒**。主要阶段 P95：岗位搜索 **3.191 秒**、简历定制 **48.120 秒**、投递包生成 **35.098 秒**；因此简历定制是当前最需要优化的延迟热点。
- 53 次真实 LLM 调用，共 **90,884 tokens**，成本区间约 **0.033640–0.106956 元**。该轮没有加入面试准备样本，因此不能用它替代上一轮的面试准备结论。
- 确定性故障注入 7/7 通过：非法动作阻断率 **1.0**、重复副作用 **0**、已完成节点重写 **0**、故障恢复/停止策略符合预期。
- Redis Cross-Encoder 缓存 A/B：命中率 **0.9286**、模型调用减少 **0.9286**、结果等价 **100%**、single-flight 通过、跨作用域命中 **0**。

这轮说明扩充后的评测集上，链路的业务完成和安全门禁是稳定的；但它仍然是离线评测集，不代表已经建立真实流量 SLO。下一轮应补充面试准备场景，并对简历定制阶段做调用预算和并发性能优化。

## 7. 如何解读失败

先看 case 的阶段和轨迹，再看总分：

1. API failure：检查 `LLMCallLog` 的 provider、route、错误类型和重试次数。
2. Tool failure：检查 AgentStep 的参数、工具合同、错误分类和是否发生了不该有的副作用。
3. Retrieval failure：检查召回质量、reranker provider、缓存命中和证据门禁，不要直接调低阈值。
4. Generation failure：检查结构化输出、事实门禁和修复调用；修复只能基于已有证据。
5. Policy failure：优先判定为安全问题，确认高风险动作是否被错误放行、是否重复执行或绕过审批。
6. Latency failure：按 task type 和 trace group 定位，是 API、Embedding、Cross-Encoder、Redis、数据库还是等待锁，而不是只看整条链路的平均值。

## 8. 当前边界

本轮已经覆盖真实全链路和运行时故障模型，但还没有替代生产观测：

- 没有真实用户流量分布、持续窗口和正式 SLO burn-rate。
- 面试准备 task type 需要单独加入真实样本后再统计。
- Redis 缓存 A/B 的确定性回调验证缓存机制；真实模型缓存收益仍需在固定输入重复的业务流量中持续采样。
- Pass^3 只说明本批评测集的一致性，不等价于所有用户输入的泛化能力。
