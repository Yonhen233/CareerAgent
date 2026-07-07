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

## SQLite 负责什么

- `profiles`、`jobs`、`resume_chunks`、`job_chunks`
- embedding JSON 和 RAG metadata
- `resume_versions`、`applications`、`interview_preps`
- `agent_runs`、`agent_steps`、`agent_artifacts`、`agent_events`
- `agent_approvals`
- `llm_call_logs`、`evaluation_runs`
- LangGraph SQLite checkpoint

## Redis 负责什么

- `REDIS_QUEUE_NAME`：后台 Agent run 队列。
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

用户确认后状态变为 `approved`，拒绝后变为 `rejected`，取消 run 后 pending approval 变为 `cancelled`。后续接浏览器投递、邮件发送或日历操作时可以复用同一张审批表。

## 取消与 Stale Run

`POST /agent/runs/{run_id}/cancel` 支持取消 `queued/running/waiting_for_confirmation` run：SQLite 状态改为 `cancelled`，写 `run_cancel_requested/run_cancelled`，写 Redis cancel flag，pending approval 标记 cancelled。后续节点开始前检查状态和 cancel flag，阻止继续写投递包或面试包。

`GET /ops/agent-runs/stale` 和 `POST /ops/agent-runs/mark-stale` 用 `agent_events` 的最后事件时间识别长时间无进展的 running run，并标记为 failed，输出 `error_type=stale_run_timeout`。

## Prompt Injection 防护

外部 JD、PDF 简历、RAG evidence 和导入面经都被视为不可信内容。`PromptInjectionGuard` 会识别覆盖系统指令、越权调用工具、数据外泄请求和 RAG 污染指令。检测结果写入结构化字段或 credibility metadata；进入 LLM context 前会过滤命中的恶意指令行。高风险投递动作仍必须经过 LangGraph interrupt 和审批审计。

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
