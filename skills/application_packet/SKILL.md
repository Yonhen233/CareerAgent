---
name: application_packet
version: 1.1.0
status: active
owner_role: application_operator
purpose: 生成投递材料，并在任何真实外发动作前执行审批、幂等和审计策略。
trigger: 用户要求生成投递包、邮件草稿、邮件发送或浏览器辅助填写时。
required_inputs:
  - profile_id
  - job_id
  - resume_version_id
allowed_tools:
  - profile_repository.load_profile
  - job_repository.load_job
  - resume_tailor.tailor_resume
  - guardrail.verify_resume
  - application.create_quick_apply_packet
  - browser_apply
  - email_draft
  - email_send
context_policy: 只读取最终定制简历、Profile 联系方式、目标岗位摘要和投递所需字段。
output_contract:
  application_id: int
  approval: dict
  packet_validation: dict
  outbound_result: dict
forbidden_behaviors:
  - 未经 approved 审批不得执行 browser_apply、email_draft 或 email_send。
  - 不得把生成投递包表述为已经成功投递。
  - 不得重复执行具有相同业务幂等键的外发动作。
success_criteria:
  - 投递包通过事实校验并持久化。
  - 高风险动作具备审批记录、执行结果 Artifact 和审计事件。
failure_policy: 审批缺失、权限不足、SMTP 或浏览器执行失败时直接终止副作用并记录可验证错误。
---
# 投递准备与外发

生成材料和执行外发是两个不同阶段。生成投递包也需要用户确认，但不会自动提交招聘网站。

邮件和浏览器工具必须通过 HighRiskActionToolService，不允许节点直接调用底层 SMTP 或 Playwright。
