from __future__ import annotations

import json
from pathlib import Path


CONCEPTS = [
    ("langgraph_orchestration", "需要使用 LangGraph 设计可恢复的多步骤 Agent 工作流。", "Built a LangGraph state graph with typed state, conditional routing and durable checkpoints.", "使用 LangGraph 构建类型化状态图、条件路由和持久化检查点。", "Need a recoverable multi-step agent workflow implemented with LangGraph.", "Studied LangChain prompt templates but did not build a graph workflow.", "学习过 LangChain 提示词模板，但没有实现图工作流。"),
    ("hybrid_retrieval", "实现 BM25 与向量召回融合的岗位 RAG，并解释融合策略。", "Implemented hybrid job retrieval with BM25, dense embeddings and reciprocal rank fusion.", "实现岗位 BM25、稠密向量召回与 RRF 融合检索。", "Build job RAG with BM25 and vector recall, including an explainable fusion strategy.", "Used SQL LIKE to search exact job titles; no semantic retrieval was implemented.", "只用 SQL LIKE 搜索岗位标题，没有语义召回。"),
    ("pdf_semantic_chunking", "简历 PDF 需要按标题、经历条目和跨页语义进行 chunk。", "Designed structure-aware PDF resume chunking by headings, experience entries and cross-page continuity.", "按标题、经历条目和跨页连续性设计结构感知的 PDF 简历切分。", "Chunk resume PDFs by headings, experience entries and cross-page semantics.", "Split every document into fixed 500-character windows without preserving section boundaries.", "所有文档机械切成 500 字窗口，没有保留栏目边界。"),
    ("multilingual_embedding", "中英文简历和 JD 要使用同一语义空间进行跨语言检索。", "Evaluated multilingual sentence embeddings for Chinese resumes and English job descriptions in one vector space.", "评测多语言句向量，使中文简历与英文 JD 能在同一向量空间检索。", "Retrieve Chinese resumes against English JDs in a shared multilingual embedding space.", "Translated UI labels into English; the retrieval model remained monolingual.", "只翻译了界面文案，检索模型仍是单语模型。"),
    ("second_stage_reranker", "对一阶段 Top20 证据使用二阶段 reranker 重排。", "Added a cross-encoder reranker over the first-stage Top20 evidence candidates and measured ranking gains.", "对一阶段 Top20 证据候选增加 cross-encoder 重排并量化排序增益。", "Rerank the first-stage Top20 evidence chunks with a second-stage model.", "Sorted candidates by vector score only and called the operation reranking.", "只按原向量分数排序，却把该操作称为重排。"),
    ("fastapi_concurrency", "FastAPI 接口要支持并发调用独立检索源并设置超时。", "Used async FastAPI endpoints with bounded concurrent source calls, per-source timeouts and cancellation.", "使用异步 FastAPI、受限并发调用多个岗位源，并设置独立超时和取消。", "Use FastAPI concurrency for independent retrieval sources with bounded timeouts.", "Declared endpoints async but called blocking browser code directly on the event loop.", "接口虽然声明 async，却在事件循环里直接运行阻塞浏览器代码。"),
    ("redis_worker_queue", "长耗时 Agent 任务要进入 Redis 外部队列并由多 worker 消费。", "Moved long-running agent jobs to Redis priority queues with multiple workers, heartbeats and a dead-letter queue.", "将长任务迁移到 Redis 优先级队列，由多 worker 消费，并实现心跳与死信队列。", "Run long agent tasks through Redis queues with multiple workers, heartbeats and DLQ handling.", "Used FastAPI BackgroundTasks inside one web process; jobs were lost after restart.", "只用单进程 BackgroundTasks，服务重启后任务会丢失。"),
    ("sqlite_vector_metadata", "SQLite 应保存 JD chunk、向量和可审计的业务元数据。", "Stored JD chunks, embedding vectors and authoritative metadata in SQLite with transactional lineage.", "在 SQLite 事务中保存 JD chunk、embedding 向量与可审计元数据血缘。", "Persist JD chunks, vectors and auditable business metadata in SQLite.", "Stored only opaque vector IDs in memory and discarded the source JD metadata.", "只在内存保存不透明向量 ID，丢弃了来源 JD 元数据。"),
    ("prompt_injection_defense", "外部 JD 和 PDF 中的提示注入必须在进入 prompt 前检测和隔离。", "Detected and quarantined prompt injection instructions from untrusted JD and PDF content before prompt assembly.", "在组装 Prompt 前检测并隔离不可信 JD、PDF 中的提示注入指令。", "Detect and quarantine prompt injection in external JDs and PDFs before prompt construction.", "Added SQL parameterization and claimed it prevented prompt injection into the LLM.", "做了 SQL 参数化，却声称这能防止 LLM Prompt 注入。"),
    ("evidence_guardrail", "定制简历不得编造技能，所有新增表述都要绑定简历证据。", "Built evidence-grounded resume tailoring that blocks unsupported skills and cites source experience chunks.", "实现证据约束的简历定制，阻止无依据技能并引用来源经历 chunk。", "Prevent fabricated resume skills by grounding every new claim in source experience evidence.", "Generated polished resume claims from the JD even when the candidate had no supporting experience.", "即使候选人没有相关经历，也直接根据 JD 生成漂亮成果。"),
    ("structured_output", "LLM 输出必须通过 Pydantic schema 校验，错误结构要可追踪。", "Validated LLM JSON with Pydantic schemas and recorded typed parse failures in traces.", "使用 Pydantic 校验 LLM JSON，并在 Trace 中记录类型化解析错误。", "Validate LLM JSON through Pydantic schemas and trace malformed outputs.", "Parsed model responses with regular expressions and silently returned empty dictionaries on errors.", "用正则解析模型响应，出错时静默返回空字典。"),
    ("tool_contracts", "Agent Tool 要有输入输出合同、超时、幂等和重试归属。", "Implemented typed tool contracts covering input/output schemas, timeout, idempotency and retry ownership.", "实现 Tool 类型合同，覆盖输入输出 schema、超时、幂等与重试归属。", "Define agent tool contracts for schemas, timeouts, idempotency and retry ownership.", "Listed tool names in a prompt but did not validate arguments or results at runtime.", "只在 Prompt 里列出工具名，运行时不校验参数和结果。"),
    ("human_approval", "投递和邮件发送属于高风险动作，执行前必须人工确认。", "Bound browser submission and email sending to durable human approval records and audit events.", "将浏览器投递与邮件发送绑定持久化人工审批记录和审计事件。", "Require durable human approval and audit before browser submission or email sending.", "Displayed a confirmation toast after the email had already been sent.", "邮件已经发出后才显示确认提示。"),
    ("checkpoint_recovery", "Agent 进程崩溃后要从最近已完成节点恢复，而不是整条重跑。", "Used durable LangGraph checkpoints to resume a crashed run from the last completed node.", "使用持久化 LangGraph checkpoint，让崩溃任务从最近完成节点恢复。", "Resume crashed agent runs from the last completed node using durable checkpoints.", "Retried the entire workflow from the beginning after every transient exception.", "每次瞬时异常都从头重跑整条工作流。"),
    ("context_compression", "长流程要压缩历史上下文，同时保留目标、证据和未完成事项。", "Compressed long agent context into goals, verified evidence, decisions and pending actions with token budgets.", "按目标、已验证证据、决策和待办压缩长上下文，并设置 Token 预算。", "Compress long agent context while retaining goals, verified evidence, decisions and pending actions.", "Truncated the oldest half of every conversation without preserving evidence or decisions.", "直接删除会话最早一半，不保留证据和决策。"),
    ("rag_evaluation", "RAG 需要评测 Recall@K、MRR、nDCG、引用正确率和错误证据放行率。", "Evaluated RAG with Recall@K, MRR, nDCG, citation accuracy and false evidence acceptance rate.", "使用 Recall@K、MRR、nDCG、引用正确率和错误证据放行率评测 RAG。", "Evaluate RAG using Recall@K, MRR, nDCG, citation accuracy and false evidence acceptance.", "Reported only average cosine similarity on five hand-picked positive examples.", "只在五个精挑正例上报告平均余弦相似度。"),
    ("business_idempotency", "写库和外发节点要使用业务幂等键避免重复产物。", "Added business idempotency keys to database writes and outbound actions to prevent duplicate artifacts.", "为写库和外发动作增加业务幂等键，避免重复生成产物。", "Use business idempotency keys for database writes and outbound actions.", "Relied on random request IDs, so retries still created duplicate application records.", "只依赖随机请求 ID，重试仍会创建重复投递记录。"),
    ("observability_trace", "每次 Agent 运行要记录节点、Tool、模型、Token、时延和错误分类。", "Captured node, tool, model, token, latency and typed error events in a correlated run trace.", "在同一 Run Trace 中记录节点、Tool、模型、Token、时延与类型化错误。", "Trace each agent run across nodes, tools, models, tokens, latency and typed errors.", "Printed a final success message but kept no intermediate events or correlation ID.", "只打印最终成功消息，没有中间事件和关联 ID。"),
    ("multitenant_rbac", "多租户用户只能读取自己租户和身份范围内的简历与运行记录。", "Enforced tenant and user ownership on profiles, runs, artifacts and child-resource queries with RBAC.", "通过 RBAC 在简历、Run、Artifact 和子资源查询中强制 tenant 与 user 所有权。", "Enforce tenant and user ownership across profiles, runs, artifacts and child resources.", "Trusted a tenant header from any browser without authentication or ownership checks.", "无认证地信任浏览器传入的 tenant header，也不检查所有权。"),
    ("browser_application", "浏览器辅助投递要能填表、上传简历并在提交前中断审批。", "Automated browser form filling and resume upload, then interrupted before submit for approval.", "自动填写浏览器表单并上传简历，在提交前通过 interrupt 等待审批。", "Automate browser form filling and resume upload, interrupting for approval before submit.", "Opened the application URL in a new tab but did not inspect or fill any form fields.", "只打开投递链接，没有识别或填写任何表单。"),
    ("jd_structured_parser", "把中文噪声 JD 解析为职责、硬性要求、加分项和学历地点字段。", "Parsed noisy Chinese JDs into responsibilities, required skills, preferred skills, education and location fields.", "将带噪中文 JD 解析为职责、必备技能、加分项、学历与地点字段。", "Parse noisy Chinese JDs into responsibilities, requirements, preferences, education and location.", "Extracted every capitalized word as a required skill, including company slogans and benefits.", "把所有大写词都当作必备技能，包括公司口号和福利。"),
    ("resume_tailoring", "根据选中 JD 重排简历经历，但修改说明必须与简历正文分离。", "Tailored a resume for the selected JD while keeping rationale, score and change summary outside the resume body.", "根据选中 JD 定制简历，并将评分、理由和改动摘要放在正文之外。", "Tailor the resume to a selected JD while keeping scores and change notes outside the document body.", "Inserted guardrail results and editing notes into the exported resume sidebar.", "把事实检查和改动摘要直接写进导出的简历侧栏。"),
    ("typed_agent_memory", "跨会话记忆只保存受治理的偏好、约束、决策和纠错。", "Stored governed typed memories for preferences, constraints, decisions and corrections with user scoping.", "按用户范围保存受治理的偏好、约束、决策和纠错类型记忆。", "Persist governed typed agent memory for preferences, constraints, decisions and corrections.", "Saved complete raw chat transcripts as permanent shared memory for all tenant users.", "把完整聊天原文永久保存，并在租户所有用户间共享。"),
    ("no_progress_detection", "Agent 连续调用相同工具却没有新产物时应停止并报告无进展。", "Detected repeated tool calls without new artifacts and terminated with a typed no-progress error.", "检测重复 Tool 调用且无新 Artifact 时，以类型化 no-progress 错误终止。", "Stop agent loops when repeated tool calls produce no new artifacts or goal progress.", "Allowed the planner to call the same search tool indefinitely until the worker timeout.", "允许 Planner 无限重复调用搜索工具，直到 worker 超时。"),
]

VARIANTS = (
    ("zh_zh", "zh", "zh", 1, 3),
    ("en_en", "en", "en", 4, 2),
    ("zh_en", "zh", "en", 1, 2),
    ("en_zh", "en", "zh", 4, 3),
    ("mixed_zh", "mixed", "zh", 1, 3),
    ("mixed_en", "mixed", "en", 4, 2),
)


def build_cases() -> list[dict]:
    cases = []
    for concept_index, concept in enumerate(CONCEPTS):
        concept_id, zh_query, en_positive, zh_positive, en_query, en_negative, zh_negative = concept
        for variant, query_language, evidence_language, query_index, positive_index in VARIANTS:
            query = concept[query_index]
            if query_language == "mixed":
                query = f"{zh_query} Core requirement: {en_query}" if evidence_language == "zh" else f"{en_query}；核心要求：{zh_query}"
            target_id = f"{concept_id}_{variant}_target"
            evidence = [
                {
                    "chunk_id": target_id,
                    "chunk_type": "project",
                    "text": concept[positive_index],
                    "expected": True,
                    "noise_profile": "target",
                },
                {
                    "chunk_id": f"{concept_id}_{variant}_hard_negative",
                    "chunk_type": "project",
                    "text": en_negative if evidence_language == "en" else zh_negative,
                    "expected": False,
                    "noise_profile": "same_topic_wrong_evidence",
                },
            ]
            for offset in range(1, 9):
                other = CONCEPTS[(concept_index + offset * 5) % len(CONCEPTS)]
                evidence.append(
                    {
                        "chunk_id": f"{concept_id}_{variant}_distractor_{offset}",
                        "chunk_type": "project" if offset % 3 else "experience",
                        "text": other[3 if evidence_language == "zh" else 2],
                        "expected": False,
                        "noise_profile": "adjacent_agent_domain" if offset <= 5 else "cross_domain_noise",
                    }
                )
            cases.append(
                {
                    "name": f"multilingual_{concept_id}_{variant}",
                    "concept_id": concept_id,
                    "language_pair": variant,
                    "query_language": query_language,
                    "evidence_language": evidence_language,
                    "difficulty": "adversarial" if variant in {"zh_en", "en_zh"} else "hard",
                    "query": query,
                    "expected_chunk_ids": [target_id],
                    "evidence_chunks": evidence,
                    "noise_profiles": ["same_topic_wrong_evidence", "adjacent_agent_domain", "cross_language"],
                }
            )
    return cases


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "evals" / "rag_multilingual_calibration.json"
    payload = {
        "version": "careeragent-multilingual-rag-calibration-v1",
        "generator": "curated in ChatGPT/Codex development session",
        "design": "Paired concepts across Chinese, English, cross-language and code-switched retrieval with hard negatives.",
        "cases": build_cases(),
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(payload['cases'])} cases at {output}")


if __name__ == "__main__":
    main()
