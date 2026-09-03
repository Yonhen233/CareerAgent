# Token Optimization V2 评测报告

## 1. 评测设计

数据集 `evals/token_optimization_cases.json` 共 36 例，每例 10 题、16 到 24 条 Evidence，覆盖长简历/JD、长历史、用户纠错、相似项目、数字、否定经历、计划学习、跨页 Citation、大 Tool Output、多 Query、部分失败、retry、JSON repair、重复调用、缓存、跨用户/租户、PDF/JD 注入、Checkpoint、审批、Artifact JIT 和 Completion Gate。

离线 A/B 使用本地 tokenizer estimator；真实 A/B 固定 `deepseek-v4-flash`、temperature 0、thinking disabled、fallback false、retry 0。V1 对每题分别生成和校验，V2 使用一次共享上下文批量生成和一次批量校验。两边业务语义、Evidence、Prompt 规则和输出字段一致。

## 2. 离线 36-case 结果

| 指标 | V1 | V2 | 变化 |
| --- | ---: | ---: | ---: |
| 估算 Input Tokens/Run | 40,502.78 | 7,309.78 | -81.95% |
| Business Calls/Run | 20 | 2 | -90.00% |
| Critical Fact Recall | 1.0 | 1.0 | 0 |
| Evidence Recall | 1.0 | 1.0 | 0 |
| Forbidden Claim Free | 1.0 | 1.0 | 0 |
| Injection Escape / Tenant Leakage | 0 | 0 | 0 |

这些 token 是 tokenizer 估算，不是供应商 usage。

## 3. 真实 Token V2：3-case Canary

样本为前三例，每例 3 题。供应商 usage 完整，`usage_missing_calls=0`。

| 指标 | V1 | V2 | 变化 |
| --- | ---: | ---: | ---: |
| Provider Input Tokens/Run | 8,483.00 | 3,880.67 | -54.25% |
| Provider Output Tokens/Run | 435.00 | 315.33 | -27.51% |
| Provider Total Tokens/Run | 8,918.00 | 4,196.00 | -52.95% |
| Business Calls/Run | 6 | 2 | -66.67% |
| HTTP Attempts/Run | 6 | 2 | -66.67% |
| P50 Latency/Run | 14,895.77 ms | 5,083.41 ms | -65.87% |
| P95 Latency/Run | 14,903.30 ms | 5,426.91 ms | -63.59% |
| Cost/Run | ¥0.009353 | ¥0.004511 | -51.77% |
| Critical Fact Recall | 1.0 | 1.0 | 0 |
| Evidence Recall | 1.0 | 1.0 | 0 |
| Forbidden Claim Free | 1.0 | 1.0 | 0 |
| Injection / Tenant Leakage | 0 | 0 | 0 |

真实 release gate 通过。原始文件：`data/runtime/token-optimization-v2-real-canary.json`。

## 4. Context Runtime V2 独立真实消融

为避免把两种优化混在一起，Context V2 单独使用 3 个 case、每例 V1/V2 各一次调用：

| 指标 | Context V1 | Context V2 | 变化 |
| --- | ---: | ---: | ---: |
| Provider Input Tokens/Run | 17,272.00 | 6,291.00 | -63.58% |
| Provider Total Tokens/Run | 17,395.33 | 6,412.00 | -63.14% |
| Cost/Run | ¥0.017519 | ¥0.006533 | -62.71% |
| Critical Fact / Citation Recall | 1.0 | 1.0 | 0 |

原始文件：`data/runtime/context-runtime-v2-real-canary.json`。这是 Context 压缩收益，不能与 Batch/调用合并收益相加。

## 5. 开发中发现的 Bad Case

### 5.1 只有 Batch，没有 Shared Context

第一版把 20 次调用合成 2 次，但每个 item 仍复制完整 Profile、JD 和 Evidence。调用下降 90%，输入 token 只下降 4.87%。修复为 `shared_context + minimal items` 后，36 例离线降幅达到 81.95%。并行解决墙钟时间，Batch 减少回合，Shared Context 才减少重复输入，三者不能混为一个指标。

### 5.2 质量门禁没有真正接入 Release Gate

第一次真实 Token A/B 中，报告虽然显示事实召回异常，release gate 仍返回 true，因为程序只检查 token 和调用数。修复后 Gate 同时要求 V1/V2 的事实、引用、禁止声明、注入和租户指标通过。

### 5.3 整句逐字匹配误判等义回答

原评分要求完整中文事实句逐字出现，模型正常改写也被判失败。没有修改 Gold Label，而是把硬事实拆为 `BM25`、`向量检索`、`RRF` 和精确 `0.xx` 指标四个原子项；允许连接语改写，不允许组件和数字丢失。1-case 调试通过后才重跑最终 3 例。

### 5.4 Context 输出遗漏，不等于 Context 丢失

Context 首轮真实实验第三例中，V2 输出漏掉地点，但 Packet 中地点仍存在。根因是评测 Prompt 没要求遍历 `hard=true`。将相同契约用于 V1/V2 后，3/3 事实与引用均通过。这个 case 说明要分别检查“输入保真”和“模型使用保真”。

## 6. 发布判断

本报告的独立 3-case canary 已通过；随后两个 V2 同时启用的联合真实 canary 也通过，因此 Token V2 已切换为正式默认路径。3-case 仍不足以替代真实流量统计，上线后继续按任务类型比较 P95、repair rate、usage missing、事实/引用门禁和成本，联合结果见 `docs/COMBINED_V2_PRODUCTION_RELEASE.md`。
