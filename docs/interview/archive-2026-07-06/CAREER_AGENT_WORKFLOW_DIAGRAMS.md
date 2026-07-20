# CareerAgent Workflow Diagrams

> 图示基于真实代码：`app/agents/langgraph_orchestrator.py`、`app/agents/natural_language.py`、`app/api/agent_runs.py`、`app/services/*`。

## 1. LangGraph 主工作流

代码位置：`app/agents/langgraph_orchestrator.py::_build_graph`

```mermaid
flowchart TD
    START([START]) --> PLAN[plan_task]

    PLAN --> ROUTE_TASK{task_type}

    ROUTE_TASK -->|find_jobs_for_profile| LOAD_PROFILE[load_profile]
    ROUTE_TASK -->|tailor_resume_for_job| LOAD_PROFILE
    ROUTE_TASK -->|quick_apply| LOAD_PROFILE
    ROUTE_TASK -->|prepare_interview_for_job| LOAD_PROFILE
    ROUTE_TASK -->|full_career_flow| LOAD_PROFILE

    LOAD_PROFILE --> PROFILE_ROUTE{need search?}
    PROFILE_ROUTE -->|find_jobs_for_profile| SEARCH[search_jobs]
    PROFILE_ROUTE -->|full_career_flow without job_id| SEARCH
    PROFILE_ROUTE -->|has target job| LOAD_JOB[load_job]

    SEARCH --> MATCH_JOBS[match_jobs]
    MATCH_JOBS --> MATCH_JOBS_ROUTE{task}
    MATCH_JOBS_ROUTE -->|find_jobs_for_profile| FIN_FIND[finalize_find_jobs]
    MATCH_JOBS_ROUTE -->|full_career_flow| SELECT_JOB[select_job]

    SELECT_JOB --> LOAD_JOB
    LOAD_JOB --> MATCH_JOB[match_job]

    MATCH_JOB --> MATCH_ROUTE{task}
    MATCH_ROUTE -->|tailor_resume_for_job| TAILOR[tailor_resume]
    MATCH_ROUTE -->|full_career_flow| TAILOR
    MATCH_ROUTE -->|quick_apply| FIT[fit_gate]
    MATCH_ROUTE -->|prepare_interview_for_job| INTERVIEW[generate_interview_prep]

    TAILOR --> TAILOR_ROUTE{task}
    TAILOR_ROUTE -->|tailor_resume_for_job| FIN_TAILOR[finalize_tailor]
    TAILOR_ROUTE -->|full_career_flow| FIT

    FIT --> ENSURE_RESUME[ensure_resume_version]
    ENSURE_RESUME --> APPLICATION[create_application_packet]
    APPLICATION --> APP_ROUTE{task}
    APP_ROUTE -->|quick_apply| FIN_QUICK[finalize_quick_apply]
    APP_ROUTE -->|full_career_flow| INTERVIEW

    INTERVIEW --> INTERVIEW_ROUTE{task}
    INTERVIEW_ROUTE -->|prepare_interview_for_job| FIN_INTERVIEW[finalize_interview]
    INTERVIEW_ROUTE -->|full_career_flow| FIN_FULL[finalize_full_flow]

    FIN_FIND --> END([END])
    FIN_TAILOR --> END
    FIN_QUICK --> END
    FIN_INTERVIEW --> END
    FIN_FULL --> END
```

## 2. Five Task Paths

```mermaid
flowchart LR
    A[find_jobs_for_profile] --> A1[load_profile] --> A2[search_jobs] --> A3[match_jobs] --> A4[finalize_find_jobs]

    B[tailor_resume_for_job] --> B1[load_profile] --> B2[load_job] --> B3[match_job] --> B4[tailor_resume] --> B5[finalize_tailor]

    C[quick_apply] --> C1[load_profile] --> C2[load_job] --> C3[match_job] --> C4[fit_gate] --> C5[ensure_resume_version] --> C6[interrupt before application] --> C7[create_application_packet] --> C8[finalize_quick_apply]

    D[prepare_interview_for_job] --> D1[load_profile] --> D2[load_job] --> D3[match_job] --> D4[generate_interview_prep] --> D5[finalize_interview]

    E[full_career_flow] --> E1[load_profile] --> E2{job_id?}
    E2 -->|no| E3[search_jobs] --> E4[match_jobs] --> E5[select_job]
    E2 -->|yes| E6[load_job]
    E5 --> E6
    E6 --> E7[match_job] --> E8[tailor_resume] --> E9[fit_gate] --> E10[ensure_resume_version] --> E11[interrupt before application] --> E12[create_application_packet] --> E13[generate_interview_prep] --> E14[finalize_full_flow]
```

## 3. Checkpoint / Interrupt / Resume

代码位置：

- `app/agents/langgraph_orchestrator.py::_application_confirmation`
- `app/agents/langgraph_orchestrator.py::resume`
- `app/api/agent_runs.py::resume_agent_run`
- `app/core/config.py::Settings.langgraph_checkpoint_file`

```mermaid
sequenceDiagram
    participant User
    participant API as FastAPI /agent/runs
    participant Graph as LangGraphAgentOrchestrator
    participant CP as AsyncSqliteSaver
    participant DB as SQLite DB

    User->>API: POST /agent/runs task=quick_apply
    API->>Graph: run(db, AgentRunRequest)
    Graph->>DB: create agent_run status=running
    Graph->>CP: setup checkpoint thread_id=graph_thread_id
    Graph->>Graph: load_profile -> load_job -> match_job -> fit_gate
    Graph->>Graph: ensure_resume_version
    Graph->>CP: save state before interrupt
    Graph-->>API: interrupt application_packet_confirmation
    Graph->>DB: finish run status=waiting_for_confirmation
    API-->>User: requires_confirmation=true, resume_api

    User->>API: POST /agent/runs/{run_id}/resume confirmed=true
    API->>Graph: resume(db, run_id, payload)
    Graph->>CP: load checkpoint by graph_thread_id
    Graph->>Graph: Command(resume=payload)
    Graph->>DB: create application packet
    Graph->>DB: finish run status=completed
    API-->>User: application_id / output_json
```

关键边界：

- interrupt 前不写 `applications`。
- 拒绝确认会让 run failed。
- 非 `waiting_for_confirmation` 状态调用 resume 返回 409。

## 4. RAG / Evidence Flow

代码位置：

- `app/services/resume_parser.py::ResumeParserService`
- `app/services/text_splitter.py::ResumeTextSplitter`
- `app/services/vector_index.py::SQLiteVectorIndex`
- `app/services/matcher.py::MatcherService`
- `app/services/evidence_classifier.py::EvidenceClassifier`
- `app/services/resume_tailor.py::ResumeTailorService`

```mermaid
flowchart TD
    PDF[PDF resume / guided profile] --> PARSE[parse structured profile]
    PARSE --> PROFILE[(profiles)]
    PARSE --> SPLIT_PROFILE[split structured fields and PDF/raw pages]
    SPLIT_PROFILE --> EMBED_PROFILE[embed chunks]
    EMBED_PROFILE --> RESUME_CHUNKS[(resume_chunks)]
    EMBED_PROFILE --> CHROMA_PROFILE[(optional Chroma mirror)]

    JD[Job JD / real job source] --> JD_PARSE[parse structured JD]
    JD_PARSE --> JOB[(jobs)]
    JD_PARSE --> SPLIT_JOB[split JD structured fields and raw text]
    SPLIT_JOB --> EMBED_JOB[embed chunks]
    EMBED_JOB --> JOB_CHUNKS[(job_chunks)]
    EMBED_JOB --> CHROMA_JOB[(optional Chroma mirror)]

    JOB --> QUERY[build retrieval query from title, required skills, responsibilities, raw JD]
    QUERY --> RETRIEVE[SQLiteVectorIndex.query_profile_chunks]
    RESUME_CHUNKS --> RETRIEVE
    RETRIEVE --> SCORE[first stage: vector + lexical + type boost]
    SCORE --> RERANK{reranker enabled?}
    RERANK -->|yes| CROSS[CrossEncoder / heuristic rerank]
    RERANK -->|no| TOPK[top_k evidence]
    CROSS --> TOPK
    TOPK --> CLASSIFY[EvidenceClassifier]
    CLASSIFY --> MATCH[MatcherService score and missing skills]
    CLASSIFY --> TAILOR[ResumeTailorService context]
    CLASSIFY --> INTERVIEW[InterviewPrepService questions]
```

## 5. Resume Tailor + Guardrail Repair

代码位置：`app/services/resume_tailor.py::ResumeTailorService.tailor_resume`

```mermaid
flowchart TD
    A[Profile + Job] --> B[MatcherService.retrieve_evidence]
    B --> C[ContextCompressor.compress_tailor_context]
    C --> D{LLM available?}
    D -->|yes| E[LLM tailor JSON]
    D -->|no and fallback enabled| F[heuristic tailor]
    D -->|no and fallback disabled| X[raise LLMConfigurationError]

    E --> G[tailored_resume_markdown]
    F --> G
    G --> H[ResumeGuardrailService.verify]
    H --> I{high risk or not passed?}
    I -->|no| SAVE[save ResumeVersion]
    I -->|yes| R[repair once with LLM or heuristic]
    R --> H2[verify repaired markdown]
    H2 --> SAVE
    SAVE --> OUT[resume_version_id + verification + diff]
```

Guardrail 会检查：

- unsupported metrics
- possible new claims
- unsupported required skill claims
- missing skill in resume body

## 6. Matcher / Negative Evidence

代码位置：`app/services/matcher.py::MatcherService.build_match_payload`

```mermaid
flowchart TD
    A[Profile support text] --> CLEAN[filter target intent and metadata]
    B[Job required/preferred skills] --> ALIAS[skill aliases and fuzzy contains]
    CLEAN --> ALIAS
    ALIAS --> SENTENCE[find sentences with skill]
    SENTENCE --> NEG{negative cue?}
    NEG -->|yes| MISSING[mark missing / negative penalty]
    NEG -->|no| POS{positive or neutral support?}
    POS -->|yes| MATCHED[matched_skills]
    POS -->|no| MISSING

    CLEAN --> SEMANTIC[semantic similarity]
    B --> RAG[retrieve evidence]
    RAG --> CLASSIFY[evidence polarity/type]
    CLASSIFY --> PROJECT[evidence relevance score]
    MATCHED --> SCORE[overall score]
    MISSING --> SCORE
    SEMANTIC --> SCORE
    PROJECT --> SCORE
    SCORE --> FIT{overall >= 55?}
    FIT -->|yes| CONTINUE[allow quick apply]
    FIT -->|no| BLOCK[fit gate blocks application]
```

## 7. Trace / Event / SSE

代码位置：

- `app/services/trace_service.py`
- `app/agents/langgraph_orchestrator.py::_record_langgraph_event`
- `app/api/agent_runs.py::_agent_event_sse`
- `app/static/js/main.js::subscribeAgentRunEvents`

```mermaid
sequenceDiagram
    participant Graph as LangGraph
    participant Trace as TraceService
    participant DB as agent_* tables
    participant API as /events/stream
    participant UI as Browser UI

    Graph->>Trace: create_run / add_event run_created
    Trace->>DB: insert agent_runs / agent_events
    Graph->>Trace: step_started
    Trace->>DB: insert agent_steps + step_started event
    Graph->>Trace: artifact_created
    Trace->>DB: insert agent_artifacts + artifact_created event
    Graph->>Trace: graph_node_started/completed
    Trace->>DB: insert agent_events

    UI->>API: EventSource /agent/runs/{id}/events/stream
    loop until terminal status
        API->>DB: query AgentEvent where id > last_id
        DB-->>API: rows
        API-->>UI: SSE event rows
        API-->>UI: heartbeat
    end
    API-->>UI: run_closed
```

Fallback：

```mermaid
flowchart LR
    A[waitForAgentRun] --> B{EventSource available?}
    B -->|yes| C[subscribeAgentRunEvents]
    B -->|no| D[skip SSE]
    A --> E[setInterval poll /agent/runs/{id}]
    C --> F[update stage from node events]
    E --> G{completed / failed / waiting?}
    G -->|yes| H[finish or resume]
```

## 8. Natural Language Agent

代码位置：`app/agents/natural_language.py::NaturalLanguageAgentService._build_graph`

```mermaid
flowchart TD
    START([START]) --> PARSE[parse_user_request]
    PARSE --> EXEC[execute_user_plan]
    EXEC --> ROUTE{execution_error?}
    ROUTE -->|no| SUCCESS[finalize_success]
    ROUTE -->|yes| REPAIR[repair_user_plan]
    REPAIR --> EXEC2[execute_repaired_user_plan]
    EXEC2 --> ROUTE2{execution_error?}
    ROUTE2 -->|no| SUCCESS
    ROUTE2 -->|yes| FAILED[finalize_failed]
    SUCCESS --> END([END])
    FAILED --> END
```

说明：

- repair 只有一次，不会无限循环。
- repair prompt 明确不得绕过事实校验、投递门禁或人工确认边界。
- 执行计划最终调用主 `AgentOrchestrator`，所以高风险动作仍走主图 guardrail/interrupt。

## 9. Data Model Overview

```mermaid
erDiagram
    Profile ||--o{ ResumeChunk : has
    Profile ||--o{ MatchResult : has
    Profile ||--o{ ResumeVersion : has
    Profile ||--o{ Application : has
    Profile ||--o{ InterviewPrep : has
    Profile ||--o{ AgentRun : has

    Job ||--o{ JobChunk : has
    Job ||--o{ MatchResult : has
    Job ||--o{ ResumeVersion : has
    Job ||--o{ Application : has
    Job ||--o{ InterviewPrep : has
    Job ||--o{ AgentRun : has

    ResumeVersion ||--o{ Application : used_by
    MatchResult ||--o{ InterviewPrep : supports

    AgentRun ||--o{ AgentStep : records
    AgentRun ||--o{ AgentArtifact : produces
    AgentRun ||--o{ AgentEvent : emits
```

模型代码位置：`app/models/entities.py`

## 10. Interview Prep Flow

代码位置：`app/services/interview_prep.py::InterviewPrepService`

```mermaid
flowchart TD
    A[Profile + Job] --> B[MatcherService.create_match_result]
    B --> C[relevant_evidence + matched/missing skills]
    A --> D[InterviewExperienceService.find_relevant_for_job]
    D --> E[source-backed interview experience evidence]
    C --> F[LLM question sets if available]
    C --> G[project deep dive questions]
    C --> H[JD technical questions]
    C --> I[gap drills for missing skills]
    E --> J[source-backed questions]
    A --> K[research checklist links]
    F --> L[question quality judge]
    G --> L
    H --> L
    I --> L
    J --> L
    K --> M[coverage + preparation angles]
    L --> M
    M --> N[(interview_preps)]
```

边界：

- 已导入面经正文可作为 source-backed evidence。
- 未导入正文时只生成搜索参考链接，不编造真实面经内容。
- missing skills 进入 gap drill，不包装成已掌握经验。
