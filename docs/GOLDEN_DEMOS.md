# CareerAgent 黄金演示

这三条路径用于产品演示、面试讲解和发布前回归。它们不是三套独立 Demo，而是同一个 LangGraph 工作流在不同用户目标下启用不同能力。结构化定义位于 `evals/golden_demo_scenarios.json`。

## 演示前准备

1. 在“简历”页上传 `demo_resumes/agent_intern_strong_resume.pdf`，或选择已有简历档案。
2. 在“岗位”页保存一份中文 Agent 开发实习 JD。需要验证真实岗位源时再运行岗位搜索。
3. 在开始页选择简历档案和目标岗位，点击“快速示例”填入对应目标。
4. 运行后先看业务摘要，再展开阶段进度和 LangGraph 事件流。

## 场景一：岗位匹配

用户目标：

> 根据我的简历搜索中文 Agent 开发实习岗位，给出匹配结论、证据和能力缺口。

重点观察：

- 路由层是否选择岗位搜索、JD 解析、证据检索和适配判断 Skill。
- 匹配结果是否给出 `match_score`、已匹配技能、缺失技能和证据数量。
- 目标岗位、headline 和求职意向是否被排除在能力证据之外。
- 真实岗位源失败时，run 是否明确失败并保留 source trace。

## 场景二：证据约束定制

用户目标：

> 结合目标 JD 和我的真实项目证据定制简历，只改写已有事实，不要把检查结果写进简历正文。

重点观察：

- RAG 是否返回具体简历 chunk，reranker 后的证据是否进入 Prompt Packet。
- Evidence Type Classifier 是否区分交付项目、量化证据、课程、计划学习和缺失技能披露。
- Guardrail 是否输出证据覆盖率、unsupported claim、forbidden claim 和 repair 次数。
- 可打印 HTML 是否只包含简历正文；事实检查和改动说明只在产品页面展示。

## 场景三：审批式投递

用户目标：

> 基于目标岗位定制简历并准备投递材料；任何浏览器填写、邮件草稿或发送动作都必须先让我确认。

重点观察：

- `full_career_flow` 是否在高风险动作前进入 LangGraph interrupt。
- 是否生成独立审批记录，并保存 action type、payload hash、决定人和决定时间。
- `browser_apply`、`email_draft`、`email_send` 是否只能通过 HighRiskActionToolService 执行。
- checkpoint 恢复是否复用业务幂等键，避免重复写入简历版本或投递包。
- 业务摘要的副作用层是否显示审批状态、真实外发结果和越权检测结果。

## 统一验收口径

每次 run 的业务摘要分为四层：

| 层级 | 回答的问题 | 关键字段 |
| --- | --- | --- |
| 路由层 | Agent 为什么选择这些能力 | `selected_skills`、`selected_subagents`、`tool_permission_validation` |
| 过程层 | 实际执行是否稳定 | `tool_call_count`、`tool_success_rate`、`repair_count`、`latency_ms`、`idempotency_reuse_count` |
| 结果层 | 给用户交付了什么 | `selected_job`、`match_score`、`evidence_coverage`、`resume_version_id`、`application_id`、`interview_prep_id` |
| 副作用层 | 是否发生高风险动作 | `approval_status`、`high_risk_tools`、`approval_bypass_detected`、`outbound_results` |

验收不能只看最终 Markdown。应同时查看：

- 用户可读摘要：`GET /agent/runs/{run_id}/summary`
- 节点与工具步骤：`GET /agent/runs/{run_id}/steps`
- LangGraph 事件：`GET /agent/runs/{run_id}/events`
- 审批审计：`GET /agent/runs/{run_id}/approvals`
- LLM 调用日志：控制台或数据库中的 `llm_call_logs`

当前业务摘要不会虚构“修改前后分数提升”。只有在同一标注集上真实执行前后对照评测后，才允许把 score delta 写入发布报告。
