---
name: resume_tailoring
version: 1.1.0
status: active
owner_role: resume_writer
purpose: 基于目标 JD 和原始简历证据生成可投递的定制简历。
trigger: 用户要求按目标岗位修改简历，或完整流程进入定制阶段时。
required_inputs:
  - profile_id
  - job_id
  - ranked_evidence
allowed_tools:
  - profile_repository.load_profile
  - job_repository.load_job
  - matcher.match_job
  - vector_index.retrieve_resume_evidence
  - resume_tailor.tailor_resume
  - guardrail.verify_resume
context_policy: 只向写作模型提供结构化事实、JD 要求和 Top evidence；完整原文按需披露。
output_contract:
  resume_version_id: int
  verification: dict
  change_summary: list
  keyword_alignment: dict
forbidden_behaviors:
  - 不得添加没有证据支持的技能、指标、公司、学校、日期或项目成果。
  - 不得把检查结果、缺口披露或修改摘要写入可投递简历正文。
success_criteria:
  - Guardrail 通过且 hallucination_count 为零。
  - 每项关键修改可以追溯到原始 Profile 或 RAG 证据。
failure_policy: 高风险草稿只允许一次 ReAct 修复；修复后仍未通过则终止并保留问题列表。
---
# 定制简历

改写目标是提高岗位相关性，而不是伪造更强经历。缺失技能只能保留在单独诊断信息中。

最终简历正文、事实检查、修改摘要和关键词缺口必须作为不同产物保存和展示。
