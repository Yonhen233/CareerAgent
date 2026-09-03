---
name: fit_assessment
version: 1.1.0
status: active
owner_role: fit_judge
purpose: 根据结构化 JD 和可追溯证据判断岗位适配度、优势和缺口。
trigger: 用户比较岗位、定制简历、准备投递或生成面试包时。
required_inputs:
  - structured_profile
  - structured_jd
  - ranked_evidence
allowed_tools:
  - profile_repository.load_profile
  - job_repository.load_job
  - matcher.match_job
  - matcher.enforce_fit_gate
  - vector_index.retrieve_resume_evidence
context_policy: 必须使用预算化上下文；禁止把全量历史和全部 Chunk 同时放入 Fit Judge。
output_contract:
  fit_label: str
  fit_score: number
  matched_evidence: list
  gaps: list
forbidden_behaviors:
  - 不得只根据技术关键词重合判断强匹配。
  - 不得忽略否定证据或把相邻领域经验等同于目标岗位交付经验。
success_criteria:
  - Fit 标签、分数、优势和缺口相互一致。
  - 每个关键判断至少绑定一条证据或明确标记证据不足。
failure_policy: 上下文超预算、证据不足或 LLM 返回非法结构时保留输入摘要和错误 Trace 后报错。
---
# 岗位适配判断

先做确定性技能和证据匹配，再由 LLM 对边界样例进行 Fit 判断。弱匹配不进入自动投递材料生成。
