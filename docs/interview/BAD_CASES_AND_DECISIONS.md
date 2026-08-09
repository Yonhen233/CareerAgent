# CareerAgent Bad Case 与设计决策

## 使用方法

面试官问“遇到过什么问题”时，不要只说修过 bug。每个案例按下面顺序讲：

```text
业务影响 -> 最初假设 -> Trace/指标证据 -> 根因 -> 方案取舍 -> 回归结果 -> 剩余边界
```

以下案例都来自项目真实开发过程，历史实现已经被替换的地方会明确说明。

## 案例一：调用越多不等于更 Agentic，面试包一次用了 59 次 LLM

### 现象与影响

旧面试包 `#44` 虽然生成成功，但调用 LLM 59 次，累计 1,490,670 个 Prompt 字符和 237,622 个 Response 字符，墙钟约 504 秒。Verifier 一项就占 37 次调用和 1,080,855 个 Prompt 字符，直接造成余额快速消耗。

### 最初错误假设

当时把检索规划、答案生成、claim 分类、citation linker、entailment、renderer、coverage judge 和 repair 都设计成 LLM 节点，认为职责越细越现代。

### 如何定位

按 `trace_name` 聚合 `llm_call_logs`，发现多个节点反复发送同一题的全部 evidence。特别是 verifier 按 claim 调用，同一 evidence 在一题内重复几十次；renderer 和 coverage judge 又重新消费已经验证过的内容，却没有获得新的外部信息。

### 最终方案

- 检索计划改为本地 multi-query，不再调用 LLM；
- exact/BM25/vector/RRF、来源配额和正文组合全部确定化；
- 10 题一次批量生成 claims，一次批量验证；
- 服务端只组合 verified claims；
- 正常路径 3 次 LLM，最多一轮失败题 repair，总调用不超过 5；
- 增加 70,000 Prompt 字符和 15,000 completion token 预留硬预算，每次 HTTP attempt 都计数。

### 结果与经验

当前成功面试包的典型实测为 10 题、8 次实际 attempt、30,478 tokens、83.07 秒；正常无 repair 的设计上限是 3 次业务调用。最重要的经验不是“压缩 Prompt”，而是按信息增益重画 Agent 边界：没有新信息的节点不应该为了架构图好看而再调用模型。

## 案例二：回答中的每句话都是真的，但没有回答问题

### 现象

用户问“Agent 在架构中的位置、为什么这样选、替代方案是什么”，旧面试答案却介绍 PDF/RAG 评测数据。引用都合法，事实也可能真实，因此单纯 claim grounding 会判通过，但用户无法直接参考。

### 根因

旧 verifier 只判断 `claim -> evidence` 是否蕴含，没有判断 `verified claims -> question` 是否覆盖问题。事实正确和回答相关是两个独立维度。

### 修复

Verifier 输出两套结果：

- `supported`：证据是否直接支持 claim；
- `answer_checks.answered`：通过支持性校验的 claims 是否覆盖问题主干和并列子问题。

对于“如何”要求步骤、组件、字段或数据流；对于“为什么/替代”分别要求理由和替代方案；要求画架构时允许用箭头式可口述数据流，不机械要求图片。

### 验证

14 个 claim verifier case 覆盖支持事实、不支持事实、未来方案伪装经历和答非所问，accuracy、positive recall、specificity 和 question-answering accuracy 都为 1，nonresponsive false accept rate 为 0。

## 案例三：多动作计划看起来正确，执行器却在第一步结束

### 现象

用户说“先根据这些信息建档，再帮我搜索 Agent 实习”。LLM 输出了 `create_profile + search_jobs`，action precision/recall 可以是满分，但主 `intent` 是 `create_profile`。旧执行器在建档分支直接 return，岗位搜索没有发生。

### 根因

计划 schema 同时有单值 intent 和多值 actions，执行器把 intent 当终止分支；评测只检查工具是否出现在计划里，没有检查最终业务状态。

### 修复

- 主 intent 根据最后一个终端业务目标归一；
- actions 按依赖连续执行；
- Prompt 中明确提供的技能和经历必须进入 `profile_patch`，缺失最多一次结构化 repair；
- Profile 更新使用增量合并，不用一次新描述覆盖旧数组；
- 规划评测同时检查 trajectory 和业务终态。

### 验证

真实 Flash 定向回归 `#114` 的三个历史失败 case 全部通过，intent、action precision/recall、必需字段和禁止动作均为 1。

## 案例四：Parser 与 Chunker 单独都合理，组合后却删除了关键证据

### 现象

一份弱候选人简历写着“经历：阅读过 Agent 文章，正在学习 Python，计划做 RAG”。模型把整段误填成 headline。Chunk cleaner 为避免元数据重复，会从原文 chunk 删除 headline，结果“计划学习、没有交付”这些关键负向证据一起消失。

### 根因

Parser 的校验只检查 headline 是否出现在原文，没有检查它是否具有“职位标题”的形态；Chunker 则相信结构化字段。两个局部正确假设组合成跨组件数据损失。

### 修复

- headline 增加形态约束：拒绝过长叙事句和带“项目/经历/技能”等章节前缀的内容；
- 被拒绝字段写入 `rejected_optional_fields`，不发布但可追踪；
- grounding 支持相邻句窗口，处理 PDF 换行拆开的动作与技能；
- 包含“阅读/计划/未实现”的 passage 仍不能作为已交付证据。

### 经验

Parser 评测和 Chunk 评测不能完全独立。需要增加“结构化字段如何反向影响原文清洗”的组合测试。

## 案例五：`Agent` 命中 `AgentTrace`，一个子串把 partial 误抬成 strong

### 现象

候选人做过名为 `AgentTrace` 的日志查看器，但没有真正实现 Agent、Tool Calling 和评测系统。旧 `term in text` 让 `Agent` 命中项目名，导致缺口减少、fit 标签过高。

### 根因

英文技能匹配使用无边界子串；同时 fit 标注没有明确区分课程、相邻项目和真实交付。

### 修复

- 英文/数字技能使用 token 边界，中文使用别名和受控匹配；
- `AgentTrace` 不再证明 Agent，但 `Agent workflow/agents` 仍可命中；
- `fit-rubric-v2` 固定 weak/partial/strong 分数区间和证据要求；
- 模型 raw matched/gaps 先保留，再由双边 verifier 发布 verified 结果；
- 用户消息由 verified evidence/gaps 组合，不直接使用模型自由文案。

### 验证

`#118` 两个历史 fit 失败 case 通过；注入噪声候选人被判 `partial_fit/70`，matched 为 Python/FastAPI/RAG，missing 为 Agent/Tool Calling/Model Evaluation。

## 案例六：跨语言忠实改写被 0.70 阈值误杀

### 现象

原简历英文写着 `Improved component reuse and UI regression coverage`，定制简历中文写“提升组件复用率和 UI 回归覆盖率”。多语言 embedding 支持分为 0.698，低于 0.70，被 Guardrail 阻断。

### 为什么没有直接把阈值改成 0.65

全局降阈值会同时放过“把复用率改写成可靠性提升”等更强、更错误的成果 claim。一个正例过不了，不能证明所有类型都该放宽。

### 修复

保留 0.70 通用阈值；0.65-0.70 只允许两类有第二证据的边界恢复：技术 taxonomy 完全一致，或最佳 evidence 能回指结构化项目/经历字段且成果语义组一致。恢复还必须通过否定极性和结果语义检查，并记录恢复方法。

### 验证

`#119` 前端完整流程 1/1，通过岗位选择、定制、quick apply、投递包、Trace、Artifact 和 LangGraph；同时用“复用率改成可靠性”的反例确认不会放行。

## 案例七：JD 同一行出现“负责”和“要求”，章节解析静默丢正文

### 现象

JD 一行写“负责 Agent 服务开发。要求 Python、FastAPI、RAG 和评测经验”。直接技能抽取能找到全部词，但完整 parser 只留下 Agent。

### 定位过程

逐层调用发现问题不在 taxonomy，而在 `_split_responsibilities`：整行包含“要求”，被识别为 qualification header；`_content_after_header` 只支持冒号，无冒号时返回空，随后整行被 `continue` 丢弃。

### 修复

先按中文句号和分号拆分行内章节；支持无冒号的 `负责/岗位职责/任职要求/Requirements` 前缀，并保留前缀后的正文。测试继续使用噪声原文，没有为了通过而改成整齐多行。

### 经验

结构化 parser 最危险的 bug 往往不是 JSON 解析失败，而是“输出合法但静默少了事实”。因此需要 required skill recall 和 grounding，而不只是 schema validation。

## 案例八：评测器自己制造假失败和额外账单

### 现象

- 单 case JSON 被 PowerShell `ConvertTo-Json` 折叠成对象，运行器把 dict 当列表迭代；
- 只跑一个正例时，release gate 仍要求至少有一个负例被 fit gate 阻断；
- 前台命令超时后 Python 子进程继续运行，随后 resume 进程与原进程重叠，产生 23 次额外调用和 9,169 tokens；
- Windows pytest 临时目录权限造成与业务无关的失败。

### 根因

把评测器当脚本，没有为数据 schema、实验 invocation、子集语义和进程生命周期建立合同。

### 修复

- `_load_case_dataset` 强制根节点数组、case 对象和非空 name；
- release gate 根据当前子集是否包含负例动态决定检查项；
- 每次启动有独立 `evaluation_invocation_id`，跨续跑仍保留 system experiment ID；
- 进度和 case trace 逐步落库，不等最后一次性写 summary；
- 测试临时文件统一进入项目目录。

### 结果

系统报告同时保留原始 194 次/227,511 tokens 和剔除重叠后的 171 次/218,342 tokens。评测器错误没有被藏起来，也没有算作产品正常成本。

## 案例九：LangGraph state 字段未声明，节点输出被静默丢失

### 现象

迁移 LangGraph 后，搜索节点输出了岗位 ID，下一节点却拿不到；另一次恢复请求的 `confirmed/note` 与业务 JSON 同名，出现覆盖风险。

### 根因

LangGraph state 不是任意 dict。跨节点字段必须在 TypedDict/schema 中声明并可序列化；ORM Session 和对象也不能进入 checkpoint。

### 修复

- 所有跨节点字段显式声明并只保存 ID/JSON；
- Session 和 service 通过运行期 `run_id` 映射注入；
- 确认 payload 独立建模；
- checkpoint 使用异步 SQLite 懒初始化；
- 副作用节点使用业务幂等键。

### 验证

测试覆盖实例一停在 interrupt、实例二从 checkpoint 恢复并继续；失败 run 也保留执行计划和 LangGraph 标记。这个案例说明“迁移 LangGraph”最难的是状态与副作用语义，不是画节点。

## 案例十：页面刷新后进度丢失，不只是 localStorage 问题

### 现象

用户切换页面后看不到任务；快速失败的 run 从状态卡消失；Redis 未启动时接口只返回 503，前端拿不到 run ID。

### 根因

浏览器内存和 localStorage 被误当作运行状态来源。终态失败也被 UI 当成“无需展示”，而实际上失败是用户最需要查看的结果。

### 修复

- 运行权威状态放在 SQLite；
- 页面先读本地关注列表，本地为空再查询最近服务端 run；
- completed/failed 保留到用户手动忽略；
- 多 run 在同一卡片内展示并支持一键忽略；
- 入队失败也创建 failed run，写事件并返回 run ID；
- 最近 50 条完整历史可选择查看。

### 经验

前端状态要区分“服务器事实、事件流、浏览器关注偏好”。把一个 run ID 存 localStorage 只能改善体验，不能提供可靠恢复。

## 案例十一：理想化 RAG 样本让错误策略看起来很好

### 现象

第一版样本短、关键词与答案高度重合，多个 Chunk 策略几乎打平，词法基线甚至全面优于真实 embedding。

### 修复过程

加入课程与交付混淆、计划学习、废弃 prototype、相邻领域、同页 hard negative、跨页干扰和多个 gold evidence；PDF 扩到 96 case/576 query，RAG 扩到 180 case。强噪声下 Recall@3 降到 0.6125，原本更高的 vector 权重不再最优，最终选择 0.45 vector + 0.50 lexical + 0.05 type boost。

### 经验

指标下降不一定是系统退化，也可能是数据终于足够难。选择 Chunk、权重和 reranker 前，必须先解释 bad case 分布，而不是只展示一张高分表。

## 案例十二：简历正文混入“检查结果”和“改动摘要”

### 现象

早期定制简历 HTML 把事实检查、风险和改动摘要放在简历右侧。用户打印或下载时，这些内部诊断会成为投递材料的一部分。

### 根因与修复

把最终 artifact 和生成过程 metadata 混成了一个展示模型。现在 HTML renderer 只输出简历正文；评分、证据、缺口、Guardrail 和 diff 放在独立前端区域与 API 字段中。

### 经验

可解释性不是把日志贴进最终产物，而是区分 artifact、diagnostics 和 audit。这个看似前端 bug，本质是领域边界 bug。

## 案例十三：岗位一个都没搜到，Agent 仍然说“完成”

### 现象

旧 `find_jobs_for_profile` 会在岗位源返回空列表时正常执行 `finalize_find_jobs`，run 状态是 completed，只是 `matches=[]`。自然语言入口后来会再检查一次空结果，但直接调用主 Agent、后台任务和其他入口仍可能把空结果当成功。

### 根因

系统把“图走到了 END”误当成“业务目标完成”。LangGraph 保证控制流执行，不会自动理解“找岗位”至少需要一个可展示岗位；最终输出存在也不代表任务结果有效。

### 修复

每类任务建立 Task Contract 和 Goal Ledger。岗位搜索要求 `jobs_retrieved`、`jobs_ranked`、`result_exposed` 以及 `execution_plan/ranked_jobs` Artifact 全部满足。所有 finalize 节点必须经过 Completion Gate，空搜索会生成完整拒绝报告并将 run 标为 failed。

### 验证与经验

新增空岗位源回归，确认 `completion_gate_rejected` 和 `graph_failed` 都进入 Trace。经验是：Agent 是否完成必须看环境状态和交付物，不应相信模型文案，也不应只看图是否结束。

## 案例十四：工具成功率 95% 看起来不错，却不能证明工具用对了

### 现象

旧系统评测统计 Tool 的 completed/failed 状态，但无法识别“先改简历后加载岗位”、把错误 `profile_id` 传给工具、定制任务调用邮件工具或同一参数反复执行。

### 根因

执行可用性、轨迹正确性和业务结果正确性被合并成一个指标。HTTP 200 或函数返回值只能证明工具运行，不证明选型、参数、顺序和结果都正确。

### 修复

Trajectory V2 使用任务级工具白名单、必要步骤、参数 invariant、偏序约束、重复签名、审批状态和 Completion Artifact 联合评分。完成闸门还检查各产物的 Profile/Job/Resume ID 一致，防止并发或恢复时混用其他流程产物。

### 验证与经验

构造错误参数、乱序和越权 `email.send` 的轨迹，旧指标会把步骤视为 completed，新评测同时报告 argument/order/unexpected-tool 三类失败。面试时应明确区分 tool availability、trajectory correctness 与 outcome correctness。

## 案例十五：恢复成功后仍被判失败，历史分支又缺少前半段轨迹

### 现象

新增严格完成闸门后，全量测试出现两个恢复回归：Tailor 节点第一次崩溃、checkpoint 恢复后成功，但旧失败 step 仍让轨迹失败；从 `tailor_resume` 前 checkpoint 创建的新 run 有完整 LangGraph state，却没有新 run 的 `plan/load/match` 数据库记录，完成闸门认为它跳过了前置步骤。

### 根因

物理执行日志和逻辑任务轨迹不是同一概念。重试会留下多个 attempt，不能只要历史上出现 failed 就判整个任务失败；checkpoint fork 继承状态但不会自动复制业务数据库中的 AgentStep 和 Artifact。

### 修复

- 同一 step/tool/input 签名采用最后一次 attempt 作为当前状态，历史失败仍保留用于可靠性统计。
- fork 时按 checkpoint 时间截点复制此前 Artifact，并保存 `checkpoint_inherited_trajectory`；分支的新步骤与继承前缀合并评测。
- 不直接复制为“新 run 实际调用”，避免把继承步骤计入新的 Tool 成本和延迟。

### 验证与经验

崩溃恢复和 checkpoint 分支两项回归通过，最终全量 277 项通过。这个案例说明 checkpoint 只保存图状态；要得到可审计的任务恢复，还必须同时设计 attempt 语义、业务幂等和轨迹 lineage。

## 案例十六：RAG 返回 TopK 不代表证据足够，错误结果会沿链路扩散

### 现象

原匹配器只要向量库返回 chunk，就把 TopK 交给简历定制。无关 chunk、很短的简历、Prompt Injection 清洗后的空文本，都可能以“有检索结果”的形式进入 LLM。中文查询还可能因为默认 CrossEncoder 偏英文而转为词法重排，但下游看不到能力降级。

### 修复

- 按完整 JD、必需技能、职责资格构造 multi-query；各查询混合召回后用 RRF 融合，只做一次 rerank。
- 记录 query coverage、first-stage score、evidence count、chunk type、multi-query hit 和 degraded route。
- 匹配与缺口分析允许低证据并降低结论强度；定制简历等生成动作必须通过 Evidence Gate。
- Prompt Injection 清洗后再次检查可用证据；不足直接失败。
- 质量报告写入 MatchResult 和 JobSearchSession，便于 Trace 和前端解释。

### 当前边界

confidence 是可解释分数，不是统计校准概率；默认中文 reranker 仍需和 BGE multilingual 在真实 hard-negative 集上对照。成熟做法不是把任意 TopK 都塞给 LLM，也不是无结果就无限改写查询，而是限次检索、明确证据边界并允许拒绝生成。

## 面试总结模板

可以这样收束这些案例：

> 这个项目里我最大的收获是，Agent 的问题很少只靠调 Prompt 解决。一次失败可能来自 state schema、解析器丢字段、检索没召回、reranker 排错、上下文压缩、模型判断、发布门禁甚至评测器本身。我建立了 run/step/artifact/event/LLM log，把 raw 模型结果和 published 结果分开；确定性错误在代码层修，语义分歧用版本化 rubric 和 verifier，只有必要节点才 repair。这样即使最终通过，我仍能看到模型原始草稿中的错误，而不是把系统描述成模型永远正确。
