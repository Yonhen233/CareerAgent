# CareerAgent 面试拷打 Q&A

## Q：为什么不用 FastAPI BackgroundTasks？

A：现在后台 Agent run 已经走 RedisTaskRunner。API 只创建 `queued` run 并把 `run_id` 入 Redis 队列，独立 worker 消费执行 LangGraph。这样 API 进程重启、页面关闭或 SSE 断开，都不会把长任务绑死在请求生命周期里。

## Q：Redis 和 SQLite 怎么分工？

A：SQLite 是 source of truth，保存业务数据、RAG 数据、trace、approval 和 checkpoint。Redis 是协调层，负责 queue、lock、rate limit、heartbeat、cancel flag 和 pub/sub。Redis 数据可以丢，SQLite 数据不能丢。

## Q：同一个 run 被多个 worker 执行怎么办？

A：worker 执行前获取 `career_agent:runs:lock:{run_id}`，使用 `SET NX EX`。拿不到锁的 worker 会跳过并写 trace。锁有 TTL，异常退出后可以靠 stale detection 接管。

## Q：worker 取到坏消息或执行器异常怎么办？

A：Redis payload 带 attempts。worker 级异常会重新入队，超过 `REDIS_WORKER_MAX_ATTEMPTS` 后写入 dead-letter queue，保留 payload、错误、worker_id 和失败时间。控制台能看到 DLQ 长度和预览。

## Q：queued run 丢在队列里怎么办？

A：SQLite 仍保存 queued run。worker 主循环会定期扫描超过阈值仍 queued 的 run 并重新入队；控制台也能手动触发 `/ops/queue/recover-queued`。

## Q：用户重复点击 resume 会不会重复创建投递包？

A：不会。`ResumeVersion/Application/InterviewPrep` 都有业务幂等键和唯一索引。节点重放时先查 idempotency key，命中就复用已有产物并写 `idempotency_reused` 事件。

## Q：投递前人工确认是否可审计？

A：是。LangGraph interrupt 前会创建 `agent_approvals` pending 记录，保存 `run_id/action_type/payload_hash/payload_summary_json`。用户确认后变 `approved`，拒绝后变 `rejected`，取消 run 后变 `cancelled`。同一张表也支持 `browser_apply`、`email_draft`、`email_send`。

## Q：用户取消 run 怎么办？

A：`POST /agent/runs/{run_id}/cancel` 支持取消 queued/running/waiting run。取消会更新 SQLite 状态、写事件、写 Redis cancel flag，并取消 pending approval。每个 LangGraph 节点开始前检查取消状态，阻止继续创建投递包或面试包。

## Q：run 卡在 running 怎么办？

A：`StaleRunService` 会根据最后一条 `agent_events` 时间识别 stale running run。运维接口可以查看 stale run，也可以标记为 failed，并在 output 中写 `error_type=stale_run_timeout`、最后事件和最后阶段。

## Q：怎么防 prompt injection？

A：JD、PDF、RAG chunk、导入面经都被当作 untrusted content。`PromptInjectionGuard` 检测覆盖系统指令、越权工具调用、数据外泄和 RAG 污染指令；命中行不会进入 LLM context，风险写入结构化 metadata。项目还新增了 adversarial eval，量化 detection recall、false positive rate、severity accuracy 和 source/category breakdown。

## Q：并发怎么设计？

A：有四层：Redis 队列削峰，Redis run lock 防重复执行，SQLite 业务幂等键防重复产物，API active run limit + Redis rate limit 防同一 Profile 短时间创建过多 run。

## Q：为什么还保留 SQLite？

A：这个项目是简历项目和单机可上线演示，SQLite 足够承载事实库和可审计 trace；Redis 补齐协调层。上到多租户和高并发后，业务库迁 PostgreSQL，向量检索可迁 pgvector/Qdrant。
