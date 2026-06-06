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

其中 `progressive_disclosure` 是当前 LLM 链路的上下文治理 skill，负责把 Profile、JD 和 RAG evidence 分层压缩后再交给适配判断和简历定制。

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

运行真实 LLM 工作流评测：

```http
POST /evaluations/llm-workflow
```

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
