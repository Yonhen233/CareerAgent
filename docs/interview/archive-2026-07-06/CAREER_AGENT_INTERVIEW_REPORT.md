# CareerAgent 面试准备报告

> 生成时间：2026-07-06  
> 范围：基于 `README.md`、`docs/`、`app/`、`tests/`、`evals/`、本地 SQLite 数据库和已有运行产物梳理。  
> 原则：只梳理，不重构；已实现、部分实现、文档描述和待完善分开写。

## 1. 项目总览

项目一句话定位：CareerAgent 是一个面向中文求职场景的 Agent / LLM 应用，能从简历和 JD 出发，完成岗位搜索、匹配、RAG 证据检索、定制简历、投递包生成、面试准备包和全链路 trace。

核心能力：

- 简历建档：PDF 上传解析、手动结构化填写、HTML 预览。
- JD/岗位处理：手动 JD 入库、真实岗位源搜索、JD parser、JD chunk。
- RAG 与匹配：Profile/JD chunk、embedding、SQLite 权威存储、可选 Chroma 镜像、reranker、证据分类、负向证据处理。
- Agent 编排：LangGraph `StateGraph`，支持 `find_jobs_for_profile`、`tailor_resume_for_job`、`quick_apply`、`prepare_interview_for_job`、`full_career_flow`。
- 高风险边界：`fit_gate`、简历事实 guardrail、投递包 guardrail、投递前 `interrupt()` 人工确认。
- 可观测性：`agent_runs`、`agent_steps`、`agent_artifacts`、`agent_events`、SSE endpoint、LLM 调用日志、评测结果。

典型输入：PDF 简历或手动 Profile、目标岗位/JD、岗位搜索 query/location、可选 `profile_id/job_id/resume_version_id`。  
典型输出：岗位匹配列表、定制简历版本、投递包、面试准备包、Agent run trace、事件流、评测 summary。

端到端流程：

1. `/profiles/upload` 或 `/profiles/guided` 创建 Profile，并生成 `resume_chunks`。
2. `/jobs` 或 `/jobs/search` 创建/搜索 Job，并生成 `job_chunks`。
3. `/agent/runs` 创建 Agent run。
4. LangGraph 进入 `plan_task -> load_profile -> search_jobs/load_job -> match_job(s)`。
5. 根据任务分支进入 `tailor_resume`、`fit_gate`、`create_application_packet`、`generate_interview_prep`。
6. 写入 `agent_steps`、`agent_artifacts`、`agent_events`，返回最终产物或 `waiting_for_confirmation`。

为什么是 Agent：

- 不是单次 RAG 问答，而是有明确 task、state、条件分支、工具调用、副作用写库、trace 和人工中断恢复。
- `AgentPlanner` 会生成 plan-execute artifact；LangGraph 节点按任务状态路由；高风险投递前通过 `interrupt()` 暂停。
- 工具边界真实存在：`JobSearchService`、`MatcherService`、`ResumeTailorService`、`ApplicationService`、`InterviewPrepService` 等。

项目当前适合简历描述：

> CareerAgent：基于 FastAPI、LangGraph、SQLite RAG、LLM 与 Guardrail 的中文求职 Agent，支持 PDF 简历解析、岗位匹配、定制简历、投递包人工确认、面试准备包、Trace/SSE 和量化评测；通过真实 DeepSeek LLM smoke、复杂 PDF 验收和 98 个回归测试验证核心链路。

面试时 1 分钟介绍：

> CareerAgent 是我做的求职 Agent 项目。它从 PDF 简历或结构化 Profile 出发，结合岗位 JD 做 RAG 检索和匹配，然后通过 LangGraph 编排岗位推荐、简历定制、投递包和面试准备包。项目里比较核心的是：第一，主流程不是脚本串接口，而是 LangGraph state graph；第二，简历定制必须基于 retrieved evidence，并用 Guardrail 防止把 JD 要求写成候选人经历；第三，投递前用 checkpoint + interrupt 做人工确认；第四，Agent run、step、artifact、event、LLM log 都可追踪，配了 full-flow eval 和真实 LLM smoke。

面试时 3 分钟介绍：

> 这个项目的目标是把“找实习、改简历、准备面试”做成一个可追踪的 Agent 工作流。用户可以上传 PDF 简历或手动建档，系统会解析成 Profile、切成 chunk、写入 SQLite embedding 索引；岗位侧也会解析 JD、生成 job chunk。匹配时会结合必备技能覆盖、语义相似度、RAG evidence、实习信号和负向证据 penalty 计算分数。主编排在 `app/agents/langgraph_orchestrator.py`，使用 LangGraph `StateGraph` 定义节点和条件边，比如 full flow 会先加载 Profile，没指定 job 就搜索岗位并选最高分，有 job 就直接加载目标岗位，然后匹配、定制简历、fit gate、人工确认投递包、生成面试包。为了避免越权自动投递，`quick_apply` 和 `full_career_flow` 会在创建投递包前调用 `interrupt()`，状态变成 `waiting_for_confirmation`，确认前不会写入 `applications`，之后通过 `/agent/runs/{run_id}/resume` 从 SQLite checkpoint 继续。工程上我还做了 Trace/SSE、LLM 调用日志、评测体系和真实 bug 修复，例如复杂 PDF 的 `raw_text` JSON 截断、`No MLflow` 被误判正向证据、`EventSource` 降级轮询等。

代码位置：`app/agents/langgraph_orchestrator.py::LangGraphAgentOrchestrator`、`app/services/resume_parser.py::ResumeParserService`、`app/services/matcher.py::MatcherService`、`app/services/resume_tailor.py::ResumeTailorService`、`app/services/application_service.py::ApplicationService`、`app/services/interview_prep.py::InterviewPrepService`  
文档位置：`README.md`、`docs/AGENT_DESIGN.md`、`docs/EVALUATION.md`、`docs/DEVELOPMENT_LOG.md`

## 2. 项目目录与模块结构

| 模块路径 | 主要职责 | 核心类/函数 | 面试中怎么讲 |
| --- | --- | --- | --- |
| `app/agents/langgraph_orchestrator.py` | 主 Agent 工作流编排、checkpoint、interrupt、LangGraph event 捕获 | `LangGraphAgentOrchestrator`、`CareerAgentGraphState`、`_build_graph`、`_node_*` | 这是项目的 Agent 大脑，负责把多个服务按状态图串起来，而不是前端串接口 |
| `app/agents/orchestrator.py` | 兼容旧 import | `AgentOrchestrator(LangGraphAgentOrchestrator)` | 旧类名保留，但实际已迁移 LangGraph |
| `app/agents/natural_language.py` | 自然语言入口独立 LangGraph | `NaturalLanguageAgentService`、`parse_user_request`、`repair_user_plan` | 用户说一句话也能规划并执行固定 task，失败可 repair 一次 |
| `app/agents/tools.py` | 工具注册和 plan-execute artifact | `AgentToolSpec`、`AgentPlanner.build_plan` | 每次 run 有可解释执行计划和工具边界 |
| `app/api/agent_runs.py` | Agent run 创建、后台运行、resume、graph-state、steps、events、SSE | `create_agent_run`、`resume_agent_run`、`_agent_event_sse` | 面试重点讲 checkpoint/resume/SSE |
| `app/api/profiles.py` | PDF 上传、手动建档、Profile HTML 预览 | `upload_resume`、`create_guided_profile` | 简历入口，触发 parser 和 chunk |
| `app/api/jobs.py` | 手动 JD、岗位搜索、job chunk 查询 | `create_job`、`search_jobs` | 岗位侧 RAG 数据入口 |
| `app/api/matches.py` | 匹配 API，异常结构化包装 | `create_match` | 修复过不支持 reranker provider 导致 500 信息不清的问题 |
| `app/api/resumes.py` | 简历定制、Markdown/HTML 预览 | `tailor_resume`、`preview_resume_html` | 简历生成落库为 `resume_versions` |
| `app/api/applications.py` | 直接生成投递包 API | `quick_apply` | 当前只生成材料，不真正提交 |
| `app/api/interview_prep.py` | 面试包、导入面经、练习状态、Markdown 导出 | `create_interview_prep`、`create_interview_experience` | 面试包基于 JD+简历证据+已导入面经，不夸大成爬虫 |
| `app/services/resume_parser.py` | PDF 文本抽取、LLM/heuristic 结构化、Profile/chunk 创建 | `ResumeParserService`、`parse_structured_resume` | 复杂 PDF 的 raw_text bug 在这里修复 |
| `app/services/text_splitter.py` | Profile/JD chunk 策略 | `ResumeTextSplitter` | 结构化字段 chunk + raw/page chunk，带 metadata |
| `app/services/vector_index.py` | SQLite 权威向量索引、可选 Chroma 镜像、混合检索 | `SQLiteVectorIndex` | 不是只存向量，metadata、provider、rerank 信息都可追踪 |
| `app/services/embedding_service.py` | embedding provider、hash fallback、query alias | `EmbeddingService` | 默认 sentence-transformers，测试可 hash |
| `app/services/reranker.py` | cross-encoder / heuristic rerank | `RerankerService` | Top20 rerank + Top5 anchor，provider 不支持可按配置 fallback 或报错 |
| `app/services/evidence_classifier.py` | 证据类型和 polarity 分类 | `EvidenceClassifier` | 区分 shipped project、coursework、planned learning、missing disclosure |
| `app/services/matcher.py` | 岗位匹配、负向证据、fit score | `MatcherService.build_match_payload` | 关键词只是输入之一，负向证据优先 |
| `app/services/resume_tailor.py` | RAG 简历定制、上下文压缩、Guardrail repair | `ResumeTailorService.tailor_resume` | LLM 只能重排和强调证据，不能编造；高风险修一次 |
| `app/services/guardrails.py` | 定制简历事实检查 | `ResumeGuardrailService.verify` | 检查 unsupported metrics/skills/gap disclosure |
| `app/services/application_guardrails.py` | 投递包 guardrail | `ApplicationPacketGuardrail.validate` | 检查投递文案 unsupported claims 和人工确认边界 |
| `app/services/application_service.py` | 生成投递包、求职信、外联、清单 | `ApplicationService.create_quick_apply_packet` | 当前不自动提交，`automation_result` 标明 manual confirm |
| `app/services/interview_prep.py` | 面试准备包生成 | `InterviewPrepService` | 三类角度：同岗面经线索、项目技术栈、其他问题 |
| `app/services/evaluation_service.py` | 多类评测和真实 LLM workflow | `EvaluationService` | 面试可讲如何评测 Agent，而不是只看 demo |
| `app/services/trace_service.py` | run/step/artifact/event 写入 | `TraceService` | Agent 可观测性中心 |
| `app/models/entities.py` | SQLAlchemy 表模型 | `Profile`、`ResumeChunk`、`Job`、`AgentRun` 等 | 能解释数据如何落库和追踪 |
| `app/models/schemas.py` | Pydantic 请求/响应 schema | `AgentRunRequest`、`AgentRunResumeRequest`、`ProfileStructured` | schema 归一化降低真实 LLM null 字段失败 |
| `app/static/js/main.js` | 前端一键流程、SSE/轮询、页面交互 | `runCareerStartFlow`、`waitForAgentRun`、`subscribeAgentRunEvents` | 前端现在创建单个后台 full flow run |
| `tests/` | 单测、workflow、frontend smoke、eval 回归 | `test_agent_workflow.py` 等 | 最近开发日志记录全量 98 tests 通过 |
| `evals/` | 评测数据集 | `agent_full_flow_cases.json`、`llm_workflow_cases.json` | 有 adversarial/negative case，不只是 toy demo |

## 3. LangGraph 主编排

### 3.1 LangGraph 总体说明

已实现。主编排文件是 `app/agents/langgraph_orchestrator.py`。`AgentOrchestrator` 只是兼容外壳，实际继承 `LangGraphAgentOrchestrator`，代码位置：`app/agents/orchestrator.py::AgentOrchestrator`。

`LangGraphAgentOrchestrator` 的职责：

- 创建/恢复 `agent_runs`。
- 生成 `graph_thread_id` 并写入 `input_json`。
- 异步懒初始化 `AsyncSqliteSaver`，compile `StateGraph`。
- 调用 `graph.astream_events(..., version="v2")` 捕获 LangGraph 事件。
- 节点内调用业务 service，并通过 `TraceService.step` 写 step trace。
- 根据 interrupt 决定返回 `waiting_for_confirmation` 还是 `completed/failed`。

代码位置：`app/agents/langgraph_orchestrator.py::LangGraphAgentOrchestrator.run`、`_execute_run`、`_ensure_graph`、`_invoke_graph`、`_build_graph`

### 3.2 State Schema

`CareerAgentGraphState` 是 `TypedDict(total=False)`。状态只保存 JSON 友好的 ID、配置和产物摘要，SQLAlchemy Session 不进入 state，而是通过 `_runtime_dbs: dict[int, Session]` 按 `run_id` 临时注入。这个设计是为了 checkpoint 序列化和 resume。

代码位置：`app/agents/langgraph_orchestrator.py::CareerAgentGraphState`、`_db_from_state`

| State 字段 | 类型/含义 | 哪个节点写入 | 哪个节点读取 | 面试中可能被问什么 |
| --- | --- | --- | --- | --- |
| `request` | 原始 `AgentRunRequest.model_dump()` | 初始 payload | `_request`、plan 相关 | 为什么不直接放 Pydantic 对象？因为 checkpoint 要 JSON 友好 |
| `run_id` | 当前 `agent_runs.id` | 初始 payload | 所有节点、trace | 为什么 state 里要有 run_id？用于 trace 和 runtime DB 映射 |
| `task_type` | 5 类 task literal | 初始 payload | 所有 route | 条件边如何选择流程 |
| `profile_id` | Profile ID | 初始、`load_profile` | load/match/tailor/interview | 为什么只存 ID 不存 ORM |
| `job_id` | Job ID | 初始、`select_job`、`load_job` | match/tailor/apply/interview | full flow 如何从搜索结果选 job |
| `resume_version_id` | 定制简历版本 ID | 初始、`tailor_resume`、`ensure_resume_version` | apply | quick apply 缺简历时如何补 |
| `query/location/limit` | 搜索参数 | 初始、`load_profile` | `search_jobs` | 默认中文 Agent 实习搜索 |
| `graph_thread_id` | LangGraph thread id | 初始 payload | output/resume/graph_state | resume 如何定位 checkpoint |
| `application_confirmed` | 是否跳过人工 interrupt | 初始 payload | `create_application_packet` | 评测为何传 true，真实用户默认 false |
| `job_ids` | 搜索得到的岗位 ID 列表 | `search_jobs` | `match_jobs` | 历史 bug：未声明字段会被 LangGraph 丢弃 |
| `matches` | 排序后的匹配列表 | `match_jobs` | `finalize_find_jobs`、`select_job`、full output | 如何选 Top1 |
| `source_errors` | 岗位源错误 | `search_jobs` | `match_jobs`/finalize | 外部源失败如何返回 |
| `selected_job` | full flow 选中岗位摘要 | `select_job`/`match_job` | full output | 有 job_id 时如何跳过搜索仍补 selected_job |
| `selected_job_id` | 选中岗位 ID | `select_job`/`match_job` | full output links | 输出链接 |
| `match_result_id` | `match_results.id` | `match_job` | tailor output/interview | 匹配产物如何关联 |
| `overall_score` | 匹配分 | `match_job` | tailor output | fit gate 来源 |
| `verification` | 简历 guardrail 结果 | `tailor_resume` | output | 如何防止编造 |
| `fit_gate` | 投递前适配检查 | `fit_gate` | apply interrupt/application | 为什么低分阻断 |
| `human_confirmation` | 人工确认结果 | `create_application_packet` | application payload | interrupt 和普通按钮区别 |
| `tailor` | 定制简历摘要 | `tailor_resume` | finalize/full | resume artifact |
| `application` | 投递包摘要 | `create_application_packet` | finalize/full | 确认后才写 |
| `interview_prep` | 面试包摘要 | `generate_interview_prep` | finalize/full | 面试包产物 |
| `execution_plan` | Plan artifact | `plan_task` | output/failed output | 如何证明走了 LangGraph/Plan |
| `output` | 最终响应 | finalize nodes | `_execute_run` | run output 如何成型 |

### 3.3 Node 列表

| Node 名称 | 职责 | 输入 | 输出 | 调用的 service/tool | 失败时怎么处理 |
| --- | --- | --- | --- | --- | --- |
| `plan_task` | 生成执行计划和 artifact | `task_type/request` | `execution_plan` | `AgentPlanner.build_plan`、`TraceService.add_artifact` | 抛异常，run failed |
| `load_profile` | 读取 Profile，补默认 query | `profile_id` | `profile_id/query` | `ProfileRepository` via `_load_profile` | Profile 缺失抛 `ValueError` |
| `search_jobs` | 搜索并存储岗位 | `query/location/limit` | `job_ids/source_errors` | `JobSearchService.search` | step failed，run failed |
| `match_jobs` | 批量匹配岗位并排序 | `job_ids/profile_id` | `matches` | `MatcherService.create_match_result` | 单个异常会使 run failed |
| `select_job` | 选择最高分岗位 | `matches` | `selected_job/job_id/selected_job_id` | `TraceService.add_artifact` | 无 matches 抛明确错误 |
| `load_job` | 读取目标岗位 | `job_id` | `job_id` | `_load_job` | Job 缺失抛 `ValueError` |
| `match_job` | 匹配单个岗位 | `profile_id/job_id` | `match_result_id/overall_score/...` | `MatcherService` | step failed |
| `tailor_resume` | RAG 定制简历 | `profile_id/job_id` | `resume_version_id/verification/tailor` | `ResumeTailorService.tailor_resume` | LLM/guardrail 失败会 run failed |
| `fit_gate` | 投递前分数门禁 | `profile_id/job_id` | `fit_gate` | `_fit_gate` + `MatcherService` | 分数 <55 抛 `ValueError` 阻断 |
| `ensure_resume_version` | quick apply 没传简历版本时补定制简历 | `resume_version_id/profile_id/job_id` | `resume_version_id` | `ResumeTailorService.tailor_resume` | 找不到/生成失败则 failed |
| `create_application_packet` | interrupt 后生成投递包 | `job_id/resume_version_id/fit_gate` | `application` | `interrupt()`、`ApplicationService` | 拒绝确认抛 `ValueError`；guardrail 失败也 failed |
| `generate_interview_prep` | 生成面试准备包 | `profile_id/job_id` | `interview_prep` | `InterviewPrepService.create_interview_prep_with_llm` | LLM/服务错误则 failed |
| `finalize_find_jobs` | 岗位搜索输出 | `matches/source_errors` | `output` | 无 | 不应失败 |
| `finalize_tailor` | 简历定制输出 | `tailor` | `output` | 无 | 不应失败 |
| `finalize_quick_apply` | 投递包输出 | `application` | `output` | 无 | 不应失败 |
| `finalize_interview` | 面试包输出 | `interview_prep` | `output` | 无 | 不应失败 |
| `finalize_full_flow` | 汇总完整流程产物和 UI 链接 | `selected_job/matches/tailor/application/interview_prep` | `output` + `full_career_flow` artifact | `TraceService.add_artifact` | 不应失败，除非状态缺失 |

代码位置：`app/agents/langgraph_orchestrator.py::_node_plan_task` 至 `_node_finalize_full_flow`

### 3.4 Edge / Conditional Edge

主图真实边：

- `START -> plan_task`
- `plan_task` 条件路由到 5 类 task。
- `load_profile` 后：`find_jobs_for_profile` 或未指定 `job_id` 的 `full_career_flow` 进入 `search_jobs`，其他进入 `load_job`。
- `match_jobs` 后：find jobs 直接 finalize；full flow 进入 `select_job`。
- `match_job` 后：tailor/full 进 `tailor_resume`；quick apply 进 `fit_gate`；interview task 进 `generate_interview_prep`。
- `tailor_resume` 后：full flow 进 `fit_gate`；单独 tailor 结束。
- `create_application_packet` 后：full flow 继续面试包；quick apply 结束。
- `generate_interview_prep` 后：full flow 汇总；interview task 结束。

流程图见 `CAREER_AGENT_WORKFLOW_DIAGRAMS.md`。

### 3.5 几条任务如何跑

`find_jobs_for_profile`：`plan_task -> load_profile -> search_jobs -> match_jobs -> finalize_find_jobs`。

`tailor_resume_for_job`：`plan_task -> load_profile -> load_job -> match_job -> tailor_resume -> finalize_tailor`。

`quick_apply`：`plan_task -> load_profile -> load_job -> match_job -> fit_gate -> ensure_resume_version -> create_application_packet -> finalize_quick_apply`。默认会在 `create_application_packet` 触发 interrupt。

`prepare_interview_for_job`：`plan_task -> load_profile -> load_job -> match_job -> generate_interview_prep -> finalize_interview`。

`full_career_flow`：

- 没有 `job_id`：`plan_task -> load_profile -> search_jobs -> match_jobs -> select_job -> load_job -> match_job -> tailor_resume -> fit_gate -> ensure_resume_version -> create_application_packet -> generate_interview_prep -> finalize_full_flow`。
- 有 `job_id`：`plan_task -> load_profile -> load_job -> match_job -> tailor_resume -> fit_gate -> ensure_resume_version -> create_application_packet -> generate_interview_prep -> finalize_full_flow`。

相关测试：`tests/test_agent_workflow.py::test_full_career_flow_orchestrator_runs_all_core_stages`、`test_full_career_flow_with_target_job_skips_job_search`、`test_quick_apply_interrupts_and_resumes_from_sqlite_checkpoint`

### 3.6 面试回答

为什么用 LangGraph？  
因为这个项目需要多步骤、有状态、可恢复、有人审中断的 Agent 工作流。LangGraph 能显式表达 node、edge、conditional edge、checkpoint 和 interrupt，比普通 service 串调用更适合展示“当前跑到哪里、为什么走这条路、从哪里恢复”。

为什么不用普通 while loop？  
普通 loop 可以跑通流程，但 checkpoint、interrupt、条件路由、事件流和恢复点都要自己造协议。这里用 LangGraph 后，`graph_thread_id` + SQLite checkpoint 能把暂停和恢复变成框架能力，节点边界也更清晰。

state 里存什么？  
只存 `run_id/profile_id/job_id/job_ids/matches/resume_version_id/fit_gate/output` 这类 JSON 友好字段，不存 DB Session 或 ORM 对象。Session 用运行期映射注入，避免 checkpoint 序列化失败。

节点怎么划分？  
一个节点对应一个可追踪业务阶段：加载数据、搜索、匹配、定制、门禁、投递包、面试包、finalize。每个会写 step/event/artifact，便于失败定位。

某个节点失败怎么办？  
`TraceService.step` 会把该 step 标为 failed 并写 `step_failed` event；外层 `_execute_run` 捕获异常，把 run 标成 failed，输出错误、execution_plan、graph_thread_id。

怎么保证 LangGraph 迁移没破坏原流程？  
保留 `AgentOrchestrator` 兼容类名；测试覆盖 full flow、target job skip search、queued run、interrupt/resume；评测里检查 `orchestration_framework=langgraph` 和 `langgraph_pass_rate`。

LangGraph 最核心价值是什么？  
把求职流程从“几个接口串起来”升级为可恢复、可观测、可人审的 Agent 状态机。

## 4. Checkpoint / Interrupt / Resume

### 4.1 设计动机

已实现。求职 Agent 需要 interrupt，因为投递包、浏览器填写、邮件发送这类动作涉及真实职业风险和隐私风险。当前项目虽然只生成投递包，不自动提交，但仍在创建 `applications` 前加入确认点，保证用户知道“下一步会生成投递材料但不会自动提交”。

### 4.2 代码路径

Orchestrator:

- `app/agents/langgraph_orchestrator.py::_ensure_graph`：使用 `aiosqlite.connect` + `AsyncSqliteSaver`，`await checkpointer.setup()`。
- `app/agents/langgraph_orchestrator.py::_application_confirmation`：默认调用 `interrupt()`。
- `app/agents/langgraph_orchestrator.py::resume`：用 `Command(resume=resume_payload)` 恢复。
- `app/agents/langgraph_orchestrator.py::graph_state`：读取 checkpoint snapshot。

API:

- `app/api/agent_runs.py::resume_agent_run`：`POST /agent/runs/{run_id}/resume`。
- `app/api/agent_runs.py::get_agent_graph_state`：`GET /agent/runs/{run_id}/graph-state`。

Schema:

- `app/models/schemas.py::AgentRunRequest`：`application_confirmed: bool = False`。
- `app/models/schemas.py::AgentRunResumeRequest`：`confirmed/note/resume_json`。

Config:

- `app/core/config.py::Settings.langgraph_checkpoint_file`：默认 `data/runtime/langgraph_checkpoints.sqlite`。
- `app/core/config.py::Settings.langgraph_checkpoint_path`。

Tests:

- `tests/test_agent_workflow.py::test_quick_apply_interrupts_and_resumes_from_sqlite_checkpoint`。

Frontend:

- `app/static/js/main.js::resumeAgentRun`。
- `app/static/js/main.js::createAgentRun`。
- `app/static/js/main.js::waitForAgentRun`。
- `app/static/js/main.js::renderRunOutcomeLinks`。

### 4.3 关键行为

- checkpointer：`AsyncSqliteSaver`。
- checkpoint 文件：默认 `data/runtime/langgraph_checkpoints.sqlite`。
- 触发 `interrupt()` 的流程：`quick_apply` 和 `full_career_flow` 在 `create_application_packet` 节点生成投递包前。
- 返回状态：外层检测 snapshot interrupts 后，把 run finish 为 `waiting_for_confirmation`，`output_json.requires_confirmation=true`。
- 确认前是否写 application：不会。测试明确断言第一次 interrupt 后 `Application` count 为 0。
- 用户拒绝：`confirmed=false` 会让 `_node_create_application_packet` 抛 `Application confirmation rejected by user.`，run failed，不写投递包。
- 非法状态：resume API 要求 run.status 是 `waiting_for_confirmation`；否则 409。
- 跨进程恢复：当前通过 SQLite checkpoint 和 `graph_thread_id` 支持跨 Orchestrator 实例恢复；测试用第二个 Orchestrator resume。

### 4.4 面试 Q&A

checkpoint 解决什么？  
解决 Agent 跑到中间状态后可暂停、可恢复，尤其是人审确认节点。没有 checkpoint，interrupt 后只能从头重跑。

interrupt 和普通确认按钮区别？  
普通按钮只是 UI 状态；LangGraph interrupt 是图执行的一部分，会把节点暂停点、state 和 next 写入 checkpoint，resume 后从中断点继续。

为什么确认前不能写 application？  
因为投递包是高风险材料产物。如果确认前就落库，用户拒绝后仍会留下 ready application，语义上像系统已经执行了投递准备。

resume 如何知道从哪里继续？  
run input/output 保存 `graph_thread_id`，API 用同一个 thread id 读取 SQLite checkpoint，再用 `Command(resume=payload)` 继续。

重复点击确认会怎样？  
第一次成功后 run 状态不再是 `waiting_for_confirmation`；再次调用 resume 会 409。生产级还应给写库节点补幂等键。

当前设计是否支持跨进程恢复？  
支持跨 Orchestrator 实例读取 SQLite checkpoint；但节点内部 DB Session 仍是运行时注入，长时间跨 worker 恢复需要进一步独立打开 session。

离生产级差什么？  
需要审批/审计表、业务幂等键、外部队列、可取消任务、Postgres/Redis 级 checkpoint、真实浏览器/邮箱工具权限控制。

## 5. Trace / Agent Events / SSE

### 5.1 run / step / event / artifact 的区别

| 概念 | 作用 | 代码位置 |
| --- | --- | --- |
| `agent_runs` | 一次 Agent 任务的总记录，包含状态、输入、输出、错误、耗时 | `app/models/entities.py::AgentRun` |
| `agent_steps` | 业务步骤级 trace，记录 step/tool/input/output/latency/error | `app/models/entities.py::AgentStep`、`app/services/trace_service.py::step` |
| `agent_artifacts` | 可复用产物，如 execution_plan、ranked_jobs、tailored_resume、fit_gate、interview_prep | `app/models/entities.py::AgentArtifact`、`TraceService.add_artifact` |
| `agent_events` | 事件流，统一 run/step/artifact/LangGraph node 事件，供 API/SSE 读取 | `app/models/entities.py::AgentEvent`、`TraceService.add_event` |
| `llm_call_logs` | LLM 调用日志，记录 trace_name、prompt/response preview、error、context | `app/models/entities.py::LLMCallLog`、`app/core/llm.py` |

当前数据库观察：本地 `data/career_agent.db` 有 `agent_runs=156`、`agent_steps=642`、`agent_artifacts=270`、`llm_call_logs=302`；`agent_events=0`。这说明当前历史 run 多数发生在事件流表落地之前或未重跑事件流版本。代码层事件流已实现，不能把历史库里没有 event 行说成已积累事件数据。

### 5.2 数据表或模型

| 表/模型 | 作用 | 关键字段 | 谁写入 | 谁读取 |
| --- | --- | --- | --- | --- |
| `AgentRun` | run 总状态 | `task_type/status/input_json/output_json/error_message` | `TraceService.create_run/finish_run` | run API、前端、eval |
| `AgentStep` | 步骤 trace | `step_name/tool_name/status/input_json/output_json` | `TraceService.step` | `/agent/runs/{id}/steps`、前端 |
| `AgentArtifact` | 关键产物 | `artifact_type/artifact_json` | Orchestrator/service | eval、debug、面试解释 |
| `AgentEvent` | 实时/历史事件 | `event_type/node_name/event_json` | `TraceService.add_event`、LangGraph event recorder | `/events`、`/events/stream` |
| `LLMCallLog` | LLM 调试 | `trace_name/model/status/prompt_chars/response_chars/context_json` | `LLMClient` | `/llm/debug/logs`、评测页 |
| `EvaluationRun` | 评测结果 | `name/summary_json/case_results_json` | `EvaluationService` | `/evaluations/results`、前端 quality |

### 5.3 实际事件类型

代码中实际存在：

`run_created`、`run_started`、`run_resumed`、`run_finished`、`step_started`、`step_completed`、`step_failed`、`artifact_created`、`graph_started`、`graph_completed`、`graph_update`、`graph_interrupt`、`graph_node_started`、`graph_node_completed`、`graph_node_update`、`graph_failed`、SSE 的 `run_closed`、`heartbeat`。

代码位置：`app/services/trace_service.py`、`app/agents/langgraph_orchestrator.py::_record_langgraph_event`、`app/api/agent_runs.py::_agent_event_sse`

### 5.4 SSE 流程

前端订阅：`app/static/js/main.js::subscribeAgentRunEvents` 使用 `new EventSource('/agent/runs/{run_id}/events/stream')`，注册上述事件类型。  
后端推送：`app/api/agent_runs.py::stream_agent_events` 返回 `StreamingResponse`，内部 `_agent_event_sse` 周期性查询 `agent_events` 表。  
SSE 读的是事件表，不是内存队列。这样同步 run、后台 run、resume run 都能复用。  
不支持 EventSource：`subscribeAgentRunEvents` 检测 `typeof EventSource === "undefined"` 返回 null；`waitForAgentRun` 同时有 `setInterval` 轮询 `/agent/runs/{run_id}` 作为降级。  
比只查最终状态更好：可以看到每个 LangGraph node start/end、step failed、artifact created、interrupt，失败定位更快。

### 5.5 面试 Q&A

为什么 Agent 需要 trace？  
Agent 是多工具、多步骤、有中间产物的系统。只看最终输出无法知道是 PDF parse、RAG、reranker、LLM、guardrail 还是投递门禁出错。

event stream 和 trace 区别？  
trace 更像持久化审计记录，包含 run/step/artifact；event stream 是围绕运行过程的实时事件视图，底层也落表。

失败时如何定位节点？  
查 `agent_steps.status=failed`、`agent_events.event_type=graph_failed/step_failed`、`LLMCallLog.trace_name`，再看 run output 的 error 和 execution_plan。

如何避免日志泄露隐私？  
当前 LLM log 记录 preview 而不是完整密钥；生产要进一步做 PII 脱敏、字段级访问控制、日志保留周期和敏感 artifact 加密。

上线还要完善什么？  
事件表索引和归档、集中日志、trace sampling、敏感字段脱敏、跨服务 trace id、metrics dashboard。

## 6. Resume Parser / PDF 解析 / 结构化简历

### 6.1 PDF / 简历解析流程

已实现。

1. `/profiles/upload` 接收 `UploadFile`。
2. `ResumeParserService.create_profile_from_pdf` 保存文件到 `data/uploads`。
3. `pypdf.PdfReader` 提取页级文本，空文本报错。
4. `parse_structured_resume` 先 heuristic parse，再在 LLM 可用时调用 LLM 输出结构化 JSON。
5. LLM prompt 明确不要输出 `raw_text`，服务端回填原文。
6. `ProfileStructured` 归一化字段，写入 `profiles`。
7. `ResumeTextSplitter.split_structured_profile` + `split_pdf_pages/split_raw_text` 生成 chunk。
8. `SQLiteVectorIndex.upsert_profile_chunks` 写入 `resume_chunks` 和 embedding，可选 Chroma 镜像。
9. `/profiles/{profile_id}/html` 用 `ResumeHTMLRenderer` 预览。

代码位置：`app/api/profiles.py::upload_resume`、`app/services/resume_parser.py::ResumeParserService`、`app/services/text_splitter.py::ResumeTextSplitter`、`app/services/vector_index.py::SQLiteVectorIndex`

### 6.2 结构化字段表

| 字段 | 含义 | 来源 | 是否进入 RAG chunk |
| --- | --- | --- | --- |
| `name/email/phone` | 基础信息 | LLM/heuristic/guided | 不作为独立 chunk；raw 清洗会过滤部分元信息 |
| `photo_data_url` | 手动上传照片 data URL | guided form | 否，照片不进 RAG |
| `location/availability` | 地点/到岗 | LLM/guided | 结构化保存，默认不重点检索 |
| `headline/self_summary` | 标题/总结 | LLM/heuristic/guided | summary 会进入 chunk |
| `target_roles` | 求职意向 | LLM/guided | 不作为匹配证据，raw support 会过滤意向行 |
| `education` | 教育经历，多段 | LLM/guided | 是，`education` chunk，但 evidence classifier 多标为 weak/coursework |
| `skills` | 技能列表 | LLM/heuristic/guided | 是，`skill` chunk |
| `projects` | 项目，多段 | LLM/guided | 是，`project` chunk |
| `work_experience` | 实习/工作，多段 | LLM/guided | 是，`experience` chunk |
| `campus_experience` | 校园/实践，多段 | LLM/guided | 是，`experience` chunk |
| `certifications/awards/languages/portfolio_links` | 证书/奖项/语言/链接 | LLM/guided | 是，按 credential/award/language/portfolio chunk |
| `raw_text` | 原始简历文本 | PDF/服务端回填/guided 合成 | 是，raw/page chunk，但清洗过滤目标意向等 |

### 6.3 raw_text bug 复盘

问题：复杂多页 PDF 简历解析时，原 prompt 要求 LLM 在 JSON 中返回完整 `raw_text`，真实调用输出过长导致 JSON 截断，上传失败。

根因：`raw_text` 本身是服务端已经拥有的原始文本，不应该让 LLM 再复制一遍；复制会浪费 token，并增加 JSON 截断概率。

定位方式：开发日志记录在 2026-06-18 08:34 的复杂 PDF smoke；本地 `pypdf` 能抽取 3 页 4486 字符，但 LLM JSON 被截断。

修复方案：prompt 中移除 `raw_text` 输出字段，明确 “Do not include raw_text in the JSON output. The service will store the original text separately.”；解析成功后 `parsed["raw_text"] = raw_text`。

相关代码：`app/services/resume_parser.py::parse_structured_resume`。  
相关测试：`tests/test_resume_parser.py::test_resume_parser_omits_raw_text_from_llm_schema_and_refills_server_side`。  
面试时怎么讲：这是一次典型的 LLM 工程 bug，核心修复不是加大 token，而是重新划分 LLM 与服务端职责。

### 6.4 面试 Q&A

为什么不能让 LLM 输出完整 raw_text？  
因为 raw_text 已经由服务端可靠提取，让 LLM 复制会浪费输出预算并导致 JSON 截断，还可能改写原文。

PDF 解析失败怎么办？  
空文件或无可提取文本返回 400；LLM 不可用时如果 `LLM_FALLBACK_ENABLED=false` 直接报错，测试环境可 heuristic fallback。

中文简历字段如何处理？  
schema 支持中文字段内容，heuristic section 识别包含“个人总结/教育/项目/实习/校园/证书/获奖”等关键词，前端手动建档也是中文主场景。

多段经历如何表示？  
`education/projects/work_experience/campus_experience` 都是 list，前端 repeat-list 支持多段，Pydantic schema 也按 list 归一化。

简历照片是否进入 RAG？  
不进入。照片作为 `photo_data_url` 存在结构化 Profile 里，RAG 只检索文本证据，避免隐私和无关信号污染。

HTML 预览和定制简历关系？  
Profile HTML 展示原始结构化简历；定制简历 HTML 展示 `ResumeVersion.tailored_resume_markdown` 渲染结果。

## 7. RAG / Chunk / Evidence

### 7.1 RAG 总流程

已实现：Profile/JD 都会 chunk 和 embedding；检索时使用 SQLite 权威存储，Chroma 是可选镜像；reranker 可启用；证据再进入 matcher、tailor、interview prep。

代码位置：`app/services/text_splitter.py`、`app/services/vector_index.py`、`app/services/embedding_service.py`、`app/services/reranker.py`、`app/services/evidence_classifier.py`

### 7.2 Chunk 策略

简历：

- 结构化字段 chunk：summary、skill、project、experience、education、award 等。
- PDF 页级 chunk：每页用段落优先 + sliding window。
- raw text chunk：`paragraph_then_sliding_window`，默认 `chunk_size=900`、`chunk_overlap=160`。
- metadata：field、item_index、page_no、char_start/char_end、strategy、embedding provider/model/dimensions。

JD：

- 结构化字段 chunk：`required_skills/preferred_skills/responsibilities/qualifications/keywords`。
- 原始 JD chunk：raw paragraph window。

污染控制：

- `ResumeChunk` 有 `profile_id`，`JobChunk` 有 `job_id`，检索必须按 profile/job filter。
- upsert 时先删除同一 profile/job 旧 chunks，避免版本混入。
- 匹配 support text 过滤 target role、headline、邮箱等元信息，避免“想做 Agent”被当成“做过 Agent”。

### 7.3 Retrieval / Reranker

Embedding：

- 默认 provider：`sentence_transformers`。
- 默认模型：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`。
- 测试/离线可用 hash embedding。

Vector store：

- 主路径：SQLite 表 `resume_chunks/job_chunks` 中保存 `embedding_json` 和 metadata。
- 可选镜像：`ChromaVectorLibrary`，collection 按 `profile_{id}_chunks`、`job_{id}_chunks`。

检索评分：

- `score = vector_score * retrieval_vector_weight + lexical_score * retrieval_lexical_weight + type_boost`。
- 默认权重来自 config：vector 0.45、lexical 0.50、type 0.05。

Reranker：

- 默认 provider：`cross_encoder`。
- 默认模型：`cross-encoder/ms-marco-MiniLM-L-6-v2`。
- Top20 候选 rerank，Top5 anchor 保护召回。
- unsupported provider：如果 fallback 是 heuristic，则记录 fallback_reason；否则 `ValueError`。

代码位置：`app/services/vector_index.py::_query_rows`、`app/services/reranker.py::_score_pairs`、`app/core/config.py::Settings`

### 7.4 Evidence 设计

正向证据：`shipped_project`、`metric_evidence`，通常有 built/implemented/deployed/构建/实现/部署 + 项目/指标。  
弱证据：`coursework`、`planned_learning`、generic skill。  
负向证据：`missing_skill_disclosure`，例如 `No MLflow`、`without production RAG`、`没有 Kubernetes 集群维护经验`。  
处理方式：matcher 会降低负向/弱证据权重，负向句里的技能不算 matched；tailor prompt 和 guardrail 禁止把缺口写入简历正文。  
代码位置：`app/services/evidence_classifier.py::EvidenceClassifier`、`app/services/matcher.py::_project_relevance`、`app/services/resume_tailor.py::_llm_tailor`

### 7.5 面试 Q&A

为什么需要 RAG？  
定制简历和面试问题必须基于候选人真实经历，RAG 提供可追溯证据，避免 LLM 只按 JD 编写。

chunk 太大/太小问题？  
太大容易把课程噪声、计划学习和真实项目混在一起；太小丢上下文。项目评测选择 `paragraph_page_900_overlap160` 是折中。

embedding 和 reranker 区别？  
embedding 用于一阶段召回；reranker 用 query-doc pair 做更细排序，但权重太高会破坏召回，所以用保守融合和 Top5 anchor。

如何防止 JD 要求写成用户经历？  
RAG 只检索 Profile chunks；tailor prompt 规定缺失技能只能进 alignment missing/notes；Guardrail 检查 unsupported required skill claim。

如何评估 RAG？  
用 `evals/rag_cases.json` 和 `/evaluations/rag-strategies` 看 Top1、Top3 Recall、Top5 Recall、MRR、nDCG@5、difficulty/noise breakdown。

什么是 RAG pollution？  
不同 profile/job 证据混入，或把目标意向/JD 要求/课程噪声当成真实经历。项目用 profile_id/job_id filter、upsert 删除旧 chunks、target intent 过滤和 evidence classifier 控制。

## 8. Matcher / Fit Gate / 负向证据

### 8.1 匹配流程

已实现。

1. 从 `job.structured_jd_json.required_skills` 和 fallback keywords 提取必备技能。
2. 从 Profile 结构化字段和 raw support text 构造支持文本，并过滤 target intent。
3. 用 skill alias + fuzzy contains 初筛。
4. 句子级检查正向/负向语境，负向优先。
5. `retrieve_evidence` 从 profile chunks 检索 Top evidence，并用 `EvidenceClassifier` 标注。
6. 计算 required coverage、semantic similarity、project evidence、internship fit、preferred coverage、negative penalty。
7. 写入 `match_results`。

代码位置：`app/services/matcher.py::MatcherService.build_match_payload`

### 8.2 fit score 计算方式

当前是 heuristic + embedding hybrid，不是纯 LLM：

```text
overall =
  required_score * 0.38
  + semantic_score * 0.24
  + project_score * 0.22
  + internship_score * 0.08
  + preference_score * 0.08
  - negative_penalty
```

然后 clamp 到 0-100。代码位置：`app/services/matcher.py::build_match_payload`。

fit gate：`overall_score >= 55` 才允许继续投递包，否则抛错阻断。代码位置：`app/agents/langgraph_orchestrator.py::_fit_gate`。

### 8.3 负向证据规则

| 负向表达 | 示例 | 处理方式 |
| --- | --- | --- |
| `no / not / without / lacks / missing` | `No MLflow experience` | 不算正向技能，增加 negative penalty |
| `did not build / did not implement` | `did not build an Agent system` | 技能进入 missing |
| `coursework / read papers / read articles` | `Coursework: RAG` | 标为 weak/coursework |
| `planned / currently learning` | `planned to learn Kubernetes` | 标为 planned_learning |
| `没有 / 未实现 / 未交付 / 缺少` | `没有 Kubernetes 集群维护经验` | 负向优先，不因“维护”算正向 |

代码位置：`app/services/matcher.py::NEGATIVE_EVIDENCE_CUES`、`app/services/evidence_classifier.py::MISSING_DISCLOSURE_CUES`

### 8.4 bug 复盘

问题：`No MLflow experience`、`没有 Kubernetes 集群维护经验` 曾因关键词命中被误判为正向技能证据。  
根因：早期关键词匹配只看 skill token 是否出现，没有句子级 polarity，也没有缺口披露优先级。  
定位方式：强噪声 RAG/eval case 和 matcher 测试暴露；开发文档明确记录。  
修复方案：增加 `NEGATIVE_EVIDENCE_CUES`、`EvidenceClassifier`、`_skill_has_positive_or_neutral_support`、`_negative_evidence_penalty`，tailor prompt 和 guardrail 也禁止缺口进正文。  
相关代码：`app/services/matcher.py`、`app/services/evidence_classifier.py`、`app/services/guardrails.py`、`app/services/resume_tailor.py`。  
相关测试：`tests/test_matcher.py::test_matcher_penalizes_negative_or_coursework_only_evidence`、`tests/test_evidence_classifier.py::test_evidence_classifier_distinguishes_delivery_and_negative_evidence`、`tests/test_guardrails.py::test_guardrail_rejects_missing_skill_learning_intent_in_resume_body`。  
面试时怎么讲：这是 RAG/Agent 项目里很典型的“检索命中不等于事实支持”，修复点是引入证据 polarity。

### 8.5 面试 Q&A

为什么关键词匹配不够？  
关键词不理解否定、课程、计划学习和真实交付的区别。

“没有 Kubernetes 经验”为什么不能算 Kubernetes 证据？  
同一句话明确否定了该经验，必须按缺口处理。

如何避免缺口写进简历正文？  
tailor prompt 写 hard rule，Guardrail 检测 `missing_skill_in_resume_body`，高风险触发 repair。

skill alias 怎么做？  
`SKILL_ALIASES` 维护常见同义词，如 evaluation/metrics、A/B testing/experiment analysis。

fit gate 如何决定？  
当前 `overall_score >= 55`，否则 quick apply 阻断，返回缺失技能说明。

低匹配 Agent 怎么处理？  
可以生成分析/面试准备或定制建议，但不自动进入投递包。

## 9. Guardrail / 投递边界 / Prompt Injection

### 9.1 Guardrail 总览

| Guardrail | 拦截什么 | 在哪里触发 | 失败后怎么处理 |
| --- | --- | --- | --- |
| ResumeGuardrail | 未支持数字、过多新 token、unsupported required skills、缺口技能写进正文 | `ResumeTailorService.tailor_resume` | 高风险触发一次 repair；仍失败则记录 high risk |
| ApplicationPacketGuardrail | 求职信/外联文案 unsupported skill claims、缺目标岗位、缺人工确认边界 | `ApplicationService.create_quick_apply_packet` | validation 不通过则抛 `ValueError`，不写 application |
| Fit gate | 匹配分低于 55 | LangGraph `fit_gate` node | 抛错，run failed |
| LangGraph interrupt | 投递包创建前需要用户确认 | `create_application_packet` node | 暂停为 `waiting_for_confirmation` |
| Evidence classifier | 课程/计划/缺口披露 | RAG/matcher/tailor/interview | 降权或要求诚实披露 |

### 9.2 投递边界

高风险动作：生成投递包、未来浏览器填写、邮件发送、真实提交申请、读取招聘平台登录态。  
当前需要确认：`quick_apply` 和 `full_career_flow` 在 `create_application_packet` 前。  
当前是否真的发送邮件/自动投递：没有。`ApplicationService` 只生成 `cover_letter/outreach/checklist/apply_url`，`automation_result.mode=manual_confirm_required`、`final_submission=user_confirmed_only`。  
如果未来接浏览器/邮箱工具：要增加审批/审计表、权限 scope、幂等键、取消/回滚策略、敏感信息脱敏和工具调用 allowlist。

### 9.3 Prompt Injection

部分实现。当前明确的 prompt injection 防护不是完整安全系统，而是靠边界规则降低风险：

- 简历定制 prompt 要求只使用 Profile/evidence 事实，不接受 JD 要求作为候选人事实。
- 缺失技能只能进入 missing/notes，不能写正文。
- Application guardrail 检查投递文案是否声明了 unsupported capability。
- 高风险副作用前需要 interrupt。

当前代码中未找到独立的 JD prompt injection detector、恶意指令分类器或工具调用 policy engine。应补：JD 指令隔离、HTML/Markdown sanitization、tool permission scope、prompt injection eval cases。

### 9.4 面试 Q&A

如何防止编造经历？  
RAG evidence 限制输入，prompt 写 hard rule，ResumeGuardrail 检查 unsupported metrics/skills，必要时 repair。

如何防止自动投递？  
当前没有最终提交能力；投递包前还有 LangGraph interrupt，application packet 的 automation_result 也标明必须人工确认。

如何处理 JD 恶意指令？  
当前主要靠“JD 只作为 job requirements，不作为 system instruction”这一边界；生产还要加 prompt injection classifier 和工具权限隔离。

用户确实缺技能怎么办？  
缺口进入 `missing_skills`、`keyword_alignment.missing`、面试 gap drill，不包装成已掌握。

Guardrail 和 RAG 关系？  
RAG 提供证据，Guardrail 验证生成内容是否越过证据边界。

当前不足？  
规则为主，覆盖有限；缺独立审批表、prompt injection 专项 eval、PII 脱敏和权限系统。

## 10. Interview Prep / 面试包模块

### 10.1 面试包生成流程

已实现。

Agent task：`prepare_interview_for_job` 路径为 `load_profile -> load_job -> match_job -> generate_interview_prep -> finalize_interview`。  
Service：`InterviewPrepService.create_interview_prep_with_llm` 先创建/使用 match result，再生成 LLM question sets，最后 `create_interview_prep` 汇总规则题组、gap drills、research checklist、coverage。  
Artifact：LangGraph 节点写 `artifact_type="interview_prep"`。  
代码位置：`app/services/interview_prep.py::InterviewPrepService`、`app/agents/langgraph_orchestrator.py::_node_generate_interview_prep`。

### 10.2 面试包包含内容

真实代码包含：

- 已导入面经追问：基于 `InterviewExperienceService.find_relevant_for_job` 找到的已导入文本。
- 同岗位面经与高频追问：生成牛客网、OfferShow、小红书等搜索参考链接和问题，不抓正文。
- 项目深挖：基于 Profile 项目、技术栈和 RAG evidence。
- LLM 项目/基础追问：LLM 可用时生成 `llm_project_implementation` 和 foundation drill。
- JD 技术深挖：围绕 required/preferred/keywords。
- 缺口 drill：针对 missing skills 诚实披露和补齐计划。
- 通用行为/协作问题。
- coverage：`required_skill_coverage_rate`、`missing_skill_drill_rate`、`evidence_backed_question_rate`、`preparation_angle_counts` 等。

模型位置：`app/models/entities.py::InterviewPrep`。

### 10.3 注意事项

项目不是稳定自动爬取真实面经。代码和文档都强调：无法获取正文时只保留标题、链接和待核验主题；用户可以通过 `/interview-prep/experiences` 导入真实面经文本，系统再提取问题和可信度。

代码位置：`app/services/interview_prep.py::_interview_reference_links`、`_online_experience_questions`、`app/api/interview_prep.py::create_interview_experience`

### 10.4 面试 Q&A

面试包和简历定制关系？  
都基于同一 Profile/JD/match evidence，但简历定制产出面向投递，面试包产出面向准备和追问。

如何使用 JD 和简历证据？  
先 match 生成 required/missing/evidence，再按项目、JD 技术点、缺口生成问题，每个问题带 evidence_refs 或 risk_level。

如何避免与用户经历不符？  
缺口技能生成 gap drill，不包装成项目经验；问题 answer_points 会提示“没有真实交付要诚实说明边界”。

是否调用外部网站？  
核心生成不依赖外部抓取；只生成搜索链接。已导入面经由用户提供。

有哪些 eval？  
`evals/interview_prep_cases.json`、`tests/test_interview_prep.py`、`tests/test_evaluation_service.py::test_interview_prep_evaluation_covers_sources_stack_and_gap_drills`。

## 11. Natural Language Agent

### 11.1 自然语言 Agent 流程

已实现为独立 LangGraph。代码位置：`app/agents/natural_language.py::NaturalLanguageAgentService`。

节点：

- `parse_user_request`：LLM 规划 intent。
- `execute_user_plan`：执行计划，可创建 profile/job 或调用主 Orchestrator。
- `repair_user_plan`：执行失败后修复一次计划。
- `execute_repaired_user_plan`：执行修复计划。
- `finalize_success/finalize_failed`：结构化返回。

条件边：

- execute 成功 -> success。
- execute 失败 -> repair。
- repaired execute 成功 -> success。
- repaired execute 失败 -> failed。

### 11.2 为什么需要 repair 节点？

自然语言输入很容易缺少 `job_id/profile_id`，或者 LLM 第一次选择了不合适 intent。repair 节点读取错误和原计划，最多修一次，例如缺少 job_id 但有 JD 时改为创建 job；匹配分不足时不能绕过 fit gate，而是改成生成建议。

不会无限循环：图里只有一条 repair 路径，没有回到 `repair_user_plan` 的循环边。代码位置：`_build_graph`、`_route_after_execute`、`_route_after_repaired_execute`。

### 11.3 面试 Q&A

自然语言入口和固定 API task 区别？  
固定 task 是结构化请求；自然语言入口先 parse 成 intent/plan，再调用同一套主 Orchestrator。

解析失败怎么办？  
如果执行失败，进入 repair 一次；仍失败则返回 `failed` 和用户可读错误。

repair 是否无限循环？  
不会，图结构只允许一次 repair。

如何控制工具边界？  
只允许预定义 intents 和内部 service/Orchestrator，不让 LLM 任意调用工具；高风险投递仍走主图 interrupt。

## 12. Eval / 测试体系

### 12.1 测试分类表

| 测试文件 | 覆盖模块 | 关键 case | 面试中怎么讲 |
| --- | --- | --- | --- |
| `tests/test_agent_workflow.py` | LangGraph、full flow、queued run、interrupt/resume | `test_quick_apply_interrupts_and_resumes_from_sqlite_checkpoint` | 证明主图、checkpoint、人审恢复可跑 |
| `tests/test_natural_language_agent.py` | 自然语言 Agent | repair missing job、empty search failure | 证明 NL 入口不是裸 prompt |
| `tests/test_resume_parser.py` | PDF/LLM parser | raw_text 不进 prompt、服务端回填、retry | 复杂 PDF bug 有回归 |
| `tests/test_matcher.py` | 匹配和负向证据 | negative/coursework penalty | 证明 No MLflow 不算正向 |
| `tests/test_guardrails.py` | 简历 guardrail | 缺口技能不能进正文 | 生成安全边界 |
| `tests/test_application_guardrails.py` | 投递包 guardrail | unsupported claims、manual confirmation | 投递文案也防编造 |
| `tests/test_vector_index.py` | SQLite RAG | 检索相关项目 | RAG 基础路径 |
| `tests/test_embedding_reranker.py` | embedding/reranker | provider metadata、rerank promotion | 真实 RAG 组件 |
| `tests/test_context_compressor.py` | 上下文压缩 | progressive disclosure budget | 长上下文治理 |
| `tests/test_interview_prep.py` | 面试包 | 三类角度、LLM followups、导入面经 | 面试包有覆盖和质量判断 |
| `tests/test_evaluation_service.py` | 评测体系 | agent full flow、JD parser、LLM workflow metrics | 不只单元测试，还有量化 eval |
| `tests/test_frontend_pages.py` | 前端 smoke | agent runs timeline、dashboard flow、quality page | 页面级可见能力 |
| `tests/test_llm_debug.py` | LLM log/debug | retry、trace context、DeepSeek thinking | 可排障 |
| `tests/test_job_chunks.py` | JD chunk | JD chunks stored/retrievable | 岗位侧 RAG |
| `tests/test_task_queue.py` | 后台任务 | LLM workflow progress | BackgroundTasks 可用但非生产队列 |

开发日志最近记录：2026-06-18 15:52 全量 `python -m pytest -q` 通过 98 个测试；`node --check app\static\js\main.js` 通过；`py_compile` 通过；`git diff --check` 通过。来源：`docs/DEVELOPMENT_LOG.md`。

### 12.2 Eval 指标

真实存在指标包括：

- RAG：Top1 Acc、Top3 Recall、Top5 Recall、MRR、nDCG@5、difficulty/noise breakdown。
- Agent full flow：`pass_rate`、`completed_rate`、`top_job_accuracy`、`score_gate_accuracy`、`tailor_pass_rate`、`quick_apply_pass_rate`、`application_packet_pass_rate`、`fit_gate_block_count`、`trace_pass_rate`、`artifact_pass_rate`、`langgraph_pass_rate`。
- JD parser：`avg_required_skill_recall`、`avg_keyword_hit_rate`、`job_type_accuracy`、`absent_required_skill_violation_count`。
- Interview prep：`research_source_pass_rate`、`source_backed_pass_rate`、`gap_drill_pass_rate`、`question_quality_pass_rate`、`avg_required_skill_coverage_rate`。
- LLM workflow：`completed_rate`、`end_to_end_pass_rate`、`resume_parse_success_rate`、`jd_parse_success_rate`、`fit_label_accuracy`、`tailor_pass_rate`、`guardrail_pass_rate`、`forbidden_claim_free_rate`、`context_compression`。

代码位置：`app/services/evaluation_service.py`、文档：`docs/EVALUATION.md`。

### 12.3 Real LLM Smoke

来自本地数据库和开发日志/评测文档：

- 当前数据库：`profiles=150`、`jobs=193`、`resume_versions=81`、`applications=22`、`interview_preps=35`、`agent_runs=156`、`llm_call_logs=302`。
- 复杂 PDF 前端验收：2026-06-18，3 页、4486 字符，真实 DeepSeek LLM，创建 Profile #150、Job #193、Resume #81、Application #22、InterviewPrep #35，Agent runs #154/#155/#156 completed。来源：`docs/DEVELOPMENT_LOG.md` 和当前 SQLite recent runs。
- 真实 LLM 18-case 长跑：EvaluationRun #31，`case_count=18`、`end_to_end_pass_rate=1.0`、`fit_label_accuracy=1.0`、`tailor_pass_rate=1.0`、`guardrail_pass_rate=1.0`，trace 文件 `data/runtime/llm_workflow_trace_deepseek_v4_full_rerun.jsonl`。来源：当前 SQLite `evaluation_runs` 和 `docs/EVALUATION.md`。

### 12.4 面试 Q&A

怎么评测 Agent？  
单测保证模块行为，workflow tests 保证图路径，eval datasets 量化检索/匹配/生成质量，真实 LLM smoke 验证端到端。

为什么只看最终结果不够？  
最终结果可能正确但中间绕过 LangGraph、没写 artifact、没过 guardrail。评测要看 trace、artifact、event、LLM log。

full-flow eval 怎么设计？  
可控岗位源 + guided profile，检查 Top1、分数区间、tailor guardrail、quick apply 门禁、trace/artifact/langgraph 标识。

如何验证 LangGraph 迁移没有绕过？  
run input/output/execution_plan 都含 `orchestration_framework=langgraph`，eval 检查 LangGraph pass rate。

LLM 输出不稳定怎么测？  
schema 归一化 null，有限 retry，JSON repair，stage_trace，失败 stage breakdown，真实 LLM smoke 不静默 fallback。

adversarial case 怎么设计？  
加入 No MLflow、没有 Kubernetes、did not build agent system、coursework、planned learning、目标意向污染等。

## 13. API 与前端页面

### 13.1 API 表

| API | 方法 | 作用 | 输入 | 输出 | 面试中怎么讲 |
| --- | --- | --- | --- | --- | --- |
| `/agent/runs` | POST | 同步创建 Agent run | `AgentRunRequest` | `AgentRunResponse` | 主 LangGraph 入口 |
| `/agent/runs/background` | POST | 创建 queued run 并后台执行 | `AgentRunRequest` | 202 run | demo 可看进度，但非生产队列 |
| `/agent/runs/{run_id}/resume` | POST | 恢复 interrupt | `AgentRunResumeRequest` | run | 人工确认后继续 |
| `/agent/runs/{run_id}/graph-state` | GET | 查看 checkpoint snapshot | run_id | next/values/interrupts/checkpoint_id | debug 恢复点 |
| `/agent/runs/{run_id}/steps` | GET | 查询 step trace | run_id | steps | 失败定位 |
| `/agent/runs/{run_id}/events` | GET | 查询事件表 | after_id/limit | events | 事件流历史 |
| `/agent/runs/{run_id}/events/stream` | GET | SSE 实时事件 | after_id/heartbeat | text/event-stream | 前端时间线 |
| `/profiles/upload` | POST | PDF 简历上传 | file | Profile | 解析 + chunk |
| `/profiles/guided` | POST | 手动建档 | `GuidedProfileRequest` | Profile | 中文结构化简历 |
| `/profiles/{profile_id}/html` | GET | Profile HTML 预览 | profile_id | HTML | 原始简历预览 |
| `/jobs` | POST | 手动创建 JD | `JobCreateRequest` | Job | 创建 job + JD chunks |
| `/jobs/search` | POST | 搜索岗位 | `JobSearchRequest` | jobs/source_errors | 真实岗位源 |
| `/jobs/{job_id}/chunks` | GET | 查看 JD chunks | job_id | chunks | 岗位侧 RAG |
| `/matches` | POST | 创建匹配结果 | profile_id/job_id | MatchResult | 匹配分和证据 |
| `/resumes/tailor` | POST | 定制简历 | profile_id/job_id | ResumeVersion | RAG + LLM + guardrail |
| `/resumes/{id}/html` | GET | 定制简历预览 | resume_version_id | HTML | 查看生成结果 |
| `/applications/quick-apply` | POST | 直接生成投递包 | QuickApplyRequest | Application | 非 LangGraph 直接 API，仍有 guardrail |
| `/interview-prep` | POST | 创建面试包 | InterviewPrepRequest | InterviewPrep | JD+证据问题 |
| `/interview-prep/experiences` | POST | 导入面经文本 | raw_text/source | InterviewExperience | 用户确认来源 |
| `/assistant/natural-language` | POST | 自然语言 Agent | instruction/context | NL response | parse/execute/repair |
| `/evaluations/*` | POST/GET | 各类评测 | query/case_limit | EvaluationRun | 量化质量 |
| `/tasks/llm-workflow` | POST | 后台 LLM workflow | case_limit/trace_path | TaskRun | 长跑任务 |
| `/llm/debug/logs` | GET | LLM 调用日志 | filters | logs | 调试 prompt/错误 |
| `/ops/readiness` | GET | readiness | none | health | 生产检查 |

### 13.2 前端页面表

| 页面 | 作用 | 关联 API | 展示内容 |
| --- | --- | --- | --- |
| `/` | 首页一键流程和自然语言入口 | `/profiles/*`、`/agent/runs/background`、SSE | 建档、搜索/匹配、定制、投递、面试 stages |
| `/ui/profiles` | Profile 管理 | `/profiles`、`/profiles/upload`、`/profiles/guided` | PDF 上传、完整中文简历表单、HTML 预览 |
| `/ui/jobs` | 岗位管理 | `/jobs`、`/jobs/search` | 岗位列表、JD、投递链接 |
| `/ui/agent-runs` | Agent 流程页 | `/agent/runs`、steps/events/SSE/resume | run 列表、step trace、事件流、确认继续 |
| `/ui/resumes` | 定制简历版本 | `/resumes`、HTML/Markdown | iframe 预览、Guardrail 状态 |
| `/ui/applications` | 投递包列表 | `/applications` | 求职信、外联、checklist、validation |
| `/ui/interview-prep` `/ui/prep` | 面试包 | `/interview-prep` | 题组、gap drill、调研链接、练习状态 |
| `/ui/quality` `/ui/evaluations` | 评测控制台 | `/evaluations/*`、`/tasks`、`/llm/debug/logs` | eval summary、LLM workflow trace、source smoke |
| `/ui/ops` | 运维页 | `/ops/readiness`、`/ops/metrics`、`/ops/config` | readiness、metrics、配置摘要 |

## 14. 真实 Bug / 工程复盘

### Bug 1：LangGraph state 未声明 `job_ids`

出现位置：`app/agents/langgraph_orchestrator.py::CareerAgentGraphState`  
现象：`search_jobs` 返回岗位后，后续 `match_jobs` 拿不到岗位列表。  
根因：LangGraph `TypedDict` state schema 会丢弃未声明字段。  
修复：在 state 中声明 `job_ids: list[int]`。  
测试：`tests/test_agent_workflow.py::test_full_career_flow_orchestrator_runs_all_core_stages`。  
面试怎么讲：框架迁移不是改 import，要理解 state schema 对持久化的约束。  
还能怎么优化：为 state 字段做 schema lint 和节点 output contract 测试。

### Bug 2：SQLAlchemy Session / ORM 不能放进 state

出现位置：LangGraph 迁移设计。  
现象：checkpoint/resume 会遇到序列化和副作用重放问题。  
根因：Session/ORM 是运行期对象，不是 JSON 状态。  
修复：state 只存 ID；`_runtime_dbs[run_id]` 临时注入 DB Session。  
测试：LangGraph workflow tests。  
面试怎么讲：这是 Agent 状态设计的核心边界。  
还能怎么优化：节点内独立打开 Session，支持更长时间恢复和多 worker。

### Bug 3：InMemorySaver 不能跨进程恢复

出现位置：早期 LangGraph checkpointer。  
现象：中断后换 Orchestrator/进程无法 resume。  
根因：checkpoint 在内存。  
修复：换 `AsyncSqliteSaver`，默认 `data/runtime/langgraph_checkpoints.sqlite`。  
测试：`test_quick_apply_interrupts_and_resumes_from_sqlite_checkpoint`。  
面试怎么讲：demo 能跑不等于可恢复，持久化 checkpointer 是 interrupt 的前提。  
还能怎么优化：生产换 Postgres/Redis checkpointer。

### Bug 4：AsyncSqliteSaver 需要异步懒初始化

出现位置：`_ensure_graph`。  
现象：同步初始化 async graph/checkpointer 不合适。  
根因：`aiosqlite.connect` 和 `checkpointer.setup()` 都是异步。  
修复：run/resume/graph_state 时懒初始化，结束后 `_close_checkpoint`。  
测试：checkpoint resume 测试。  
面试怎么讲：框架能力要和 async runtime 对齐。  
还能怎么优化：连接生命周期池化。

### Bug 5：interrupt 后前端把 waiting 当失败

出现位置：`app/static/js/main.js::createAgentRun`、`waitForAgentRun`。  
现象：新增 interrupt 后 quick apply 不再直接 completed，旧 UI 报失败。  
根因：前端只认识 completed/failed。  
修复：识别 `waiting_for_confirmation`，可显示“确认继续”或 auto confirm。  
测试：`tests/test_frontend_pages.py::test_agent_runs_page_exposes_langgraph_event_timeline`。  
面试怎么讲：引入人审会改变前后端状态机，不能只改后端。  
还能怎么优化：独立审批 UI 和 audit 表。

### Bug 6：PDF `raw_text` JSON 截断

出现位置：`ResumeParserService.parse_structured_resume`。  
现象：复杂 3 页 PDF 上传失败。  
根因：要求 LLM 在 JSON 中复制完整 raw_text。  
修复：LLM 只返回结构化字段，服务端回填 raw_text。  
测试：`tests/test_resume_parser.py::test_resume_parser_omits_raw_text_from_llm_schema_and_refills_server_side`。  
面试怎么讲：把确定性数据留在服务端，LLM 只做结构化抽取。  
还能怎么优化：layout-aware parser、字段级置信度。

### Bug 7：不支持的 reranker provider 导致匹配 500 不清晰

出现位置：`RerankerService` + `/matches`。  
现象：配置 `RERANKER_PROVIDER=keyword` 时匹配阶段失败，前端只看到泛化 500。  
根因：provider 不在支持集合，旧 `/matches` 没包装根因。  
修复：`/matches` 捕获异常、rollback、返回 `Match generation failed: ...`；配置支持 heuristic fallback 或 error。  
测试：`tests/test_matcher.py::test_matches_api_returns_structured_error_for_matching_failure`。  
面试怎么讲：真实系统要把 provider 配置错误暴露给排障，而不是吞成 500。  
还能怎么优化：启动前 readiness 检查阻断错误配置。

### Bug 8：负向证据误判正向技能

出现位置：`MatcherService`、`EvidenceClassifier`、`ResumeGuardrailService`。  
现象：`No MLflow`、`没有 Kubernetes 经验` 被关键词匹配当正向。  
根因：只看 token，不看句子 polarity。  
修复：负向 cue、evidence polarity、negative penalty、Guardrail 检查。  
测试：`tests/test_matcher.py::test_matcher_penalizes_negative_or_coursework_only_evidence`。  
面试怎么讲：检索命中不等于事实支持。  
还能怎么优化：训练/LLM verifier 做证据分类。

### Bug 9：EventSource 不可用需要轮询降级

出现位置：`main.js::subscribeAgentRunEvents`、`waitForAgentRun`。  
现象：部分内置浏览器不支持 EventSource，一键流程进度监听失败。  
根因：前端假设 SSE 一定可用。  
修复：能力检测，不支持时返回 null；`waitForAgentRun` 同时轮询 run 状态。  
测试：前端页面 smoke；开发日志记录。  
面试怎么讲：实时体验要有降级路径。  
还能怎么优化：WebSocket/polyfill 或 server polling API。

### Bug 10：`/ui/resumes` 一次加载所有 iframe 变重

出现位置：`main.js::loadResumes`。  
现象：历史 81 个简历版本时页面仍可用但继续增长会变慢。  
根因：列表直接为每个版本创建 iframe。  
修复：当前未修复，开发日志标为待完善。  
测试：页面 smoke 只验证存在，不验证性能。  
面试怎么讲：这是 demo 到生产的前端性能差距。  
还能怎么优化：分页、懒加载、只展开当前预览。

## 15. 当前不足与上线改造

| 不足 | 为什么是问题 | 当前影响 | 后续怎么改 |
| --- | --- | --- | --- |
| BackgroundTasks 不是生产级队列 | 进程重启丢任务，不支持分布式重试/取消 | demo 可用，多实例不稳 | Celery/Arq/RQ + Redis/Postgres |
| SQLite 适合本地 demo | 高并发写入、锁、备份和权限有限 | 单机足够 | PostgreSQL + pgvector/Qdrant |
| checkpoint 节点内部 DB Session 仍需独立化 | 长时间恢复和多 worker 不稳 | 当前跨 Orchestrator 可测，但不够生产 | 节点内按需打开 session |
| 写库节点缺业务幂等键 | resume/retry 可能重复写 application/resume/prep | 当前靠状态限制 | 对 profile/job/resume/task 加唯一业务键 |
| 投递确认无独立审批表 | 审计不可查询，不适合真实高风险工具 | run output 里有记录 | 新增 approval_requests/decisions |
| 真实邮件/浏览器工具权限未建 | 自动化提交风险高 | 当前不自动提交 | 工具 scope、审批、沙盒、日志脱敏 |
| RAG 版本管理弱 | 简历多版本/多 profile 污染风险 | 通过 profile_id 初步隔离 | resume_version_id 级索引和 metadata filter |
| `/ui/resumes` iframe 太多 | 页面越来越重 | 81 版本已可感知风险 | 分页、懒加载 |
| real LLM smoke 未完全自动 E2E | 浏览器文件上传曾受限 | 仍需人工/脚本组合 | Playwright + API upload + UI 验证 |
| Prompt Injection 防护不系统 | JD 可能携带恶意指令 | 当前靠规则边界 | 注入分类器、tool policy、红队 eval |
| 评测数据多为合成 | 可能不代表真实简历/JD | 对 demo 足够 | 脱敏真实数据 + 人工标注 |
| Chroma 只是镜像 | 主检索仍 SQLite 全表向量计算 | 数据量大时性能差 | 主路径接 Qdrant/Milvus/pgvector |

## 16. 自检清单

- 重要结论是否有代码路径：已在各节标注。
- 是否区分已实现和待完善：已区分，prompt injection/审批表/队列等标为不足。
- 是否没有把未来计划写成已完成：已避免，面经抓取、真实投递、生产队列未夸大。
- 是否覆盖 LangGraph、RAG、Guardrail、Trace、Eval、SSE、Checkpoint、Interrupt：已覆盖。
- 是否包含真实 bug 复盘：第 14 节 10 个。
- 是否包含面试问答：本报告每节有 Q&A，另见 `CAREER_AGENT_INTERVIEW_QA.md`。
- 是否包含当前不足：第 15 节。
- 是否能支撑 30 分钟项目深挖：可以按 LangGraph、RAG、Guardrail、Eval 四条主线展开。
- 是否能解释 Codex 参与：建议面试中诚实说 Codex 辅助实现与梳理，但自己理解了 state、service、tests、eval 和验收方式。
- 是否修改功能代码：本次只新增 Markdown 文档。
