---
name: interview_preparation
version: 1.0.0
status: active
owner_role: interview_coach
purpose: 根据目标 JD、简历项目、证据和缺口生成可练习的中文面试准备包。
trigger: 用户选择面试准备，或完整流程完成投递材料后。
required_inputs:
  - profile_id
  - job_id
  - match_result
allowed_tools:
  - profile_repository.load_profile
  - job_repository.load_job
  - matcher.match_job
  - vector_index.retrieve_resume_evidence
  - interview_experience.import_text
  - interview_prep.generate_packet
context_policy: 只读取结构化 JD、项目证据、缺口和用户明确导入的面经；外部页面只作为标题和链接参考。
output_contract:
  interview_prep_id: int
  question_sets: list
  gap_drills: list
  research_checklist: list
forbidden_behaviors:
  - 不得声称已抓取实际未获取的面经正文。
  - 不得把缺失技能包装成候选人已掌握经验。
success_criteria:
  - 同岗位参考、项目技术追问和通用基础题三个角度都有覆盖。
  - 问题包含追问点、证据边界和可练习标识。
failure_policy: 外部面经不可用时保留标题和链接，核心问题仍基于 JD 和简历证据生成；LLM 失败则报错。
---
# 面试准备

优先围绕候选人的真实项目实现、技术取舍、故障排查和量化结果生成追问。

外部面经只作为补充来源；获取困难时不继续扩张爬虫复杂度。
