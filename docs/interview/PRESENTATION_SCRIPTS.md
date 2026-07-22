# CareerAgent 项目讲述稿与简历写法

## 1. 30 秒版本

> 我做了一个面向中文 Agent/LLM 实习岗位的 CareerAgent。它不是单次 Prompt，而是一条 LangGraph 求职工作流：用户可以上传 PDF 或直接搜索真实岗位，系统把简历和 JD 分别结构化并建立 RAG 索引，再做岗位排序、证据约束的简历定制、人工审批投递和面试准备。工程上使用 FastAPI、SQLite、Chroma、真实 embedding/reranker、Redis worker、checkpoint、审批审计和 LLM Trace；质量上有 PDF/RAG、工具轨迹、业务终态、Prompt Injection、pass^k、延迟与 Token 的分层门禁。

## 2. 两分钟版本

> CareerAgent 解决的是求职流程中“信息很多、过程很长、模型容易编造”的问题。用户有三种方式：只描述想找什么岗位、只上传简历自动匹配，或者同时提供偏好和简历。系统并发搜索腾讯、百度、美团、字节和阿里等公司招聘站，把 JD 结构化后存进 SQLite 和岗位 chunk 索引；简历则用 pypdf 按页提取，同时建立结构化字段 chunk 和 900/160 的页级段落 chunk。
>
> 检索不是只算 embedding。我使用 0.45 向量、0.50 词法和 0.05 类型 boost 做一阶段 Top20，再用 reranker 和 recall anchor 排序。匹配结果会列出真实证据、已匹配技能和缺口。定制简历时，LLM 只读取 Profile/JD 摘要和 Top evidence，初稿必须经过事实 Guardrail，高风险最多修复一次，缺失技能不能写成已掌握经历。
>
> 主流程用 LangGraph，岗位选择和投递确认使用 interrupt，SQLite checkpointer 支持跨进程恢复；长任务通过 Redis 优先级队列和独立 worker 执行，还有 run lock、心跳、幂等、recovery scanner 和 DLQ。浏览器投递、邮件草稿和发送都必须绑定 approval table。
>
> 我没有只看接口是否 200。项目有 96 个 PDF、180 个 RAG、70 个 Injection 等数据集，整轮真实 DeepSeek 评测会统计 trajectory、业务终态、pass^k、p95、Token 和成本。最近完整基线暴露了规划、fit 和跨语言 grounding 问题，已知 bad case 已定向修复，但全量门禁尚未重新跑，所以我不会把局部回归说成系统 100%。

## 3. 五分钟结构化版本

### 第一段：问题和用户流程

> 我把项目定位成真实求职产品，而不是简历生成器。用户可以不提供简历直接浏览岗位；也可以上传 PDF，让系统自动匹配；选中某个 JD 后，再按需做差距分析、定制简历、投递和面试准备。这样岗位发现是主线，简历是增强匹配的证据，不是浏览岗位的硬前置。

### 第二段：知识与检索

> 简历和岗位使用两套 RAG。PDF 经过页级提取、Prompt Injection 清洗和 LLM 结构化，既保存原文页 chunk，也保存项目、技能和经历字段 chunk。JD 在搜索阶段先用确定性 parser 快速入库，深度使用时再用 LLM 和 grounding gate。SQLite 保存文本、metadata 和 embedding，是权威数据；Chroma 是可重建镜像。
>
> 召回使用词法和向量混合，Top20 再重排。简历 evidence 还会区分已交付项目、指标、课程、计划学习和缺口披露，防止 `No MLflow` 因为含有 MLflow 就被当成掌握。

### 第三段：Agent 与生产运行时

> LangGraph 主图有计划、搜索、匹配、选择岗位、定制、fit gate、投递和面试等节点。状态只存 ID 和 JSON，Session 不进入 checkpoint。Redis 负责谁执行，LangGraph checkpoint 负责执行到哪里，SQLite 业务表负责已经产生什么。重复消费由 run lock 和业务幂等共同控制。
>
> 高风险动作不是一个 `confirmed=true`。系统先创建 approval 记录，再 interrupt；恢复后工具网关仍检查 action type 和 approved 状态，执行结果写 artifact 和审计。

### 第四段：LLM 边界

> LLM 只放在自然语言规划、简历/JD 语义抽取、简历定制和面试 claim 生成/验证这些需要语义的地方。状态转移、权限、引用 ID、幂等和最终发布由代码控制。Flash 处理短结构化节点，Pro 处理长上下文面试；所有调用有 trace 和预算。
>
> 面试模块曾经因为节点拆太细，一包用了 59 次调用。后来我根据 trace 删除没有信息增益的 LLM planner、renderer 和 coverage judge，改成本地 multi-query、一次批量生成、一次批量验证，正常路径收敛到 3 次业务调用，最多 5 次。

### 第五段：评测和诚实边界

> PDF 96 case/576 query 选出 900/160 策略；RAG 180 case 的 Top1 和 MRR 是 1，Recall@5 是 0.7292，nDCG@5 是 0.7862。岗位排序 13 query/130 candidate 的 nDCG@5 是 0.9495。70-case Injection 固定集 recall 1、FPR 0。
>
> 但完整系统 `#113` 的发布门禁失败：规划 17/20，LLM workflow 18/24，full-flow 5/6，pass^2 0.6667。后续我按 trace 修复多动作提前结束、`AgentTrace` 子串污染、跨语言阈值误杀和评测器假失败，定向回归通过。因为尚未重跑全量，我的结论是系统具备完整机制和受控试运行能力，但仍在发布质量收敛，而不是已经达到公网 SLA。

## 4. 推荐的现场演示路径

### 演示一：岗位搜索，不要求简历

1. 在开始页只填写“深圳或远程 Agent 开发实习，偏 RAG/LangGraph”。
2. 展示来源模式、岗位结果、完整 JD 和搜索 session。
3. 说明无简历时不会伪造个人匹配分。

讲述重点：三种输入模式、真实来源故障隔离、岗位库持久化和跨页恢复。

### 演示二：上传 PDF 并匹配

1. 上传 `demo_resumes/agent_intern_strong_resume.pdf`。
2. 打开 Profile HTML，展示结构化栏目和原始证据。
3. 搜索岗位并打开详情，展示匹配维度、matched/missing skills 和 evidence。

讲述重点：PDF 页级/字段 chunk、双 RAG、Evidence Type 和匹配公式。

### 演示三：定制与投递审批

1. 从岗位详情选择档案并定制简历。
2. 展示纯净简历预览，以及独立的改动摘要、评分和 Guardrail。
3. 启动投递流程，展示 LangGraph interrupt 和 approval；不要绕过最终确认。
4. 在历史记录打开 run summary、step、event 和 LLM trace。

讲述重点：raw-vs-published、一次 ReAct、幂等、checkpoint、审批表和 Artifact。

### 演示四：面试包

1. 选择同一 Profile/Job 生成面试包。
2. 展示三类准备角度、10 道题、直接参考答案和证据引用。
3. 展开一题说明 claim 生成、verifier 和 answer relevance；更新练习状态。

讲述重点：广义 RAG、来源权限、批量生成/验证和 Token 预算。真实 API 余额有限时使用已生成包展示，不要现场盲跑完整面试链路。

## 5. 简历项目描述

### 版本 A：Agent 岗位，三条

- 设计并实现中文求职 Agent，使用 FastAPI + LangGraph 编排岗位发现、RAG 匹配、简历定制、投递审批与面试准备；通过 SQLite checkpoint、Redis 优先级队列、幂等键、DLQ 和 SSE 支持长任务跨进程恢复与可观测执行。
- 构建简历/JD 双 RAG：基于 96 份 PDF、576 个 Chunk query 和 180 个检索 case 选择段落页级 `900/160` Chunk、真实多语言 embedding、BM25/向量混合召回与 Top20 重排；固定集达到 Top1=1.0、MRR=1.0、Recall@5=0.7292、nDCG@5=0.7862。
- 建立证据约束生成与发布门禁：区分交付、指标、课程、计划和缺口证据，使用 Guardrail + 单轮 ReAct、Prompt Injection 检测、approval table 和 claim verifier 控制简历与外发风险；整轮评测统计 trajectory、pass^k、p95、Token 和成本，并按 bad case 修复规划与 grounding 问题。

### 版本 B：后端/平台岗位，两条

- 基于 FastAPI、LangGraph、Redis 和 SQLite 构建可恢复 Agent 运行平台，支持多 worker 优先级消费、run lock、阶段心跳、取消、queued recovery、DLQ 人工处置、业务幂等、审批审计和 LangGraph SSE 事件流。
- 统一 15 个 Tool Policy 和 7 个 Skill 契约，落地 session/RBAC、租户过滤、浏览器/SMTP 高风险工具网关、LLM 调用与 Token 监控、分层 release gate；完整本地回归 264 项。

### 版本 C：RAG 岗位，两条

- 为中文简历与招聘 JD 构建结构感知双 RAG，持久化字段/页级 chunk、provenance 和 embedding，采用 lexical 0.50 + vector 0.45 + type boost 0.05 混合召回、Top20 二阶段排序和 Top5 recall anchor，并按 evidence type/polarity 约束下游匹配与生成。
- 建立 PDF Chunk、RAG、岗位排序和 claim grounding 评测集，分别覆盖 96/180/13 个主 case 与多类 adversarial 噪声；用 Recall@K、MRR、nDCG、citation integrity 和 release gate 选择策略并暴露课程/交付混合证据短板。

## 6. 简历数字怎么写才不过度承诺

可以写：

- “固定评测集达到……”
- “真实 DeepSeek 整轮评测记录……”
- “已知失败 case 定向回归通过……”
- “完整本地回归 264 项……”

不要写：

- “线上准确率 100%”，因为没有真实线上流量；
- “全链路通过率 100%”，因为最新整轮门禁失败后尚未全量重跑；
- “支持百万并发”，因为没有压测和分布式数据库；
- “Prompt Injection 防御率 100%”，应限定为 70-case 固定集；
- “多语言 CrossEncoder”，当前中文 query 有 lexical language route；
- “自动一键投递所有平台”，真实高风险动作仍需要人工审批和平台约束。

## 7. 项目亮点与不足的平衡回答

> 项目最强的部分不是技术栈数量，而是把 Agent 的控制面、知识面和执行面分开：LangGraph 管状态和人工中断，双 RAG 管证据，Tool Policy/审批管副作用，评测门禁管发布。最明显的不足也很具体：SQLite 仍是单机权威库，中文 reranker 还没完成独立校准，fit gold 缺少双人标注，完整面试与 pass^k 样本仍小。我已经通过 Trace 和 release gate 让这些不足可见，下一步会先补评测可信度和证据粒度，而不是继续堆新框架。

## 8. 被追问“你本人主要做了什么”

> 我主要完成了系统边界和闭环：把最初的简历/JD Demo 重构为 LangGraph 主图和 Redis worker；设计 PDF/JD chunk 与双 RAG；实现匹配、简历定制 Guardrail、投递审批和面试 claim verifier；建立 run/step/artifact/event/LLM log 与分层评测。开发中我重点处理了多动作执行提前结束、Parser 与 Chunker 组合丢证据、子串匹配抬分、跨语言 grounding 误杀、面试 Token 爆炸和评测器重复进程等 bad case。我的工作不只是接 API，而是把失败变成可定位、可验证的工程问题。
