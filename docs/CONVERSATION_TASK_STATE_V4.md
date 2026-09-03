# CareerAgent 对话任务状态与压缩完整性 V4

## 1. 发布结论

CareerAgent 已将“可执行任务状态”从 Conversation Summary 中拆出，并接入自然语言 Planner、LangGraph State、Checkpoint 和恢复链路。Planner 在原有一次 LLM 调用中同时返回执行计划与 `state_updates`，没有为每轮增加独立总结调用。

普通短对话仍是 1 次 Planner 业务调用；只有较早历史超过阈值时，才增加 1 次 Conversation Compactor 调用。正式状态由确定性 `TaskStateReducer` 合并，摘要只是非权威讨论背景。

## 2. 四层上下文

```text
当前用户消息
  -> Planner 同一次调用输出 plan + state_updates
  -> Pydantic 校验 state_updates
  -> TaskStateReducer 原子合并
  -> 当前 TaskState 写入 LangGraph State / Checkpoint

较早历史超过阈值
  -> Conversation Compactor
  -> 非权威 Summary Artifact
```

后续 Planner 输入为：

```text
当前 TaskState（执行依据）
+ 最近 3 轮原文
+ 较早对话摘要（背景信息）
+ 当前用户消息
```

Profile、JD、RAG Evidence 仍由业务表和 Citation 管理，不写入 TaskState，也不能修改 TaskState。

## 3. 正式任务状态

`TaskState` 位于 `app/models/schemas.py`：

```json
{
  "version": 3,
  "goal": "寻找 Agent 实习",
  "target_role": "RAG Agent",
  "location": "深圳",
  "constraints": ["只看实习或校招"],
  "forbidden_actions": ["auto_apply"],
  "selected_actions": ["search_jobs"],
  "pending_actions": ["tailor_resume"],
  "completed_actions": [],
  "corrections": [
    {
      "field": "location",
      "old_value": "北京",
      "new_value": "深圳",
      "source_message_id": "m17"
    }
  ],
  "provenance": {"location": "m17"}
}
```

它只保存影响当前任务执行的信息，不保存完整简历、JD、Evidence、Assistant 建议或工具原文。

## 4. Planner 单调用增量合同

Planner JSON 新增 `state_updates`。标量字段只允许 `set/clear`，列表使用显式 `to_add/to_remove`；缺失字段表示不修改。未知字段、未知 Action、非法 operation 和空 `set` 值由 Pydantic 拒绝。

来源消息 ID 不由模型生成。程序优先使用请求的 `message_id`，否则匹配最近一条与当前 instruction 相同的用户消息，再否则使用稳定的 `run-{run_id}:user`。

计划或状态增量不合法时，复用现有 Plan Contract Repair，最多修复一次。第二次仍失败则 Planner 节点失败，Reducer 不执行，旧状态不发生部分更新。

## 5. 确定性 Reducer

`app/services/task_state.py` 的 `TaskStateReducer` 实现：

1. 只有当前 `role=user` 的消息能修改状态；Assistant、JD、RAG 和 Tool 内容直接拒绝。
2. 标量值变化时写入 Correction，旧值不再作为当前值。
3. 本轮没出现的字段保持原值，不用默认值覆盖。
4. `forbidden_actions` 默认只追加；删除时必须在当前用户原文中找到明确授权。
5. 每项变化记录当前消息 ID，发生有效变化后版本加一。
6. completed action 会从 pending action 中移除。

高风险确定性兜底覆盖：不要自动投递、不要发送邮件、不要访问其他用户数据、只生成草稿、必须经过确认。该兜底不替代 Planner，而是在 Planner 漏抽取时保证安全限制仍进入正式状态。

## 6. Conversation Compactor V2

摘要字段变为：

```json
{
  "discussion_summary": "",
  "rationales": [],
  "unresolved_questions": [],
  "source_message_ids": [],
  "task_state_version": 3,
  "task_state_claims": {
    "target_role": "RAG Agent",
    "location": "深圳",
    "forbidden_actions": ["auto_apply"],
    "completed_actions": []
  },
  "historical_changes": [],
  "authoritative": false
}
```

完整性检查包括：

- 被压缩消息 ID 必须精确覆盖，不能遗漏或新增；
- `task_state_version` 必须等于压缩时的正式状态版本；
- 状态声明不得与当前 target role/location 冲突；
- 不得新增禁止操作或完成项；
- 纠错旧值若出现在正文中，必须带“曾、原先、已失效、改为”等历史语义；
- 未知摘要字段和未知状态声明拒绝。

首次失败会携带校验错误定向重试一次。第二次仍失败时，如果原文仍在 Planner Token Budget 内则保留原文；原文也放不下则停止 Planner，不能静默接受错误摘要。

## 7. Checkpoint 与 Rewind

`task_state` 是 LangGraph 可序列化 State 字段，`context_refs` 同时记录 `task_state_version`。恢复时从 Checkpoint 读取并重新进行 Pydantic 校验，不从旧 Prompt 或 Summary 推断。

Checkpoint Rewind 复制所选历史 checkpoint 的 `channel_values`，因此使用当时的 TaskState 版本；它不会查询另一个较新分支的状态。恢复仍复用 tenant/user/profile 校验和 Tool Receipt 防重放。

如果当前消息改变了状态，而本轮早期摘要仍绑定旧版本，该摘要 Artifact 保留作审计，但不会写入新的恢复引用。

## 8. 离线评测

数据集：`evals/conversation_task_state_cases.json`，共 48 组，覆盖岗位/地点修改、多限制、撤销限制、省略指代、中英混合、旧新值冲突、Assistant 错误建议、JD/RAG 伪指令、Checkpoint 和长对话。

| 指标 | 结果 |
| --- | ---: |
| Case 通过率 | 48/48（100%） |
| 目标与限制字段准确率 | 100% |
| 用户纠错生效率 | 100% |
| 禁止操作召回率 | 100% |
| 压缩后状态保持率 | 100% |
| 摘要冲突误接受率 | 0% |
| Checkpoint 恢复一致率 | 100% |
| 实际触发 Compactor | 24/48 |
| Compactor 调用 | 24 |
| 普通 Planner 预期调用 | 88 个用户轮次各 1 次 |

离线估算 Token 为 `4,701 -> 2,990.33`，只用于容量分析，明确标记 `offline_estimate_not_provider_usage`，不等同账单 Token。

## 9. 真实 DeepSeek 长对话 A/B

固定 `deepseek-v4-flash`、routing/fallback/thinking disabled。5 组均运行：

- Raw：完整历史直接进入 Planner，1 次业务调用；
- Compressed：1 次 Compactor + 1 次 Planner，共 2 次业务调用。

| 指标 | Raw | Compressed |
| --- | ---: | ---: |
| 完整 Case | 5/5 | 5/5 |
| Compactor 触发率 | 0% | 100% |
| 平均 Provider Input Token | 27,716.8 | 28,281.4 |
| 平均 Provider Total Token | 27,897.2 | 28,827.6 |
| 平均业务调用 | 1 | 2 |
| 平均延迟 | 3,080.49 ms | 6,808.63 ms |
| 字段准确率 | 100% | 100% |
| 纠错生效率 | 100% | 100% |
| 禁止操作召回率 | 100% | 100% |
| Provider usage 完整率 | 100% | 100% |

首次压缩把总输入 Token 增加 `2.04%`，Total Token 增加 `3.34%`，因为压缩本身也需要读取旧历史。这证明 Compactor 不是“首次调用省 Token”的免费操作；它的收益预期来自后续轮次复用摘要、避免持续重复完整历史。本轮尚未实现跨 Run 摘要增量复用，因此不能宣称已获得长期 Token 节省。

最终报告由首批 4 个通过的完整 pair，加修复后重新运行的 `explicit_permission_removal` 完整 pair 合并；每项数据均保留真实 run_id 和 Provider usage。原始报告：`data/runtime/conversation-task-state-real-ab-final.json`。

## 10. 本轮 Bad Case

1. **评测标注伪纠错**：初始角色已经是 RAG Agent，却又标注“改为 RAG Agent”应产生 Correction。修正数据生成规则，只有值真实变化才要求 Correction。
2. **确认表达召回不足**：规则识别“必须经过确认”，漏掉“外发前仍要确认”。扩展语义模式后定向重跑，禁止操作召回恢复 100%。
3. **Prompt Section 未注册**：Compactor 日志使用 `task_state/repair` 新 section，被 Token Harness 在 Provider 调用前拒绝。改用注册的 `working/repair_context` 并增加回归。
4. **Windows 报告路径合并失败**：真实定向 Run 已完成，但相对路径直接 `relative_to` 失败。改用 resolved path，并增加从数据库复用最近完整 pair 的无 API 合并模式。
5. **首次压缩成本为正**：完整历史 Raw 只调用 Planner；Compressed 还要调用 Compactor，因此输入和延迟上升。该结果保留，不用离线估算覆盖。

## 11. 已验证与边界

已验证：单调用计划+状态增量、原子 Reducer、高风险兜底、旧值失效、摘要冲突拒绝、一次重试/原文回退、Checkpoint 序列化、历史版本恢复、跨租户现有门禁、48 组离线和 5 组真实长对话。

尚未充分验证：跨多个用户请求自动复用旧 Summary Artifact、摘要增量更新、真实浏览器前端的多轮聊天入口、长会话线上 P95。当前 API 已可接收和返回 TaskState，但调用方需要在同一会话的下一轮显式带回该状态。

关键任务状态保持率 100% 不代表全部对话语义无损。讨论措辞、非执行背景和低优先级理由仍可能在摘要中损失。
