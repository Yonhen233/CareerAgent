# Career Agent LLM 选型评测（2026-09-12）

## 结论

本轮建议将 **DeepSeek-V4-Flash** 作为 Career Agent 默认模型。它是本轮唯一同时通过规划、JD 解析、匹配/证据、简历定制、反幻觉和面试题库硬门禁的有效候选，并且累计 Token、提供方累计延迟和 repair 次数最低。

**DeepSeek-V4-Pro** 可作为高复杂度任务的候选 fallback，但本轮没有比 Flash 提供质量增益：核心业务门禁相同，Token 多 9.1%，提供方累计延迟约 1.9 倍，面试还触发了 1 次 repair。

**Qwen3.5-Plus、Kimi-K2.5、GLM-5.2** 暂不作为默认模型。它们的共同问题不是基础解析失败，而是 `mixed_zh_en_agent_observability_role` 这个中英混合、需要严格证据约束的岗位在 fit explanation 或 tailor 阶段未通过；这正是 Career Agent 不能放宽的业务约束。

## 评测范围和方法

评测运行在每个模型独立的 SQLite/checkpoint 目录中，关闭模型路由、fallback、thinking 和 Redis，保证一次运行不会混用候选模型。固定切片覆盖：

- 自然语言规划：4 个 case，包含中文、否定、内嵌 JD、显式 UI 动作覆盖和高风险歧义。
- JD 解析：4 个 case，检查 required skill precision/recall/F1、grounding 和不存在技能的拒绝。
- Career Agent 工作流：3 个 case，覆盖 profile/JD grounding、Top-K 证据、fit label/score、一致性、简历定制和 forbidden claim。
- 面试题库：1 个真实面试准备 case，覆盖题目生成、技能覆盖、来源支撑、答案生成、验证和 repair。

评测命令：

```powershell
python scripts/run_multi_model_selection_eval.py --concurrency 3
python scripts/run_multi_model_selection_eval.py --models GLM-5.2 --concurrency 1 --output-dir evals/results/model_selection_20260912_glm52
```

硬门禁为：无调用失败；规划通过率不低于 0.9；JD 解析通过；工作流端到端通过率为 1.0；简历定制通过率为 1.0；forbidden claim-free 为 1.0；面试准备通过。软指标再比较 JD required skill F1、Token、延迟、repair 次数。

## 结果矩阵

| 模型 | Planner | JD F1 | Workflow | Tailor | 禁止声明安全 | 面试 | 总 Token | 提供方累计延迟 | 核心批次 wall | 面试批次 wall | Repair | 结论 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **DeepSeek-V4-Flash** | **1.000** | **0.9643** | **1.000** | **1.000** | **1.000** | **1.000** | **53,490** | **110.8s** | **110.3s** | **46.7s** | **1** | **默认** |
| DeepSeek-V4-Pro | 1.000 | 0.9643 | 1.000 | 1.000 | 1.000 | 1.000 | 58,334 | 210.1s | 144.6s | 95.5s | 2 | 复杂任务候选 |
| Qwen3.5-Plus | 0.750 | 0.9365 | 0.667 | 1.000 | 1.000 | 1.000 | 79,363 | 384.3s | 200.1s | 222.1s | 2 | 暂缓 |
| Kimi-K2.5 | 1.000 | 0.9643 | 0.667 | 0.667 | 1.000 | 1.000 | 71,910 | 428.1s | 228.8s | 231.0s | 3 | 暂缓 |
| GLM-5.2 | 1.000 | 0.9365 | 0.667 | 0.667 | 1.000 | 1.000 | 57,693 | 268.2s | 203.1s | 97.6s | 2 | 暂缓 |

这里的“提供方累计延迟”是该模型所有 LLM 调用 latency 的求和，适合比较模型调用成本；“批次 wall”是套件批量运行耗时，受并发和进程开销影响，不能直接当作单用户请求 SLO。网关未配置价格，因此没有把 Token 换算成人民币成本。

## 业务差异解读

Flash 和 Pro 的基础能力相同：规划、JD 解析和工作流硬门禁均通过。Pro 的额外能力没有转化成这组 case 的业务收益，反而带来更高延迟和一次额外面试修复。

Qwen 在 JD 和简历定制的基础结构上表现稳定，但规划有 1 个歧义输入失败，且 fit explanation grounding 为 `0.6667`。Kimi 的 fit explanation 通过，但 mixed case 的简历定制 semantic grounding 只有 `0.5`，并留下 unsupported semantic claim。GLM-5.2 同一 mixed case 的 tailor semantic grounding 为 `0`，repair 后仍未通过。它们说明“结构化输出正确”不能替代 Career Agent 所需的事实边界控制。

面试题库的结构门禁在所有有效模型上都通过，但调用链差异明显：Flash 3 次调用、0 次 interview repair；Pro 5 次调用、1 次 repair；Qwen 7 次调用、2 次 repair；Kimi 和 GLM-5.2 各 5 次调用、1 次 repair。因此面试链路要同时看质量和 repair/延迟，不能只看最终 pass rate。

## 网关与结果边界

本环境当前只有一个可用的聚合 base URL：`https://llmapi.paratera.com`。本轮在同一网关内并行切换模型 ID，优点是提示词、运行参数和数据集保持一致；它不能代表 DeepSeek、Qwen、Kimi、GLM 等独立供应商在网络可用性、价格和限流策略上的结论。要形成供应商级选型结论，需要补充各厂商官方 base URL 和对应密钥后复跑同一套评测。

`GLM-5` 的 smoke probe 返回 200，但完整业务调用返回 HTTP 404 `Invalid model/no healthy deployments`，因此按部署兼容性失败排除；已用可用的 `GLM-5.2` 补跑。这个结果应作为网关模型路由配置问题处理，不能解释成 GLM 模型质量分数。

面试 case 只有 1 个，当前结果是选型初筛而不是总体能力排名。正式发布前应扩大中英混合 JD、事实缺失、计划性表述、相邻技能和多事实页面的工作流集，并在独立供应商 endpoint 上重复至少两轮。

详细机器可读结果：[`final_comparison.json`](../evals/results/model_selection_20260912/final_comparison.json)。原始分模型结果保留在同目录，未纳入版本库。
