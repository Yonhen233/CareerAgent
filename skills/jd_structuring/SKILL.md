---
name: jd_structuring
version: 1.0.0
status: active
owner_subagent: job_analyst
purpose: 搜索或读取目标岗位，把原始 JD 转换为职责、硬要求、软要求和可检索 Chunk。
trigger: 用户搜索岗位、粘贴 JD 或选择岗位池中的目标岗位时。
required_inputs:
  - query_or_job_id_or_jd_text
allowed_tools:
  - job_search.search_jobs
  - job_repository.load_job
  - jd_parser.parse_jd
  - vector_index.upsert_job_chunks
context_policy: 完整 JD 只在解析阶段读取；匹配和定制阶段使用结构化要求、摘要和按需 Chunk。
output_contract:
  jobs: list
  structured_jd: dict
  job_chunks: list
forbidden_behaviors:
  - 不得把加分项、否定项或宣传文案误标成硬性要求。
  - 不得绕过招聘站登录、验证码或反爬限制。
success_criteria:
  - required_skills、preferred_skills、职责和资格条件可以区分。
  - 岗位来源错误单独记录，不伪造成无岗位结果。
failure_policy: 岗位源、JD 解析或入库失败时记录具体 source/stage 后报错；不使用虚构岗位替代。
---
# JD 分析

中文岗位优先。搜索结果必须保留来源、原始 JD、投递链接和结构化解析结果。

当用户明确给出 Job ID 或 JD 时，使用指定岗位，不再用外部搜索结果覆盖用户选择。
