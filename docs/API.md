# API 说明

默认 Base URL：

```text
http://localhost:8000
```

## 健康检查

```http
GET /health
```

返回应用状态、版本和 LLM 是否已配置。

## 自然语言助手

```http
POST /assistant/natural-language
Content-Type: application/json
```

用于用户直接描述需求，例如生成简历档案、按 JD 定制简历、搜索岗位、生成投递包和面试包。该入口本身也是 LangGraph 图：先调用 LLM 解析意图和计划，再执行受控工具链；执行失败会进入一次 plan repair，最终成功、等待确认或失败都会写入 trace 和 `agent_events`。

```json
{
  "instruction": "我想找 Agent 开发实习岗位，请根据下面 JD 帮我生成简历档案，并改简历、生成投递包和面试准备问题。",
  "profile_id": 1,
  "job_id": null,
  "jd_text": "岗位：Agent 开发实习生\n职责：参与 RAG、工具调用、LLM 调试和 FastAPI 后端开发...",
  "query": "Agent 开发实习生",
  "location": "深圳",
  "limit": 8
}
```

返回：

- `run_id`：自然语言需求本身的 Agent Run ID，可继续查询步骤。
- `status`：`completed` 或 `failed`。
- `user_message`：给用户展示的中文结果或失败原因。
- `plan_json`：LLM 解析后的意图、动作和原因。
- `result_json`：生成的 Profile、Job、ResumeVersion、Application、InterviewPrep 或推荐岗位。
- `repair_attempts`：首次执行失败后的自动修复记录。

失败语义：

- 首次执行失败会触发 1 轮 plan repair。
- repair 后仍失败时返回 HTTP 500，但 body 仍保留 `run_id/status/user_message/plan_json/result_json`，方便前端显示失败卡片并跳转流程记录。
- 岗位搜索返回 0 个 matches 会被视为失败，不会伪装成“推荐成功”。

## 岗位发现

岗位发现入口不要求先建立简历。`preference_text` 与 `profile_id` 可以只提供一个，也可以同时提供。

```http
POST /job-discovery/sessions
Content-Type: application/json
```

```json
{
  "preference_text": "想找北京或远程的 Agent 开发实习，偏 RAG 和后端工程",
  "profile_id": 12,
  "location": "北京 / 远程",
  "internship_only": true,
  "limit": 20,
  "source_mode": "hybrid"
}
```

`source_mode`：

- `corpus`：只检索已经同步到系统的岗位库。
- `live`：刷新真实招聘来源，再对本次入库岗位和已有岗位统一检索。
- `hybrid`：面向用户的默认模式，真实来源异常会在 `source_errors_json` 中显式返回。

真实来源搜索先保存原始 JD，再使用确定性结构化解析、技能别名归一化和 Prompt Injection 检测建立可搜索字段，不为每条搜索结果等待 LLM。用户触发岗位匹配、简历评审或定制简历时才进入真实 embedding/reranker 与 LLM 推理链路。

返回内容包括：

- `session.input_mode`：`preference_only`、`profile_only`、`preference_and_profile` 或 `browse`。
- `session.resolved_query`：实际用于岗位 RAG 的查询。
- `results[].retrieval_score`：需求与岗位的检索相关度。
- `results[].match_score`：只有提供简历后才存在的简历匹配分。
- `results[].reason`：相关性原因、语义分、匹配技能和能力缺口。

刷新或跨页恢复：

```http
GET /job-discovery/sessions/{session_id}
```

最近搜索记录：

```http
GET /job-discovery/sessions
```

岗位站内详情页：

```text
/ui/jobs/{job_id}?session_id={session_id}&profile_id={profile_id}
```

详情页可继续调用 `POST /matches`、`POST /profiles/{profile_id}/review` 和 `POST /resumes/tailor`。匹配分析、修改建议和事实检查单独展示，不进入简历正文。

搜索结果持久化在 `job_search_sessions/job_search_results`。历史旧维度向量在检索时会批量迁移并写回 SQLite；这会产生一次性冷启动成本，迁移后的后续请求直接复用已存 embedding。

## 简历档案

### 上传 PDF

```http
POST /profiles/upload
Content-Type: multipart/form-data
```

字段：

- `file`：PDF 简历。

效果：

- 提取 PDF 页级文本。
- 解析结构化 Profile。
- 生成结构化 chunk 和 PDF page chunk。
- 写入 `profiles` 和 `resume_chunks`。

### 问答式创建 Profile

```http
POST /profiles/guided
Content-Type: application/json
```

```json
{
  "name": "李明",
  "email": "liming@example.com",
  "phone": "13800000000",
  "photo_data_url": "data:image/png;base64,...",
  "location": "深圳",
  "availability": "2026 年暑期可实习",
  "headline": "Agent 开发实习生候选人",
  "self_summary": "熟悉 FastAPI、RAG 和 Agent workflow，有真实求职助手项目经验。",
  "enabled_sections": ["intent", "summary", "photo", "education", "projects", "skills"],
  "target_roles": ["Agent 开发实习生"],
  "education": [
    {
      "school": "XX大学",
      "degree": "本科",
      "major": "计算机科学与技术",
      "duration": "2023.09 - 2027.06",
      "details": "GPA 3.7/4.0，核心课程：数据库、机器学习、软件工程。"
    }
  ],
  "skills": ["Python", "FastAPI", "RAG", "SQLite"],
  "projects": [
    {
      "name": "CareerAgent",
      "description": "构建求职助手 Agent 工作流。",
      "tech_stack": ["FastAPI", "SQLite"],
      "impact": "完成可运行的端到端求职流程。"
    }
  ],
  "work_experience": [
    {
      "company": "AI Lab",
      "role": "后端开发实习生",
      "duration": "2025.07 - 2025.10",
      "details": "维护 FastAPI 服务和 SQLite 数据链路。",
      "tech_stack": ["FastAPI", "SQLite"]
    }
  ],
  "campus_experience": [
    {
      "company": "AI 社团",
      "role": "技术组成员",
      "duration": "2024.09 - 2025.01",
      "details": "组织 Agent 技术分享。"
    }
  ],
  "certifications": ["英语六级"],
  "awards": ["校级二等奖学金"],
  "languages": ["中文", "英语 CET-6"],
  "portfolio_links": ["https://github.com/example/CareerAgent"]
}
```

手动建档字段按中文求职简历常见结构组织。除姓名外，其余字段都可以留空；前端会通过 `enabled_sections` 记录用户选择的栏目，并只提交已启用栏目。`education`、`work_experience`、`projects`、`campus_experience` 都是数组，前端支持添加多段条目。已填写的教育、实习/工作、项目、校园实践、证书和奖项会进入结构化 Profile、简历 chunk 与 HTML 预览。`photo_data_url` 只用于 HTML 预览，不会写入 raw resume text 或向量 chunk。

### 查询 Profile

```http
GET /profiles
GET /profiles/{profile_id}
```

### 预览简历档案

```http
GET /profiles/{profile_id}/html
```

返回 `text/html`，用结构化 Profile 渲染一份可预览、可打印、可另存为 PDF 的简历页面。该接口用于“我的简历档案”中的预览按钮。

## 岗位

### 搜索真实岗位

```http
POST /jobs/search
Content-Type: application/json
```

```json
{
  "query": "Agent 开发实习生",
  "location": "上海",
  "internship_only": true,
  "limit": 20,
  "sources": ["tencent", "baidu", "meituan", "bytedance", "alibaba"],
  "store_results": true
}
```

效果：

- 默认并发请求腾讯、百度、美团、字节跳动和阿里巴巴五个中文岗位源；海外 ATS 类 source 仅作为显式开启的英文辅助源。
- 对 source 返回结果执行中文岗位相关性排序，让 Agent/开发/实习信号强的岗位优先于产品、销售或泛 AI 岗位。
- 并发解析多个 JD。
- 顺序写入 SQLite，避免 Session 并发写入风险。
- 对每个岗位生成 `job_chunks`。
- 如果 `VECTOR_BACKEND=hybrid` 且 Chroma 可用，同步写入 Chroma 镜像。

### 手动创建岗位

```http
POST /jobs
Content-Type: application/json
```

```json
{
  "title": "Agent 开发实习生",
  "company": "Example AI",
  "apply_url": "https://example.com/jobs/agent-intern",
  "jd_text": "构建 Agent 工作流，使用 FastAPI、RAG、SQLite、评测和 Guardrails..."
}
```

### 查询岗位与 JD Chunk

```http
GET /jobs
GET /jobs/{job_id}
GET /jobs/{job_id}/chunks
```

`GET /jobs/{job_id}/chunks` 用于检查某个职位 JD 的切分结果。

## 匹配

```http
POST /matches
Content-Type: application/json
```

```json
{
  "profile_id": 1,
  "job_id": 1
}
```

返回：

- 总分。
- required skill coverage。
- semantic similarity。
- evidence relevance。
- internship fit。
- matched/missing skills。
- RAG evidence。

## 简历定制

```http
POST /resumes/tailor
Content-Type: application/json
```

```json
{
  "profile_id": 1,
  "job_id": 1
}
```

返回：

- 定制简历内容。
- change summary。
- keyword alignment。
- source evidence。
- guardrail verification。
- diff。

HTML 预览：

```http
GET /resumes/{resume_version_id}/html
```

返回 `text/html`，将历史 Markdown 内容渲染为可打印、可另存为 PDF 的简历排版页面。事实检查、改动摘要和关键词覆盖情况只在前端定制简历详情中单独展示，不写入可投递简历正文。

下载 Markdown：

```http
GET /resumes/{resume_version_id}/markdown
```

## Agent Runs

`POST /agent/runs` 现在由 LangGraph 主编排执行。兼容类名仍是 `AgentOrchestrator`，但内部实际调用 `LangGraphAgentOrchestrator` 的 `StateGraph`。所有任务都会先执行 `plan_task` 节点，再根据 `task_type` 通过条件边进入对应流程。返回的 `input_json`、`output_json` 和 `execution_plan` 会包含 `orchestration_framework=langgraph`，`execution_plan.graph_thread_id` 记录本次运行的 LangGraph thread 标识。checkpoint 默认持久化到 `data/runtime/langgraph_checkpoints.sqlite`。每个 run 还会写入 `agent_events`，用于 JSON 查询和 SSE 实时进度。

### 一体化求职流程

```http
POST /agent/runs
Content-Type: application/json
```

```json
{
  "task_type": "full_career_flow",
  "profile_id": 1,
  "query": "Agent 开发实习生",
  "location": "深圳",
  "limit": 8
}
```

如果用户已经粘贴 JD 或选择了目标岗位，也可以直接传入 `job_id`，LangGraph 会跳过岗位搜索，直接围绕该岗位执行匹配、定制、投递包和面试包：

```json
{
  "task_type": "full_career_flow",
  "profile_id": 1,
  "job_id": 12
}
```

效果：

- 搜索真实岗位并按匹配分选择最高岗位。
- 生成定制简历。
- 通过 `fit_gate` 后生成投递包。
- 生成面试准备包。
- 在同一个 `agent_run` 下写入 execution plan、selected job、tailored resume、fit gate、application 和 interview prep artifacts。
- 每个业务节点仍写入 `agent_steps`，因此前端和调试 API 可以继续按原方式查看步骤、耗时和错误。
- 如果请求没有显式传入 `"application_confirmed": true`，流程会在投递包生成前返回 `status=waiting_for_confirmation`，`output_json.interrupts` 中包含待确认岗位、简历版本和 fit gate 信息。

### 后台启动 Agent Run

```http
POST /agent/runs/background
Content-Type: application/json
```

请求体与 `POST /agent/runs` 相同。接口立即返回 `status=queued` 的 `AgentRunResponse`，并通过 `RedisTaskRunner` 写入 Redis 队列；独立 worker 使用 `scripts/run_agent_worker.py` 消费执行同一个 LangGraph graph。Redis 未启用或不可用时接口返回 503，不会静默退回进程内后台任务。

### 查询事件列表

```http
GET /agent/runs/{run_id}/events?after_id=0&limit=200
```

返回 `agent_events` 中保存的运行事件，包括：

- `run_created`、`run_started`、`run_resumed`、`run_finished`
- `graph_started`、`graph_node_started`、`graph_node_update`、`graph_node_completed`、`graph_interrupt`、`graph_completed`
- `step_started`、`step_completed`、`step_failed`
- `artifact_created`

### LangGraph SSE 事件流

```http
GET /agent/runs/{run_id}/events/stream
Accept: text/event-stream
```

服务端会持续推送上述事件，并在 run 进入 `completed`、`failed`、`waiting_for_confirmation` 或 `cancelled` 后发送 `run_closed`。前端流程页和首页一键流程都使用该接口展示节点级进度。

### 取消未完成流程

```http
POST /agent/runs/{run_id}/cancel
Content-Type: application/json
```

```json
{
  "reason": "用户决定暂不投递"
}
```

只允许取消 `queued`、`running` 和 `waiting_for_confirmation`。取消会写 `run_cancel_requested`、`run_cancelled`，设置 `output_json.cancelled=true`，取消 pending approval，并写 Redis cancel flag。已完成、失败或已取消的 run 返回 409。

### 查询审批审计

```http
GET /agent/runs/{run_id}/approvals
```

返回投递包等高风险动作的审批记录，包括 `action_type`、`status`、`payload_hash`、`payload_summary_json`、`note` 和 `decided_at`。

### 恢复等待确认的流程

```http
POST /agent/runs/{run_id}/resume
Content-Type: application/json
```

确认继续：

```json
{
  "confirmed": true,
  "note": "用户确认生成投递包",
  "resume_json": {
    "source": "ui"
  }
}
```

拒绝继续：

```json
{
  "confirmed": false,
  "note": "暂不投递"
}
```

确认后 LangGraph 会从 SQLite checkpoint 中按 `graph_thread_id` 恢复，继续执行 `create_application_packet` 节点；拒绝时 run 会变为 `failed`，且不会创建投递包。

### 查询 Graph State

```http
GET /agent/runs/{run_id}/graph-state
```

返回当前 checkpoint 的 `next` 节点、`interrupts`、`checkpoint_id` 和已保存的 state 摘要，用于排查等待确认和跨请求恢复。

### 搜索并排序岗位

```http
POST /agent/runs
Content-Type: application/json
```

```json
{
  "task_type": "find_jobs_for_profile",
  "profile_id": 1,
  "query": "Agent 开发实习生",
  "limit": 12
}
```

### 为岗位定制简历

```json
{
  "task_type": "tailor_resume_for_job",
  "profile_id": 1,
  "job_id": 1
}
```

### 生成投递包

```json
{
  "task_type": "quick_apply",
  "profile_id": 1,
  "job_id": 1,
  "resume_version_id": 1
}
```

### 生成面试准备包

```json
{
  "task_type": "prepare_interview_for_job",
  "profile_id": 1,
  "job_id": 1
}
```

返回的 `output_json` 包含 `interview_prep_id`、`coverage`、题组数量、缺口 drill 数量和调研项数量。该任务会写入 `interview_prep` artifact。

### 查询 Trace

```http
GET /agent/runs
GET /agent/runs/{run_id}
GET /agent/runs/{run_id}/steps
```

### 查询业务运行摘要

```http
GET /agent/runs/{run_id}/summary
```

返回面向用户的四层摘要：

- `routing_layer`：本次选择的 Skill、SubAgent、Tool 和权限校验。
- `process_layer`：工具调用、成功率、repair、幂等复用和耗时。
- `result_layer`：目标岗位、匹配证据、Guardrail、简历/投递/面试包产物 ID。
- `side_effect_layer`：高风险 Tool、审批状态、真实外发结果和审批绕过检测。

该接口会从当前 run、step、artifact、approval 和产物表实时重建摘要。run 完成时同一结构也会写入 `output_json.business_summary` 和 `business_summary` artifact。

### 查询 Agent Tool 注册表

```http
GET /agent/tools
```

返回当前 LangGraph Orchestrator 可调用的工具，以及输入输出、副作用、风险等级、审批要求、幂等策略、超时、重试、审计事件、允许该工具的 Skill 和是否适合后续 MCP 化。

### 查询 Agent Skill 与 SubAgent 注册表

```http
GET /agent/skills
GET /agent/skills/{skill_name}
GET /agent/subagents
```

`GET /agent/skills` 返回能力目录 metadata，包括名称、版本、状态、owner、触发条件、允许工具、上下文、输出契约、禁止行为、成功标准和失败策略，但不返回 `SKILL.md` 正文指令。

`GET /agent/skills/{skill_name}` 按需返回指定 Skill 的完整契约和正文指令；未知名称返回 404。这是 Skill 渐进式披露边界，避免把所有能力指令塞进每次 LLM 上下文。

`GET /agent/subagents` 返回 SubAgent 职责、拥有的 skill、读取/写入边界和上下文策略。

上下文治理不作为独立 skill/subagent 暴露，而是在 Agent 执行计划的 `context_policy`、简历版本的 `context_compression` 和 LLM workflow 的逐 case trace 中查看。

## 面试准备包

生成：

```http
POST /interview-prep
Content-Type: application/json
```

```json
{
  "profile_id": 1,
  "job_id": 1,
  "experience_ids": [1, 2]
}
```

`experience_ids` 可选；不传时系统会自动检索相关面经，传空数组时不会引用任何导入面经，适合评测隔离。

导入同岗面经材料：

```http
POST /interview-prep/experiences
Content-Type: application/json
```

```json
{
  "job_id": 1,
  "source_site": "牛客网",
  "source_url": "https://www.nowcoder.com/discuss/123456789",
  "title": "腾讯 Agent 开发实习一面",
  "company": "腾讯",
  "role_keyword": "Agent 开发实习生",
  "raw_text": "一面：面试官问 RAG 的 chunk 切分策略怎么选？追问：FastAPI 并发接口如何记录 trace？"
}
```

查询：

```http
GET /interview-prep
GET /interview-prep/{prep_id}
GET /interview-prep/{prep_id}/questions
GET /interview-prep/{prep_id}/markdown
GET /interview-prep/{prep_id}/practice
GET /interview-prep/experiences
GET /interview-prep/experiences?job_id=1
GET /interview-prep/experiences/{experience_id}
```

导入同岗面经材料使用 `POST /interview-prep/experiences`。请求体包含 `source_site`、`source_url`、`title`、`company`、`role_keyword` 和 `raw_text`；返回的 `extracted_questions_json`、`topics_json`、`rounds_json` 和 `credibility_json` 都来自导入文本本身，不会在文本缺失时编造具体面经题。生成面试准备包时可以传入 `experience_ids`，系统会把这些来源作为 `source_backed_interview_experience` 证据引用。

按题练习状态：

```http
PUT /interview-prep/{prep_id}/practice
Content-Type: application/json
```

```json
{
  "question_id": "q01_01",
  "status": "ready",
  "confidence_score": 4,
  "notes": "已按项目背景、技术取舍和指标准备 90 秒回答。"
}
```

`status` 支持 `todo`、`practicing`、`ready` 和 `deferred`。`question_id` 必须来自 `GET /interview-prep/{prep_id}/questions`，不存在时直接返回 422，方便开发期通过 trace 排查数据问题。每道题分别返回 `question_generation_source(_label)` 与 `answer_framework_source(_label)`：前者说明题目来自 LLM、结构化规则还是已导入面经，后者说明回答思路由谁生成，不能用面试包级 `generation_mode=llm_augmented` 推断每道题都由 LLM 生成。

每道题还返回 `reference_answer`、`reference_answer_source(_label)`、`reference_answer_version` 和 `reference_answer_basis`。`reference_answer` 是结合当前题目、JD、简历项目与能力缺口生成的第一人称完整参考回答；前端把它作为主内容展示，`answer_framework` 与 `evidence_refs.preview` 折叠为辅助的“回答思路与证据”。答案指纹包含题型、题目、项目证据和岗位信息，分类规则或上下文变化后会重新生成，避免历史答案错误复用。参考答案只允许引用当前简历和项目记录，不会把待学习技能写成已交付经历。`GET /interview-prep/{prep_id}/markdown` 返回可下载的 Markdown 面试包，包含基本信息、问题来源分布、练习状态、完整参考答案、回答思路、缺口 drill、外部调研清单和证据边界。

返回：

- `summary_json`：岗位、匹配分、fit level、匹配技能、缺口技能和准备重点。
- `question_sets_json`：按同岗位面经、高频技术追问、简历项目技术栈、缺口追问、工程协作和通用问题分组的问题。
- `gap_drills_json`：对缺口技能的诚实披露话术和最小补齐任务。
- `research_checklist_json`：牛客网、OfferShow、小红书和搜索引擎的同岗位面经/业务调研 query。当前只生成调研线索，不声称已经抓取真实帖子。
- `coverage_json`：题目数、必备技能覆盖率、缺口 drill 覆盖率、证据题占比、来源分布、三角度覆盖和是否通过。

## LLM 调用调试

```http
GET /llm/debug/logs?limit=50
GET /llm/debug/logs?limit=200&evaluation_run_id=12
GET /llm/debug/logs?evaluation_run_id=12&case_name=agent_candidate_strong_agent_role&stage=jd_parse
```

用于查看：

- 调用名称。
- 模型和 base_url。
- prompt 预览。
- response 预览。
- 调用状态。
- 延迟。
- 错误信息。
- `context_json`：可包含 `evaluation_run_id`、`case_name` 和 `stage` 等调用上下文。

支持按 `evaluation_run_id`、`case_name` 和 `stage` 过滤。过滤是在最近日志窗口内完成，适合开发期从评测 run 快速定位对应 LLM 调用；接口仍不会返回 API key。

Resume parser 和 JD parser 的真实 LLM 链路会显式记录 `resume_parser.parse_structured_resume.retry_1`、`jd_parser.parse_jd.retry_1`、`jd_parser.parse_jd.retry_2` 和 `jd_parser.parse_jd.repair_json` 等 trace 名称。空返回/超时/服务端断连只做有限业务层重试；JD 截断或非法 JSON 会触发一次 repair/reparse，仍失败时直接向上报错，不静默兜底。
底层 LLM HTTP 客户端对网络断连、429 和 5xx 会做有限短重试；中间失败会以 `retryable_failed` 写入 `llm_call_logs.status`，最终失败仍会暴露到调用方。

## 量化评测

运行基础匹配样例集：

```http
POST /evaluations/run
```

运行 PDF Chunk 策略评测：

```http
POST /evaluations/pdf-chunk-strategies
```

运行 RAG 策略评测：

```http
POST /evaluations/rag-strategies
```

运行 Agent 全流程评测：

```http
POST /evaluations/agent-full-flow
```

该评测覆盖 `find_jobs_for_profile`、`tailor_resume_for_job`、`quick_apply`、`fit_gate`、Trace 和 Artifact。弱匹配 case 期望 `quick_apply` 失败并在 step trace 中记录阻断原因。

运行 JD Parser 标注集评测：

```http
POST /evaluations/jd-parser
```

该评测使用 `evals/jd_parser_cases.json`，单独衡量 JD parser 的结构化质量。返回的 `summary_json` 包含 `completed_rate`、`pass_rate`、`avg_required_skill_recall`、`avg_keyword_hit_rate`、`job_type_accuracy`、`responsibility_min_pass_rate`、`qualification_min_pass_rate`、`absent_required_skill_violation_count`、`parser_mode_counts`、`failure_breakdown`、`difficulty_breakdown` 和 `noise_breakdown`。`case_results_json` 会保留每个 JD 的 parsed required/preferred skills、missing required skills、负向技能误抽取和失败检查项。

运行中文岗位排序标注集评测：

```http
POST /evaluations/job-relevance
```

该评测使用 `evals/job_relevance_cases.json`，不访问外部招聘站，也不调用 LLM，只衡量 source 层中文岗位相关性排序。数据集包含 13 个中文为主 query、130 个候选岗位，每个候选使用 0-4 级相关性标注。返回的 `summary_json` 包含 `pass_rate`、`top1_accuracy`、`avg_top3_recall`、`avg_top5_recall`、`avg_mrr`、`avg_ndcg_at_5`、`low_grade_above_strong_count`、`intent_breakdown`、`difficulty_breakdown` 和 `noise_breakdown`。`case_results_json` 会保留每个候选岗位的排序名次、人工 grade、排序分和 `relevance_reasons`。

运行投递包 Guardrail 评测：

```http
POST /evaluations/application-packet
```

该评测使用 `evals/application_packet_cases.json`，不访问外部招聘站，也不调用 LLM，只验证投递包质量校验。返回的 `summary_json` 包含 `high_risk_recall`、`false_block_count`、`missed_high_risk_count`、`issue_code_hit_rate`、`risk_level_counts`、`difficulty_breakdown` 和 `noise_breakdown`。`case_results_json` 会保留每个 case 的 `actual_issue_codes`、`warning_codes` 和完整 validation 结果。

运行 Prompt Injection Guard 对抗评测：

```http
POST /evaluations/prompt-injection
```

该评测使用 `evals/prompt_injection_cases.json`，覆盖 JD、PDF 简历、RAG chunk 和导入面经四类不可信来源，包含中英文指令覆盖、工具越权、数据外泄、RAG 污染和良性安全工程描述。返回的 `summary_json` 包含 `detection_recall`、`false_positive_rate`、`true_negative_rate`、`category_recall`、`severity_accuracy`、`source_breakdown` 和 `category_breakdown`。

运行面试准备包评测：

```http
POST /evaluations/interview-prep
```

该评测使用 `evals/interview_prep_cases.json`，不访问牛客网、OfferShow、小红书等外部平台，只验证面试包是否覆盖同岗位面经调研线索、已导入面经证据、简历项目技术栈追问、JD 缺口 drill 和通用面试问题。返回的 `summary_json` 包含 `pass_rate`、`category_pass_rate`、`research_source_pass_rate`、`source_backed_pass_rate`、`experience_site_pass_rate`、`gap_drill_pass_rate`、`question_id_pass_rate`、`source_perspective_pass_rate`、`preparation_angle_pass_rate`、`llm_question_generation_pass_rate`、`question_quality_pass_rate`、`avg_question_quality_score`、`markdown_export_pass_rate`、`avg_question_count`、`avg_source_backed_question_count`、`avg_required_skill_coverage_rate`、`difficulty_breakdown` 和 `role_type_breakdown`。

运行面经来源 smoke：

```http
POST /evaluations/interview-source-smoke
POST /evaluations/interview-source-smoke?query=Agent%20%E5%BC%80%E5%8F%91%E5%AE%9E%E4%B9%A0%E7%94%9F%20%E9%9D%A2%E7%BB%8F&limit=5&sources=nowcoder&sources=offershow
```

该评测默认探测牛客网、OfferShow 和小红书公开搜索页，只记录 source 层健康度，不绕过登录、不处理反爬、不把结果写入 `interview_experiences`，也不影响 `interview-prep` 核心回归。返回的 `summary_json` 包含 `reachable_source_rate`、`result_source_rate`、`total_result_count`、`url_rate`、`interview_signal_rate`、`query_relevance_rate`、`content_extractable_rate`、`source_errors`、`source_empty` 和 `core_regression_independent`。`case_results_json` 会按 source 保存 `latency_ms`、错误、结果数量和 `sample_experiences`。

运行真实岗位源 smoke：

```http
POST /evaluations/real-job-source-smoke
POST /evaluations/real-job-source-smoke?query=Agent%20%E5%BC%80%E5%8F%91%E5%AE%9E%E4%B9%A0%E7%94%9F&limit=8&sources=tencent&sources=baidu&sources=meituan&sources=bytedance&sources=alibaba
```

该评测只检查招聘源可达性、返回数量和岗位质量，不调用 LLM 解析 JD，不写入主岗位库，也不影响核心 `agent-full-flow` 回归。返回的 `summary_json` 包含 `reachable_source_rate`、`result_source_rate`、`total_result_count`、`non_empty_jd_rate`、`apply_url_rate`、`internship_like_rate`、`query_relevance_rate`、`agent_related_rate` 和 `source_errors`；`case_results_json` 会按 source 保存错误、耗时和样例岗位。所有 source 可达但部分 source 没有结果时，状态为 `completed_with_empty_sources`。

运行真实 JD 解析与入库 smoke：

```http
POST /evaluations/real-job-ingest-smoke
POST /evaluations/real-job-ingest-smoke?query=Agent%20%E5%BC%80%E5%8F%91%E5%AE%9E%E4%B9%A0%E7%94%9F&limit=1&sources=tencent
```

该评测从真实岗位源获取 posting 后，继续验证 JD 解析、SQLite upsert、JD chunk、embedding/reranker provider、检索 probe 和 parser quality probe。它会写入 `jobs` 与 `job_chunks`，但仍独立于核心 `agent-full-flow` 回归。返回的 `summary_json` 包含 `parse_success_rate`、`ingest_success_rate`、`chunk_index_success_rate`、`retrieval_probe_success_rate`、`parser_quality_pass_rate`、`avg_parser_quality_required_recall`、`avg_parser_quality_structured_recall`、`avg_parser_quality_query_coverage`、`embedding_provider_counts`、`retrieval_query_embedding_provider_counts`、`reranker_provider_counts`、`embedding_fallback_job_count` 和 `retrieval_fallback_job_count`。如果入库成功但 parser quality probe 失败，状态会变成 `completed_with_parser_quality_failures`，不会把核心技能漏抽当作完全成功。

运行真实 LLM 工作流评测：

```http
POST /evaluations/llm-workflow
POST /evaluations/llm-workflow?case_limit=3
POST /evaluations/llm-workflow?case_limit=3&resume_from_last_completed=true
```

`case_limit` 用于真实 LLM smoke 评测。`resume_from_last_completed=true` 时，服务默认读取 `data/runtime/llm_workflow_trace_latest.jsonl`，跳过 trace 中连续完成的 case，从第一个缺失 case 继续运行。返回的 `case_results_json` 中，每个 case 都包含 `stage_trace`，用于检查简历解析、JD 解析、RAG 证据、fit judge、tailor 和 Guardrail 的中间结果。新 trace 事件会写入完整 `case_result`，便于长跑中断后继续。

`/ui/quality` 提供该接口的轻量运行入口，会展示最新 LLM workflow 的 summary、逐 case stage trace、失败阶段、fit label/score 和当前 `evaluation_run_id` 关联的 LLM 调用日志、retry/repair 计数。旧路径 `/ui/evaluations` 仍兼容。18-case 长跑也可以使用 `scripts/run_llm_workflow_eval.py` 写入 JSONL checkpoint。

后台运行真实 LLM workflow：

```http
POST /tasks/llm-workflow?case_limit=18&trace_path=data/runtime/llm_workflow_trace_latest.jsonl
GET /tasks
GET /tasks/{task_id}
```

该接口使用 RedisTaskRunner 入队，`scripts/run_agent_worker.py` 独立消费执行，`task_runs` 表记录 queued/running/completed/failed、进度、错误和最终 `evaluation_run_id`。Redis 未启用或不可用时入队直接失败，不会退回进程内后台任务。

## 权限与运维接口

```http
GET /ops/readiness
GET /ops/metrics
GET /ops/config
GET /ops/audit-events
POST /ops/queue/dead-letter/{dlq_index}/replay
POST /ops/queue/dead-letter/{dlq_index}/discard
POST /ops/high-risk-actions/request
POST /ops/high-risk-actions/{approval_id}/execute
```

- `ADMIN_API_KEY` 配置后，管理接口需要 `X-Admin-Token`。
- `REQUIRE_ADMIN_FOR_MUTATIONS=true` 时，所有写操作都需要 `X-Admin-Token`。
- `RBAC_ENABLED=true` 时，管理接口也接受可信 header：`X-Tenant-Id`、`X-User-Id`、`X-User-Roles`。带 `owner/admin/ops` 角色的用户可访问运维接口，审计 actor 使用 `X-User-Id`。
- `/ops/readiness` 返回数据库、LLM、embedding 和 reranker 的健康状态。
- `/ops/metrics` 返回请求计数、平均延迟、状态码分布、Agent run/task/LLM call 状态分布和最近评测摘要。
- `/ops/config` 只返回脱敏配置摘要，不返回 API key。
- `/ops/queue/status` 返回 Redis queue、dead-letter queue、带 `dlq_index` 的 DLQ 预览、最大重试次数和 queued recovery 配置。
- `/ops/queue/recover-queued` 扫描 SQLite 中长时间 `queued` 的 Agent run，并重新写入 Redis 队列。
- `/ops/queue/dead-letter/{dlq_index}/replay` 从 DLQ 中移除指定 payload，重置 attempts 后重新入主队列，并写 `ops_audit_events` 与 run trace。
- `/ops/queue/dead-letter/{dlq_index}/discard` 从 DLQ 中移除指定 payload，不再重放，并写 `ops_audit_events` 与 run trace。
- `/ops/approvals` 查询投递包、浏览器投递、邮件草稿和邮件发送等高风险动作审批记录。
- `/ops/approvals/{approval_id}/decision` 用于控制台审批通过或拒绝 pending approval。
- `/ops/high-risk-actions/request` 为 `browser_apply`、`email_draft`、`email_send` 创建或复用 pending approval。
- `/ops/high-risk-actions/{approval_id}/execute` 是高风险工具执行网关：只有对应 approval 为 `approved` 才执行真实工具，否则返回 409。请求体可传 `tool_payload_json` 覆盖或补充审批摘要；`email_draft` 生成 `.eml` artifact，`email_send` 使用 SMTP，`browser_apply` 使用 Playwright selector 填表。
- `/ops/audit-events` 查询 DLQ 处置、高风险工具放行等运维审计事件。
- `/ops/agent-runs/stale` 返回长时间无事件进展的 running run。
- `/ops/agent-runs/mark-stale` 将 stale running run 标记为 failed，并写 `run_marked_stale` 事件。
- `/ui/ops` 是对应的前端运维面板，会展示 readiness、metrics、脱敏配置、后台任务和最近 LLM 调用日志，并支持在本机浏览器保存 `X-Admin-Token`。

查询历史评测：

### 登录与会话

```http
POST /auth/login
POST /auth/logout
GET /auth/me
```

`POST /auth/login` 使用 `tenant_id/email/password` 登录，成功后写入 HttpOnly session cookie。配置 `SESSION_BOOTSTRAP_ADMIN_EMAIL` 和 `SESSION_BOOTSTRAP_ADMIN_PASSWORD` 后，应用启动会自动创建默认租户管理员。`RBAC_ENABLED=true` 时，session 用户的 `owner/admin/ops` 角色可访问运维接口。

### 外发 Smoke 页面

- `/ui/outbound-smoke`：展示 `browser_apply`、`email_draft`、`email_send` 的本地 smoke payload。
- `/ui/outbound-smoke/target`：本地浏览器填写目标页，适合 Playwright smoke，不依赖外部招聘站。

本地 SMTP 可用 `docker-compose.smtp.yml` 启动 Mailpit：SMTP `127.0.0.1:1025`，Web UI `127.0.0.1:8025`。

```http
GET /evaluations/results
```

评测指标包括：

- `pass_rate`
- `avg_overall_score`
- `avg_required_skill_precision`
- `avg_required_skill_recall`
- `avg_missing_skill_precision`
- `avg_evidence_hit_rate`
- `top3_context_hit_rate`
- `avg_top3_recall`
- `top_job_accuracy`
- `score_gate_accuracy`
- `quick_apply_pass_rate`
- `application_packet_pass_rate`
- `fit_gate_block_count`
- `trace_pass_rate`
- `artifact_pass_rate`
- `reachable_source_rate`
- `result_source_rate`
- `non_empty_jd_rate`
- `apply_url_rate`
- `internship_like_rate`
- `query_relevance_rate`
- `agent_related_rate`
- `job_type_accuracy`
- `responsibility_min_pass_rate`
- `qualification_min_pass_rate`
- `absent_required_skill_violation_count`
- `parser_mode_counts`
- `parse_success_rate`
- `ingest_success_rate`
- `chunk_index_success_rate`
- `retrieval_probe_success_rate`
- `parser_quality_evaluable_count`
- `parser_quality_pass_rate`
- `avg_parser_quality_required_recall`
- `avg_parser_quality_structured_recall`
- `avg_parser_quality_query_coverage`
- `parser_quality_failure_count`
- `completed_rate`
- `end_to_end_pass_rate`
- `resume_parse_success_rate`
- `jd_parse_success_rate`
- `fit_label_accuracy`
- `fit_score_in_range_rate`
- `tailor_pass_rate`
- `guardrail_pass_rate`
- `forbidden_claim_free_rate`
- `context_compression`
- `difficulty_breakdown`

## 投递包

```http
POST /applications/quick-apply
Content-Type: application/json
```

```json
{
  "profile_id": 1,
  "job_id": 1,
  "resume_version_id": 1,
  "browser_assist": false
}
```

查询：

```http
GET /applications
```

`automation_result_json` 会包含：

- `mode=manual_confirm_required`
- `final_submission=user_confirmed_only`
- `packet_validation`：投递包 Guardrail 结果，包括 `passed`、`risk_level`、`issues`、`warnings` 和支持证据中的技能词。

`/ui/applications` 会展示这些 Guardrail 结果：`issues` 是阻断问题，`warnings` 是需要用户人工补充或确认的问题。
