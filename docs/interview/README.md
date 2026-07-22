# CareerAgent 面试材料导航

> 重建时间：2026-07-22
>
> 事实基线：当前 `main` 代码、`data/career_agent.db` 中的 EvaluationRun、`docs/DEVELOPMENT_LOG.md` 与本地完整测试。
> 使用原则：专项满分、修复后定向回归和整轮系统评测是三种不同证据，面试时不能混为“全系统 100%”。

这套材料不是从旧文档摘关键词，而是按当前项目重新组织。建议先读总览和实现，再看评测与 bad case，最后用讲述稿练习。

## 阅读顺序

| 顺序 | 材料 | 解决的问题 |
| --- | --- | --- |
| 1 | [项目总览与架构](PROJECT_OVERVIEW_AND_ARCHITECTURE.md) | CareerAgent 解决什么问题，整体架构如何分层，LangGraph、Redis、SQLite、RAG 和 LLM 怎么协作 |
| 2 | [核心链路实现详解](IMPLEMENTATION_DEEP_DIVE.md) | PDF/JD 如何解析，双 RAG 如何检索，岗位如何排序，简历如何定制，面试包如何生成 |
| 3 | [生产工程与安全设计](PRODUCTION_ENGINEERING.md) | FastAPI 并发、外部队列、恢复、幂等、审批、RBAC、可观测性和 Prompt Injection 防护 |
| 4 | [评测体系与当前指标](EVALUATION_AND_METRICS.md) | 数据集有多大，各指标是什么意思，哪些门禁通过，当前系统还不能宣称什么 |
| 5 | [Bad Case 与设计决策](BAD_CASES_AND_DECISIONS.md) | 开发中真正遇到过哪些问题，如何定位、修复和验证，为什么没有只调 Prompt 或降低阈值 |
| 6 | [高频面试问答](INTERVIEW_QA.md) | LangGraph、Chunk、RAG、向量库、模型路由、并发、安全等问题如何完整回答 |
| 7 | [项目讲述稿与简历写法](PRESENTATION_SCRIPTS.md) | 30 秒、2 分钟、5 分钟怎么讲，如何演示，简历 bullet 怎么写，哪些表述会过度承诺 |

## 运行时证据文件

下面两份文件不仅是给人看的文档，还会被面试 Agentic RAG 作为项目证据读取，因此保留短小、事实化的结构：

- [CAREER_AGENT_PROJECT_EVIDENCE.md](CAREER_AGENT_PROJECT_EVIDENCE.md)：项目已经实现的交付事实。
- [TECHNICAL_KNOWLEDGE_BASE.md](TECHNICAL_KNOWLEDGE_BASE.md)：面试问题需要的技术原理与边界。

不要把长篇讲述稿合并进这两份文件，否则会增加面试检索噪声和 Token 消耗。

## 历史材料

`archive-2026-07-06/` 是旧架构下的历史资料，只用于观察项目演进。主编排、岗位发现、面试 RAG、模型路由和评测门禁都已经变化，准备面试时以本目录根部的新材料为准。

## 一句话结论

CareerAgent 当前已经具备完整的工程闭环和可运行产品形态，但截至 2026-07-22，最新整轮严格发布门禁仍是“修复前失败、已知 bad case 定向回归通过、尚未重新付费全量认证”。最可信的讲法不是“已经完美上线”，而是“系统已具备上线所需的主要机制，当前仍用发布门禁推动剩余质量收敛”。
