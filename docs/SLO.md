# CareerAgent SLI、SLO 与误差预算

## 设计原则

CareerAgent 的 SLO 从用户旅程反推，而不是把“进程存活”和“单元测试通过”当作可用性。设计参考 Google SRE 的 SLI/SLO/误差预算方法：不同负载应分别定义目标，SLO 必须写清测量窗口和有效条件，发布决策应受误差预算约束。

参考：[Google SRE - Service Level Objectives](https://sre.google/sre-book/service-level-objectives/)、[Google SRE Workbook - SLO Document](https://sre.google/workbook/slo-document/)。

真实用户流量与合成探针通过 `traffic_class` 完全分离。合成结果只能证明发布前回归，不能宣称线上 SLO 已达到。正式统计从 `2026-08-11 12:00:00 +08:00` 开始，旧开发 Run 不进入真实流量窗口。

## 当前目标

| SLI | 目标 | 最小样本 | 失败定义 |
| --- | ---: | ---: | --- |
| 用户 API 可用率 | >= 99.5% | 50 | 用户 API 返回 5xx；4xx 属于输入或权限决策 |
| 用户 API P95 时延 | <= 1,500 ms | 50 | 仅统计非 5xx 同步请求，不包含后台 Agent 执行 |
| Agent 有效终态率 | >= 95% | 20 | 排除用户取消/撤回后，没有到达 completed 或 waiting_for_confirmation |
| Agent P95 时延 | <= 180,000 ms | 20 | 成功 Agent Run 的端到端时延 |
| 完成声明完整率 | 100% | 20 | completed Run 缺少或未通过 Completion Gate |

策略存放在 `evals/slo_policy.json`。报告同时返回观测值、样本量、Wilson 95% 下界，以及按样本数折算的允许失败数、已消耗数和剩余数。

## 数据链路

1. FastAPI middleware 在响应结束后写入 `http_request_metrics`，保存 route template、状态码、时延和流量类型。
2. 指标持久化失败不会改变用户响应，但会在 SLO 样本缺口中暴露；SQLite 使用 WAL 和 busy timeout。
3. Agent SLI 读取 `agent_runs`，完成完整性回查 `completion_verification` 或 `natural_language_completion_verification` Artifact。
4. `GET /ops/slo?window_days=7&traffic_class=real` 返回窗口报告；控制台并排展示 real 与 synthetic。
5. `python -m scripts.run_slo_probes` 执行 HTTP 探针和 20 次真实 LangGraph 岗位检索旅程，并硬性要求 LLMCallLog 增量为 0。

## 2026-08-11 合成结果

| 指标 | 样本与结果 | 状态 |
| --- | --- | --- |
| API 可用率 | 375/375，100% | 达标 |
| API P95 | 110.457 ms | 达标 |
| Agent 有效终态率 | 67/69，97.1014% | 达标，剩余 1 次失败预算 |
| Agent P95 | 54,917 ms | 达标 |
| 完成声明完整率 | 67/67，100% | 达标 |

两次 Agent 失败均来自修复前的 LangGraph checkpoint SQLite lock。修复后最新一轮新增 20 次连续探针全部完成，且 LLMCallLog 增量为 0。由于 95% 目标在 69 个样本中允许 3 次失败，当前 7 天合成窗口剩余 Agent 失败预算为 1；旧失败继续保留在窗口中，没有通过删数据制造全绿结果。

真实流量尚未形成，控制台应显示 `insufficient_data`，不能用上述合成数字替代线上结论。Wilson 下界也提示 49 个样本的统计置信度仍有限，需要上线后持续积累。

## 本轮 Bad Case

### 错误探针污染 SLO

第一版探针调用完整离线评测，混入了未配置 LLM 时按预期失败的简历定制与投递任务。问题不是产品执行错误，而是 SLO 分母定义错误。修复为只运行前提满足的岗位检索旅程，并把依赖诊断记录标为 `diagnostic`。

### checkpoint 在业务完成后锁冲突

一次 Run 的全部 Tool 与 Completion Gate 均成功，但最终 checkpoint 写入报 `database is locked`，系统仍正确标为失败。根因是 checkpoint SQLite 未配置 WAL/busy timeout，且原生 `sqlite3.OperationalError` 未进入瞬时依赖分类。修复后 checkpoint 使用 WAL、30 秒 busy timeout 和 NORMAL synchronous，错误分类为可重试 `dependency_transient`。

### 进程终止不等于子进程终止

中断 PowerShell 父进程后，Python 子进程继续写入旧探针数据。最终通过进程审计、traffic class 重分类和独立报告生成消除污染。正式 CI 应使用可回收的进程组或容器运行探针。

## 尚未完成

- 还没有真实用户流量，因此真实 7/30 天 SLO 均不能判定。
- 当前 HTTP 指标逐请求写 SQLite，适合当前规模；高并发部署应改为 Prometheus/OpenTelemetry 聚合，避免观测写入竞争业务库。
- 尚未实现多窗口 burn-rate 告警；上线后应增加 1h/6h 快速燃烧与 3d 慢速燃烧告警。
