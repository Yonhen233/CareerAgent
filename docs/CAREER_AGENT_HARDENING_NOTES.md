# CareerAgent Production Hardening Notes

## 已实现能力

1. Redis 外部任务队列：`POST /agent/runs/background` 只负责创建 queued run 并入 Redis 队列，worker 独立消费。
2. Redis run lock、heartbeat、DLQ：worker 执行前用 `SET NX EX` 获取锁，执行时写阶段化 heartbeat，worker 级异常超过重试次数进入 dead-letter queue。
3. Run cancellation：用户可以取消 queued/running/waiting run，取消写 event、output 和 Redis flag。
4. Business idempotency：简历版本、投递包、面试包都有业务幂等键，重复执行复用已有产物。
5. Approval audit：投递包生成前创建独立审批记录，确认、拒绝、取消都有状态流转。
6. Prompt injection guard：JD、PDF、RAG chunk、面经文本进入 LLM 前检测和过滤恶意指令，并有 adversarial eval 输出召回率和误报率。
7. Stale run detection：运维接口可以发现并标记长时间无事件进展的 running run。
8. Queued recovery scanner：worker 定期扫描 SQLite 中卡在 queued 的 run 并重新入队，控制台也可手动恢复。
9. Active run / rate limit：同一 Profile 有 active run 上限；Redis 开启时有每分钟创建 run 限流。
10. Event pub/sub：SQLite 写入事件后发布 Redis channel，SSE 仍以 SQLite 为权威事件源。

## 关键接口

- `POST /agent/runs/background`：创建 queued run 并入 Redis 队列。
- `POST /agent/runs/{run_id}/cancel`：取消未完成 run。
- `POST /agent/runs/{run_id}/resume`：恢复 LangGraph interrupt。
- `GET /agent/runs/{run_id}/approvals`：查看审批审计记录。
- `GET /ops/queue/status`：查看 Redis queue、DLQ 和 worker 配置。
- `POST /ops/queue/recover-queued`：扫描并恢复卡住的 queued run。
- `GET /ops/approvals`：查看所有高风险动作审批记录。
- `GET /ops/agent-runs/stale`：查看 stale running run。
- `POST /ops/agent-runs/mark-stale`：标记 stale run。

## 失败语义

- Redis 未启用或不可用时，后台入队返回 503，不再静默退回进程内任务。
- 已完成、失败或已取消的 run 再取消返回 409。
- 已取消的 waiting run 再 resume 返回 409，不创建投递包。
- Prompt injection 不会成为 tool instruction；风险写入结构化字段，高风险动作仍需要人工确认。
- Stale run 不删除数据，只标记 failed 并保留最后事件信息。

## 仍不是完整生产平台的点

- 轻量 Redis worker 不是 Celery/Arq 级调度平台，已经有 DLQ 和恢复扫描，但还没有完整 supervisor、优先级队列和多队列路由。
- SQLite 适合单机/轻量部署，多租户高并发仍应迁 PostgreSQL。
- Prompt injection guard 是规则版，生产级可叠加分类器、红队样本和 LLM-as-judge。
- 还没有完整用户体系/RBAC，只做了 admin token 和审批审计骨架。
- 浏览器真实投递和邮件发送尚未接入；当前只生成材料、链接和人工确认边界。
