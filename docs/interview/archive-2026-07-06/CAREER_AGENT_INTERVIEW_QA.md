# CareerAgent 面试 Q&A

> 用法：先背每类前 3-5 个高频问题，再根据面试官追问回到代码路径。

## A. 项目总览

1. CareerAgent 是什么？  
CareerAgent 是一个中文求职 Agent / LLM 应用，从简历和岗位 JD 出发，完成岗位搜索、匹配、定制简历、投递包和面试准备包。核心代码在 `app/agents/langgraph_orchestrator.py::LangGraphAgentOrchestrator`。

2. 为什么它是 Agent？  
因为它有任务状态、工具调用、条件路由、持久化 checkpoint、人工 interrupt、trace 和 artifact，不是一次 LLM 生成。LangGraph 主图定义在 `app/agents/langgraph_orchestrator.py::_build_graph`。

3. 和普通简历生成器有什么区别？  
普通生成器通常只根据 JD 改写文本；CareerAgent 会先解析简历、检索证据、匹配岗位、过 Guardrail，再生成简历，并且投递包前需要人审。

4. 项目输入输出是什么？  
输入是 PDF/结构化 Profile、JD/job、query/location、task_type；输出是 matches、ResumeVersion、Application packet、InterviewPrep、run trace。schema 在 `app/models/schemas.py`。

5. 端到端流程是什么？  
建档 -> Job/JD 入库 -> LangGraph run -> match -> tailor -> fit gate -> interrupt 确认 -> application packet -> interview prep。

6. 项目最核心的工程亮点？  
LangGraph 编排、SQLite RAG、负向证据处理、Guardrail repair、checkpoint/interrupt/resume、Trace/SSE、真实 LLM workflow eval。

7. 项目当前不能夸大的点是什么？  
它当前不自动提交申请、不稳定爬取真实面经、不具备完整 prompt injection 安全系统，后台任务也不是生产队列。

8. 你怎么描述 Codex 在项目中的作用？  
可以诚实说 Codex 参与了实现和迭代，但我需要能解释真实代码、测试、bug 和验收，比如 LangGraph state 为什么不能放 Session、raw_text bug 为什么发生。

9. 为什么面向中文求职？  
默认 query、前端文案、JD parser 和面试包都围绕中文 Agent/LLM/RAG 实习场景；配置和测试有 `test_chinese_first_defaults.py`。

10. 项目当前适合简历怎么写？  
“基于 FastAPI + LangGraph + SQLite RAG + LLM Guardrail 的中文求职 Agent，支持 PDF 解析、岗位匹配、定制简历、投递包人审、面试准备、Trace/SSE 和量化评测。”

## B. LangGraph

11. 为什么用 LangGraph？  
因为任务有多节点、条件分支、人审中断和恢复需求。`StateGraph` 能显式表达节点与边，`AsyncSqliteSaver` 能保存恢复点。

12. 主编排在哪里？  
`app/agents/langgraph_orchestrator.py`，类是 `LangGraphAgentOrchestrator`；`app/agents/orchestrator.py` 只是兼容外壳。

13. state 怎么设计？  
`CareerAgentGraphState` 是 `TypedDict(total=False)`，只存 JSON 友好的 ID 和产物摘要，如 `profile_id/job_id/job_ids/matches/fit_gate/output`。

14. 为什么 state 里不放 SQLAlchemy Session？  
Session 不能序列化，也不适合 checkpoint/replay。代码用 `_runtime_dbs[run_id]` 在运行期注入。

15. 节点怎么划分？  
按业务阶段划分：`load_profile/search_jobs/match_job/tailor_resume/fit_gate/create_application_packet/generate_interview_prep/finalize_*`。

16. conditional edge 怎么设计？  
`plan_task` 根据 `task_type` 分流，`load_profile` 根据是否要搜索岗位分流，`match_job` 根据任务去 tailor/apply/interview。

17. full_career_flow 怎么跑？  
没有 job_id 时搜索岗位并选 Top1；有 job_id 时跳过搜索，直接加载目标岗位，然后 match、tailor、fit gate、application、interview。

18. quick_apply 怎么跑？  
`load_profile -> load_job -> match_job -> fit_gate -> ensure_resume_version -> create_application_packet`，投递包前默认 interrupt。

19. prepare_interview_for_job 怎么跑？  
`load_profile -> load_job -> match_job -> generate_interview_prep -> finalize_interview`。

20. 如果节点失败怎么办？  
`TraceService.step` 写 failed step 和 event，外层 `_execute_run` 捕获异常，把 run 标为 failed 并保留 execution_plan。

21. LangGraph 迁移踩过什么坑？  
`job_ids` 没声明会被丢弃；Session/ORM 不能放 state；InMemorySaver 不能跨进程恢复；async graph 要用 `AsyncSqliteSaver`。

22. 怎么证明没有绕过 LangGraph？  
run input/output 和 execution_plan 都写 `orchestration_framework=langgraph`；eval 有 `langgraph_pass_rate`；测试覆盖主图路径。

23. `graph_thread_id` 有什么用？  
它是 LangGraph checkpoint thread id，用于 resume 和 graph-state API 定位同一次图运行。

24. 为什么图执行用 `astream_events(version="v2")`？  
这样能同时执行图并捕获 LangGraph 节点事件，写入 `agent_events` 供 SSE/前端时间线使用。

## C. Checkpoint / Interrupt / Resume

25. checkpoint 解决什么问题？  
保存图运行状态，让 Agent 在人工确认点暂停后能从同一节点继续，而不是从头跑。

26. 用的什么 checkpointer？  
`langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`，初始化在 `LangGraphAgentOrchestrator._ensure_graph`。

27. checkpoint 文件在哪里？  
默认 `data/runtime/langgraph_checkpoints.sqlite`，配置项是 `LANGGRAPH_CHECKPOINT_FILE` / `Settings.langgraph_checkpoint_file`。

28. 哪些流程会 interrupt？  
`quick_apply` 和 `full_career_flow` 在创建投递包前触发，代码在 `_application_confirmation`。

29. 确认前会写 application 吗？  
不会。测试 `test_quick_apply_interrupts_and_resumes_from_sqlite_checkpoint` 断言 interrupt 后 `Application` count 为 0。

30. resume API 是哪个？  
`POST /agent/runs/{run_id}/resume`，实现是 `app/api/agent_runs.py::resume_agent_run`。

31. graph-state API 是哪个？  
`GET /agent/runs/{run_id}/graph-state`，可看 next、values、interrupts、checkpoint_id。

32. 用户拒绝确认怎么办？  
resume payload `confirmed=false` 会让 `_node_create_application_packet` 抛错，run failed，不创建投递包。

33. 重复确认会怎样？  
第一次完成后状态不再是 `waiting_for_confirmation`，再次 resume 会 409。生产还要加业务幂等键。

34. 当前支持跨进程恢复吗？  
支持跨 Orchestrator 实例从 SQLite checkpoint 恢复；更严格的多 worker/跨天恢复仍需节点内独立 session 和幂等写库。

## D. RAG

35. CareerAgent 为什么需要 RAG？  
简历定制和面试准备必须基于用户真实经历，RAG 提供证据，防止 LLM 按 JD 编造。

36. chunk 怎么切？  
结构化 Profile 字段单独 chunk，PDF/原文按段落 + sliding window；JD 也按 structured fields + raw JD chunk。代码在 `app/services/text_splitter.py`。

37. chunk metadata 有什么？  
field、item_index、page_no、char_start/end、strategy、embedding provider/model/dimensions、retrieval/rerank metadata。

38. 如何避免 RAG pollution？  
ResumeChunk 按 `profile_id` 查询，JobChunk 按 `job_id` 查询；upsert 删除旧 chunk；support text 过滤求职意向和 headline。

39. embedding 怎么用？  
默认 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，测试可 hash；代码在 `EmbeddingService`。

40. vector store 是什么？  
SQLite 是权威存储，保存 chunk、metadata、embedding JSON；Chroma 是可选镜像，不是唯一主路径。

41. reranker 怎么用？  
一阶段取 TopN，默认 cross-encoder rerank，Top5 anchor 保留召回，代码在 `RerankerService.rerank_chunks`。

42. provider 不支持怎么办？  
如果 fallback 配置为 heuristic 就降级并记录 reason，否则抛 `ValueError`。`/matches` 会包装错误。

43. evidence classifier 做什么？  
把 chunk 标成 `shipped_project/metric_evidence/coursework/planned_learning/missing_skill_disclosure` 和 polarity。

44. 如何防止把 JD 要求写成用户经历？  
tailor 只看 Profile evidence，prompt 禁止缺失技能进正文，Guardrail 检查 unsupported required skill claim。

45. RAG 怎么评估？  
用 `evals/rag_cases.json` 看 Top1、Top3/Top5 Recall、MRR、nDCG@5 和 difficulty/noise breakdown。

46. chunk 太大有什么风险？  
会把真实项目和课程噪声、计划学习、缺口披露混在一起，增加错误证据。

47. chunk 太小有什么风险？  
丢失项目上下文和指标，LLM 拿到碎片后容易误解。

48. 为什么不是直接把整份简历塞给 LLM？  
成本高、难追溯、容易混入无关或负向信息；RAG 可以只给 Top evidence 和 metadata。

## E. Matcher / Fit Gate / 负向证据

49. 匹配分怎么计算？  
heuristic + embedding hybrid：required coverage、semantic similarity、evidence relevance、internship fit、preferred coverage，再减 negative penalty。

50. fit gate 阈值是多少？  
当前 `overall_score >= 55` 才允许继续 quick apply，代码在 `_fit_gate`。

51. 为什么关键词匹配不够？  
关键词无法区分“使用过 Kubernetes”和“没有 Kubernetes 经验”。

52. “No MLflow experience” 怎么处理？  
句子被识别为负向证据，MLflow 不计入 matched skills，并增加 negative penalty。

53. “没有 Kubernetes 集群维护经验”为什么不能算维护经验？  
负向 cue “没有”优先级高于正向动词“维护”，不能把缺口包装成能力。

54. skill alias 怎么做？  
`SKILL_ALIASES` 管理同义词，如 A/B testing、evaluation、metrics、agent workflow。

55. evidence relevance 怎么影响分数？  
`_project_relevance` 会给 shipped/metric evidence 加权，给 coursework/planned/missing disclosure 降权。

56. 低匹配岗位怎么处理？  
可以生成分析、定制建议或面试缺口 drill，但 quick apply 会被 fit gate 阻断。

57. 相关测试是什么？  
`tests/test_matcher.py::test_matcher_penalizes_negative_or_coursework_only_evidence`。

58. 这个 bug 面试怎么讲？  
“检索命中不是事实支持”，我把关键词匹配升级为句子级 polarity 和证据类型判断。

## F. Guardrail / 投递边界

59. 如何防止编造经历？  
`ResumeGuardrailService.verify` 检查 unsupported metrics、new tokens、unsupported required skills 和缺口技能正文披露。

60. Guardrail 失败后怎么办？  
`ResumeTailorService` 会尝试一次 repair，删除 unsupported 或 missing-skill disclosure，再二次验证。

61. 投递包 guardrail 检查什么？  
`ApplicationPacketGuardrail` 检查求职信/外联 unsupported claims、是否提到岗位、是否保留人工确认边界。

62. 当前会自动投递吗？  
不会。`ApplicationService` 只生成材料和链接，`automation_result.final_submission=user_confirmed_only`。

63. 如何防止自动投递？  
高风险动作前 LangGraph interrupt；投递包里 checklist 明确“提交前人工确认”。

64. Prompt Injection 做了吗？  
部分做了边界隔离，但没有独立 injection detector。当前 JD 被当作数据，不当作工具指令；生产要加专项防护。

65. Guardrail 和 RAG 的关系？  
RAG 提供证据，Guardrail 检查生成内容是否越过证据。

66. 当前不足是什么？  
规则覆盖有限、缺审批表、缺系统化 prompt injection eval、缺 PII 脱敏策略。

## G. Trace / Observability / SSE

67. run/step/event/artifact 区别？  
run 是总任务，step 是业务阶段执行记录，artifact 是产物，event 是实时/历史事件流。

68. TraceService 做什么？  
创建 run、包装 step、写 artifact、写 event、finish run，代码在 `app/services/trace_service.py`。

69. agent_events 怎么设计？  
`event_type/node_name/event_json/created_at`，由 run/step/artifact 和 LangGraph event recorder 写入。

70. SSE endpoint 是哪个？  
`GET /agent/runs/{run_id}/events/stream`，实现 `_agent_event_sse`。

71. SSE 读内存还是数据库？  
读 `agent_events` 表，不是内存队列。

72. 前端不支持 EventSource 怎么办？  
`waitForAgentRun` 同时轮询 `/agent/runs/{id}`，EventSource 不可用时自动降级。

73. 如何定位失败节点？  
看 `agent_steps` failed、`agent_events` graph/step failed、`llm_call_logs` trace_name 和 run error。

74. 现在数据库里 event 行为什么是 0？  
当前历史 DB 大量 run 发生在事件表版本前或未重跑事件流版本；代码已实现事件写入，但不能把历史数据说成已有 event 积累。

75. LLM log 有什么价值？  
可以看到 trace_name、模型、prompt/response preview、错误和 latency，定位 parse/tailor/interview 的真实失败。

76. 上线 observability 还要什么？  
集中日志、metrics dashboard、trace id、脱敏、访问控制、日志归档和 alert。

## H. Eval / 测试

77. 你怎么评测 Agent？  
单元测试 + workflow tests + full-flow eval + RAG/JD/interview eval + real LLM workflow smoke。

78. full-flow eval 覆盖什么？  
岗位搜索、Top1、分数区间、tailor guardrail、quick apply 门禁、trace/artifact/langgraph 标识。

79. real LLM smoke 怎么做？  
`/evaluations/llm-workflow` 真实调用 LLM parse resume/JD、match/RAG、fit judge、tailor、guardrail，并写 stage_trace。

80. 最近真实 LLM 18-case 结果？  
本地 EvaluationRun #31：18 case，`end_to_end_pass_rate=1.0`、`fit_label_accuracy=1.0`、`tailor_pass_rate=1.0`、`guardrail_pass_rate=1.0`。

81. 复杂 PDF smoke 结果？  
开发日志记录 3 页、4486 字符，创建 Profile #150、Job #193、Resume #81、Application #22、InterviewPrep #35，runs #154/#155/#156 completed。

82. LLM 输出不稳定怎么测？  
schema 归一化 null，有限 retry，JD JSON repair，失败 stage breakdown，LLM logs 记录 trace。

83. adversarial case 有哪些？  
No MLflow、没有 Kubernetes、did not build agent system、coursework、planned learning、target role pollution。

84. 为什么只看 pass rate 不够？  
还要看 trace/artifact/event/LLM logs，否则可能绕过关键节点或隐藏中间失败。

85. 普通 pytest 数量？  
开发日志最近记录 `python -m pytest -q` 全量 98 个测试通过。当前报告没有重新跑全量测试，只引用已有记录。

86. 评测数据有什么不足？  
很多是合成数据，真实简历/JD 需要脱敏和人工标注。

## I. API / 前端

87. `/agent/runs/background` 有什么用？  
先创建 queued run，再用 FastAPI `BackgroundTasks` 后台执行，首页一键流程可以通过 SSE/轮询看进度。

88. BackgroundTasks 有什么问题？  
它是进程内任务，不支持分布式、可靠重试、取消和跨实例调度，生产要换队列。

89. 首页一键流程怎么变成单个 Agent run？  
`runCareerStartFlow` 创建 background `full_career_flow`，再 `waitForAgentRun` 等同一个 run 完成。

90. 前端如何处理人工确认？  
run 卡片显示“确认继续”；首页一键流程可 auto confirm，调用 `/agent/runs/{id}/resume`。

91. `/ui/resumes` 有什么性能问题？  
`loadResumes` 会一次加载所有定制版本 iframe，历史版本多时变重，需要分页或懒加载。

92. `/ui/quality` 做什么？  
运行和展示 eval、LLM workflow trace、LLM logs、source smoke，是评测控制台。

93. 自然语言入口 API 是哪个？  
`POST /assistant/natural-language`，内部走 `NaturalLanguageAgentService` 的 LangGraph。

94. 直接 `/applications/quick-apply` 和 Agent quick_apply 区别？  
直接 API 调 `ApplicationService` 生成投递包；Agent quick_apply 还经过 LangGraph fit gate 和 interrupt。

## J. 后端工程

95. FastAPI 为什么适合这个项目？  
异步 API、Pydantic schema、BackgroundTasks、StreamingResponse/SSE 都适合 LLM workflow demo。

96. SQLite 为什么可接受？  
本地 demo 和简历项目足够，能存 Profile/Job/chunk/embedding/trace/eval；生产高并发要换 Postgres。

97. 为什么 SQLite 存 embedding？  
它是权威、可审计、无额外服务的主存储；Chroma 只是可选镜像。

98. 幂等性是什么？  
同一个写库节点重试/resume 不应重复创建业务产物。当前仍需为 application/resume/prep 补业务幂等键。

99. async/await 用在哪里？  
LangGraph run/resume、LLM 调用、Job search、SSE generator、AsyncSqliteSaver 初始化。

100. 为什么 config 默认 fallback 是 error？  
生产/真实评测不应静默降级掩盖模型或 provider 问题；测试环境显式开启 fallback。

## K. 行为面试

101. 最大难点是什么？  
把“能跑的 LLM demo”变成可追踪、可恢复、能防编造的 Agent 工程，尤其是 RAG 证据和投递边界。

102. 最有成就感的部分？  
LangGraph checkpoint/interrupt/resume 和负向证据处理，因为它们让项目从生成器更像真实 Agent。

103. 如果重做会先做什么？  
先定 state schema、trace schema 和 eval cases，再写 UI；这样减少后期迁移成本。

104. 如果上线会怎么改？  
Postgres、外部队列、审批表、幂等键、权限系统、PII 脱敏、prompt injection eval、真实数据标注。

105. 如果用户量扩大怎么办？  
拆出 worker 队列，embedding/rerank 服务化，向量库换 Qdrant/pgvector，LLM 调用限流和缓存。

106. 你如何说明自己理解了架构？  
能从 `AgentRunRequest` 讲到 LangGraph state、节点、service、数据库表、trace、测试和具体 bug，而不是只说“Codex 帮我写的”。

107. 面试官问“这项目真实跑过吗”？  
可以说本地数据库有 150 profiles、193 jobs、81 resume versions、22 applications、35 interview preps；开发日志记录复杂 PDF + DeepSeek 真实 LLM 前端流程跑通。

108. 面试官问“哪些是未来计划”？  
自动真实投递、稳定外部面经抓取、完整 prompt injection 防护、生产队列、审批审计表、Postgres/pgvector 都是待完善。

109. 面试官问“为什么不直接用 LangChain Agent”？  
这个项目更需要确定性流程、人审中断和恢复，而不是开放式工具循环。LangGraph 的显式状态图更适合。

110. 面试官问“项目最像生产系统的点是什么”？  
不是模型效果，而是 trace/eval/guardrail/checkpoint/interrupt 这些工程边界。
