# Context Runtime V2 与 Token Optimization V2 联合上线报告

## 上线结论

2026-08-31 起，CareerAgent 默认同时启用：

```text
CONTEXT_RUNTIME_V2_ENABLED=true
CONTEXT_RUNTIME_V2_SHADOW_MODE=false
TOKEN_OPTIMIZATION_V2_ENABLED=true
TOKEN_OPTIMIZATION_SHADOW_MODE=false
```

两套开关仍可独立回滚，但正式流程不再以 Shadow 作为默认路径。Execution Provenance 会把实际生效版本、开关、节点合同和运行预算写入每次 Run，不能只根据部署配置推测某次任务使用了哪套 Runtime。

## 为什么必须做联合评测

独立消融回答“某一套 V2 自己贡献了多少”，联合评测回答“两个正式开关一起打开会不会互相污染或降低质量”。两者不能互相替代，也不能把两份独立降幅直接相加。

联合工作负载由两条真实主链路组成：

1. 一次长 Profile/JD/Evidence 的结构化上下文任务，主要验证 Context Runtime V2。
2. 一次三问题的面试答案与事实校验任务，主要验证共享上下文 Batch、调用预算和 Output Policy。

这是一种可解释的组合口径，不代表所有线上流量都按 1:1 分布。生产分析仍应分别查看各 lane 指标。

## 离线全量结果

- Context 数据：40 case。
- Token 数据：36 case，每例 10 题。
- 平均估算输入 Token：`64,030.38 -> 14,411.83`，下降 `77.49%`。
- 组合业务调用：`21 -> 3`，下降 `85.71%`。
- Context Critical Fact Recall、Citation Integrity：`1.0`。
- Prompt Injection Escape、Cross-tenant Leakage：`0`。
- 联合离线 Gate：通过。

原始报告：`data/runtime/combined-v2-offline.json`。

## 真实 API 联合 canary

固定 `deepseek-v4-flash`、temperature 0、thinking disabled、fallback disabled、模型路由 disabled。Context 与 Token 各取 3 case，Token 每例 3 题。

| 指标 | 两套 V2 关闭 | 两套 V2 开启 | 变化 |
| --- | ---: | ---: | ---: |
| Provider Input Tokens | 25,756.33 | 10,225.67 | -60.30% |
| Provider Total Tokens | 26,313.33 | 10,755.00 | -59.13% |
| Business Calls | 7 | 3 | -57.14% |
| 组合延迟 | 17,387.20 ms | 9,511.22 ms | -45.30% |
| 成本 | ¥0.026870 | ¥0.011284 | -58.01% |

质量结果：

- Context Critical Fact Recall：`1.0`。
- Context Citation Integrity：`1.0`。
- Token V2 Critical Fact Recall：`1.0`。
- Token V2 Evidence Recall：`1.0`。
- Forbidden Claim Free Rate：`1.0`。
- Prompt Injection Escape：`0`。
- Cross-tenant Leakage：`0`。
- Provider usage 缺失：`0`。
- 联合 release gate：通过，`production_default_eligible=true`。

原始报告：`data/runtime/combined-v2-real-canary.json`。

## 上线前发现的 Bad Case

### 未注册节点在 Active 模式读取空 Context Result

`observe_text_prompt` 对没有 Context Contract 的 trace 会返回 `None`，Shadow 默认时调用端不会读取 packet；切到 Active 默认后，原代码直接访问 `context_v2_result.packet`，普通测试调用会崩溃。

修复为只有“开关启用且命中合同”时才替换 system/user prompt。未注册调用保持原 Prompt，但仍由 LLM 日志和 Token Budget 管理。这个问题说明 Feature Flag 从 Shadow 转 Active 必须覆盖“不适用”分支，而不只是成功分支。

### 默认值切换污染独立 A/B

如果脚本依赖应用默认配置，正式启用后 Token A/B 会夹带 Context V2，Context A/B 会夹带 Token V2，历史结论无法复现。修复后独立实验强制关闭非目标变量，联合实验显式设置 V1 全关、V2 全开，并在报告的 `ab_controls` 中持久化控制条件。

## 运行与回滚

联合离线：

```powershell
python scripts/run_combined_v2_ab.py --output data/runtime/combined-v2-offline.json
```

真实 canary 必须通过进程环境注入 API key，不写入脚本或报告：

```powershell
python scripts/run_combined_v2_ab.py --real-llm --context-limit 3 --token-limit 3 --question-limit 3
```

紧急回滚可以分别将对应 `*_ENABLED` 设为 `false`；是否同时开启 Shadow 由排障需要决定。回滚后应检查 Execution Provenance、usage missing、Critical Fact Recall、repair rate、P95 和成本，而不是只确认接口返回 200。

## 仍需持续观察

真实联合 canary 只有 3+3 case，它证明当前固定模型和样本下可以正式默认启用，不等价于已经获得真实用户流量下的长期统计置信度。上线后仍需按 Planner、Parser、Matcher、Tailor、Interview 分 lane 观察 SLO 和 bad case，并保留独立回滚能力。

## 后续 V3 完整流程校正

上述 canary 仍是 V2 上线时的特定混合工作负载历史结论。后续 Context Management V3 使用 5 对真实完整 Agent Run 重跑了 PDF 解析、JD 解析/入库、匹配、定制、Guardrail、投递 Dry Run、面试和 Completion Gate。质量门禁全部通过，但 V3 相对“两套 V2 已开启”的基线平均 Input/Total Token 增加 4.07%/4.38%，成本增加 4.60%，不能把本页的 -60.30% 外推成 V3 完整流程收益。最新结论见 [上下文管理 V3 正式实现与评测](CONTEXT_MANAGEMENT_V3_PRODUCTION.md)。
