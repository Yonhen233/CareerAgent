# CareerAgent Context Runtime V2

## 1. 目标与发布状态

Context Runtime V2 将“把一个大字典截到固定字符数”升级为节点级 Context Harness。它不负责 LangGraph 调度，也不是调用 LLM 的 `context_manager` SubAgent；它在每次模型调用前，根据节点合同从业务状态中构建最小、可追踪、可恢复的 Context Packet。

当前状态：**实现完成，独立确定性/真实门禁及 Context + Token 联合真实 canary 均通过，已经切换为正式默认路径。**

```env
CONTEXT_RUNTIME_V2_ENABLED=true
CONTEXT_RUNTIME_V2_SHADOW_MODE=false
CONTEXT_MANAGEMENT_V3_ENABLED=true
```

V2 关闭时仍使用原 `ContextCompressor`；Shadow 同时构建 V2 并记录差异，但模型使用 V1；Active 才把 V2 结构化投影用于 fit/tailor，并对其他已注册 LLM trace 执行节点 Token 合同。

V2 是节点合同、预算与事实账本基线；正式流程已经叠加 CareerAgent 定向的 V3。V3 增加六类业务上下文、普通对话 LLM 压缩、最终完整性检查后的 JIT 正文补载、`context_refs` 恢复、长文档 Schema Batch 和面试共享上下文 Batch。完整说明及 5 对真实全流程 A/B 见 [上下文管理 V3 正式实现与评测](CONTEXT_MANAGEMENT_V3_PRODUCTION.md)。

## 2. 在 Agent Harness 中的位置

```text
LangGraph State / Checkpoint
        |
        v
Node + Task Contract + Skill Policy
        |
        v
Context Runtime V2
  - Context Contract
  - Token Budget
  - Critical Fact Ledger
  - Projection / Evidence Shard / Compaction
  - JIT / Cache / Trace
        |
        v
Prompt Registry -> LLMClient -> Provider
```

Graph State 可以保存大量 ID 和恢复信息，但只有 V2 Packet 中的内容会进入 Prompt。Artifact、完整 PDF 和旧 Tool Output 默认保留在数据库，只向模型暴露引用。

## 3. 节点合同

源码：`app/services/context_runtime.py` 的 `CONTEXT_CONTRACTS`。

| 合同 | 主要上下文 | 特殊策略 |
| --- | --- | --- |
| `natural_language_planner` | Goal、约束、Skill metadata、Memory | 允许长期 Memory 与 Context Reset |
| `profile_resume_parser` | 原始简历页/Chunk | 高 Evidence 预算，允许原文展开 |
| `jd_parser` | JD section/Chunk | 保留 required/preferred/negative |
| `job_matcher` | Profile、JD、匹配证据 | Evidence 优先，不加载完整聊天 |
| `resume_tailor` | 项目事实、JD、正负 Evidence | 负向事实和数字强制保留 |
| `application_packet` | Verified Resume、JD、审批边界 | 只消费已验证产物 |
| `interview_question_generator` | JD、项目、面经 | 按题型检索 Evidence |
| `interview_answer_generator` | 当前问题 Evidence Shard | 允许局部 Citation 展开 |
| `claim_verifier` | Claim 与最小支持证据 | 小窗口、零 Memory |
| `guardrail` | Candidate Output 与 source facts | 数字、否定、Citation 优先 |
| `completion_gate` | Goal、Step、Artifact、数据库状态 | 不需要完整自然语言 Evidence |

合同包含版本、必需/可选/禁止字段、允许 Evidence 类型、输入上限、输出预留、Tool Schema 预算、Memory 权限、JIT 策略、Critical Fact 类型和预算不足时的失败方式。

## 4. 六类业务上下文

V3 的正式分类是 Control、Task State、Profile/JD、Evidence、Conversation 和 Artifact/Receipt。下面的 Control、Working、Evidence、Memory、Artifact 是 V2 Runtime 的内部预算视图：其中 Working 对应 Task State，Memory 中只有 Conversation 允许语义压缩，Profile/JD 不再混入 Working 或由摘要替代。

### Control Context

只包含 System Prompt、Task Contract、Skill Contract 和 Tool Policy。普通压缩器不能改写；PDF、JD、RAG、Memory 和 Tool Output 无论内容是什么，都不能通过 `promote_to_control` 提升权限。Trace 只保存版本和哈希。

### Working Context

保存当前 Goal、约束、未完成步骤、最近错误、最新决策和必要 Tool Receipt。完成步骤的长输出改为 Artifact/Receipt 引用；Profile/JD 使用字段白名单，调试 dump、网页 tracking payload 和 raw tool log 不进入 Prompt。

### Evidence Context

Evidence 按当前节点 query 重新构建，不复用一份全局 TopK。排序综合检索 score、trust、critical、recency 和 negative boost；去重键优先使用 Citation/Chunk UID。长 Chunk 围绕 query、否定词或关键事实截取窗口，而不是固定取开头。输出保留 Citation、类型、polarity、来源、页码、score、trust、untrusted 和 injection risk。

### Memory Context

`AgentMemory` 已支持 tenant/user/profile/memory_type、active/superseded、TTL 和 source run。V2 只加载当前 scope 的 active、未过期记录；高风险 Prompt Injection Memory 不进入上下文。最近记录保留原结构，旧内容可压缩为非权威结构化记录并保留 source IDs。

### Artifact Context

完整 PDF、历史简历、评测报告和工具日志默认只注入 artifact ID、type、URI、SHA256、status、summary 或 receipt ID。模型需要原文时必须通过 JIT Loader 按指定字段、Citation 或 Artifact ID 展开。

## 5. Token Budget

Estimator 优先顺序：

1. `CONTEXT_TOKENIZER_MODEL` 指向的本地模型 Tokenizer；
2. 可用时使用 `tiktoken/cl100k_base` 作为代理；
3. 中英文感知 heuristic。

后两种必须标记 `tokens_are_estimated=true`。供应商返回真实 `prompt_tokens` 后记录到 `context_compression_traces` 并校准估算比例；供应商 usage 缺失时实际 Token 保持 0，不用字符数补造。

```text
可分配输入 = min(节点上限, 模型窗口)
           - 输出预留
           - 安全余量
           - Control Context
           - Tool Schema
```

每个合同再用自己的 Working/Evidence/Memory/Artifact 权重分配，不能共用固定比例。

## 6. 分级压缩

1. Level 0：去重消息、Evidence 和重复 Tool Result。
2. Level 1：确定性 Profile/JD/Run 字段投影。
3. Level 2：query-specific Evidence Shard、最小支持窗口和 Citation 保留。
4. Level 3：旧 Conversation 由 LLM 压缩为带 source message IDs 的结构化非权威摘要；Profile/JD/Evidence 不做 LLM 摘要。
5. Level 4：写 Handoff，重建 Goal/约束/未完成步骤/Receipt/Artifact/Citation 上下文。

软水位用于低损去重，高水位触发 Compaction，硬水位只允许声明 `context_reset` 的合同 Reset；其他节点在模型调用前抛出 `ContextBudgetExceededError`。

## 7. Critical Fact Ledger

Ledger 覆盖身份、目标、城市/时间/岗位约束、项目技术栈和数字、实习信息、JD required/preferred、否定事实、Citation、审批、外部副作用 Receipt 和未完成 Goal。

压缩后计算：

```text
Critical Fact Recall = retained / before
```

缺失事实只做局部展开；硬事实仍缺失时拒绝使用压缩结果。Ledger 不把摘要升级为数据库事实，原始来源 ID 始终保留。

## 8. JIT Context Loading

`ContextJITLoader` 提供：

- `load_profile_fragment`
- `load_job_fragment`
- `load_evidence_fragment`
- `load_artifact_excerpt`
- `load_session_decisions`
- `load_prior_run_outcome`

构造 Loader 时必须给出 `ContextScope` 和当前 Skill/Tool Policy 允许的 operation set。每次调用检查 tenant/user/profile、字段白名单、调用次数和单次 Token。Receipt 记录 source、selector、Token、untrusted 和哈希；不允许通过重复 JIT 重载整份文档。

## 9. Cache 与失效

缓存键包含：tenant/user/profile、data version、contract/version、Prompt version、query 和输入哈希。用户数据不跨 scope 共享；Profile/JD 更新后 data version 改变，自然形成新键。缓存命中不会替代数据库/JIT 的当前权限检查。

当前缓存是进程内有界 LRU，只适合单 Worker 投影复用；跨 Worker 共享缓存尚未实现，不能宣称 Redis Context Cache 已上线。

## 10. 可观测性

每次已注册 LLM trace 会产生 `ContextCompressionTrace`，记录 Runtime/Contract/Prompt/Skill/Tool Policy 版本、估算与实际 Token、类别占比、压缩 Level、去重数、Evidence 数、Critical Fact Recall、JIT、Cache、Reset、延迟和质量门禁。Trace 不保存完整简历、邮箱、电话、API Key 或原始 Artifact。

Execution Provenance v5 同时固化 Run 使用的 V2 模式、合同 Manifest 和 Token 水位。

## 11. 回滚和发布

确定性 Context Gate 通过只能证明投影和保真机制，不足以切默认。切换前必须在同模型、同 Prompt、同温度、同输出上限、同 Tool/RAG 配置下运行真实 A/B，并满足：Critical Fact/Citation/negative/approval 100%，Evidence 与 E2E 下降不超过 1 个百分点，幻觉和禁止声明不增加，平均实际 Input Token 降低至少 40%，P95 和费用无显著恶化。

出现回归时将 `CONTEXT_RUNTIME_V2_ENABLED=false` 即刻回到 V1；Shadow 可继续保留诊断，也可单独关闭。

## 12. V3 上线后的当前限制

- V2 的 40 Case 确定性与 3 Case 窄切片仍是历史基线；V3 已补 5 对真实完整 Agent Run，但尚不足以建立长期流量置信区间。
- 默认模型没有配置本地专用 Tokenizer，当前离线报告使用显式标记的代理/估算 Token，不是供应商账单 Token。
- V3 完整流程已经测量 Fit、Tailor、Guardrail、Application、Interview 与 Completion；5 对全部完成且 Critical Fact/Citation 为 1.0。
- Context Cache 为进程内 LRU，不是多 Worker 共享缓存。
- Level 3 的普通 Conversation LLM Compaction 已接入并有遗漏拒绝测试；本轮完整 case 是短会话，没有触发 compactor，仍需长多轮真实切片。
- V3 已接入 Planner、Parser、Matcher、Tailor、Interview、Application 和 Completion；恢复的操作系统级崩溃注入本轮未使用真实 API 重跑。
- 5 对完整流程中 V3 Input/Total Token 和成本分别增加 4.07%/4.38%/4.60%，不能继续用 V2 窄切片宣称 V3 已节省 Token。

## 13. 修改文件清单

核心代码：`app/services/context_runtime.py`、`app/services/context_compressor.py`、`app/core/llm.py`、`app/core/config.py`、`app/models/entities.py`、`app/services/execution_provenance.py`。

评测与测试：`evals/context_runtime_cases.json`、`scripts/generate_context_runtime_cases.py`、`scripts/run_context_runtime_ab.py`、`tests/test_context_runtime_v2.py`、`tests/test_llm_debug.py`。

配置与文档：`.env.example`、`README.md`、`docs/CONTEXT_RUNTIME_V2.md`、`docs/CONTEXT_RUNTIME_V2_EVALUATION.md`、`docs/EVALUATION.md`、`docs/CAREER_AGENT_SYSTEM_DESIGN_AND_EVALUATION.md`、`docs/DEVELOPMENT_LOG.md`。
