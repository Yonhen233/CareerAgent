# CareerAgent 高频面试问答

## 一、项目与 Agent 架构

### Q1：一句话介绍这个项目，它解决的核心问题是什么？

CareerAgent 是一个面向中文 Agent/LLM 实习岗位的可恢复求职 Agent。它把 PDF/结构化简历、真实岗位搜索、岗位与经历双 RAG、匹配与差距分析、证据约束简历定制、人工审批投递和面试准备连接成 LangGraph 工作流，并用 Redis 队列、SQLite 权威存储、Guardrail、Trace 和分层评测控制长任务、事实风险和外部副作用。

核心不是“让模型写简历”，而是让每个判断有来源、每个副作用可审批、每次失败能定位到解析、检索、模型、门禁或执行器。

### Q2：为什么最终迁移到 LangGraph？

这个流程有条件分支、人工中断、长任务恢复和副作用重放问题。普通 service chain 可以调用完一条路径，但随着“先搜索后选择岗位”“投递前确认”“失败题增量修复”增加，状态和恢复逻辑会散落在 API 中。

LangGraph 提供了三个直接价值：显式 state schema、节点/条件边、checkpoint + interrupt/resume。CareerAgent 把五类任务统一到一张主图，并把岗位选择和投递确认设计为 interrupt。跨进程恢复时用 `graph_thread_id` 和 `Command(resume=...)` 继续，不从头执行。

困难也很具体：未在 TypedDict 声明的字段会丢失；ORM Session 不能 checkpoint；副作用节点可能被重放。因此 state 只保存 ID/JSON，Session 运行时注入，写库节点增加幂等键。选择框架不是为了简历上多一个名词，而是因为它真的承担了状态、恢复和事件流。

### Q3：为什么不一开始就用 LangGraph？

早期任务只有“简历 + JD -> 匹配/改写”，普通服务编排更容易验证领域逻辑。等到任务出现多路径、interrupt、跨进程恢复和逐节点事件时再迁移，能够先冻结解析、RAG 和 Guardrail 的接口。代价是迁移时需要兼容旧 Orchestrator，但避免在业务边界尚不清楚时同时调框架和领域逻辑。

### Q4：Plan-Execute、ReAct 分别放在哪里？

Plan-Execute 用于有明确工具 DAG 的主流程：加载 Profile、搜岗位、解析 JD、匹配、检索证据、定制、审批、投递和面试。Planner 生成 Skill、Tool 和权限合同，执行器只运行注册工具。

ReAct 只放在简历定制：生成初稿后观察 Guardrail issue，高风险时基于 issue 和压缩上下文修复一次，再重新验证。它不适合无限循环，因为每轮都可能增加成本和事实漂移。面试 Agentic RAG 也有最多一轮失败题 repair，但本质是验证驱动的增量修复，不是开放式无限 ReAct。

### Q5：Skill、Tool、SubAgent 有什么区别？

- Skill 是版本化能力合同，说明何时触发、需要什么输入、允许哪些 Tool、产物和禁止行为。
- Tool 是实际可执行能力，并声明风险、审批、幂等、超时、重试和审计。
- SubAgent 是责任和上下文边界，例如 profile analyst 或 resume writer，不一定对应独立模型进程。

Planner 选择 Skill，Skill 限制 Tool，SubAgent 帮助解释当前责任边界。这样可以渐进式披露，也能在执行前验证工具授权。

### Q6：为什么没有把上下文压缩做成一个 SubAgent？

上下文压缩是每次模型调用前都要执行的 runtime policy，不是需要独立目标和工具选择的业务角色。把它做成 SubAgent 会多一次模型调用，还会让“谁负责保留证据”变得模糊。

当前只保留 Profile 摘要、JD 摘要、Top evidence 和最终 prompt packet 四层预算，记录原始/压缩字符、保留证据和 shrink event。压缩的成功标准是预算内且不丢任务证据，不是层级越多越好。

### Q7：为什么暂时没有全面引入 MCP？

Profile、JD、RAG 和匹配都在同一进程、事务和权限域内，直接 Python Tool 的类型和事务边界更清楚。为了展示 MCP 而包装一层网络协议没有实际收益。

最适合 MCP 化的是跨授权域能力：浏览器、邮箱、日历、云盘和需要登录态的招聘平台。这些工具已经有稳定 Tool Policy、审批、幂等和审计；未来加 MCP adapter 时必须继承同一策略，不能绕过高风险网关。

## 二、PDF、RAG 与向量库

### Q8：PDF Chunk 策略怎么选择？

我没有固定按 token 切。当前同时建立结构化字段 chunk 和页级原文 chunk，原文先按段落合并，超过 900 字符再用 160 overlap 滑窗，并保留页码、字段、字符范围和策略 metadata。

选型依据是 96 份简历、576 个 query 的对照评测。最终策略 Top3 关键词、页码、上下文命中分别是 94.79%、82.99%、77.60%，平均 Top1 772.77 字符、每份 10 个 chunk，满足发布阈值。它在上下文完整性和噪声之间最好，不是因为 900/160 是行业固定答案。

### Q9：PDF Chunk 当前最大的 bad case 是什么？

`coursework_vs_shipped` 分桶的 Top3 上下文命中只有 5.21%。同一段同时写已交付项目和课程/计划内容时，chunk 级 evidence type 无法精确绑定到句子和技能。当前保守降权能减少误投，但会损失正向召回。下一步做 sentence/facet 级 evidence 与 polarity，而不是简单缩小所有 chunk。

### Q10：为什么简历和岗位都要做 RAG？

岗位 RAG 解决“岗位库里哪些 JD 与用户需求相关”；简历 RAG 解决“这份简历里哪些经历能证明满足当前 JD”。前者是跨岗位检索，后者是岗位到候选人证据检索，query、metadata 和排序目标不同。

如果只对简历做 RAG，岗位搜索仍依赖标题关键词；如果只对岗位做 RAG，定制简历会把完整简历塞给模型，无法说明改动依据。双 RAG 让“找到什么岗位”和“为什么适合”都可追踪。

### Q11：RAG 为什么用混合检索？

招聘文本同时有精确技术词和语义同义表达。BM25/词法更擅长 FastAPI、Redis、MCP、LangGraph；embedding 更擅长“智能体研发≈Agent 开发”“检索增强≈RAG”。当前一阶段权重是 vector 0.45、lexical 0.50、type boost 0.05，这是在 180-case 强噪声集上选出的，不是只追求向量技术。

一阶段 Top20 后用 reranker 改善顺序，但 Top5 作为召回锚点，避免重排器把强精确证据挤掉。

### Q12：RAG 效果怎么样？为什么 Top1 是 100%，Recall@3 只有 61.25%？

180-case 固定集上，Top1 accuracy=1.0、MRR=1.0，说明每个 query 的第一个主证据都正确；Recall@3=0.6125、Recall@5=0.7292，说明一个 case 有多个 gold evidence，Top5 没覆盖全部；nDCG@5=0.7862 表示整体顺序达到门禁但仍有改进空间。

因此不能只报 Top1。定制和面试有时需要多个事实共同证明，Recall@K 与来源多样性同样重要。

### Q13：为什么用 SQLite + Chroma，不直接用 Milvus/Qdrant/FAISS？

SQLite 是业务 source of truth，保存 chunk 文本、来源、metadata、embedding、Profile/Job 关系、审批和 Trace，支持事务、唯一约束和审计。Chroma 是可选向量镜像，可从 SQLite 重建。

FAISS 适合纯向量索引，但不承担业务事务和元数据关系；Milvus/Qdrant 更适合大规模分布式向量服务，但会增加部署和运维复杂度。当前单机/小团队规模先用 SQLite + Chroma，等岗位达到百万级、需要多副本和低延迟 ANN 时再迁移；接口已经通过 VectorIndex 隔离。

### Q14：Reranker 的选型和边界是什么？

默认模型是 `cross-encoder/ms-marco-MiniLM-L-6-v2`，对 Top20 计算 query-document 分，权重 0.30。CrossEncoder 比双塔 embedding 慢，但只处理小候选集，能够看 query 与文档联合语义。

边界是该模型偏英文。代码对中文 query 使用 CJK lexical rerank，避免错误地把英文模型称为多语言。固定 RAG 集 provider 门禁通过，不代表全部中文请求都经过真实中文 cross-encoder。后续应比较 BGE multilingual reranker 的 nDCG、Recall 和 p95。

## 三、匹配、生成与幻觉控制

### Q15：岗位匹配是推荐系统吗？

不是传统 CTR/协同过滤系统。项目没有曝光点击样本、UserCF、DeepFM 或多目标推荐。它是垂直岗位 RAG：元数据过滤、BM25/embedding 混合召回、rerank，再根据当前 Profile 计算可解释匹配和缺口。

Matcher 分数由 required coverage 38%、语义 24%、证据相关性 22%、实习和 preferred 各 8%，再减负向证据。它服务当前 query 和 Profile，不预测用户点击。

### Q16：如何防止模型把不存在的经历写进简历？

使用五层约束：

1. Profile/JD parser 先做原文 grounding；
2. RAG 只给 Top evidence，并标记 evidence type/polarity；
3. Prompt 明确缺口只能进入 notes，不能进入正文；
4. Guardrail 检查数字、技能、成果语义和否定极性；
5. 高风险时最多一轮 ReAct repair，仍失败不发布。

此外 raw 模型输出与 published 结果分开保存。`#120` 中模型原始 gap grounding 只有 0.6667，但 verifier 拒绝错误项，最终用户消息 grounding 为 1.0，说明系统并不假设模型永远正确。

### Q17：为什么不完全依赖 LLM Judge？

LLM Judge 适合开放语义，但它有成本、波动和自洽偏差。权限、schema、引用 ID、数字一致性、标签分数区间和业务状态都能由确定性代码检查，就不应该交给 Judge。

CareerAgent 的做法是：本地合同先挡结构和权限，LLM 只做 entailment/answer relevance 等难以规则化的语义判断，最后仍由服务端组合 verified 结果。评测标签独立版本化，不能让生成模型自己定义正确答案。

### Q18：模型返回非法 JSON 或空 content 怎么办？

统一客户端先检查 HTTP、`choices[0].message.content`、空字符串和 JSON object。网络断连、429 和 5xx 有有限重试；非法 JSON 是否 repair 由具体服务决定，最多一次并使用独立 trace name。业务门禁失败不当网络错误重试。

DeepSeek V4 结构化节点在官方接口上自动 `thinking: disabled`，避免只有 reasoning_content、content 为空。每次 attempt 都记录状态并占用工作流预算，不能靠重试绕过费用上限。

## 四、并发、队列和恢复

### Q19：FastAPI 并发是怎么设计的？哪些流程能并行？

能并行的是互不共享事务的外部 I/O：五个岗位源、美团详情、阿里批次、批量 JD 解析、HTTP/LLM 请求和批量 reranker。它们使用 gather + semaphore 或 batch predict。

不能盲目并行的是同一 SQLAlchemy Session 的写入、具有前后依赖的 LangGraph 节点和高风险副作用。多个 worker 可以并行不同 run；单 run 内写库按顺序执行。这样既利用 I/O 等待时间，又避免同步 Session 和 SQLite 写锁问题。

### Q20：为什么需要 Redis，它和 LangGraph checkpoint 是否重复？

不重复。Redis 解决谁来执行：队列、优先级、worker lock、心跳、取消、限流和 Pub/Sub；checkpoint 解决图执行到哪里；SQLite 业务表解决已经创建了什么。

API 进程重启后，Redis 仍可让 worker 消费；Redis 消息丢失时，queued recovery 从 SQLite 重建；worker 崩溃后，checkpoint 决定从哪个节点继续；幂等键防止节点重放产生重复产物。

### Q21：如何保证任务不重复执行？

不能只靠分布式锁。锁有 TTL，进程可能在副作用完成后、状态提交前崩溃。项目采用 Redis run lock 防并发，SQLite 唯一幂等键防重复产物，approval ID 防高风险动作重复，所有重用都写事件。队列是至少一次语义，业务层实现幂等。

### Q22：DLQ、recovery scanner 和 heartbeat 分别解决什么？

- heartbeat 判断 worker 是否仍在某个阶段工作；
- recovery scanner 找 SQLite 中长时间 queued 但 Redis 可能没有消息的 run，并重新入队；
- DLQ 保存超过重试次数或 payload 异常的消息，等待人工 replay/discard。

Stale 不等于自动重跑。运维人员先看 heartbeat、step 和 checkpoint，再决定取消、标 stale 或重放，避免把一个仍在慢速 LLM 调用的任务重复执行。

### Q23：为什么没有直接用 Celery？

当前队列语义很小：Agent run、评测 task、三档优先级和有限重试。自建 Redis worker 能完整展示 lock、heartbeat、DLQ、recovery 和 drain，也减少 Celery broker/result backend 的配置。

这不是说自建永远更好。如果增加定时调度、复杂路由、批量 canvas、分布式监控或大量任务类型，就应该迁移 Celery/Arq。当前代码边界已经把 enqueue/worker 与业务 Orchestrator 分开，迁移成本可控。

## 五、安全、权限和可观测性

### Q24：如何防 Prompt Injection？

首先把 JD、PDF、RAG 和面经都当作不可信数据。进入 LLM 前做 pattern + 特征评分检测，删除恶意指令行并保留风险 metadata。Prompt 明确证据只是数据；Planner 只能调用注册 Tool，Skill 进一步限制 allowed tools；浏览器和邮件必须有 approval；生成结果还有事实 Guardrail。

70-case 固定集 recall=1、FPR=0，但当前 classifier 仍是本地 pattern-feature scoring，不是训练模型，不能声称对未知攻击 100%。真正安全来自纵深边界，而不是某个 detector 的单点分数。

### Q25：审批为什么既要 LangGraph interrupt，又要 approval table？

Interrupt 负责暂停和恢复图，approval table 负责业务审计，tool gateway 负责执行时强制检查。只靠 interrupt 无法回答谁批准、批准什么 payload、是否被取消；只靠表又不能自动恢复图；只靠前端 confirmed 布尔值可以被绕过。三者共同形成闭环。

### Q26：多租户怎么做？现在能公网使用吗？

已有 tenant/user 表、PBKDF2 密码、签名 session cookie、owner/admin/ops 角色、可信 header 兼容和核心表 tenant filter。运维接口可由 Admin Token、session 或管理员角色保护。

但还不能把它描述成完整公网身份平台：没有 OIDC/SSO、完整 CSRF、细粒度资源 ACL 和全表越权验证。当前适合单机、作品演示或受控内网；正式公网部署要接企业 IdP 并迁移数据库。

### Q27：如何调试一次 LLM 或 Agent 错误？

先按 run 看业务摘要，再沿 `agent_steps` 找失败 stage；看 `agent_events` 确认 LangGraph 节点和 interrupt；看 artifact 确认输入证据与中间产物；最后按 trace context 查 `llm_call_logs` 的模型、route、Prompt 字符、usage、响应预览、耗时和错误。

如果最终结果错，要区分：parser 字段错、RAG 没召回、reranker 排错、压缩丢证据、LLM raw 输出错、verifier 误判或发布组合错。项目中的 bad case 都按这条链路定位，不直接从最终答案开始反复改 Prompt。

## 六、评测和上线判断

### Q28：当前最重要的评测指标是什么？

没有单一指标。PDF 看页码/上下文命中，RAG 看 Recall@K/MRR/nDCG，规划看 action 和终态，生成看 grounding/forbidden claim，Agent 看 trajectory + business outcome，安全看 recall/FPR，可靠性看 pass^k，性能看 p50/p95/Token/费用。

系统使用硬门禁，不算总分。最近整轮 `#113` 因规划 0.85、LLM workflow 18/24、full-flow 5/6、pass^2 0.6667 而失败，即使 PDF/RAG/Injection 通过也没有宣布上线。

### Q29：现在可以上线吗？

从功能和工程机制看，已经有真实可用产品形态：前端、真实岗位、双 RAG、LangGraph、Redis、审批、可观测性和安全门禁都在。但从发布认证看，最新完整系统 gate 是修复前失败；已知 bad case 定向回归通过，尚未重跑完整 24-case 和足够的面试/可靠性样本。

所以准确答案是“具备受控试运行或作品部署条件，但还不应宣称达到开放公网的生产 SLA”。下一步是修完 JD 离线 grounding 两例、双人复标、全量 workflow、pass^3 和多面试包评测。

### Q30：如果继续优化，你会优先做什么？

优先级不是再加框架，而是补证据和发布可信度：

1. sentence/facet 级 evidence，解决课程与交付同 chunk；
2. 双人 fit 标注与 Cohen's kappa；
3. 中文 reranker 对照；
4. 修复最新 JD grounding gate 后全量重跑；
5. OIDC + PostgreSQL，扩大部署边界；
6. 面试包至少 5 个组合和 pass^3；
7. 真实注入失败样本与在线反馈闭环。

这些工作比再引入一个“看起来现代”的 SubAgent 更能提高系统可信度。
