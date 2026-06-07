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
  "query": "Agent Development Intern",
  "location": "Shanghai",
  "internship_only": true,
  "limit": 20,
  "sources": ["tencent", "lever"],
  "store_results": true
}
```

效果：

- 并发请求多个岗位源。
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
  "query": "Agent Development Intern",
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

运行真实岗位源 smoke：

```http
POST /evaluations/real-job-source-smoke
POST /evaluations/real-job-source-smoke?query=Agent%20Development%20Intern&limit=8&sources=tencent&sources=lever
```

该评测只检查招聘源可达性、返回数量和岗位质量，不调用 LLM 解析 JD，不写入主岗位库，也不影响核心 `agent-full-flow` 回归。返回的 `summary_json` 包含 `reachable_source_rate`、`result_source_rate`、`total_result_count`、`non_empty_jd_rate`、`apply_url_rate`、`internship_like_rate`、`query_relevance_rate`、`agent_related_rate` 和 `source_errors`；`case_results_json` 会按 source 保存错误、耗时和样例岗位。所有 source 可达但部分 source 没有结果时，状态为 `completed_with_empty_sources`。

运行真实 JD 解析与入库 smoke：

```http
POST /evaluations/real-job-ingest-smoke
POST /evaluations/real-job-ingest-smoke?query=Agent%20Development%20Intern&limit=1&sources=tencent
```

该评测从真实岗位源获取 posting 后，继续验证 JD 解析、SQLite upsert、JD chunk、embedding/reranker provider 和检索 probe。它会写入 `jobs` 与 `job_chunks`，但仍独立于核心 `agent-full-flow` 回归。返回的 `summary_json` 包含 `parse_success_rate`、`ingest_success_rate`、`chunk_index_success_rate`、`retrieval_probe_success_rate`、`embedding_provider_counts`、`retrieval_query_embedding_provider_counts`、`reranker_provider_counts`、`embedding_fallback_job_count` 和 `retrieval_fallback_job_count`。

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
