# CareerAgent 上下文管理 V3 正式实现与完整流程评测

## 1. 发布结论

CareerAgent 已将上下文管理 V3 接入正式流程，默认配置为：

```env
CONTEXT_RUNTIME_V2_ENABLED=true
CONTEXT_RUNTIME_V2_SHADOW_MODE=false
TOKEN_OPTIMIZATION_V2_ENABLED=true
TOKEN_OPTIMIZATION_SHADOW_MODE=false
CONTEXT_MANAGEMENT_V3_ENABLED=true
```

V3 不另建通用上下文平台，也不引入 `context_manager` SubAgent。它是在现有 Context Runtime V2 上，针对 CareerAgent 各业务节点增加严格白名单、对话语义压缩、关键事实 JIT 补载、Checkpoint 引用恢复、长文档 Schema Batch 和面试共享上下文 Batch。

最终 5 对真实完整 Agent Run 的质量门禁全部通过；V3 的平均端到端延迟下降 `6.60%`，但平均输入 Token 增加 `4.07%`、平均总 Token 增加 `4.38%`、总成本增加 `4.60%`。因此，V3 的上线依据是上下文隔离、事实完整性、恢复能力和批处理合同，不是“已经证明节省 Token”。

## 2. V3 在 Agent Harness 中的位置

```text
用户请求 / Worker 恢复
        |
        v
LangGraph State + Checkpoint + context_refs
        |
        v
节点 Context Contract
        |
        v
Context Runtime V3
  1. 严格白名单投影
  2. 数据库按字段读取
  3. Query-specific Evidence
  4. Conversation Compaction
  5. Token Budget
  6. Final Fact/Citation Check
  7. JIT Minimal Evidence Restore
        |
        v
Prompt Registry -> LLMClient -> Provider
        |
        v
Artifact / Receipt / LLM Trace / Checkpoint
```

LangGraph 负责执行顺序、中断和 Checkpoint；Context Runtime 负责某次节点调用能看到哪些数据。Checkpoint 不保存完整 Prompt，恢复时依据业务 ID 和节点合同重建最小上下文，避免恢复一份过期、不可审计的大字符串。

## 3. 六类真实上下文

| 类型 | 内容 | V3 处理方式 |
| --- | --- | --- |
| Control | System Prompt、Skill、Tool 权限 | 不压缩，只记录版本和哈希 |
| Task State | 用户目标、限制、当前节点、待办步骤 | 结构化持久化，节点白名单读取 |
| Profile/JD | 简历字段、岗位职责和要求 | 数据库存原文，按字段或 Section 读取 |
| Evidence | RAG Chunk、正负证据、Citation | 按当前 Query 重检索、去重和截取 |
| Conversation | 补充、纠错、历史决策 | 最近 3 轮保留原文，较早内容允许 LLM 结构化压缩 |
| Artifact/Receipt | PDF、长结果、审批、外发记录 | Prompt 只传 ID、摘要、状态和哈希 |

只有 Conversation 使用 LLM 语义压缩。Profile、JD 和 Evidence 不由模型摘要替代原始事实；这样既避免数字、否定关系和技能边界被改写，也保留 Citation 可回查性。

## 4. 节点级策略

### 4.1 Planner

Planner 只读取当前请求、正式 `TaskState`、最近 3 轮对话、较早对话摘要、Profile/Job ID 和紧凑 Tool Catalog。它在原有一次调用中同时返回执行计划和 `state_updates`；确定性 Reducer 把目标、地点、约束、纠错、待办和禁止操作写入 LangGraph State。较早对话超过预算时，`ConversationCompactor` 生成带 `source_message_ids` 和 `task_state_version` 的非权威背景摘要。摘要不再重复承担关键状态保存，只要求不与正式状态冲突。详见 [对话任务状态 V4](CONVERSATION_TASK_STATE_V4.md)。

### 4.2 Resume Parser 与 JD Parser

Parser 不加载聊天历史、长期 Memory 和无关产物。短文档直接解析；长文档按页或 Section 分批调用，`DocumentSchemaBatcher` 使用 Python 按 Schema 合并。冲突保留页码、Chunk ID 和来源值，不允许 LLM 静默选择或补造事实。

### 4.3 Matcher 与 Resume Tailor

这两个节点只读取 Profile 白名单、JD 必需/加分/负向要求、当前要求对应的 Evidence、关键负向证据和 Citation。完整聊天、完整 PDF 和全部 TopK 不进入 Prompt。长证据围绕技能、数字和否定语句截取；低相关但关键的“未实现/仅学习”证据会加权保留。

### 4.4 Interview Answer 与 Claim Verifier

面试链路使用：

```json
{
  "shared_context": {"profile": {}, "job": {}, "evidence": []},
  "items": [{"question_id": "q1", "question": "..."}]
}
```

共享的 Profile、JD 和 Evidence 在一个 Batch 中只传一次。答案批量生成、声明批量校验；只对失败题 Repair 和复验。`SharedContextBatcher` 在预算不足时拆 Batch，不会因单题失败重跑全部题。

### 4.5 Application 与 Completion Gate

这两个节点优先使用确定性代码，只读取业务 ID、审批状态、Artifact 状态、Tool Receipt 和数据库终态。高风险工具存在成功 Receipt 时禁止重放；Completion Gate 依据任务合同判断必需产物，不通过“模型说完成了”结束任务。

## 5. 统一构建顺序与关键事实补载

每次 LLM 调用前按以下顺序执行：

```text
读取节点合同
-> 从 Graph State 取得业务 ID
-> 从数据库加载白名单字段
-> Evidence 去重和 Query-specific 筛选
-> 长工具结果转换为 Artifact 引用
-> 加载最近对话和已有摘要
-> 检查 Token Budget
-> 必要时压缩较早对话
-> 生成最终 Context Packet
-> 检查关键事实、Citation 和证据正文
-> 缺失时 JIT 补载最小字段/证据片段
-> 复检；仍缺失则停止 LLM 调用
```

关键点是完整性检查发生在所有压缩之后。JIT 不能只补一个数字或 Citation ID，必须连同解释该事实的最小证据正文一起恢复。JIT 由 Python 和数据库查询完成，不再额外调用 LLM。

## 6. Checkpoint 最小恢复

LangGraph State 持久化 `context_refs`：

```json
{
  "profile_id": 7,
  "job_id": 42,
  "resume_version_id": 18,
  "evidence_citations": ["resume-p3-project"],
  "artifact_ids": [12, 15],
  "approval_id": 9,
  "tool_receipt_ids": ["send-1"],
  "conversation_summary_artifact_id": 21,
  "data_versions": {}
}
```

恢复时，`ContextRecoveryService` 校验 tenant/user/profile，按“下一个节点”的合同查询数据库，重新检索所需 Evidence，并检查审批和已执行 Receipt。Conversation Summary 复用 `AgentArtifact`；数据版本变化或摘要失效时重新生成。

注意：只有以 `checkpoint_resume` 或 `rewind` 执行模式进入编排器，才会从 Checkpoint 继续。直接把失败 Run 改成 `running` 后调用 `run_existing` 仍属于初始执行，不是恢复测试方法。

## 7. 可观测性

每次 LLM 调用日志至少包含：

- `run_id`、LangGraph 节点、trace 名称；
- `business_call_id`、`batch_id`、HTTP attempt；
- 实际模型和路由原因；
- Provider input/output/total Token 与 usage 状态；
- compactor/repair 类型、Prompt section 和预算；
- Context Contract、关键事实与 Citation 检查结果。

这些字段让“调用慢或失败”可以定位到具体节点、Batch 和 Repair，而不是只看到最终 HTTP 失败。

## 8. 实现文件

| 文件 | 职责 |
| --- | --- |
| `app/services/context_runtime.py` | 节点合同、白名单投影、预算、事实账本、JIT |
| `app/services/conversation_compactor.py` | 较早自然语言历史的结构化 LLM 压缩与 Artifact 持久化 |
| `app/services/context_recovery.py` | `context_refs` 校验、节点最小恢复和副作用 Receipt 检查 |
| `app/services/shared_context_batcher.py` | 面试共享上下文 Batch 和预算拆分 |
| `app/services/document_schema_batcher.py` | PDF/JD 分批解析与带来源的确定性 Schema 合并 |
| `app/agents/langgraph_orchestrator.py` | Graph State 中保存并恢复 `context_refs` |
| `app/agents/natural_language.py` | Planner 对话摘要和引用写入正式流程 |
| `app/core/llm.py` | 业务调用、Batch、attempt 和 Provider usage 日志 |

## 9. 测试与真实评测

专项测试覆盖任务书要求的 9 类能力：长对话压缩、遗漏拒绝、节点隔离、负向证据、Citation JIT、最小恢复、副作用防重放、共享 Batch、跨租户拒绝。代码最终全仓回归：

```text
406 passed in 118.18s
```

真实 A/B 固定 `deepseek-v4-flash`，每个 Variant 均运行完整流程：

```text
生成 PDF -> PDF 解析 -> JD 解析/入库 -> 岗位匹配
-> 简历证据检索 -> 简历定制 -> 独立 Guardrail
-> 投递材料 Dry Run -> 6 题面试准备 -> Completion Gate
```

5 个 case 覆盖中文 RAG 后端、LLM 评测、Agent 平台、中英双语 RAG 和 Tool Agent。最终报告由 4 个无污染完整对 + 修复后重新运行的 1 个双语完整对合并，保留每个原始 run_id 和来源报告；不是用局部节点结果拼成完整流程。

| 指标 | V2 | V3 | 变化 |
| --- | ---: | ---: | ---: |
| 完整 Run | 5/5 | 5/5 | 持平 |
| 平均 Input Token | 20,657.4 | 21,498.4 | +4.07% |
| 平均 Output Token | 4,842.2 | 5,118.8 | +5.71% |
| 平均 Total Token | 25,499.6 | 26,617.2 | +4.38% |
| 平均 Business Calls | 8.2 | 8.8 | +7.32% |
| 平均端到端延迟 | 56,072.49 ms | 52,373.55 ms | -6.60% |
| 总成本 | ¥0.151709 | ¥0.158680 | +4.60% |
| Critical Fact Recall | 1.0 | 1.0 | 持平 |
| Citation Integrity | 1.0 | 1.0 | 持平 |
| Guardrail / Application / Interview / Completion Gate | 1.0 | 1.0 | 持平 |
| Provider Usage 完整率 | 1.0 | 1.0 | 持平 |
| Repair Calls | 3 | 5 | +2 |

V3 Token 增加主要来自两个额外定向 Repair，而不是共享上下文被重复复制。当前数据证明 V3 没有破坏质量并改善了该样本的延迟，但不能证明节省 Token。早期窄切片和 3+3 canary 的大幅下降仍是 V2 特定工作负载的历史结果，不能替代本轮完整流程指标。

原始报告：`data/runtime/context-management-v3-full-ab-final.json`。

## 10. 真实 Bad Case 与修复

1. **Hash embedding 让中文 RAG 失真**：完整流程首轮使用 hash embedding，检索失败不是 Context 策略结论。正式重跑改用 `paraphrase-multilingual-MiniLM-L12-v2` 384 维真实向量。
2. **英文 CrossEncoder 降低中文重排**：CJK 改为多语 embedding 语义重排，英文保留 `ms-marco-MiniLM-L-6-v2`。
3. **大 JD 稀释精确技能证据**：相关性门禁增加“Query 中结构化精确技能 Chunk”判断，不再只按整份 JD 技能集合分母计算。
4. **Parser 与 Grounding 别名漂移**：`vector search` 可规范化为 `Vector Database`，但无原文支撑的 `Kubernetes` 继续拒绝。
5. **英文小数句号被当成虚构数字**：数字边界同时兼容中文标点和英文句号；愿望性语句不再当成候选人既有事实。
6. **Repair 配置与质量门禁矛盾**：JSON Repair 从 0 调为 1，答案 Repair 为 2，总调用上限 8，Prompt 上限 100,000；仍受全局 Token 和 Repair 硬预算约束。
7. **面试覆盖只看 skills 标签**：改为读取真实问题和追问文本，避免题目实际覆盖但标签漏写造成误杀。
8. **高风险题被误判为缺失技能**：只有真实 missing skill 或 `jd_gap_drill` 才执行能力边界门禁。
9. **跨语言 Matcher 别名不足**：补充 hybrid/vector/semantic retrieval、多语 embedding、evaluation set/recall 等可交付证据，不把普通 vector search 越级推断成 Vector Database 生产经验。
10. **Checkpoint 重放让轨迹顺序门禁误报**：顺序检查改为仅在前置节点完全不早于后置节点时失败，允许恢复后重放后续节点。
11. **手工恢复测试方法错误**：未设置 `execution_mode=checkpoint_resume` 的 `run_existing` 会从图起点执行。该污染 Run 不进入最终指标，恢复能力以正确模式和确定性测试为准。

## 11. 已验证与尚未验证的边界

已验证：节点白名单、对话摘要遗漏拒绝、关键负向证据、Citation 正文 JIT、跨租户隔离、最小 Checkpoint 重建、Receipt 防重放、共享 Batch、Provider usage、完整流程质量门禁。

尚未充分验证：

- 5 对真实样本不足以给 Token、成本和 P95 建立高置信区间；
- 本轮没有再次用真实 API 做操作系统级进程崩溃注入，跨进程恢复由自动 recovery scanner 与确定性测试覆盖；
- V3 Repair 率高于 V2，需要继续按节点追踪 bad case，而不是放宽事实门禁；
- Conversation Compactor 在本轮完整 case 没有被触发，长多轮真实会话还需独立线上切片；
- 跨 Worker Context Cache 仍不是正式能力。

因此当前发布判断是：V3 已是正式运行能力，质量与恢复合同可上线；效率收益需要持续 SLO 和更大真实流量验证。
