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
  "name": "Candidate",
  "email": "candidate@example.com",
  "target_roles": ["Agent 开发实习生"],
  "skills": ["Python", "FastAPI", "RAG", "SQLite"],
  "projects": [
    {
      "name": "CareerAgent",
      "description": "构建求职助手 Agent 工作流。",
      "tech_stack": ["FastAPI", "SQLite"],
      "impact": "完成可运行的端到端求职流程。"
    }
  ]
}
```

### 查询 Profile

```http
GET /profiles
GET /profiles/{profile_id}
```

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
  "sources": ["tencent"],
  "store_results": true
}
```

效果：

- 默认请求腾讯招聘中文岗位源；海外 ATS 类 source 仅作为显式开启的英文辅助源。
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

- 定制简历 Markdown。
- change summary。
- keyword alignment。
- source evidence。
- guardrail verification。
- diff。

下载 Markdown：

```http
GET /resumes/{resume_version_id}/markdown
```

## Agent Runs

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

### 查询 Agent Tool 注册表

```http
GET /agent/tools
```

返回当前 Orchestrator 可调用的工具、输入输出描述、副作用和是否适合后续 MCP 化。

### 查询 Agent Skill 与 SubAgent 注册表

```http
GET /agent/skills
GET /agent/subagents
```

`GET /agent/skills` 返回能力名称、状态、所属 SubAgent、触发条件、使用工具、上下文策略和输出契约。

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
  "source_url": "https://www.nowcoder.com/discuss/example",
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

`status` 支持 `todo`、`practicing`、`ready` 和 `deferred`。`question_id` 必须来自 `GET /interview-prep/{prep_id}/questions`，不存在时直接返回 422，方便开发期通过 trace 排查数据问题。`GET /interview-prep/{prep_id}/markdown` 返回可下载的 Markdown 面试包，包含基本信息、问题来源分布、练习状态、题组、缺口 drill、外部调研清单和证据边界。

返回：

- `summary_json`：岗位、匹配分、fit level、匹配技能、缺口技能和准备重点。
- `question_sets_json`：按同岗位面经、高频技术追问、简历项目技术栈、缺口追问、工程协作和通用问题分组的问题。
- `gap_drills_json`：对缺口技能的诚实披露话术和最小补齐任务。
- `research_checklist_json`：牛客网、OfferShow、小红书和搜索引擎的同岗位面经/业务调研 query。当前只生成调研线索，不声称已经抓取真实帖子。
- `coverage_json`：题目数、必备技能覆盖率、缺口 drill 覆盖率、证据题占比、来源分布、三角度覆盖和是否通过。

## LLM 调用调试

```http
GET /llm/debug/logs?limit=50
```

用于查看：

- 调用名称。
- 模型和 base_url。
- prompt 预览。
- response 预览。
- 调用状态。
- 延迟。
- 错误信息。

接口不会返回 API key。

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
POST /evaluations/real-job-source-smoke?query=Agent%20%E5%BC%80%E5%8F%91%E5%AE%9E%E4%B9%A0%E7%94%9F&limit=8&sources=tencent
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

查询历史评测：

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
