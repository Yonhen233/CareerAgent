# API Guide

Base URL: `http://localhost:8000`

## Health

```http
GET /health
```

## Profiles

Upload PDF:

```http
POST /profiles/upload
Content-Type: multipart/form-data
```

Create from guided answers:

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
      "description": "Built a job-search Agent workflow.",
      "tech_stack": ["FastAPI", "SQLite"],
      "impact": "End-to-end usable workflow"
    }
  ]
}
```

List profiles:

```http
GET /profiles
```

## Jobs

Search real career sources:

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

Create a manual JD:

```http
POST /jobs
Content-Type: application/json
```

```json
{
  "title": "Agent 开发实习生",
  "company": "Example AI",
  "apply_url": "https://example.com/jobs/agent-intern",
  "jd_text": "Build Agent workflows with FastAPI, RAG, SQLite, evaluation and guardrails..."
}
```

## Matches

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

## Resume Tailoring

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

Download markdown:

```http
GET /resumes/{resume_version_id}/markdown
```

## Agent Runs

Find and rank jobs:

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

Tailor resume:

```json
{
  "task_type": "tailor_resume_for_job",
  "profile_id": 1,
  "job_id": 1
}
```

Quick apply:

```json
{
  "task_type": "quick_apply",
  "profile_id": 1,
  "job_id": 1,
  "resume_version_id": 1
}
```

Inspect trace:

```http
GET /agent/runs/{run_id}/steps
```

## Applications

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

List packets:

```http
GET /applications
```
