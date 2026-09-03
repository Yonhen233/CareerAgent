# 量化评测方案

> 2026-09-01 对话任务状态 V4 完成 48 组离线多轮评测和 5 对真实 `deepseek-v4-flash` 长对话 A/B。离线字段准确率、纠错生效率、禁止操作召回率、压缩状态保持率和 Checkpoint 一致率均为 `1.0`，摘要冲突误接受率为 `0`；24/48 case 触发 Compactor。真实 5/5 case 均触发 Compactor 并通过，字段/纠错/禁止操作/usage 均为 `1.0`。首次压缩相对完整历史 Raw 的 Provider Input Token `27,716.8 -> 28,281.4`（增加 `2.04%`），业务调用 `1 -> 2`，因此不能宣称首次压缩省 Token。详见 [对话任务状态与压缩完整性 V4](CONVERSATION_TASK_STATE_V4.md)。

> 2026-08-31 上下文管理 V3 已完成 5 对真实完整 Agent Run A/B。每个 Variant 均执行 PDF 解析、JD 解析/入库、岗位匹配、简历证据检索、简历定制、独立 Guardrail、投递材料 Dry Run、6 题面试准备和 Completion Gate。V2/V3 完成率均为 `5/5`，Critical Fact、Citation、Guardrail、Application、Interview、Completion 和 Provider Usage 完整率均为 `1.0`。V3 平均 Input Token `20,657.4 -> 21,498.4`（增加 `4.07%`），Total Token `25,499.6 -> 26,617.2`（增加 `4.38%`），总成本增加 `4.60%`，平均延迟下降 `6.60%`。因此 V3 的发布价值是上下文隔离、最小恢复和事实完整性，完整流程尚未证明 Token 节省。详见 [V3 正式实现与完整流程评测](CONTEXT_MANAGEMENT_V3_PRODUCTION.md)。

> 2026-08-31 完成 Context / Token V2 独立真实消融及两个 V2 同时开启的联合 canary。联合混合工作负载中 Provider Input Tokens `25,756.33 -> 10,225.67`（-60.30%）、Total Tokens `26,313.33 -> 10,755`（-59.13%）、业务调用 `7 -> 3`，事实、引用、禁止声明、注入、跨租户与 usage 完整性 Gate 均通过。两个 V2 已切为正式默认路径，详见 [联合上线报告](COMBINED_V2_PRODUCTION_RELEASE.md)。

> 2026-08-31 首轮 Context Runtime V2 的 40 Case 离线确定性 A/B 中，平均估算 Input Token 从 `23527.60` 降至 `7102.05`（`-69.81%`）；Critical Fact、Required/Negative Evidence 和 Citation 均为 `100%`。该轮尚未调用真实 LLM，所以当时结论仅为继续 Shadow；之后的独立真实与联合真实 canary 已补齐并切换正式默认，详见 [Context Runtime V2 评测](CONTEXT_RUNTIME_V2_EVALUATION.md) 与 [联合上线报告](COMBINED_V2_PRODUCTION_RELEASE.md)。

> 2026-07-22 最新的分层 Agent 系统真实评测、指标解释、成本与失败样本见 [CareerAgent Agent 系统评测报告](AGENT_SYSTEM_EVALUATION_2026-07-22.md)。本轮严格发布门禁未通过，不能只依据单项满分判断系统已经可上线。

> 2026-08-09 新增 Task Contract、Completion Gate、Trajectory V2 和 RAG Evidence Gate。离线全量代码回归为 `277 passed`；独立 SQLite 数据库上的 6-case Agent 全流程确定性评测通过率、Trajectory V2、Artifact、LangGraph、岗位 Top1 和投递包门禁均为 `1.0`，3 个低匹配 case 均被 Fit Gate 按预期阻断。该结果证明新的确定性控制面和固定评测场景通过，不替代 24-case 真实 LLM workflow 重跑，也不改写 2026-07-22 整体 Release Gate 仍为失败的历史结论。

> 2026-08-09 Agent Runtime v2 增加执行时 Tool 合同、ErrorEnvelope、单一重试所有权、持久化熔断、typed memory、在线质量复核和 Prompt 指纹。最终全量回归为 `291 passed in 103.21s`；新增用例全部使用本地故障注入，没有调用 DeepSeek。该结果证明运行控制面通过确定性回归，不替代真实 LLM 输出质量评测。

> 2026-08-11 二次成熟度审计将 RAG Evidence Gate 升级为 v2、Completion Gate 升级为 v2、Task Contract 升级为 v3，并增加 Tool 强类型合同、无进展循环检测、SQLite 产物 lineage 回查和父子 LLM 总预算。首轮全量为 `294 passed, 5 failed`，5 个失败揭示门禁自身的枚举解析与跨检索器 metadata 契约问题；继续收紧 Planner 输出合同后最终为 `300 passed in 160.18s`。新增 bad case 均为确定性回归，不调用 DeepSeek。

> 2026-08-11 新增 144-case 中英双语/跨语言 RAG 校准集和 1,440 个证据对。真实多语 embedding 的纯向量 Top1 为 `0.9792`、Recall@5 为 `1.0`；校准后的 Evidence Gate v3 在 Recall `0.9583` 下把错误证据误放率从 `0.9715` 降到 `0.0185`。同日建立 7/30 天窗口 SLO；合成 7 天切片为 HTTP `300/300`、P95 `103.974ms`，Agent `47/49`、P95 `62083ms`，Completion Integrity `47/47`。详见 [SLO](SLO.md) 与 [多语言 RAG 校准](RAG_MULTILINGUAL_CALIBRATION.md)。

## Agent Runtime Bad Case 评测

这部分不评“回答写得像不像”，而是验证成熟 Agent 的控制面在故障下是否作出正确决策：

| 指标 | 定义 | 当前门禁 |
| --- | --- | --- |
| Tool contract rejection | 未注册 Tool、缺参数和非法输出是否在执行层拒绝 | 必须 100% 拒绝 |
| Retry precision | 发生自动重试的 case 中，是否全部为合同允许的幂等瞬时错误 | 1.0 |
| Side-effect replay rate | email/browser 等外发 Tool 是否被自动重试 | 0 |
| Circuit open accuracy | 连续可重试失败达到阈值后是否 open，冷却前是否拒绝新调用 | 1.0 |
| Error routing accuracy | 配置/预算/熔断是否直接失败，可修输入/完成缺项是否只 repair 一次 | 1.0 |
| Poison-message immediate DLQ rate | 不可重试 queue payload 是否第一次失败即 DLQ | 1.0 |
| Memory isolation | tenant/user/profile 边界外的记忆是否进入上下文 | 0 条泄漏 |
| Negative-feedback review rate | incorrect/incomplete/unsafe 或低评分是否创建复核项 | 1.0 |
| Diagnostic secret leakage | Key/Bearer/邮箱/手机号是否进入预览 | 0 |
| Prompt provenance coverage | LLM 日志是否具备 prompt hash、route 和 policy version | 1.0 |
| Duplicate evidence rejection | 重复正文能否被用于凑够最小证据数 | 100% 拒绝 |
| Semantic type gate | 高分但错误类型 chunk 能否进入事实敏感生成 | 100% 拒绝 |
| No-progress loop rejection | 输入变化但连续输出相同的循环是否在下一次调用前停止 | 100% 拒绝 |
| Artifact lineage integrity | 不存在、跨 profile/job 或已撤回产物能否通过完成门禁 | 100% 拒绝 |
| Nested LLM budget coverage | 子工作流调用和 usage 是否同时计入父工作流预算 | 1.0 |

上述门禁由 `tests/test_agent_runtime_maturity.py` 和 Redis hardening 测试完成。它们使用真实 SQLAlchemy 状态迁移和异步 timeout/retry 逻辑，不调用 LLM。Online Quality score 只表示运行控制面的可疑程度，不应被解释为用户满意度或答案正确率。

## 面试 Agentic RAG v3 成本与质量门禁

面试评测不再只检查题目数量和回答长度，还检查完整链路契约：

- 默认问题数是否等于 10；
- 正常路径由问题生成、答案 Batch 和 Verifier Batch 组成；发生 JSON/答案定向修复时，整个面试工作流业务调用不得超过 8；
- 累计 Prompt 字符和最大输出 token 预留是否低于工作流硬预算；
- 本地 multi-query plan、source inventory 与按题目视角设置的来源配额是否兼容；
- citation integrity 与局部证据别名合法率；
- claim type/source policy 覆盖率；
- LLM entailment judge 对引用证据的支持判断；
- verified claims 本地组合后的回答长度、复盘结构和用户可用性；
- repair error count 与 dirty question count 是否逐轮收敛；
- release gate 失败时 InterviewPrep 是否保持不落库。

真实 DeepSeek 旧包 `#44` 使用 59 次调用、1,490,670 Prompt 字符和 237,622 Response 字符，其中 verifier 占 37 次调用和 1,080,855 Prompt 字符。当前契约正常路径由问题生成、答案共享上下文 Batch 和 Verifier Batch 组成；JSON repair 最多 1 次，答案定向 repair 最多 2 轮，整个面试链业务调用上限为 8。答案和 verifier 各处理共享上下文批次，repair 与复验只处理失败题。根据真实完整流程 bad case，Prompt 字符硬预算调整为 100,000，仍受全局 input/output/total token 和 repair budget 约束。完整 JSON 若漏题，只重试漏项题；历史日志没有供应商 usage 字段时不能把字符数伪装成真实 token。

独立 `interview_claim_verifier` 数据集包含 14 个 case，覆盖 4 个可支持方案、4 个伪装成方案的既有经历、2 个支持事实、2 个不支持事实和 2 个“事实正确但答非所问”样本。真实 run `#50` 分两批各 7 case：support accuracy、strategy recall、question-answering accuracy 均为 1.0，false positive rate、disguised-experience false positive rate 和 nonresponsive false accept rate 均为 0。真实在线评测必须先通过该低成本闸门，完整面试链路才允许继续运行。

最终真实 DeepSeek 面试包 `#47` 使用简历 `#159` 与岗位 `#218`：10 题、8 次调用、83.07 秒，实际输入 23,028、输出 7,450、总计 30,478 tokens；一次定向 repair 后发布。question quality 为 1.0，必备技能覆盖 `6/7=0.8571`，引用完整性、来源权限和参考答案可用性均为 1.0。该数字是单次成功工作流成本，不代表开发期总试错成本；`llm_call_logs #1153-#1253` 合计 397,770 tokens。

离线评测中的 `DeterministicInterviewEvaluationLLM` 只在显式 `LLM_FALLBACK_ENABLED=true` 的评测 harness 使用，不进入产品路径；产品未配置 LLM 或 release gate 失败时直接报错。

## 2026-07-22 DeepSeek V4 Flash 与 Pro 固定切片对照

运行器：

```powershell
$env:LLM_API_KEY='<通过进程环境注入>'
python scripts/run_model_comparison_slice.py --model deepseek-v4-flash --mode canary --token-budget 50000
python scripts/run_model_comparison_slice.py --model deepseek-v4-pro --mode canary --token-budget 50000
```

`mode=core` 固定选择 4 个自然语言规划、4 个 JD Parser 和 3 个 hard/adversarial workflow case；`mode=interview` 固定选择 `agent_intern_with_mlflow_gap`。脚本强制 `thinking=disabled`、`fallback=false`，并按 benchmark ID 汇总供应商真实 usage。API key 不写入报告或日志。

比较脚本同时强制 `LLM_ROUTING_ENABLED=false`，保证 Flash 报告的所有节点都使用 Flash、Pro 报告的所有节点都使用 Pro。产品运行则开启路由：普通节点走 Flash，简历深度建议和面试节点走 Pro。

| 分层 | Flash | Pro |
| --- | ---: | ---: |
| Canary 通过率 | 1/1 | 1/1 |
| Canary Token / wall time | 7,629 / 40.0s | 7,469 / 57.1s |
| Planner | 4/4 | 4/4 |
| JD Parser | 4/4 | 4/4 |
| Core workflow（按当前门控） | 0/3 | 0/3 |
| Core Token / wall time | 22,175 / 79.1s | 15,737 / 128.8s |
| Interview release gate | 失败 | 通过 |
| Interview Token / wall time | 29,135 / 87.1s | 30,615 / 129.2s |

结论边界：Flash 在短结构化节点和正常 canary 上质量与 Pro 接近且更快；Flash core 因两次定制 repair 反而比 Pro 多用 Token。面试 case 中两者都需要一次 repair，Pro 的增量复验通过，Flash 仍有两题未覆盖架构位置、选型理由和替代方案。当前只支持“面试节点保留 Pro、短节点继续评估 Flash 路由”，不支持全局替换，也不支持把 1/1 面试结果外推为总体胜率。

本轮门控校准包括：JD retrieval keyword 与事实字段分离、负向 gap 双边验证、unsupported target role 硬失败、英文句界隔离，以及投递文案的“词法校验 -> 本地多语言 embedding -> 否定极性/结果语义一致性”二阶段门禁。所有模型质量结论使用校准后的统一门控；被代码 bug 污染的首次运行只作为开发 bad case，不进入上表。

CareerAgent 的评测分为十二类：

- 基础匹配评测：Profile/JD 匹配质量。
- PDF Chunk 策略评测：不同 PDF 切分方案对证据召回的影响。
- RAG 策略评测：不同检索排序策略对证据召回的影响。
- JD Parser 评测：衡量真实 JD 结构化解析质量，避免核心技能漏抽或把可选技能误写成 required。
- 自然语言规划评测：衡量意图、动作依赖、显式禁止动作和实体抽取，避免关键词命中覆盖用户否定意图。
- LLM 实景流程评测：真实调用 LLM 判断岗位适配度并按 JD 改写简历。
- Agent 全流程评测：覆盖岗位搜索、匹配排序、简历定制、一键投递门禁、Trace 和 Artifact。
- 投递材料 Guardrail 评测：衡量事实支持、高风险召回、误拦截、漏拦截和人工确认边界。
- Prompt Injection 对抗评测：按来源与攻击类别衡量召回率、误报率和发布阈值。
- 面试准备包评测：衡量网上同岗位面经、简历项目技术栈、其他可能面试问题三类准备角度，以及同岗面经调研线索、缺口 drill 和通用问题覆盖。
- 真实岗位源 Smoke：只评测招聘源可达性、结果数量和岗位质量，不参与核心 Agent 回归 pass rate。
- 真实 JD Ingest Smoke：只评测真实 JD 解析、SQLite 入库、JD chunk、embedding/reranker 和检索 probe，不参与核心 Agent 回归 pass rate。

## 数据集

### 基础匹配数据

```text
evals/sample_cases.json
```

当前 3 个样例，用于快速回归。

### PDF Chunk 策略数据

```text
evals/pdf_chunk_cases.json
```

规模：

- 96 个合成 PDF 简历案例。
- 每个案例 5 页。
- 每个案例 6 个查询。
- 共 576 条 PDF chunk 查询。

数据设计：

- 覆盖 Agent/RAG、LLM Eval、后端平台、前端工具、ML 平台、数据工程等候选人类型。
- 每页包含目标证据、相邻岗位项目、课程噪声、计划学习、废弃 prototype 和重复技术词。
- 查询要求同时命中关键词、页码和上下文关键词。
- 查询按 `easy`、`medium`、`hard`、`adversarial` 分桶。
- 噪声类型包括 `coursework_vs_shipped`、`hard_negative_project_same_page`、`planned_learning_negative`、`cross_page_distractor`、`late_page_appendix` 等。

### RAG 策略数据

```text
evals/rag_cases.json
```

规模：

- 180 个 RAG 检索案例。
- 每个案例 12 个候选证据 chunk。
- 每个案例 4 个期望命中的 evidence chunk。
- 共 2160 个候选 chunk。

数据设计：

- 覆盖 12 类技术岗位：Agent/RAG、LLM Eval、后端平台、前端工具、ML 平台、数据工程、DevOps、AI 安全、移动 AI、推荐算法、产品分析、计算机视觉。
- 一部分查询使用精确技术关键词，一部分使用同义表达，例如 `retrieval augmented generation` -> `RAG`。
- 每个 case 包含 hard negative、planned learning、coursework、adjacent domain、generic tools、rejected prototype、long noise 等噪声 chunk。
- 按 `easy`、`medium`、`hard`、`adversarial` 分桶统计。

### JD Parser 评测数据

```text
evals/jd_parser_cases.json
```

规模：

- 30 个 JD 解析 case。
- 覆盖 `easy`、`medium`、`hard`、`adversarial` 四类难度。
- 覆盖 `preferred_skill_noise`、`negative_requirement`、`synonym_alias`、`chinese_jd`、`rag_stack`、`platform_stack`、`metric_evidence`、`agent_framework_stack` 等噪声画像。

数据设计：

- 每个 case 都包含原始 JD、岗位标题、公司/地点、期望 job_type、期望 required skills、期望关键词、期望不应进入 required 的技能。
- 中英混合覆盖 Agent/RAG、LLM Eval、Prompt Security、ML Platform、Backend、Frontend、Data Engineering、Recommendation、MLOps、Computer Vision 等真实岗位类型。
- 刻意加入 `Preferred`、`Nice to have`、`加分项`、`No prior X required`、`不要求 X`、同义词和相邻领域噪声。
- 指标同时检查 required skill recall、keyword hit rate、job_type accuracy、responsibility/qualification 最小覆盖和 absent required skill violation。

### LLM 实景流程数据

```text
evals/llm_workflow_cases.json
```

规模：

- 24 个端到端 LLM 流程案例，不再只评测 3 条岗位适配标签。
- 17 个案例会进入简历定制流程。
- 覆盖 `strong_fit`、`partial_fit`、`weak_fit` 三类标签。
- 覆盖 `easy`、`medium`、`hard`、`adversarial` 四类难度。

数据设计：

- 覆盖 Agent/RAG、LLM Eval、后端、前端、数据工程、ML、AI 安全、移动 AI、推荐、分析、DevOps、CV 等岗位。
- 每个 case 包含原始简历文本、期望 Profile 技能、期望 Profile 关键词、JD、期望 JD 技能、期望 fit label、期望 fit score 区间、定制简历关键词和禁止编造 claim。
- hard/adversarial case 明确加入 `did not build`、`No shipped project`、相邻岗位经验等反例，测试模型是否把“读过/计划学习/课程提到”误判成真实交付经验。
- 新增中文 PDF 版式噪声、Prompt Injection 文本、观测性技术栈、课程/计划学习边界与相邻 Agent 岗位等样本。

### 自然语言规划评测数据

```text
evals/natural_language_plan_cases.json
```

规模：20 个中文为主 case。覆盖无简历浏览、已有档案、自然语言建档/更新、粘贴 JD、多动作、显式禁止投递、UI 显式选项以及少量英文和中英混合输入。标注 intent、required/forbidden actions、`needs_profile`、`needs_job` 与实体关键词；`needs_profile` 表示完成计划是否依赖简历，不表示当前请求里是否已经上传简历。

### 投递材料 Guardrail 数据

```text
evals/application_packet_cases.json
```

规模：27 个 case。除技能名编造外，还覆盖非技能经历编造、不支持/支持的数字指标、相近实现掩盖新增结果、负面能力披露、双语近义改写、目标岗位 claim scope、缺少投递链接和自动提交边界。

### Agent 全流程数据

```text
evals/agent_full_flow_cases.json
```

规模：

- 6 个端到端 Agent 流程案例。
- 覆盖 Agent、前端、数据工程、推荐算法、ML 平台和弱匹配 Agent 候选人。
- 每个 case 都使用可控岗位源写入真实 `jobs`、`job_chunks` 和匹配结果，避免外部招聘站波动影响回归。
- 强匹配 case 会跑通 `find_jobs_for_profile`、`tailor_resume_for_job` 和 `quick_apply`。
- 弱匹配 case 会允许定制简历或检索分析，但 `quick_apply` 必须被 `fit_gate` 阻断。

数据设计：

- 每个 case 包含 guided profile、候选岗位列表、期望 Top1 岗位、期望分数区间、是否运行 tailor、是否运行 quick_apply、是否期望投递门禁拦截。
- 评测会检查 Top1 岗位准确率、分数门禁、tailor Guardrail、quick apply 行为、Agent step trace 和 execution plan artifact。
- 岗位 external_id 每次评测运行都会带唯一 namespace，重复运行不会撞 SQLite 唯一约束；原始岗位 ID 保存在 `eval_external_id` 里用于断言。

### 面试准备包数据

```text
evals/interview_prep_cases.json
```

规模：

- 9 个面试准备案例，其中 1 个 case 带已导入牛客网同岗面经材料，用于验证 source-backed 面经追问。
- 中文岗位为主，英文 LLM Application Intern 只作为辅助样例。
- 覆盖 Agent 开发、前端、数据开发、推荐算法、AI 产品、ML 平台和弱匹配候选人。

数据设计：

- 每个 case 包含 Profile、目标 JD、期望题组、期望外部调研源、期望缺口 drill 和题目关键词。
- 外部调研源检查牛客网、OfferShow、小红书等 query 是否生成；如果 case 提供已导入面经，还会检查 source-backed 问题数量和来源站点，不把真实平台网络可达性混入核心质量评测。
- 缺口 case 刻意加入 `没有 MLflow`、`没有 Kubernetes 集群维护经验`、`没有构建过 Agent 系统` 等否定证据，验证面试包不会把缺口包装成已掌握经验。

## 标注标准

### `strong_fit`

- 分数区间：85-100。
- 候选人已经在项目、实习或工作中交付过岗位核心能力的大部分要求。
- 允许少量工具名缺失，但必须有可追溯证据，例如 shipped project、服务/API、评测指标、部署或可量化结果。
- 目标岗位、headline、求职意向不能作为匹配证据。

### `partial_fit`

- 分数区间：55-84。
- 候选人至少交付过一个与岗位核心任务直接相关或高度相邻的完整产物，但仍缺少部分核心工具、平台经验或业务场景。
- 可以进入简历定制和人工评估；是否一键投递由 `fit_gate` 分数、缺口和风险共同决定。
- 单纯“学过/读过/计划学习/课程提到”不能标为 `partial_fit`。

### `weak_fit`

- 分数区间：0-54。
- 出现以下任一情况即归入弱匹配：只有目标意向或 headline；只有课程、阅读、计划学习；明确写了 `did not build`、`No shipped project`、`No MLflow` 等核心否定证据；只有相邻岗位经验但缺少岗位核心交付。
- `weak_fit` 不代表候选人完全没有潜力，而是当前证据不足以直接投递。`quick_apply` 必须被 `fit_gate` 阻断，并在 Agent trace 中保留阻断原因。
- 负面证据优先级高于关键词重合，不能因为同一句话里出现技术词就算作已掌握。

## 运行方式

```bash
pytest -q
```

API：

```http
POST /evaluations/run
POST /evaluations/pdf-chunk-strategies
POST /evaluations/rag-strategies
POST /evaluations/agent-full-flow
POST /evaluations/jd-parser
POST /evaluations/natural-language-plan
POST /evaluations/job-relevance
POST /evaluations/application-packet
POST /evaluations/prompt-injection
POST /evaluations/interview-prep
POST /evaluations/real-job-source-smoke
POST /evaluations/real-job-ingest-smoke
POST /evaluations/llm-workflow
GET /evaluations/results
```

## JD Parser 评测

运行：

```http
POST /evaluations/jd-parser
```

最近一次离线回归结果：

| 指标 | 结果 |
| --- | ---: |
| case_count | 30 |
| completed_rate | 1.0000 |
| pass_rate | 1.0000 |
| avg_required_skill_recall | 1.0000 |
| avg_required_skill_precision | 0.9769 |
| avg_required_skill_f1 | 0.9876 |
| avg_keyword_hit_rate | 1.0000 |
| job_type_accuracy | 1.0000 |
| responsibility_min_pass_rate | 1.0000 |
| qualification_min_pass_rate | 1.0000 |
| grounding_quality_gate_pass_rate | 1.0000 |
| absent_required_skill_violation_count | 0 |
| release_gate.passed | true |

说明：

- 这次结果是离线 `heuristic_fallback` 全量基线 `#62`，用于可重复验证 30 条标注和 release gate；生产配置中 LLM 可用时仍调用真实 JD parser LLM 链路。
- 本轮评测先暴露了两个问题：`Tool Calling`、`A/B Testing` 被后续负向句误判为不要求；`internal tools` 被 `intern` 子串误判成实习岗位。
- 新的真实 Flash 分层评测又暴露了 `实习`/`internship` 口径不一致和中英文技能重复。修复后统一 job type 与技能别名，负向语境只在当前行/句内判断，preferred 技能单独抽取；precision、F1 和 grounding 与 recall 一起进入 release gate。
- 该评测不会替代真实 JD ingest smoke；它负责 parser 质量，ingest smoke 负责 source posting 进入 SQLite、chunk、embedding/reranker 和 retrieval probe 的链路健康。

## PDF Chunk 策略评测

对比策略：

- `fixed_window_450_overlap80`
- `paragraph_page_900_overlap160`
- `paragraph_page_1200_overlap200`
- `section_aware_700_overlap120`

最近一次评测结果：

| 策略 | Top3 关键词 | Top3 页码 | Top3 上下文 | Top1 平均字符 | 平均 Chunk 数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| fixed_window_450_overlap80 | 0.8472 | 0.7951 | 0.6771 | 449.89 | 20.96 |
| paragraph_page_900_overlap160 | 0.9479 | 0.8299 | 0.7760 | 772.77 | 10.00 |
| paragraph_page_1200_overlap200 | 0.9358 | 0.8403 | 0.8316 | 1054.09 | 9.00 |
| section_aware_700_overlap120 | 0.9479 | 0.8281 | 0.7865 | 534.33 | 16.57 |

评测 `#65` 使用真实多语言 embedding 完整运行 96 份简历、576 条查询，release gate 通过。门槛为 Top3 关键词 >=0.90、页码 >=0.80、上下文 >=0.75，同时限制 Top1 平均长度 <=950 字符、平均 chunk 数 <=14，防止只靠放大 chunk 获得更高命中。

选择：

```text
paragraph_page_900_overlap160
```

理由：

- 900 窗口与 section-aware 的 Top3 关键词命中率并列最高，为 0.9479。
- 900 窗口 Top3 页码命中率 0.8299，略高于 section-aware 的 0.8281。
- 1200 窗口上下文命中率最高，但平均 Top1 长度超过 1054 字符，更容易把 hard negative 和课程噪声一起带入上下文。
- section-aware 上下文表现略高于 900，但平均 chunk 数 16.57，检索和 rerank 成本更高。
- 因此当前选择 `paragraph_page_900_overlap160`，作为上下文保留、噪声控制和检索成本之间的折中。

暴露的问题：

- `coursework_vs_shipped` 噪声最难。900 窗口在这个噪声下 Top3 context hit 只有 0.0521。
- 说明仅靠 chunk 切分和向量/词法检索，仍难区分“课程里提到某技术”和“真实项目里交付某技术”。
- 当前已经在 RAG ranking 中加入 `EvidenceClassifier`：`shipped_project`、`metric_evidence` 会被加权，`coursework`、`planned_learning`、`missing_skill_disclosure` 会被降权。它解决的是“检索到了关键词，但证据性质不可靠”的排序问题，而不是替代 PDF chunk 策略本身。

## RAG 策略评测

对比策略：

- `hash_vector_only`
- `hash_lexical_only`
- `hash_lexical_80_vector_15_type_5`
- `real_embedding_vector_only`
- `real_embedding_70_vector_30_lexical`
- `real_embedding_55_vector_40_lexical_5_type`
- `real_embedding_45_vector_50_lexical_5_type`
- `real_embedding_top20_rerank`

真实模型：

- Embedding：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Reranker：`cross-encoder/ms-marco-MiniLM-L-6-v2`
- Rerank 候选：一阶段 Top20。
- Rerank 保护策略：Top5 作为召回锚点保持一阶段顺序，第 6 到第 20 个候选在分数带内二阶段排序。

最近一次评测结果：

| 策略 | Embedding | Reranker | Top1 Acc | Top3 Recall | Top5 Recall | MRR | nDCG@5 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| hash_vector_only | hash | none | 0.7500 | 0.4792 | 0.6875 | 0.8750 | 0.6734 |
| hash_lexical_only | hash | none | 0.7500 | 0.4792 | 0.7500 | 0.8333 | 0.7296 |
| hash_lexical_80_vector_15_type_5 | hash | none | 1.0000 | 0.5625 | 0.7292 | 1.0000 | 0.7748 |
| real_embedding_vector_only | sentence-transformers | none | 0.8333 | 0.4681 | 0.6014 | 0.9167 | 0.6415 |
| real_embedding_70_vector_30_lexical | sentence-transformers | none | 1.0000 | 0.4694 | 0.6403 | 1.0000 | 0.6968 |
| real_embedding_55_vector_40_lexical_5_type | sentence-transformers | none | 1.0000 | 0.5958 | 0.7292 | 1.0000 | 0.7830 |
| real_embedding_45_vector_50_lexical_5_type | sentence-transformers | none | 1.0000 | 0.6125 | 0.7292 | 1.0000 | 0.7862 |
| real_embedding_top20_rerank | sentence-transformers | cross-encoder | 1.0000 | 0.6125 | 0.7292 | 1.0000 | 0.7862 |

选择：

```text
real_embedding_top20_rerank
```

理由：

- 强噪声评测后，真实 embedding 策略中 `vector=0.45 / lexical=0.50 / type=0.05` 达到最高 Top3 Recall。
- `real_embedding_top20_rerank` 在 Top5 anchor 保护下与最佳一阶段真实 embedding 策略持平，同时保留 CrossEncoder 对 Top20 尾部证据的二阶段排序能力。
- hash baseline 的表现不再稳定：`hash_lexical_80_vector_15_type_5` Top3 Recall=0.5625，低于真实 embedding + rerank 的 0.6125。
- 选择真实 embedding 主路径更贴近真实 JD 和简历语义表达，例如中英文混写、同义表达、职责描述不直接出现技术名的情况。

分桶结果：

| 难度 | Top1 Acc | Top3 Recall | Top5 Recall | MRR | nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| easy | 1.0000 | 0.5000 | 0.7500 | 1.0000 | 0.7650 |
| medium | 1.0000 | 0.7500 | 0.7500 | 1.0000 | 0.8319 |
| hard | 1.0000 | 0.5833 | 0.7500 | 1.0000 | 0.7968 |
| adversarial | 1.0000 | 0.6167 | 0.6667 | 1.0000 | 0.7512 |

发布门禁：

- `Recall@3 >= 0.60`。每题人工标注 4 个相关 chunk，而 Top3 最多容纳 3 个，因此理论上限是 0.75；0.60 对应达到理论上限的 80%。
- `Recall@5 >= 0.70`、`MRR >= 0.85`、`nDCG@5 >= 0.75`、`Top1 >= 0.80`。
- 实际 provider 必须包含 `sentence_transformers` 和 `cross_encoder`，`fallback_reasons` 必须为空，不能用 hash/词法兜底结果冒充真实向量评测。
- 2026-07-22 的 180-case 完整重跑满足上述所有条件，`release_gate.passed=true`；对抗桶 Recall@5=0.6667 仍单独列为改进项。

调试发现：

- 第一轮真实评测中，裸 CrossEncoder 权重过高，会把强关键词证据推出 Top3，Top3 Recall 从 0.9444 降到 0.8889。
- 修复方式是采用保守融合：一阶段分数为主，rerank 分数为辅，并设置 Top5 recall anchor。
- 依赖调试中发现 `transformers 5.x` 与当前 SentenceTransformers 加载不稳定，已在 `requirements.txt` 中约束 `transformers<5.0.0`、`huggingface-hub<1.0`。
- 强噪声数据集把 Top3 Recall 从原来的 0.9444 拉低到 0.6125，这是有意为之：新数据更接近真实简历里的课程噪声、计划学习和相邻项目干扰。
- 后续优化重点不再是继续调 embedding 权重。本轮已有证据类型分类与负向证据降权；下一步应补充真实人工脱敏 JD/简历 pair，并对对抗桶做错误分析，必要时再引入小模型 classifier 或抽样 LLM verifier，而不是继续在合成集上调权重。

## RAG 向量库选型

当前选择：

```text
SQLite 权威存储 + Chroma 可选向量库镜像
```

理由：

- SQLite 保存 Profile、JD、chunk、metadata、embedding 和评测结果，是可审计的权威存储。
- Chroma 是常见本地向量库，API 简单，适合展示真实 RAG 工程组件。
- Chroma 作为镜像而不是唯一存储，避免业务元数据被锁死在向量库里，也避免向量库不可用时主流程崩溃。
- 与 FAISS 相比，Chroma 更方便持久化和按 collection 管理 Profile/JD chunk。
- 与 Qdrant、Milvus 相比，Chroma 不需要额外服务，更适合个人简历项目和本地面试演示。
- 后续如果需要规模化，可替换为 Qdrant、Milvus、pgvector 或云向量库。

## Agent 全流程评测

### Trajectory V2 与完成语义

旧指标的 `tool_success_rate` 只统计 Tool 是否正常返回，`trace_passed` 只检查 `plan_task` 是否完成，无法发现工具选错、参数错、顺序错、重复调用或 Agent 提前结束。当前 `careeragent-trajectory-eval-v2` 增加：

- required step：必要步骤是否执行；
- allowed tool：是否调用当前任务无权使用的工具；
- argument invariant：`profile_id/job_id/resume_version_id` 是否存在并与请求一致；
- precedence constraint：parse/load/match/tailor/approval/application/interview 的先后关系；
- duplicate signature：同一工具与参数是否无进展重复；
- approval invariant：投递工具执行前是否存在 approved action；
- outcome artifact：完成 run 是否有 `completion_verification`；
- policy-block trajectory：Fit Gate 正确阻断必须停在投递工具之前，不能当普通执行失败。

任务完成率不再由最终文本判断，而由 Goal Ledger、业务表、Artifact 和 Validator 联合判断。Checkpoint recovery 采用“同签名最后一次 attempt”语义；checkpoint rewind 的轨迹由继承前缀和分支新步骤共同组成。

2026-08-09 离线验证结果：

| 指标 | 结果 |
| --- | ---: |
| case 数 | 6 |
| 全流程通过率 | 1.0 |
| Top1 岗位准确率 | 1.0 |
| Trajectory V2 通过率 | 1.0 |
| Artifact 通过率 | 1.0 |
| LangGraph 通过率 | 1.0 |
| 投递包通过率 | 1.0 |
| 预期 Fit Gate 阻断 | 3/3 |

运行使用 `LLM_API_KEY=''`、`LLM_FALLBACK_ENABLED=true`、hash embedding 和关闭 reranker，只验证编排与控制面，不产生 DeepSeek Token，也不能作为真实模型质量指标。

接口：

```http
POST /evaluations/agent-full-flow
```

评测内容：

- 通过 guided profile 创建候选人档案和简历 chunk。
- 使用可控岗位源写入岗位、JD chunk 和向量索引。
- 运行 `find_jobs_for_profile`，检查 Top1 岗位和匹配分数区间。
- 对需要定制的 case 运行 `tailor_resume_for_job`，检查 Guardrail 和关键词覆盖。
- 对需要投递的 case 运行 `quick_apply`；弱匹配 case 期望被 `fit_gate` 阻断。
- 检查每个 Agent run 是否生成 `execution_plan` artifact，并记录完整 step trace。
- 检查 `execution_plan.orchestration_framework=langgraph` 和 `output_json.orchestration_framework=langgraph`，确保全流程评测覆盖 LangGraph 主编排入口。
- 检查 `agent_events` 中是否出现 LangGraph node start/end、step completed、run finished 等事件，避免只凭最终结果判断流程健康。
- 自动化评测会显式传入 `application_confirmed=true`，避免人工确认 interrupt 阻塞批量回归；真实用户默认仍会在投递包生成前等待确认。

最新离线全流程结果：

| 指标 | 结果 |
| --- | ---: |
| case_count | 6 |
| pass_rate | 1.0000 |
| completed_rate | 1.0000 |
| top_job_accuracy | 1.0000 |
| score_gate_accuracy | 1.0000 |
| tailor_pass_rate | 1.0000 |
| quick_apply_pass_rate | 1.0000 |
| application_packet_pass_rate | 1.0000 |
| fit_gate_block_count | 3 |
| trace_pass_rate | 1.0000 |
| artifact_pass_rate | 1.0000 |
| langgraph_pass_rate | 1.0000 |
| avg_top_job_score | 57.3650 |
| avg_ranking_margin | 29.8817 |
| release_gate.passed | true |

全流程发布门禁要求 pass/completed、Top1、分数、Trace、Artifact 和 LangGraph 完整率全部为 1.0，并且至少有一个弱匹配 case 被 fit gate 正确阻断。这样“六条流程都跑完”不能掩盖审批或可观测性缺失。

本轮暴露并修复的问题：

- Guided profile 的 `raw_text` 会包含 headline 和 `Target roles`，如果直接参与匹配，会把“想做某岗位”误判成“做过某岗位”。已改为 support text 和 profile chunk 都过滤目标意向、headline、邮箱等元信息。
- `No MLflow or feature store experience` 这类否定证据必须覆盖关键词命中。匹配器现在在句子级识别 `no/not/without/lacks/missing/did not build/coursework/read articles` 等负面证据。
- 重复运行评测时，评测岗位 external_id 曾经撞 SQLite 唯一约束。现在每次 Agent full-flow evaluation 都会生成唯一 namespace，原始 ID 仍保存在 `eval_external_id`。
- 推荐算法和 ML 平台两个弱匹配 case 被重新标注为“可分析/可定制，但不可一键投递”，更符合真实求职风险控制。

## 中文岗位排序评测

接口：

```http
POST /evaluations/job-relevance
```

评测内容：

- 使用 `evals/job_relevance_cases.json`，覆盖 13 个 query、130 个候选岗位。
- 中文主场景包括 Agent 开发实习、智能体校招、RAG 平台、AI Agent 产品、推荐算法、后端 FastAPI、LLM 评测、大模型安全、数据开发、Agent 工程师、AI 产品经理、Prompt 工程；英文 Agent Development Intern 只作为辅助样例。
- 每个候选岗位有 0-4 级人工相关性标注：4 为最匹配，3 为强匹配，2 为相关但有关键缺口，1 为相邻岗位，0 为噪声。
- 不访问外部招聘站，不调用 LLM，只评估 source 层确定性排序算法，避免网络波动或模型输出掩盖排序问题。
- `case_results_json` 保留每个候选岗位的 `rank`、`grade`、`score` 和 `reasons`，用于定位具体误排原因。

核心指标：

| 指标 | 含义 |
| --- | --- |
| `top1_accuracy` | 每个 query 的第一名是否为最高人工 grade。 |
| `avg_top3_recall` | Top3 是否召回所有 grade >= 3 的强相关岗位。 |
| `avg_mrr` | 第一个强相关岗位的平均倒数排名。 |
| `avg_ndcg_at_5` | 使用 0-4 级 relevance 的 graded nDCG@5。 |
| `low_grade_above_strong_count` | grade <= 1 的噪声排在强相关岗位前面的次数。 |

最新离线评测：

| 指标 | 结果 |
| --- | ---: |
| case_count | 13 |
| candidate_count | 130 |
| pass_rate | 1.0000 |
| top1_accuracy | 1.0000 |
| avg_top3_recall | 1.0000 |
| avg_top5_recall | 1.0000 |
| avg_mrr | 1.0000 |
| avg_ndcg_at_5 | 0.9495 |
| low_grade_above_strong_count | 0 |
| release_gate.passed | true |

排序发布门禁要求 pass/top1/Top3/MRR 均 >=0.90、nDCG@5 >=0.90，并且不允许低相关岗位排在强相关岗位前。

本轮首次运行暴露出 `推荐算法实习生` case 的 `top3_recall=0.5000`：`排序模型实习生` 是强相关同义岗位，但旧规则把“开发/工程”这种泛技术信号排得过高，导致 `数据开发实习生`、`Agent开发实习生` 这类低相关岗位压过强相关岗位。修复方式是在 source 排序中加入领域意图 boost，包括算法/推荐、后端/API、数据开发、安全、评测和 Prompt；修复后该 case 的 `top3_recall=1.0000`、`nDCG@5=0.9698`，整体评测状态为 `completed`。

## 投递包 Guardrail 评测

接口：

```http
POST /evaluations/application-packet
```

评测内容：

- 使用 `evals/application_packet_cases.json`，覆盖 27 个中文为主投递包 case。
- 正例包括 Agent、前端、数据、产品等非单一岗位投递包，验证动态 fallback 不再硬编码 Agent/RAG/FastAPI/SQLite。
- 反例包括编造 MLflow/Kubernetes、非技能经历、数字指标和结果类声明，非 Agent 岗位硬写 Agent 经验、缺少目标岗位、负面披露误判以及越过人工确认边界。
- 缺少投递链接和外联文案过短当前作为 warning，不直接阻断。
- 不调用外部招聘站，也不调用 LLM，只验证投递包最后一公里的事实校验和自动化边界。

核心指标：

| 指标 | 含义 |
| --- | --- |
| `high_risk_recall` | 应阻断的高风险投递包被正确阻断的比例。 |
| `false_block_count` | 正常投递包被误拦截的数量。 |
| `false_block_rate` | 正常投递包被误拦截的比例。 |
| `missed_high_risk_count` | 高风险投递包漏拦截的数量。 |
| `missed_high_risk_rate` | 高风险投递包漏拦截的比例。 |
| `issue_code_hit_rate` | 期望 issue code 是否被正确命中。 |
| `avg_warning_count` | 每个 case 平均 warning 数量。 |

最新离线评测：

| 指标 | 结果 |
| --- | ---: |
| case_count | 27 |
| pass_rate | 1.0000 |
| high_risk_recall | 1.0000 |
| false_block_count | 0 |
| false_block_rate | 0.0000 |
| missed_high_risk_count | 0 |
| missed_high_risk_rate | 0.0000 |
| issue_code_hit_rate | 1.0000 |
| release_gate.passed | true |

本轮暴露并修复两个问题：句级 claim 检查会把“申请 Agent 岗位，我有 Python 经验”中的目标岗位 `Agent` 也当成候选人自述能力，造成误拦截；高 embedding 相似度也可能让真实实现掩盖同句新增的“可靠性提升”。当前按子句识别 claim scope，跨语言恢复除相似度外还检查否定极性与结果语义组；`ApplicationPacketGuardrail` 同时检查语义经历、数字来源、缺目标岗位和自动提交边界。最新离线全量为评测 `#82`。

## 面试准备包评测

接口：

```http
POST /evaluations/interview-prep
```

评测内容：

- 使用 `evals/interview_prep_cases.json`，覆盖 9 个中文为主 case，少量英文岗位作为辅助；其中 1 个 case 带已导入牛客网面经文本。
- 覆盖 Agent 开发、前端、数据开发、推荐算法、AI 产品、ML 平台、弱匹配 Agent 候选人和英文 LLM Application 岗位。
- 检查题组是否包含同岗位面经与高频追问、简历项目技术栈追问、LLM 项目实现追问、LLM 八股与基础追问、通用面试与行为问题。
- 检查 `research_checklist_json` 是否生成牛客网、OfferShow、小红书等同岗位面经调研 query。
- 检查已导入面经是否进入 `source_evidence_json`，并生成 `source_backed_interview_experience` 问题。
- 检查缺口技能是否进入 `gap_drills_json`，避免把 `没有 MLflow`、`没有 Kubernetes 集群维护经验` 这类缺口披露误写成已掌握。
- 检查每道题是否有唯一 `question_id`，并且 `source_perspective` 覆盖同岗位面经/面经调研、简历项目技术栈和其他可能面试问题。
- 检查每道题是否带有 `preparation_angle`，且 `summary_json.preparation_angles` 和 `coverage_json.preparation_angle_counts` 显式覆盖网上同岗位面经、简历项目技术栈、其他可能面试问题三类准备角度。
- 检查 `summary_json.question_quality`：本地可解释 judge 会衡量 JD 贴合、连续追问深度、缺口诚实边界、项目绑定、证据可追溯、回答框架行动性、参考答案可用性和重复率。当前门禁要求存在非空的已验证回答框架与 claims、`reference_answer` 达到 `INTERVIEW_RAG_MIN_ANSWER_CHARS`，且不含 TODO 或“请自行补充”等占位语；不再用固定段数、句号数或强制第一人称误杀技术说明。release 阈值仍要求 `actionability >= 0.9` 且 `reference_answer_usability >= 0.9`。
- 检查 `summary_json.interview_reference_links` 和 Markdown 是否包含面经参考标题/链接或搜索入口；外部平台正文难以获取时，不把抓正文作为核心通过条件。
- 检查 Markdown 交付包是否可渲染，且包含问题来源分布、准备角度、面经参考链接、连续追问、外部调研清单和证据边界。

核心指标：

| 指标 | 含义 |
| --- | --- |
| `category_pass_rate` | 必需题组是否完整覆盖。 |
| `research_source_pass_rate` | 牛客网、OfferShow、小红书调研线索是否完整生成。 |
| `gap_drill_pass_rate` | 缺口技能是否进入诚实披露 drill。 |
| `source_backed_pass_rate` | 提供已导入面经的 case 是否生成来源支撑问题。 |
| `question_id_pass_rate` | 每道题是否有稳定且唯一的题目 ID。 |
| `source_perspective_pass_rate` | 是否同时覆盖同岗位面经、简历项目技术栈和其他可能面试问题。 |
| `preparation_angle_pass_rate` | 是否显式覆盖网上同岗位面经、简历项目技术栈、其他可能面试问题三类准备角度。 |
| `llm_question_generation_pass_rate` | 是否生成 LLM 项目实现追问和 LLM 八股/基础追问，并且每类至少有可用问题。 |
| `question_quality_pass_rate` | 面试题质量 judge 是否通过，要求题目贴合 JD、带连续追问、缺口技能有诚实边界、项目题绑定简历证据。 |
| `avg_question_quality_score` | 面试题质量 judge 平均分，范围 0-1。 |
| `markdown_export_pass_rate` | Markdown 交付包是否包含来源分布、准备角度、面经参考链接、连续追问、调研清单和证据边界。 |
| `avg_source_backed_question_count` | 每个面试包平均来源支撑问题数。 |
| `avg_question_count` | 每个面试包平均题目数。 |
| `avg_required_skill_coverage_rate` | JD 必备技能是否被题目或缺口 drill 覆盖。 |

最新离线评测：

| 指标 | 结果 |
| --- | ---: |
| case_count | 9 |
| pass_rate | 1.0000 |
| category_pass_rate | 1.0000 |
| research_source_pass_rate | 1.0000 |
| gap_drill_pass_rate | 1.0000 |
| source_backed_pass_rate | 1.0000 |
| experience_site_pass_rate | 1.0000 |
| question_id_pass_rate | 1.0000 |
| source_perspective_pass_rate | 1.0000 |
| preparation_angle_pass_rate | 1.0000 |
| llm_question_generation_pass_rate | 1.0000 |
| question_quality_pass_rate | 1.0000 |
| avg_question_quality_score | 1.0000 |
| markdown_export_pass_rate | 1.0000 |
| avg_question_count | 10.0000 |
| avg_research_item_count | 4.0000 |
| avg_source_backed_experience_count | 0.1111 |
| avg_source_backed_question_count | 0.3333 |
| avg_required_skill_coverage_rate | 0.9778 |

本轮质量门禁使用本地可解释契约，而不是再增加一次 LLM-as-judge。面试语义验证已经由批量 entailment 调用完成，release gate 需要稳定、低成本、可离线回归。10 题模式要求 JD 必备技能覆盖率至少 80%，明确缺口必须 100% 进入诚实披露 drill；题组标签允许“工程协作与落地”作为“通用面试与行为问题”的细分类别。修复后 `question_quality_pass_rate=1.0000`，`avg_question_quality_score=1.0000`。

历史暴露并修复的问题：中文句号没有参与句子切分时，`没有 MLflow 生产经验` 会和前一句“构建 CareerAgent”粘在一起；同时“没有 Kubernetes 集群维护经验”会同时命中否定词“没有”和正向词“维护”。修复后 matcher 使用中文/英文标点切分句子，并让否定证据优先级高于正向动作词。

新增 source-backed 面经 case 后又暴露两个边界：第一，真实粘贴的面经常是一整段短文本，多个问题之间不一定换行，抽取器必须先按中文/英文问号、句号、分号切句，再判断是否为问题；第二，评测运行在持久 SQLite 时，历史导入的面经可能被后续 case 自动检索到，造成评测污染。修复后 `experience_ids=None` 表示产品自动检索相关面经，`experience_ids=[]` 表示评测隔离空集合。

## 面经来源 Smoke

接口：

```http
POST /evaluations/interview-source-smoke
POST /evaluations/interview-source-smoke?query=Agent%20%E5%BC%80%E5%8F%91%E5%AE%9E%E4%B9%A0%E7%94%9F%20%E9%9D%A2%E7%BB%8F&limit=5&sources=nowcoder
```

评测内容：

- 并发探测牛客网、OfferShow、小红书公开搜索页。
- 不绕过登录、不处理反爬、不抓取需要授权的正文，也不把 smoke 结果写入 `interview_experiences`。
- 每个 source 记录 `status`、`source_reachable`、`result_count`、`latency_ms`、`error` 和 `sample_experiences`。
- 把可达但空结果、搜索页客户端渲染、登录限制、低质量结果和网络错误都保留为 source 层指标，不影响面试包核心回归。

核心指标：

| 指标 | 含义 |
| --- | --- |
| `reachable_source_rate` | 可访问的面经来源比例。 |
| `result_source_rate` | 返回至少一个候选结果的来源比例。 |
| `total_result_count` | 本次 smoke 返回的候选面经总数。 |
| `url_rate` | 候选结果里包含可追踪 URL 的比例。 |
| `interview_signal_rate` | 候选结果里命中面经、面试、一面、二面、笔试、追问等信号的比例。 |
| `query_relevance_rate` | 候选结果里命中当前岗位 query token 的比例。 |
| `content_extractable_rate` | 候选结果是否至少有可用于摘要或导入的标题/摘要文本。 |
| `source_error_count` | 发生网络、登录、反爬或解析异常的来源数量。 |
| `source_empty` | 可达但没有返回候选结果的来源列表。 |

该评测的定位是面经 source 层探针：它可以暴露“牛客网可访问但无结果”“小红书需要登录/客户端渲染”“OfferShow 搜索页结构变化”等问题，但不会替代 `interview_prep` 的可重复生成质量评测。真实结果质量足够时，下一步才应进入“导入为 source-backed 面经证据”的人工确认流程。

`/ui/evaluations` 提供面经源探测工作台，可以填写 query、limit 和 source 列表运行 `interview-source-smoke`，并展示最新 summary、source errors、空源、source 级耗时和样例结果。页面里的“填入导入草稿”只会把候选标题、URL 和摘要预填到人工确认表单；用户必须补全真实面经正文后再提交到 `interview_experiences`。导入成功后页面会显示新建面经 ID、抽取题目数、主题和“用该面经生成面试包”入口，该入口会把 `experience_ids` 带到 `/ui/interview-prep` 并预填表单。这个页面面向开发和调试，不把外部平台失败解释成核心 Agent 失败，也不把搜索摘要自动当作 source-backed 证据。

## 真实岗位源 Smoke

接口：

```http
POST /evaluations/real-job-source-smoke
POST /evaluations/real-job-source-smoke?query=Agent%20%E5%BC%80%E5%8F%91%E5%AE%9E%E4%B9%A0%E7%94%9F&limit=8&sources=tencent&sources=baidu&sources=meituan&sources=bytedance&sources=alibaba
```

评测内容：

- 并发访问腾讯、百度、美团、字节跳动和阿里巴巴五个真实岗位源；Lever/Greenhouse 这类海外 ATS 不参与默认中文评测，只能在显式英文辅助场景下单独评测。
- 字节由 Playwright 触发官网动态签名请求并读取结构化 JSON；阿里先动态发现 2027 届、日常和研究型实习批次，再并发搜索完整 JD。
- 对每个 source 单独记录 `status`、`source_reachable`、`result_count`、`latency_ms`、`error` 和 `sample_jobs`。
- 不调用 LLM 解析 JD，不写入主岗位库，只评估 source 层健康度，避免 LLM、embedding 或数据库状态掩盖招聘源问题。
- 网络失败、招聘站接口变化、空结果都会进入 `source_errors` 或 `source_unavailable`，不污染 `agent_full_flow` 的核心 pass rate。
- 如果所有 source 可达但部分 source 对当前 query 为空，summary 状态为 `completed_with_empty_sources`，并通过 `result_source_rate` 暴露空源比例。

核心指标：

| 指标 | 含义 |
| --- | --- |
| `reachable_source_rate` | 可访问的岗位源比例。 |
| `result_source_rate` | 返回至少一个岗位的岗位源比例。 |
| `total_result_count` | 本次 smoke 返回的岗位总数。 |
| `non_empty_jd_rate` | 返回岗位里 JD 文本非空的比例。 |
| `apply_url_rate` | 返回岗位里包含投递链接的比例。 |
| `internship_like_rate` | 返回岗位里标题、类型或 JD 命中 intern/实习/校招等信号的比例。 |
| `query_relevance_rate` | 返回岗位里标题、类型或 JD 命中当前 query token 的比例。 |
| `agent_related_rate` | 返回岗位里命中 Agent/RAG/LLM/AI/大模型/智能体等信号的比例。 |
| `avg_relevance_score` | Source 层确定性中文相关性排序的平均分，越高代表越贴近当前 query 意图。 |
| `avg_top_relevance_score` | 每个非空 source 的第一名岗位相关性分数均值。 |
| `source_error_count` | 发生异常的岗位源数量。 |

该评测的定位是 source 层真实环境探针：它可以暴露外部招聘站波动，但不会替代可控岗位源下的 Agent full-flow 回归。

最新真实 smoke：

| 指标 | 结果 |
| --- | ---: |
| query | `Agent 开发实习生` |
| sources | `tencent, baidu, meituan, bytedance, alibaba` |
| status | `completed` |
| reachable_source_rate | 1.0000 |
| result_source_rate | 1.0000 |
| total_result_count | 40 |
| non_empty_jd_rate | 1.0000 |
| apply_url_rate | 1.0000 |
| internship_like_rate | 0.8500 |
| query_relevance_rate | 1.0000 |
| agent_related_rate | 0.9750 |
| avg_relevance_score | 26.0850 |
| avg_top_relevance_score | 29.7400 |
| source_error_count | 0 |

五源 top sample：

| Source | 岗位 | relevance_score |
| --- | --- | ---: |
| 腾讯 | Agent Evaluation Intern | 26.9000 |
| 百度 | Agent策略算法实习生 | 29.2000 |
| 美团 | 大模型应用开发实习生 | 30.8000 |
| 字节 | Agent开发实习生-开发者服务 | 31.8000 |
| 阿里 | 研究型实习生-高性能算子生成 Agent 研发 | 30.5000 |

本次五源运行是 EvaluationRun `#40`，总耗时 6905ms。8 类查询的完整 suite 对应 `#40-#47`，共返回 316 条岗位，8/8 case 通过；每个 case 的五源可达率、五源有结果率、JD 非空率、投递链接率和 query relevance 都是 1.0000，实习率为 0.7778-0.9000，Agent 相关率为 0.8333-1.0000，单 case 总耗时 6.5-7.6 秒。详细接入边界和站点筛选记录见 `docs/REAL_JOB_SOURCES.md`。

## 真实 JD Ingest Smoke

接口：

```http
POST /evaluations/real-job-ingest-smoke
POST /evaluations/real-job-ingest-smoke?query=Agent%20%E5%BC%80%E5%8F%91%E5%AE%9E%E4%B9%A0%E7%94%9F&limit=1&sources=tencent
```

评测内容：

- 先访问真实岗位源获取 posting，再对每条 posting 跑 JD parser。
- 将解析后的 JD upsert 到 `jobs`，切分并写入 `job_chunks`。
- 对写入后的 JD chunk 执行一次 retrieval probe，确认索引可检索。
- 每条岗位单独记录 `parse_error`、`ingest_error`、`chunk_count`、`chunk_types`、`required_skill_count`、`retrieved_chunk_preview`。
- 对 query、title 和原始 JD 执行 parser quality probe，检查保守识别出的核心技能是否进入 structured JD。
- 记录 `embedding_provider_counts`、`retrieval_query_embedding_provider_counts`、`reranker_provider_counts` 和 fallback job count，用于区分真实模型链路与 hash/heuristic 降级链路。

核心指标：

| 指标 | 含义 |
| --- | --- |
| `parse_success_rate` | JD 结构化解析成功比例。 |
| `ingest_success_rate` | 解析后写入 `jobs` 成功比例。 |
| `chunk_index_success_rate` | 写入 `job_chunks` 且 chunk 数量非零的比例。 |
| `retrieval_probe_success_rate` | 对新写入 JD 执行检索 probe 能返回结果的比例。 |
| `parser_quality_evaluable_count` | query/title/JD 中能提取到核心技能、可以评估 parser 质量的岗位数。 |
| `parser_quality_pass_rate` | parser quality probe 通过比例。 |
| `avg_parser_quality_required_recall` | 原始 JD 核心技能进入 `required_skills` 的平均召回。 |
| `avg_parser_quality_structured_recall` | 原始 JD 核心技能进入 required/preferred/keywords 任意结构化字段的平均召回。 |
| `avg_parser_quality_query_coverage` | query/title 中核心技能进入 structured JD 的平均覆盖率。 |
| `parser_quality_failure_count` | 入库成功但 parser quality probe 失败的岗位数。 |
| `avg_chunks_per_job` | 每个真实 JD 平均切分出的 chunk 数。 |
| `avg_required_skill_count` | 每个真实 JD 解析出的 required skills 平均数量。 |
| `embedding_provider_counts` | JD chunk 写入时使用的 embedding provider 分布。 |
| `reranker_provider_counts` | retrieval probe 使用的 reranker provider 分布。 |
| `embedding_fallback_job_count` | 写入阶段出现 embedding fallback 的岗位数。 |
| `retrieval_fallback_job_count` | 检索阶段出现 embedding/reranker fallback 的岗位数。 |

最新真实 ingest smoke：

| 指标 | 结果 |
| --- | ---: |
| query | `Agent 开发实习生` |
| sources | `tencent` |
| limit | 1 |
| status | `completed` |
| parse_success_rate | 1.0000 |
| ingest_success_rate | 1.0000 |
| chunk_index_success_rate | 1.0000 |
| retrieval_probe_success_rate | 1.0000 |
| parser_quality_evaluable_count | 1 |
| parser_quality_pass_rate | 1.0000 |
| avg_parser_quality_required_recall | 1.0000 |
| avg_parser_quality_structured_recall | 1.0000 |
| avg_parser_quality_query_coverage | 1.0000 |
| parser_quality_failure_count | 0 |
| avg_chunks_per_job | 8.0000 |
| avg_required_skill_count | 3.0000 |
| avg_keyword_count | 46.0000 |
| embedding_provider_counts | `sentence_transformers: 8` |
| retrieval_query_embedding_provider_counts | `sentence_transformers: 3` |
| reranker_provider_counts | `cross_encoder: 3` |
| embedding_fallback_job_count | 0 |
| retrieval_fallback_job_count | 0 |

本次真实运行说明：中文 query `Agent 开发实习生` 可以通过腾讯真实岗位源进入 LLM parser、SQLite/`job_chunks`、embedding/reranker 和 retrieval probe，parser quality probe 可以确认 `Agent`、`Python`、`SQL` 三个核心技能进入 structured JD。首次接入 quality probe 时真实运行暴露出 LLM parser 只返回 `Python`、`SQL`，漏掉标题和职责中的 `Agent`；修复为 LLM 输出与 heuristic 输出的有序并集合并后，中文复测通过。运行时仍可能出现 `Transformer cache_dir argument is deprecated` 的第三方兼容层告警，不影响本次 ingest 指标。

## LLM 实景流程评测

接口：

```http
POST /evaluations/llm-workflow
```

评测内容：

- 真实调用 LLM 解析简历。
- 真实调用 LLM 解析 JD。
- 基于 SQLite chunk 和 RAG 证据做岗位匹配与 evidence retrieval。
- 真实调用 LLM 判断岗位适配度，要求返回 strict JSON。
- 对标记为 `run_tailor=true` 的案例真实调用简历定制流程。
- 简历与 JD parser 分别执行字段/语句 grounding；RAG 检查 Top3/Top5、负向证据和 chunk 引用完整性。
- fit judge 校验标签/分数一致性、候选人证据、JD 差距和用户消息。用户消息只由已验证的 `matched_evidence` 与 `gaps` 组合，模型原话保留在 trace。
- 使用 Guardrail 验证是否引入未支持数字、语义 claim 和禁止 claim；高风险定制最多执行一次可追踪 ReAct repair。
- 不做静默 fallback；失败 case 记录 `failed_stage` 和异常类型，LLM 调用日志记录 prompt/response/error trace。
- Resume parser 和 JD parser 在真实 LLM 评测中会把空返回/超时/服务端断连记录为 `.retry_1`、`.retry_2`；JD 截断或非法 JSON 会记录 `jd_parser.parse_jd.repair_json`，便于区分模型波动、输出格式损坏和业务解析失败。
- `EvaluationRun` 会在评测开始时创建，之后每完成一个 case 就更新 `summary_json` 和 `case_results_json`。
- 每个 case 带 `stage_trace`，记录 resume parse、JD parse、match/RAG、fit judge、tailor 的中间摘要。
- `/ui/evaluations` 可以直接运行 smoke 级 LLM workflow，并展示最新评测的 summary、逐 case stage trace、失败阶段和当前 `evaluation_run_id` 关联的 LLM 调用日志、retry/repair 计数；这样真实失败可以先在页面定位，再进入 SQLite 或 JSONL 深挖。
- 开发脚本可传 `trace_path` 写 JSONL，即使长跑被中断，也能看到已经完成 case 的中间结果。
- `resume_from_last_completed=true` 时，评测会从 JSONL trace 中读取连续完成的 case 前缀，并从第一个缺失 case 继续跑；新 trace 事件会写入完整 `case_result`，因此恢复后仍能保留每个阶段的中间结果。
- `tailor_resume` stage 会记录 `react_repair` 元数据；如果触发修复，可以看到触发风险、问题类型、使用工具、修复后风险和二次 Guardrail 是否通过。

量化指标：

| 指标 | 含义 |
| --- | --- |
| `completed_rate` | 端到端流程完成率。 |
| `end_to_end_pass_rate` | 全流程验收通过率。 |
| `resume_parse_success_rate` | 简历结构化解析成功率。 |
| `profile_grounding_gate_pass_rate` | 简历字段能否回指原始简历。 |
| `avg_profile_field_grounding_rate` | 简历已评估字段的平均支持率。 |
| `avg_profile_skill_recall` | 结构化 Profile 对期望技能的召回。 |
| `jd_parse_success_rate` | JD 结构化解析成功率。 |
| `jd_grounding_gate_pass_rate` | JD 技能、语句和元数据 grounding 通过率。 |
| `avg_jd_skill_recall` | 结构化 JD 对期望技能的召回。 |
| `fit_label_accuracy` | LLM 适配度标签准确率。 |
| `fit_score_in_range_rate` | LLM 分数是否落入人工期望区间。 |
| `avg_fit_score_range_error` | 分数超出期望区间时的平均偏差。 |
| `avg_matcher_top3_evidence_hit_rate` / `avg_matcher_top5_evidence_hit_rate` | 前 K 条证据是否覆盖人工标注关键词。 |
| `avg_negative_evidence_hit_rate` | “未实现/课程/计划学习”等负向证据是否被召回。 |
| `evidence_integrity_pass_rate` | 每条检索证据是否有稳定 chunk ID 和正文。 |
| `fit_explanation_grounding_pass_rate` | 匹配证据、JD 差距、发布消息与标签分数是否共同通过。 |
| `fit_message_grounding_pass_rate` | 面向用户发布的适配说明是否只来自已验证结构化事实。 |
| `tailor_success_rate` | 简历定制调用成功率。 |
| `tailor_pass_rate` | 定制简历同时通过 Guardrail、关键词覆盖和禁止 claim 检查的比例。 |
| `guardrail_pass_rate` | Guardrail 通过率。 |
| `forbidden_claim_free_rate` | 没有出现禁止 claim 的比例。 |
| `tailor_semantic_grounding_pass_rate` | 定制简历的语义成果 claim 是否能回指原简历。 |
| `context_compression` | fit judge 与 tailor 阶段的压缩上下文数量、平均压缩率和保留证据数。 |
| `difficulty_breakdown` | 按 easy/medium/hard/adversarial 分桶的指标。 |

### 2026-07-22 新门控的真实分层结果

这轮先按风险与失败类型分层取样，再只复测失败 case；它证明门控有效并证明修复后的样本通过，但不等于 24-case Flash 全量发布认证。

| 评测 | 首轮 | 修复后定向复测 | 关键结论 |
| --- | --- | --- | --- |
| 自然语言规划 | `#56`：6 case，pass=0.6667，intent/action=1.0，needs_profile=0.6667 | `#59`：2/2，release gate 通过 | 修正动作依赖语义，不再把“是否已上传”混作“流程是否需要”。 |
| JD Parser | `#57`：4 case，pass=0.5，precision=0.8422 | `#60`：2/2，precision/recall/F1/grounding=1.0 | 中英文技能和 job type 已统一归一化。 |
| LLM workflow | `#58`：2 case，端到端=0.5，fit grounding=0.5 | `#61`：1/1，端到端和 release gate 均通过 | 修正错误 gold，禁止从 JD 外推“缺少生产/规模/实习经验”；定制一次 repair 后通过。 |
| 真实投递材料 | 首次被 `unsupported_claims` 阻断 | Application `#25` 为 ready、low risk、语义 grounding=1.0 | 修正目标岗位与能力 claim 的子句作用域，并禁止在求职信加入计划学习。 |

本轮真实日志 `#1288-#1317` 共 30 次完成调用，输入 25,897、输出 6,680、合计 32,577 tokens，provider latency 累计 112.145 秒。当前 24-case 全量数据仍应通过 checkpoint 分批执行，不能把上述分层复测写成全量成功。

### 历史 DeepSeek V4 Pro 18-case 全量基线

| 指标 | 结果 |
| --- | ---: |
| case_count | 18 |
| completed_rate | 1.0000 |
| end_to_end_pass_rate | 1.0000 |
| resume_parse_success_rate | 1.0000 |
| jd_parse_success_rate | 1.0000 |
| fit_judge_success_rate | 1.0000 |
| fit_label_accuracy | 1.0000 |
| fit_score_in_range_rate | 1.0000 |
| avg_fit_score_range_error | 0.0000 |
| avg_matcher_evidence_hit_rate | 1.0000 |
| tailor_case_count | 13 |
| tailor_success_rate | 1.0000 |
| tailor_pass_rate | 1.0000 |
| guardrail_pass_rate | 1.0000 |
| forbidden_claim_free_rate | 1.0000 |
| avg_hallucination_count | 0.0000 |
| avg_tailor_reduction_ratio | 0.6011 |
| avg_tailor_retained_evidence_count | 6.0769 |

运行配置：`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-v4-pro`、`LLM_THINKING_MODE=auto`、`LLM_FALLBACK_ENABLED=false`。本次 run 关联 LLM 调用 68 次，68 次完成，0 次失败，1 次 repair；trace 文件为 `data/runtime/llm_workflow_trace_deepseek_v4_full_rerun.jsonl`。

### DeepSeek V4 Flash 有限全链路替换实验

2026-07-22 使用 `deepseek-v4-flash` 进行分层真实验证。该结果用于判断模型路由，不替代上面的 18-case Pro 发布基线。

| 能力 | 真实样例结果 | 结论 |
| --- | --- | --- |
| 简历/JD 解析、RAG、适配判断 | strong/partial/weak 三个 case 全通过，预测分别为 strong_fit=92、partial_fit=65、weak_fit=20 | 可用。 |
| 定制简历与 Guardrail | 两个需要定制的 case 均通过，禁止 claim 与幻觉计数为 0 | 可用。 |
| PDF 建档 | 姓名、11 个技能、项目与 1,203 字原文落库 | 可用。 |
| 自然语言入口 | 修复显式否定优先级后，“不要定制/搜索/投递”只执行建档 | 可用，但必须保留确定性权限约束。 |
| 简历评分建议 | 模型生成 5 条建议，但全部未满足简历原文 grounding 契约；系统拒绝后发布 3 条安全建议 | 安全但可用性不足。 |
| 投递材料与人工审批 | LangGraph interrupt、确认恢复、packet validation 和 LLM trace 均通过 | 可用。 |
| 面试 Agentic RAG | 两次均未通过发布：第一次超 Prompt 预算，第二次被证据 verifier 拒绝 | 不可直接替换 Pro。 |

可追踪调用共 34 条，输入 75,229、输出 20,967、合计 96,196 tokens；其中面试两次失败使用 71,205 tokens。第二次面试在 claims 状态压缩后为 35,173 tokens、约 77.2 秒；对照 Pro 成功基线为 30,478 tokens、83.07 秒，因此 Flash 没有获得端到端成本优势，且输出质量未达到发布要求。

本实验修复了最终 Top5 只保留来源配额、不保留第一阶段检索通道头部的问题。架构题离线重放后，BM25 排名第一的项目事实卡能和 reranker 首位文档同时进入 Top5。该检索修复尚未进行第三次付费 Flash 面试重跑，因此当前发布结论仍是：保留 Pro 默认模型，后续通过显式 workflow 路由将 Flash 用于低风险、短上下文节点。

最新全量分桶结果：

| 难度 | Case 数 | 完成率 | 端到端通过率 | Fit 标签准确率 | Fit 分数区间命中 | Tailor 通过率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| easy | 9 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| medium | 6 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| hard | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| adversarial | 1 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 不适用 |

旧全量基线保留为长期对照：引入分级上下文压缩前曾出现 `completed_rate=0.9444`、`end_to_end_pass_rate=0.8889`、`tailor_pass_rate=0.9286`。这组历史结果证明后续压缩、标注校准和 retry 修复确实改善了真实链路。

上一轮较重压缩后的真实 smoke 结果：

| 评测 | Case | 覆盖 | 结果 |
| --- | ---: | --- | --- |
| 5-case smoke | 5 | strong/partial/weak/hard/adversarial，3 个 tailor case | `completed_rate=1.0000`，`end_to_end_pass_rate=0.8000`，`fit_label_accuracy=0.8000`，`tailor_pass_rate=1.0000`，`guardrail_pass_rate=1.0000` |
| 2-case context smoke | 2 | strong + hard partial 边界，2 个 tailor case | `completed_rate=1.0000`，`end_to_end_pass_rate=0.5000`，`tailor_pass_rate=1.0000`，`avg_tailor_reduction_ratio=0.3614`，`avg_tailor_retained_evidence_count=5.5` |

上一轮曾尝试重跑 18-case 全量真实评测，但 20 分钟命令超时，没有拿到 summary。根因是当时评测服务先把所有 case 放在内存 list 中，最后才创建 `EvaluationRun`。现在已经改为逐 case 落库，并可写 `trace_path`，不再只依赖最终 summary。

最新轻量上下文策略后的真实 trace smoke：

| Case | 难度 | 期望标签 | 预测标签 | 分数 | Case 通过 | Tailor 通过 |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `agent_candidate_strong_agent_role` | easy | `strong_fit` | `strong_fit` | 95 | 是 | 是 |
| `ml_candidate_weak_agent_role` | hard | `weak_fit` | `weak_fit` | 30 | 是 | 未运行 |
| `analytics_candidate_weak_recommendation_role` | hard | `weak_fit` | `weak_fit` | 40 | 是 | 是 |
| `beginner_candidate_weak_agent_role` | adversarial | `weak_fit` | `weak_fit` | 15 | 是 | 未运行 |
| `cv_candidate_partial_ml_platform_role` | medium | `partial_fit` | `partial_fit` | 60 | 是 | 是 |

汇总：

- `completed_rate=1.0000`
- `end_to_end_pass_rate=1.0000`
- `fit_label_accuracy=1.0000`
- `fit_score_in_range_rate=1.0000`
- `tailor_pass_rate=1.0000`
- `guardrail_pass_rate=1.0000`
- `forbidden_claim_free_rate=1.0000`
- `avg_tailor_reduction_ratio=0.4938`
- `avg_tailor_retained_evidence_count=6.3333`
- trace 文件：`data/runtime/llm_workflow_trace_latest.jsonl`

这次 trace 直接显示每个 case 的中间返回：简历解析出的技能、JD 解析出的 required skills、RAG Top evidence、fit judge 标签和分数、tailor guardrail 结果。`ml_candidate_weak_agent_role` 的 RAG Top evidence 明确包含 “did not build an agent system”，`analytics_candidate_weak_recommendation_role` 的 Top evidence 明确包含 “did not implement ranking models or CTR features”，模型判 `weak_fit` 是符合新标注标准的结果。

本轮 ReAct repair 和断点续跑新增验证：

- 先跑 1 个真实 LLM case 写入 `data/runtime/llm_workflow_trace_latest.jsonl`，再用 `resume_from_last_completed=true` 跑 `case_limit=3`，服务正确跳过 1 个已完成 case，`resumed_case_count=1`。
- 3-case resume smoke 结果：`completed_rate=1.0000`、`end_to_end_pass_rate=1.0000`、`fit_label_accuracy=1.0000`、`fit_score_in_range_rate=1.0000`、`tailor_pass_rate=1.0000`、`guardrail_pass_rate=1.0000`。
- trace 中 `match_and_retrieve.details.top_evidence` 已能看到 `metric_evidence`、`missing_skill_disclosure`、`shipped_project`、`generic_skill` 等证据类型和 `positive/negative/neutral` polarity。
- 专门构造的真实 repair smoke 中，初稿包含 `Eager to learn MLflow` 并被 Guardrail 判为 high risk；`resume_tailor.repair_resume` 调用后删除正文缺口披露，二次 Guardrail 变为 `risk_level=low` 且 `passed=true`。
- 真实 18-case 长跑中曾暴露一次 `RemoteProtocolError: Server disconnected without sending a response.`；已给 resume parser 增加有限 retry，复测第 12 个 case 和全量 18-case 均通过。

调试发现：

- 第一轮真实评测中，简历解析会因为 LLM 把 `impact`、`duration` 等叶子字段返回为 `null` 而失败。
- 修复方式是在 Pydantic schema 层把“应为字符串但缺失”的字段归一为空字符串，把列表字段的 `null` 归一为空列表；这不是兜底生成内容，只是接受真实 LLM 常见的缺失表达。
- 修复后，`resume_parse_success_rate` 从 0.7778 提升到 1.0000，`end_to_end_pass_rate` 从 0.6667 提升到 0.8889。
- 剩余 1 个失败是 `agent_candidate_strong_agent_role` 的 `tailor_resume` 阶段 `httpx.ReadTimeout`，说明长 prompt 的简历定制仍需要更好的超时预算或 prompt 压缩。
- 旧标注中 `ml_candidate_partial_agent_role` 被模型判成 `weak_fit`，trace 证明模型依据的是 “did not build an agent system” 这类核心否定证据；该 case 已重标为 `ml_candidate_weak_agent_role`，并作为 partial/weak 边界回归样例。
- 原异常记录使用 `str(exc)`，`ReadTimeout` 会显示为空字符串；已改为记录异常类型和 `repr(exc)`，保证 trace 可追溯。
- 上下文压缩已从过重的多阶段收缩改成 Profile 摘要、JD 摘要、Top evidence 和一次总 prompt packet 预算检查；短小 fit judge 上下文如果因为结构化元数据变大，会用 `expansion_ratio` 单独记录。
- 轻量策略第一轮真实 trace 发现 strong case 的 tailor packet 曾超过 9000 字符预算；修复方式是压缩 evidence metadata，只保留排序调试必要字段，并将预算 trim 调整为更明确的 Top evidence 片段。
- 本轮 5-case 真实 trace 发现 `ranking model` 出现在 “did not implement ranking models” 否定句中时，旧 forbidden claim 检查会误判；已改为否定上下文感知。
- 本轮 5-case 真实 trace 还发现 `A/B testing`、`model evaluation` 与源简历里的 `A/B tests`、`experiment analysis`、`evaluation dashboards` 属于同义证据；Guardrail 已增加技能别名，避免误伤真实证据。
- 简历定制 prompt 已明确要求：缺失 JD 要求只能写入 `keyword_alignment.missing/notes`，不能以 “eager to learn” 等形式写进简历正文。
- 本轮补充了 `resume_tailor` 的 1 轮 ReAct repair loop：Guardrail 高风险时读取 issues、压缩上下文和当前草稿，修复后再次验证，并把 `react_repair` 元数据写入简历版本。
- `match_and_retrieve.details.top_evidence` 已增加 `evidence_type` 和 `polarity`，用于排查 RAG 命中的到底是交付证据、课程噪声还是缺口披露。

## 四层业务验收

三条黄金路径定义在 `evals/golden_demo_scenarios.json`，演示和人工验收步骤见 `docs/GOLDEN_DEMOS.md`。它们把现有评测指标重新组织为面向一次真实 run 的四层检查：

| 层级 | 单次 run 可观测指标 | 对应回归评测 |
| --- | --- | --- |
| 路由层 | Skill/SubAgent 选择、Tool 权限校验 | Agent full-flow 任务覆盖、Planner 权限测试 |
| 过程层 | tool call 数、成功率、repair、幂等复用、延迟 | 全流程 trace 完整率、LLM workflow retry/repair、worker recovery |
| 结果层 | match score、证据覆盖、缺失技能、Guardrail、产物 ID | Job relevance、RAG、PDF Chunk、投递包和面试包评测 |
| 副作用层 | 审批状态、approval bypass、真实外发 artifact | interrupt/resume、高风险工具门禁、审批审计测试 |

`GET /agent/runs/{run_id}/summary` 是单次运行的业务可观测视图，不替代离线标注集，也不把一个 run 的结果包装成模型整体质量。发布判断仍以各评测集的 aggregate metrics 和 release gate 为准。

当前已落库并可展示：

- `match_score`、matched/missing skill 数量。
- `evidence_coverage`、source evidence 数量。
- `guardrail_passed`、risk level、unsupported claim 和 forbidden claim 阻断数。
- repair 数、Tool 成功率、失败 Tool 数、幂等复用和总耗时。
- approval status、外发 artifact 和 approval bypass 检测。

当前没有宣称：

- “定制简历后匹配分提升 X%”：还没有在同一人工标注集上执行修改前后对照实验。
- “投递成功率提升 X%”：没有真实用户投递结果和录用反馈闭环。
- “多 Agent 优于单 Agent”：当前 SubAgent 是职责边界，不是为了增加数量而做的独立自治模型实验。

## 后续优化

- 增加真实 PDF 简历和真实岗位 JD 的人工标注评测集。
- 用真实招聘 JD 和真实候选人简历重新验证 Top5 anchor 是否仍然合理。
- 用真实标注数据校准 evidence type classifier，补充 abandoned prototype、research prototype、internship delivery 等更细类型，必要时增加 LLM verifier 做二次核验。
- 对 LLM fit judge 增加 partial/weak 边界样例，特别是“相邻 ML/LLM 技能但缺少 Agent/RAG 交付”的情况。
- 将 `resume_from_last_completed` 从 JSONL trace 恢复扩展到基于 `EvaluationRun` 的恢复，并在 UI/API 中展示可恢复 checkpoint。
- 继续评估不同 evidence budget 对 Guardrail 和关键词覆盖的影响。
- 增加 LLM-as-judge，但保留人工抽检。
- 在 CI 中设置最低 `fit_label_accuracy`、`top3_recall`、`guardrail_pass_rate` 和 `end_to_end_pass_rate` 阈值。
- 增加同一简历/同一 JD 的定制前后对照集，只有真实计算 score delta 后才在业务摘要展示提升幅度。
- 引入用户反馈表，区分材料生成成功、人工确认、真实投递、面试邀请和录用结果，建立长期 outcome 指标。
