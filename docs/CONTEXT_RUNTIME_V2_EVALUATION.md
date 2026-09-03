# Context Runtime V2 评测报告

## 1. 评测时间与范围

- 时间：2026-08-31（Asia/Shanghai）
- 数据集：`evals/context_runtime_cases.json`
- Case 数：40
- 模式：40 Case 离线确定性 A/B + 3 Case 真实 V1/V2 canary
- 外部 LLM 调用：最终 canary 6 次；修复评测契约前的首次 6 次作为 bad case 保留在开发日志
- 报告：`data/runtime/context-runtime-v2-real-canary.json`

本报告先完成 40 Case 离线机制评测，随后完成 3 Case 真实模型 canary。真实部分只验证 Context 使用保真和 Provider usage，不外推为 Fit/Tailor 或全流程线上质量。

## 2. 数据覆盖

40 Case 覆盖长中文/中英简历、双栏/跨页 Citation、相似项目、课程与交付混淆、计划学习、明确未实现、多个数字、长 JD、required/preferred/negative、多轮纠错、城市/岗位修改、长 Tool Output、低相关 Evidence、PDF/JD/Memory 注入、同租户不同用户、跨租户、Checkpoint、Compaction、Artifact/JIT、局部扩展、缓存更新、审批、Receipt replay、英文与双语别名等场景。

每个 Case 都含一个高分主证据、一个低分否定证据、18 个长噪声 Chunk、8 轮历史和一个带完整内容的大 Artifact，用于检验 V2 是否只靠删除全部内容获得 Token 降幅。

## 3. A/B 控制

V1 与 V2 使用相同数据与 query。离线部分使用同一个 Token Estimator；真实 canary 固定 `deepseek-v4-flash`、temperature 0、thinking disabled、fallback false、retry 0 和 1200 输出上限。

## 4. 实际结果

| 指标 | V1 | V2 | 变化 |
| --- | ---: | ---: | ---: |
| 平均估算 Input Tokens | 23527.60 | 7102.05 | -69.81% |
| 实际 Provider Input Tokens（3 Case） | 17,272.00 | 6,291.00 | -63.58% |
| Provider Total Tokens（3 Case） | 17,395.33 | 6,412.00 | -63.14% |
| LLM Calls/Run | 0 | 0 | 0 |
| Context 构建 P50 | 不适用 | 77.93 ms | 未形成线上对照 |
| Context 构建 P95 | 不适用 | 87.72 ms | 未形成线上对照 |
| Cost/Run（3 Case） | ¥0.017519 | ¥0.006533 | -62.71% |
| Critical Fact Recall | 基线原文 100% | 100% | 0pp |
| Required Evidence Recall | 100% | 100% | 0pp |
| Negative Evidence Recall | 100% | 100% | 0pp |
| Citation Integrity | 100% | 100% | 0pp |
| Context Quality Pass Rate | 不适用 | 100% | 40/40 |
| Cache Hit（同 key 二次构建） | 不适用 | 100% | 40/40 |
| Context Reset Recovery | 不适用 | 100% | 1/1 专项机制 Case |
| Prompt Injection Escape | 0 | 0 | 无增加 |
| Cross-tenant Leakage | 0 | 0 | 无增加 |
| Forbidden Field Count | V1 含噪声字段 | 0 | 已删除 |
| Profile/JD Grounding | 未测 | 未测 | 未测 |
| Fit Accuracy | 未测 | 未测 | 未测 |
| Tailor Pass Rate | 未测 | 未测 | 未测 |
| Forbidden Claim Free Rate | 未测 | 未测 | 未测 |
| Hallucination Count | 未测 | 未测 | 未测 |
| End-to-end Pass Rate | 未测 | 未测 | 未测 |

估算 Token 来自当前可用 Token Estimator，报告中的 `tokens_estimated=true`。它可用于同估算器下的离线相对比较，不能等同 DeepSeek 实际账单 Token。

## 5. 发布门禁结论

确定性 Context Gate 通过：估算降幅超过 40%，关键事实、必需/负向 Evidence 和 Citation 为 100%，注入逃逸、跨租户泄漏与 forbidden field 为 0，Context Reset 专项恢复成功。

3 Case 真实 Context canary 的 `v2_can_be_default=true`：V1/V2 的关键事实和 Citation Recall 均为 1.0，usage missing 为 0，实际输入和总 Token 均下降超过 60%。随后 Context + Token 联合真实 canary 的事实、引用、禁止声明、注入和跨租户门禁也通过，因此生产默认已切换到 V2。这个结论仍只适用于当前 canary 与固定模型，真实流量需持续观测，联合结果见 `docs/COMBINED_V2_PRODUCTION_RELEASE.md`。

## 6. 测试结果

- V1 修改前基线：20 passed in 24.11s。
- V2 专项初轮：20 passed in 0.54s。
- 补充 JIT Policy、Required Contract、Memory quarantine 与 Reset 后：23 passed in 0.59s。
- Context + LLM Trace 组合回归：40 passed in 1.84s。
- 完整测试结果见同日开发日志；必须以最终全量 pytest 为准。

## 7. 下一步真实 A/B 预算

3 Case、6 次真实调用已完成。40 Case 全量仍需 80 次调用；应先结合灰度真实流量判断是否值得扩大，避免为重复证明压缩率消耗余额。

真实 A/B 需要补齐：实际 Input/Completion/Total Token、LLM calls、P50/P95、Cost、Critical/Citation 输出 Recall、Fit、Tailor、Forbidden Claim、Hallucination 和 E2E。只有当前版本全部门禁通过，才能把 V2 从 Shadow 切为 Active。
