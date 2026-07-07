# CareerAgent Redis + SQLite 架构说明

CareerAgent 现在采用 Redis + SQLite 混合架构：SQLite 是 source of truth，保存用户档案、岗位、chunk、embedding、Agent run、step、artifact、event、审批、checkpoint 和评测结果；Redis 是 coordination layer，负责后台队列、run lock、短窗口限流、cancel flag、heartbeat 和事件 pub/sub。

## 后台运行链路

1. 前端调用 `POST /agent/runs/background`。
2. FastAPI 创建 `status=queued` 的 `agent_runs` 记录。
3. `RedisTaskRunner` 把 `run_id` 写入 `REDIS_QUEUE_NAME`。
4. `scripts/run_agent_worker.py` 从 Redis 队列消费。
5. Worker 用 `career_agent:runs:lock:{run_id}` 获取 run lock。
6. Worker 写 Redis heartbeat，然后调用 LangGraph Orchestrator 执行。
7. 每个节点写 SQLite trace；`TraceService` 同步发布 Redis event channel。
8. run 完成、失败、等待确认或取消后，SQLite 记录最终状态。

Redis 不可用时，后台 run 入队会直接返回 503，避免“接口成功但任务没有真实进入队列”的假成功。

Worker 主循环会定期运行 queued recovery scanner：扫描 SQLite 中超过阈值仍处于 `queued` 的 Agent run，并重新入队。控制台也可以手动调用 `/ops/queue/recover-queued`。worker 级异常会按 `REDIS_WORKER_MAX_ATTEMPTS` 重试，超过次数写入 DLQ，保留失败 payload、错误原因、`dlq_id` 和失败时间；控制台可按 `dlq_index` 人工选择重放或丢弃，并写入运维审计。

## SQLite 负责什么

- `profiles`、`jobs`、`resume_chunks`、`job_chunks`
- embedding JSON 和 RAG metadata
- `resume_versions`、`applications`、`interview_preps`
- `agent_runs`、`agent_steps`、`agent_artifacts`、`agent_events`
- `agent_approvals`
- `ops_audit_events`
- `llm_call_logs`、`evaluation_runs`
- LangGraph SQLite checkpoint

## Redis 负责什么

- `REDIS_QUEUE_NAME`：后台 Agent run 队列。
- `REDIS_DEAD_LETTER_QUEUE_NAME`：worker 级异常超过最大重试次数后的 dead-letter queue。
- `career_agent:runs:lock:{run_id}`：避免多个 worker 同时执行同一个 run。
- `career_agent:runs:cancel:{run_id}`：用户取消 flag，节点开始前会检查。
- `career_agent:runs:heartbeat:{run_id}`：worker 执行心跳。
- `career_agent:rate:profile:{profile_id}`：短窗口 run 创建限流。
- `career_agent:events:{run_id}`：事件 pub/sub，加速 SSE 实时性。

## 幂等与并发

关键写库节点都有业务幂等键：

- `agent_run:{run_id}:resume:{profile_id}:{job_id}`
- `agent_run:{run_id}:application:{profile_id}:{job_id}:{resume_version_id}`
- `agent_run:{run_id}:interview_prep:{profile_id}:{job_id}`

这些 key 写入业务表并加唯一索引。checkpoint 重放、resume 重复点击、worker retry 或节点被再次调用时，节点会先查询已有产物，命中后复用并写 `idempotency_reused` 事件。

## 人工审批审计

投递包生成前不只依赖前端按钮。`create_application_packet` interrupt 前会创建 `agent_approvals`：

- `action_type=application_packet`
- `status=pending`
- `payload_hash`
- `payload_summary_json`

用户确认后状态变为 `approved`，拒绝后变为 `rejected`，取消 run 后 pending approval 变为 `cancelled`。审批动作类型已覆盖 `application_packet`、`browser_apply`、`email_draft`、`email_send`，后续接浏览器投递、邮件发送或日历操作时可以复用同一张审批表。

浏览器辅助填写、邮件草稿和邮件发送不直接暴露裸工具入口，而是通过 `HighRiskActionToolService`：

1. `request_approval()` 创建或复用 `agent_approvals` pending 记录。
2. 人工在控制台审批。
3. `execute_after_approval()` 检查 approval 是否为 `approved`，未通过则直接报错。
4. 放行时写 `ops_audit_events` 和对应 run trace。

## 运维审计

`ops_audit_events` 保存不一定绑定某个 run 的运维动作，例如：

- `dlq_payload_replayed`
- `dlq_payload_discarded`
- `browser_apply_tool_execution_released`
- `email_draft_tool_execution_released`
- `email_send_tool_execution_released`

这张表和 `agent_events` 分工不同：`agent_events` 是单个 Agent run 的业务 trace，`ops_audit_events` 是跨队列、跨工具的运维审计。

## 取消与 Stale Run

`POST /agent/runs/{run_id}/cancel` 支持取消 `queued/running/waiting_for_confirmation` run：SQLite 状态改为 `cancelled`，写 `run_cancel_requested/run_cancelled`，写 Redis cancel flag，pending approval 标记 cancelled。后续节点开始前检查状态和 cancel flag，阻止继续写投递包或面试包。

`GET /ops/agent-runs/stale` 和 `POST /ops/agent-runs/mark-stale` 用 `agent_events` 的最后事件时间识别长时间无进展的 running run，并标记为 failed，输出 `error_type=stale_run_timeout`。

## Prompt Injection 防护

外部 JD、PDF 简历、RAG evidence 和导入面经都被视为不可信内容。`PromptInjectionGuard` 会识别覆盖系统指令、越权调用工具、数据外泄请求和 RAG 污染指令。检测结果写入结构化字段或 credibility metadata；进入 LLM context 前会过滤命中的恶意指令行。高风险动作仍必须经过 LangGraph interrupt、approval table 或高风险工具网关。

`evals/prompt_injection_cases.json` 覆盖真实形态的中文 JD、PDF OCR 噪声、RAG chunk、面经网页片段和 benign 安全工程表述。`evals/prompt_injection_release_policy.json` 定义 release gate：样本量、最低 detection recall、最高 false positive rate、最低 category recall 和 severity accuracy，评测 summary 会输出 `release_gate.passed` 与失败项。

## 启动方式

```powershell
pip install -r requirements.txt
$env:REDIS_ENABLED='true'
$env:REDIS_URL='redis://localhost:6379/0'
uvicorn app.main:app --reload
```

另开一个终端启动 worker：

```powershell
$env:REDIS_ENABLED='true'
$env:REDIS_URL='redis://localhost:6379/0'
python scripts/run_agent_worker.py
```

当前项目保留 SQLite，是因为它足够承载单机可上线演示和可审计 trace；真正多租户、高并发、在线迁移、托管备份、复杂审计或 pgvector 需求出现后，再把业务库迁到 PostgreSQL 更合理。
