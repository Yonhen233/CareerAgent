# CareerAgent Token 用量优化 V2

## 1. 目标与边界

Token V2 把一次 Agent Run 的成本拆成可治理对象：业务级 LLM 调用、HTTP attempt、retry、JSON repair、质量 repair、Prompt 区段、Tool Schema、Tool Result、重复上下文和输出上限。优化必须在事实、Citation、Prompt Injection 与租户隔离门禁不退化的前提下生效。

当前正式默认配置为 `TOKEN_OPTIMIZATION_V2_ENABLED=true`、`TOKEN_OPTIMIZATION_SHADOW_MODE=false`。独立 3-case 真实 canary 与 Context/Token 联合真实 canary 均已通过；样本仍不能替代真实用户流量下的持续 SLO 观测，两个开关保留独立回滚能力。

## 2. 已接入主链路的实现

### 2.1 LLM 调用可观测性

`LLMClient` 为每次业务调用生成 `business_call_id`，每个 HTTP attempt 仍单独落一条 `LLMCallLog`。因此可以同时回答业务调用、网络请求、retry/repair token、Provider usage 状态、Prompt 最贵区段和跨调用重复内容。

Prompt 分为 `system_control`、`task_contract`、`skill_instructions`、`tool_schemas`、`profile`、`job`、`evidence`、`conversation_history`、`memory`、`tool_observations`、`repair_context`、`output_schema` 和 `working`。每段记录 token 估算方法、字符数与 SHA256。供应商不返回 usage 时标记 `usage_status=missing`，数据库 token 列保持 0，不用字符估算伪装真实 usage。

### 2.2 Run 级硬预算

`LLMCallBudget` 保留旧 `calls=HTTP attempts` 语义，同时增加 `business_calls`、`repair_calls`、HTTP attempt 上限、输入/输出/总 token 上限和重复上下文 token。Natural Language Agent 和 Interview Agentic RAG 在 V2 Active 时应用这些上限。预算耗尽不可重试，返回已有 Artifact 与结构化错误。

### 2.3 节点级 Context Contract

`NodeTokenBudgetRegistry` 为 Planner、Resume/JD Parser、Matcher、Resume Tailor、Application Packet、Interview Question/Answer、Claim Verifier、Guardrail 和 Completion Gate 定义独立预算。合同声明必需、可选和禁止字段，以及 Skill、Tool、Evidence 类型、输入、输出、History、Evidence 与 Repair 预算。

Planner 的 V2 路径只使用目标、ID、约束、压缩 Profile、typed memory 和紧凑 Tool Catalog，不再读取完整简历、完整 JD 和全量 Evidence。

### 2.4 Skill 与 Tool 渐进披露

`DynamicToolCatalog` 先按 task type 的 Skill allowlist，再按节点、风险和审批状态收紧工具。Planner 只看名称、用途、风险、所属 Skill 和输入字段；进入执行阶段后才允许加载完整 schema。真实调用仍经过 Registry、参数 schema、tenant、risk、approval 和预算校验。

当前固定 LangGraph 主链路原本没有每轮向 LLM 注入 19 个完整 Tool Schema，因此目录容量下降只作为权限与未来 Tool Calling 基础设施，不计入本轮真实 token 降幅。

### 2.5 Batch、共享上下文与增量 Repair

面试链路使用 `BatchToolExecutor` 执行相互独立、低风险、无共享副作用的批次，并为每个 item 返回 `item_id/status/result/error/usage/artifact_ref`。高风险外发、有依赖、修改同一实体或需要审批的操作禁止批处理。

10 题正常路径为批量答案生成与批量 Claim Verification；repair 只接收失败题、失败原因、相关旧输出和最小证据。JSON repair 只接收 malformed JSON 与 parse error。V2 的批量数据协议是：

```text
shared_context = Profile / Job / Evidence
items = question_id / question / required fields
```

批量不是把十个完整 Prompt 放进一个数组。共享上下文只传一次，item 只保留差异字段。

### 2.6 Tool Result、Delta 与缓存

长 Tool Result 可由 `ToolResultArtifactizer` 写入 `AgentArtifact`，后续只传 Artifact ID、类型、哈希、状态和数量；`DeltaContextBuilder` 传递新增 Evidence、状态变化、失败项和审批结果；`ScopedVersionedCache` 的 key 包含 tenant、user、数据/Tool/Prompt/Contract/模型版本和参数哈希，并拒绝缓存副作用工具结果。

Context Runtime V2 已在实际上下文路径使用版本化投影缓存、Artifact 引用和 JIT 读取。通用 Token V2 cache/artifact/delta 类目前主要服务后续工具迁移，并非所有旧 Service 都已经改成统一接口。

## 3. Retry 所有权

网络与 429/5xx 归 LLM HTTP Client；Tool 瞬时错误归 Tool Runtime；Worker 崩溃归 Queue Recovery；JSON 格式归一次 JSON Repair；证据不足归一次质量 Repair；配置、权限、预算耗尽和高风险副作用不重试。每类错误只有一个 owner，避免多层 retry 乘法放大。

## 4. Feature Flag 与回滚

相关变量见 `.env.example`。Shadow 只构建和记录 V2 差异，模型仍走旧路径；Active 才应用 Planner 投影、节点输出上限和新 Run 预算。Execution Provenance 记录实际模式和所有上限，回滚只需关闭 V2。

## 5. 测试与使用

```powershell
python scripts/generate_token_optimization_cases.py
python scripts/run_token_optimization_ab.py
python scripts/run_token_optimization_ab.py --real-llm --limit 3 --question-limit 3
python -m pytest tests/test_token_optimization_v2.py -q
```

真实运行必须通过进程环境注入 API key，关闭 fallback，固定同一模型、temperature、thinking、Prompt 和输出上限。报告见 `docs/TOKEN_OPTIMIZATION_V2_EVALUATION.md`。

## 6. 当前限制

- 真实 Token V2 只跑了 3 case、每例 3 题，不是 36 case 全量付费结论。
- 动态 Tool Catalog 的 schema 容量是 counterfactual 指标，不是当前主链路实际节省。
- 通用 scoped cache 是进程内实现；跨 Worker 共享仍应落 Redis。
- V2 尚未默认 Active；应先对真实用户流量灰度。
