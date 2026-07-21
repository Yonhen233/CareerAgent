# CareerAgent 项目事实卡

当前仓库的整体架构和模块交互是：用户请求 -> FastAPI -> LangGraph 编排层 -> PDF、RAG、Matcher、简历生成等领域 Tool/Service -> SQLite 与向量索引；长任务进入 Redis 队列，由 worker 执行并把 step、event、artifact 和结果写回持久化存储。本文证明仓库实现，候选人归属仍需同时引用简历。

## 整体架构、Agent 位置、选型理由与替代方案

Agent 位于 API 与确定性领域服务之间，负责状态、工具选择、条件路由、失败修复、暂停恢复和人工确认，不替代 parser、检索或审批。选择 Agent 是因为求职流程存在动态决策和长流程状态；固定步骤可改用状态机或任务 DAG，单次改写可直接调用 LLM，开放式局部任务可用 ReAct。当前以 LangGraph 主图控制边界，只在局部修复使用 ReAct，高风险外发由审批表和 Tool Gateway 决定。

## 并发、Trace 与恢复

FastAPI 接收长任务后返回 run_id，Redis 队列把任务交给 worker，worker 为每个任务独立创建数据库 Session。运行阶段、heartbeat、重试、错误、LangGraph event、step 和 artifact 都写入持久化存储；thread_id 用于恢复图状态，业务幂等键防止写库节点重复执行。取消、超时、优雅 drain、队列积压和 worker 健康状态由运维控制台观测。

## RAG 与事实校验

简历和 JD 分别解析为带来源、章节、页码或业务 ID 的 chunk；检索使用 Exact、BM25、向量召回、RRF 与二阶段 reranker，SQLite 保存权威原文和 metadata，向量索引可重建。生成结果拆为 claims，verifier 重新绑定引用并检查整题是否被正面回答；JD、面经、技术知识和简历只能支持各自允许的事实类型。
