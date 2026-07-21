from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from app.models.entities import Job, Profile
from app.services.interview_references import InterviewReferenceService


class InterviewAnswerFrameworkService:
    """Build evidence-aware answer frameworks and upgrade legacy interview questions."""

    FRAMEWORK_VERSION = "evidence_framework_v1"
    REFERENCE_ANSWER_VERSION = "grounded_reference_answer_v2"
    _LEGACY_MARKERS = (
        "先标注来源",
        "说明可引用的项目、指标或缺口边界",
        "不能包装成生产经验",
    )

    def normalize_question_sets(
        self,
        question_sets: list[dict[str, Any]] | None,
        *,
        profile: Profile,
        job: Job,
    ) -> list[dict[str, Any]]:
        normalized = deepcopy(question_sets or [])
        for group in normalized:
            for question in group.get("questions") or []:
                self._normalize_question(question, profile=profile, job=job)
        return normalized

    def _normalize_question(self, question: dict[str, Any], *, profile: Profile, job: Job) -> None:
        question["evidence_refs"] = self._normalize_evidence_refs(question.get("evidence_refs") or [])
        question_source, question_source_label = self._question_source(question)
        question["question_generation_source"] = question_source
        question["question_generation_source_label"] = question_source_label

        project = self._best_project(profile, question)
        project_ref = self._project_evidence_ref(project)
        if project_ref and not any(ref.get("ref") == project_ref["ref"] for ref in question["evidence_refs"]):
            question["evidence_refs"].append(project_ref)

        question_kind = self._question_kind(question)
        existing_framework = self._valid_framework(question.get("answer_framework"))
        if existing_framework and not self._is_legacy(question):
            question["answer_framework"] = existing_framework
            question.setdefault("answer_framework_source", "llm_generated")
            question.setdefault("answer_framework_source_label", "LLM 生成，引用证据由系统校验")
            question.setdefault("answer_framework_version", self.FRAMEWORK_VERSION)
        else:
            framework = self._build_framework(question, job=job, project=project, question_kind=question_kind)
            question["answer_framework"] = framework
            question["answer_points"] = [f"{item['section']}：{item['guidance']}" for item in framework]
            question["answer_framework_source"] = "evidence_rule_enriched"
            question["answer_framework_source_label"] = "系统根据 JD、简历证据和题目类型生成"
            question["answer_framework_version"] = self.FRAMEWORK_VERSION

        existing_answer = str(question.get("reference_answer") or "").strip()
        reference_answer_basis = self._reference_answer_basis(
            question,
            project=project,
            job=job,
            question_kind=question_kind,
        )
        if (
            not existing_answer
            or question.get("reference_answer_version") != self.REFERENCE_ANSWER_VERSION
            or question.get("reference_answer_basis") != reference_answer_basis
        ):
            question["reference_answer"] = self._build_reference_answer(
                question,
                profile=profile,
                job=job,
                project=project,
                question_kind=question_kind,
            )
            question["reference_answer_source"] = "grounded_rule_composer"
            question["reference_answer_source_label"] = "已结合当前岗位和你的简历生成，请按真实经历调整"
            question["reference_answer_version"] = self.REFERENCE_ANSWER_VERSION
            question["reference_answer_basis"] = reference_answer_basis

    def _build_framework(
        self,
        question: dict[str, Any],
        *,
        job: Job,
        project: dict[str, Any] | None,
        question_kind: str,
    ) -> list[dict[str, str]]:
        project_guidance = self._project_guidance(project)
        if question_kind == "chunk_strategy":
            return [
                self._step("先给结论", "没有对所有文档都最优的固定长度。先按标题、经历和职责等语义边界切分，超长段落再按字符窗口切分，并保留页码、章节和来源元数据。"),
                self._step("说明选择依据", "比较固定长度、递归字符和语义/章节切分；重点说明 PDF 断行、列表、表格、跨页标题及 overlap 对上下文完整性的影响。"),
                self._step("绑定项目证据", project_guidance),
                self._step("用评测做决定", "在带噪声样本上比较 Recall@K、MRR/nDCG、证据命中率、引用正确率和检索延迟；根据坏样本决定 chunk 大小与 overlap，而不是凭经验拍参数。"),
                self._step("说明取舍边界", "短 chunk 容易丢上下文，长 chunk 会稀释相关信息，overlap 过大会增加索引和重复召回；没有真实测过的参数和指标要明确说未验证。"),
            ]
        if question_kind == "fastapi_concurrency":
            return [
                self._step("先界定场景", "先区分请求内 IO 并发、CPU 密集任务和长耗时工作流，再说明同步返回、异步任务或外部队列分别适用什么情况。"),
                self._step("讲清实现链路", "说明 FastAPI async 入口、任务入队、worker 消费、状态持久化、幂等键和超时/重试的边界。"),
                self._step("绑定项目证据", project_guidance),
                self._step("说明可观测性", "用 run_id、trace_id 和 stage heartbeat 串联 API、队列、worker、数据库与 LLM 调用，并观察吞吐、P95 延迟、失败率和积压量。"),
                self._step("说明失败处理", "明确连接池、重复消费、worker 崩溃和 stale run 的处理；没有压测结果时只说明设计与待验证项。"),
            ]
        if question_kind == "storage_boundary":
            return [
                self._step("先划分职责", "SQLite 保存岗位、JD chunk 原文、metadata、embedding、业务状态和审计记录，是可审计的权威存储；Chroma 在 hybrid 模式下只是可重建的向量镜像。"),
                self._step("解释查询链路", "先按岗位和租户等条件缩小候选，再融合 embedding 相似度、词项匹配和 chunk 类型加权，最后对候选做 reranker。"),
                self._step("绑定项目证据", project_guidance),
                self._step("验证一致性", "SQLite 写入成功后再同步可选镜像；镜像失败可以根据稳定 chunk_uid 和 SQLite 中的 embedding 重建，不能让镜像反过来成为业务真相。"),
                self._step("说明选型边界", "SQLite 适合当前单机和中小规模岗位库；数据量、并发写入或高可用要求提升后，再把业务库迁到 PostgreSQL，并把向量召回迁到 pgvector 或专用向量库。"),
            ]
        if question_kind == "agent_workflow":
            return [
                self._step("先画出状态流", f"围绕 {job.title} 说明输入状态、规划节点、工具节点、校验节点、人工审批和最终产物，不把 Agent 简化成一次 Prompt 调用。"),
                self._step("解释决策依据", "说明哪些步骤由确定性工作流控制，哪些交给 LLM 决策；高风险外发动作必须 interrupt 并等待用户审批。"),
                self._step("绑定项目证据", project_guidance),
                self._step("验证恢复与追踪", "用持久化 checkpoint、幂等键、事件流和 run artifact 支持跨进程恢复，并能定位每次模型、工具和写库结果。"),
                self._step("说明质量边界", "结构化输出校验、证据引用、guardrail 和 repair loop 只能降低错误率；无法确认的事实要报错或请求用户确认。"),
            ]
        if question_kind == "rag_retrieval":
            return [
                self._step("先说明检索目标", "明确 query、候选文档、需要召回的证据类型和最终回答用途，避免只描述向量库 API。"),
                self._step("拆解检索链路", "说明结构化解析、元数据过滤、BM25 与向量混合召回、TopK reranker、上下文压缩和带引用生成。"),
                self._step("绑定项目证据", project_guidance),
                self._step("给出评测方法", "离线比较 Recall@K、MRR/nDCG、reranker 增益、引用正确率和延迟，并按来源与噪声类型查看失败样本。"),
                self._step("说明取舍边界", "区分召回失败、排序失败和生成幻觉；未被检索证据支持的结论不能写进答案。"),
            ]
        if question_kind == "skill_gap":
            skill = self._primary_skill(question) or "该能力"
            return [
                self._step("先诚实定级", f"明确 {skill} 是已交付、相邻经验、课程/实验还是待学习，不用模糊措辞把它说成生产经验。"),
                self._step("迁移相邻能力", "只迁移可证明的方法，例如需求拆解、评测、可观测性或故障处理，并解释哪些部分仍不能等价。"),
                self._step("绑定项目证据", project_guidance),
                self._step("给出验证计划", "说明会用什么最小任务、数据和验收指标补齐能力，并给出可执行的时间与产物。"),
                self._step("接受追问边界", "如果面试官继续追问未做过的生产细节，直接说明未知并给出排查思路，不编造参数或事故经验。"),
            ]

        existing = [str(item).strip() for item in question.get("answer_points") or [] if str(item).strip()]
        direct = existing[0] if existing else "先用一句话直接回答问题，再解释判断依据。"
        method = existing[1] if len(existing) > 1 else f"把回答映射到 {job.title} 的职责、输入、处理过程和输出。"
        verification = existing[2] if len(existing) > 2 else "给出可验证结果、指标、用户反馈或失败样本；没有量化结果时明确说明。"
        return [
            self._step("直接回答", direct),
            self._step("展开方法", method),
            self._step("绑定项目证据", project_guidance),
            self._step("补充验证", verification),
            self._step("说明边界", "只陈述简历或运行记录能够支持的事实，区分已交付、相邻经验和待补齐能力。"),
        ]

    def _build_reference_answer(
        self,
        question: dict[str, Any],
        *,
        profile: Profile,
        job: Job,
        project: dict[str, Any] | None,
        question_kind: str,
    ) -> str:
        project_name, project_fact = self._project_answer_evidence(project)
        is_career_agent = "careeragent" in project_name.replace(" ", "").lower()
        question_text = str(question.get("question") or "").lower()
        blob = self._question_blob(question)

        if question_kind == "interview_research":
            return (
                "我不会把面经标题直接当成标准答案，而会先做两次核对。第一步看它是否和当前 JD 的职责、技术栈和面试轮次一致；第二步回到自己的简历，确认每个问题有没有真实项目证据。"
                "像牛客、OfferShow 或小红书上的内容，我只把它当作高频问题线索，不会把未经核验的帖子描述成目标公司的固定题库。\n\n"
                f"针对当前 {job.title}，我会优先整理 Agent 工作流、RAG 检索与评测、FastAPI 工程化、SQLite/向量存储边界和失败排查等主题。"
                f"项目主线使用《{project_name}》：{project_fact} 每个问题至少准备一段直接结论、一项真实实现、一种验证方法和一个限制。\n\n"
                "如果参考链接需要登录、正文不完整或来源互相矛盾，我会保留标题和入口，但不会引用无法确认的细节。最终练习内容仍然由 JD、简历证据和自己的项目实现决定。"
            )

        if question_kind == "project_overview":
            return (
                f"我会以《{project_name}》作为回答主线。{project_fact} 这个项目不是一个单轮对话 Demo，而是把简历建档、岗位检索、匹配分析、定制简历、投递审批和面试准备连接成一条可恢复的求职流程。\n\n"
                "技术上，FastAPI 提供用户 API 和状态查询，LangGraph 负责有状态编排，SQLite 保存业务数据、checkpoint、chunk 和审计记录，RAG 负责从简历经历与 JD 中检索证据，Redis worker 承接长耗时任务。"
                "LLM 主要用于意图理解和内容生成，确定性的流程顺序、权限、幂等和审批由代码控制。\n\n"
                f"它适合 {job.title}，因为我不仅做了模型调用，还处理了检索质量、结构化输出、失败恢复、Trace、评测和高风险动作审批。"
                "我会把已经跑通的实现和仍待压测或扩容的部分分开说明，不把架构设计直接说成生产规模经验。"
            )

        if question_kind == "fastapi_design":
            if "依赖注入" in question_text:
                return (
                    "FastAPI 的依赖注入不是传统容器自动扫描，而是根据路由函数和 Depends 声明构建一棵依赖图。请求到来后，框架按依赖顺序解析参数、执行依赖，并在同一次请求内缓存结果；"
                    "使用 yield 的依赖还会在响应结束后执行清理逻辑，所以很适合管理数据库会话。\n\n"
                    "在项目里我会把 get_db 写成 yield dependency：请求开始创建 Session，业务 service 通过参数拿到它，结束时统一 close；异常时由事务边界决定 rollback。"
                    "配置对象可以通过带缓存的 settings dependency 复用，当前用户、tenant 和权限也可以作为独立依赖组合，而不是在每个路由里重复解析 header。\n\n"
                    "这样做的价值是路由只负责 HTTP 契约，事务、认证和配置可以单独测试和替换。需要注意的是依赖注入本身不会解决连接池和并发问题；长事务、Session 跨任务复用或把同步数据库调用塞进高并发 async 路由，仍然会造成阻塞。"
                )
            return (
                "在这个项目里，FastAPI 位于 Agent 工作流之前，主要负责参数校验、身份与租户上下文、创建 run、查询状态和返回事件流。它的优势是 Pydantic 数据契约和依赖注入与 Python Agent 技术栈配合自然，"
                "但我不会把长时间的 LLM、PDF 或 embedding 工作直接放在请求里等待。\n\n"
                f"结合《{project_name}》，{project_fact} API 创建任务后返回 run_id，实际流程交给外部 worker；数据库会话、配置和权限通过依赖注入管理，错误会写入统一 trace。"
                "如果不用 FastAPI，我会根据团队栈选择 Flask、Django Ninja 或其他服务框架，但无论框架是什么，请求边界、后台任务、幂等和可观测性都必须保留。"
            )

        if question_kind == "evaluation":
            return (
                "我会把评测拆成组件层和端到端两层，而不是只看最终回答像不像。PDF 解析看字段完整率、章节边界和噪声恢复；RAG 看 Recall@K、MRR/nDCG、reranker 增益和引用正确率；"
                "LLM 节点看结构化输出成功率、事实一致性、repair 成功率和耗时。\n\n"
                f"结合《{project_name}》，{project_fact} 数据集会同时包含正常样本、长文本、PDF 断行、否定技能、prompt injection 和相似但不相关的 JD。"
                "每次评测保存配置、模型版本、逐 case 结果和 stage trace，发布门禁使用固定阈值，失败样本进入回归集。\n\n"
                "端到端还要验证用户能否完成建档、找岗位、匹配、定制、审批和面试准备，并分别记录失败发生在哪一层。这样指标下降时可以判断是解析、检索、重排还是生成问题，而不是只得到一个无法排查的总分。"
            )

        if question_kind == "python_design":
            return (
                "Python 在这个岗位里不只是写 Prompt 脚本，而是承担 Agent 编排、工具实现、数据处理和评测。以一次岗位匹配为例，输入是用户偏好、结构化简历和岗位 JD；服务层完成字段校验、chunk/embedding、混合检索和重排，"
                "LangGraph 决定后续是否生成定制简历或进入人工确认，最终输出带证据的匹配结果和可追踪产物。\n\n"
                f"结合《{project_name}》，{project_fact} 我选择 Python 是因为 FastAPI、Pydantic、LangGraph、embedding/reranker 和数据评测生态完整，能够减少跨语言胶水。"
                "性能敏感部分不会只靠 Python 循环硬扛，而会通过批量 embedding、外部队列、多进程或底层库优化；如果是极高吞吐网关，也可以让 Go/Java 承担入口，Python 保留模型与 Agent 服务。"
            )

        if question_kind == "llm_integration":
            return (
                "LLM API 在我的架构里是受控能力，不是系统唯一核心。它负责自然语言意图解析、JD/简历内容生成和需要语义判断的环节；岗位数据、状态流转、审批、幂等和权限仍由确定性代码控制。"
                "每次调用都会记录模型、Prompt 摘要、耗时、重试和响应预览，并使用 Pydantic/JSON schema 校验输出。\n\n"
                f"结合《{project_name}》，{project_fact} 如果模型返回空 content、非法 JSON 或证据不足，节点会失败并留下 trace，必要时只做有限的 repair，而不会静默换成一段假结果。"
                "如果更换模型供应商，业务层仍通过统一 LLMClient 和结构化 schema 交互；真正需要重新评测的是输出质量、延迟、价格和 tool calling 兼容性。"
            )

        if question_kind == "motivation":
            return (
                f"我想做 {job.title}，是因为我更感兴趣的不是单次模型调用，而是怎样让 LLM 在真实流程里稳定完成任务。"
                "我做 CareerAgent 时发现，真正困难的部分是检索证据是否可靠、长流程能否恢复、模型输出如何校验，以及投递这类高风险动作怎样让用户保持控制权。\n\n"
                f"《{project_name}》让我积累了从 FastAPI、RAG、LangGraph 到评测和 Trace 的完整实践：{project_fact} 这个岗位和我当前阶段匹配，"
                "因为我已经能够独立搭建并调试一条 Agent 工程链路，同时还需要在更真实的数据规模、团队协作和线上稳定性上继续学习。\n\n"
                "我希望实习中能参与有明确用户和业务反馈的 Agent 产品，把离线评测、线上问题和产品体验连接起来，而不是只追求 Demo 中看起来聪明的回答。"
            )

        if question_kind == "behavioral":
            if any(term in question_text for term in ("失败", "返工", "指标不达", "定位并修复")) and is_career_agent:
                return (
                    "我遇到过一次比较典型的问题：岗位匹配的 RAG 会把“没有 MLflow 生产经验”里的 MLflow 当成正向技能证据，导致匹配分和面试准备都偏乐观。"
                    "最初只看最终分数不容易发现原因，所以我沿着 trace 检查了命中的 chunk、evidence type 和最终 matched_skills，确认问题出在检索后的证据语义分类，而不是 embedding 本身。\n\n"
                    "修复时我没有简单屏蔽 MLflow 这个词，而是增加否定证据识别和 evidence type classifier，把 shipped project、coursework、planned learning 和 missing-skill disclosure 分开；"
                    "匹配和面试包遇到负向证据时会降权或进入缺口披露。随后我把这个样本加入回归集，检查缺失技能、匹配分和参考回答都不再把它包装成已掌握。\n\n"
                    "这次经历让我意识到，RAG 不能只评估是否召回关键词，还要判断证据的极性和可用性；否则检索看似成功，业务结论仍然是错的。"
                )
            return (
                "我会先把模糊任务转换成可验收的问题：目标用户是谁、必须解决什么、输入数据和权限有哪些、截止时间是什么、用什么指标算完成。"
                "然后把工作拆成数据/接口契约、最小链路、质量评测和上线治理四部分，先跑通可验证的小版本，再根据结果扩展。\n\n"
                f"结合《{project_name}》，{project_fact} 我会在每个阶段同步已完成内容、下一步、阻塞项和风险，不等到截止前才暴露问题。"
                "遇到技术分歧时用日志、样例和评测结果做决定；需求变化时重新确认影响范围和优先级，并保留决策记录，确保团队对验收标准保持一致。"
            )

        if question_kind == "chunk_strategy":
            implementation = (
                "在 CareerAgent 里，我没有给所有内容套同一个固定长度。结构化简历会按技能、项目、实习和教育等字段分别建 chunk；"
                "PDF 原文先保留页码，再按自然段合并，超过上限的段落才使用滑动窗口。当前默认窗口是 900 个字符、overlap 160 个字符。"
                if is_career_agent
                else "我的原则是先保留标题、项目和经历等语义边界，只有超长段落才使用带 overlap 的滑动窗口，并保留页码、章节和来源元数据。"
            )
            return (
                f"我认为 chunk 策略没有一个对所有文档都最优的固定答案，关键是先看文档结构和后续检索任务。{implementation}\n\n"
                f"我会结合《{project_name}》来说明。{project_fact} 选择参数时，我不会只看文本是否切得均匀，而会在带断行、重复页眉、长项目描述和跨页内容的样本上比较 Recall@K、MRR/nDCG、证据命中率、引用正确率和检索延迟。"
                "如果小 chunk 导致上下文缺失，就增大窗口或按章节合并；如果大 chunk 稀释关键词，就缩小窗口；overlap 只用来补边界，不会为了提高表面召回率无限增大。"
            )

        if question_kind == "storage_boundary":
            if is_career_agent:
                return (
                    "我会把 SQLite 定位成可审计的权威存储，而不是只把它当成一个向量库。CareerAgent 的 jobs 表保存岗位来源、原始 JD 和结构化字段，"
                    "job_chunks 表保存稳定的 chunk_uid、chunk 类型、来源、原文、token 数、embedding_json 和 embedding 模型等 metadata。默认 hybrid 模式下还可以同步写入 Chroma，但 Chroma 只是可重建的检索镜像，业务真相仍然在 SQLite。\n\n"
                    "检索时先按岗位范围和业务条件缩小候选，再融合 embedding 余弦相似度、词项匹配和 chunk 类型加权，取候选后交给 reranker 做二阶段排序。"
                    "这样原文、向量、模型版本和最终引用都能沿着 chunk_uid 追溯。如果 Chroma 写入失败，可以从 SQLite 重建，不会出现向量库有数据但业务库无法解释来源的情况。\n\n"
                    "这个选型适合当前单机、中小规模岗位库，优点是部署简单、事务和审计清楚；缺点是 SQLite 并发写入和 JSON 向量扫描不适合大规模。"
                    "当岗位量和并发明显增长时，我会把业务数据迁到 PostgreSQL，并根据压测结果选择 pgvector、Qdrant 或 Milvus，而不是一开始就引入复杂的分布式组件。"
                )
            return (
                f"我会把业务库和向量索引的职责分开：业务库保存岗位原文、结构化 JD、chunk 原文、来源和版本，向量侧只负责相似度召回，并通过稳定 chunk_id 关联。"
                f"结合《{project_name}》，{project_fact}\n\n"
                "查询时先做元数据过滤，再进行向量与关键词混合召回和 reranker；回答引用必须回到业务库原文。向量索引应当能够从业务库重建，不能成为唯一数据源。"
                "小规模可以使用 SQLite 简化部署，规模和并发提升后再迁移 PostgreSQL/pgvector 或专用向量库，并用压测决定边界。"
            )

        if question_kind == "fastapi_concurrency":
            return (
                "我不会把 FastAPI 的 async 等同于系统已经具备高并发能力。请求内的数据库和网络 IO 可以使用异步接口，但 PDF 解析、批量 embedding、长时间 Agent 工作流这类任务不能一直占着 HTTP 请求，应该进入 Redis 外部队列，由多个 worker 消费。\n\n"
                f"结合《{project_name}》，{project_fact} 我的处理方式是让 API 只负责校验参数、创建 run 并返回 run_id，worker 负责执行 LangGraph 节点；"
                "SQLite 持久化状态和产物，Redis 保存队列、心跳和取消信号。run_id、trace_id、stage heartbeat 会贯穿 API、worker、LLM 和写库节点，页面通过事件流或轮询恢复进度。\n\n"
                "并发测试时我会分别观察 API P95 延迟、队列积压、worker 吞吐、数据库锁等待和失败率。CPU 密集任务要放到独立进程，重复消费靠幂等键处理，worker 崩溃则由 stale run scanner 和重试/DLQ 接管；没有实际压测过的吞吐量我不会直接报数字。"
            )

        if question_kind == "agent_workflow":
            return (
                "我把 Agent 理解为有状态、可恢复、能调用工具并接受人工控制的工作流，而不是一次大 Prompt。CareerAgent 使用 LangGraph 把读取简历、岗位检索、匹配、定制简历、事实校验、投递审批和面试准备拆成节点；"
                "确定性的业务顺序由图控制，LLM 只负责意图解析、内容生成和需要语义判断的环节。\n\n"
                f"结合《{project_name}》，{project_fact} 每次运行都保存 checkpoint、step、event 和 artifact。投递、浏览器填写和邮件发送等高风险动作会在工具执行前 interrupt，"
                "只有审批通过后才能恢复；恢复和重试依赖业务幂等键，避免重复写库或重复外发。\n\n"
                "为了降低模型错误，我会使用结构化输出校验、RAG 证据引用、prompt injection 检测和 guardrail repair。无法被简历或 JD 证据支持的内容不会进入最终产物；这些机制不能保证模型永远正确，所以完整 trace、失败直报和人工审批仍然是最后边界。"
            )

        if question_kind == "rag_retrieval":
            return (
                "我的 RAG 链路不是只做一次向量 TopK。数据进入系统后先按简历字段或 JD 职责、要求、技能等语义结构切分，保存原文、chunk 类型、来源和 embedding metadata。"
                "查询阶段先做范围过滤和 query 扩展，再融合 embedding 相似度、词项匹配和类型加权，从第一阶段候选中取 Top20 交给 reranker，最后只把高相关证据送给 LLM。\n\n"
                f"结合《{project_name}》，{project_fact} 评测时我会把问题拆成召回、排序和生成三层：用 Recall@K 看正确证据是否被找到，用 MRR/nDCG 看排序，"
                "再看引用正确率、faithfulness 和端到端延迟。噪声样本要包含 PDF 断行、同义词、否定描述和相似但不相关的经历，不能只用理想关键词命中样本。\n\n"
                "如果答案出错，我会先看正确 chunk 是否进入候选集，再看 reranker 是否排错，最后检查 LLM 是否忽略证据。只有先定位失败层，才能决定该调 chunk、embedding、检索权重还是 Prompt。"
            )

        if question_kind == "skill_gap":
            skill = self._primary_skill(question) or "这个技能"
            return (
                f"目前我不会把 {skill} 描述成已经有生产经验。如果简历中没有直接交付证据，我会明确说这是待补强项，再说明和它最接近的真实经验。"
                f"我可以结合《{project_name}》回答：{project_fact}\n\n"
                f"接下来我会围绕目标岗位做一个可验收的最小验证，例如用 {skill} 接入一条真实流程，补上配置、日志、异常处理和评测记录，并把结果写成可复现的文档或测试。"
                "如果面试官继续追问我没有做过的生产细节，我会直接说明未知，再给出我会查哪些指标、文档和故障信号，而不是临时编一个经验。"
            )

        if "自我介绍" in blob or "介绍一下你" in blob:
            skills = "、".join(str(item) for item in (profile.structured_profile_json or {}).get("skills", [])[:6])
            return (
                f"您好，我目前的目标方向是 {job.title}。我的主要技术栈是 {skills or 'Python 和大模型应用开发'}。"
                f"我最想介绍的项目是《{project_name}》：{project_fact}\n\n"
                "这个项目让我完整经历了从需求拆解、Agent 工作流设计、RAG 检索、评测到可观测和风险控制的过程。"
                "我希望在实习中继续做真实业务中的 Agent 工程，尤其关注工具调用可靠性、检索质量和长流程恢复，而不只是完成一个可以演示的对话页面。"
            )
        if any(term in blob for term in ("协作", "冲突", "需求不清", "推进", "沟通")):
            return (
                "遇到需求不清或意见不一致时，我会先把争论从方案偏好转成可验收的问题：目标用户是谁、必须解决什么、时间和数据约束是什么、用什么指标判断完成。"
                "然后把大需求拆成一个最小可验证版本，先用样例、接口契约或短链路跑通，再根据结果决定是否扩大实现。\n\n"
                f"我会结合《{project_name}》来讲这个方法。{project_fact} 如果过程中出现分歧，我会保留决策记录、实验结果和失败样例，让团队根据证据调整，而不是靠职位或表达强弱决定。"
                "最后我会明确负责人、截止时间和风险项，并在需求变化时同步影响范围，避免做到最后才发现双方理解不一致。"
            )

        existing = [str(item).strip() for item in question.get("answer_points") or [] if str(item).strip()]
        supporting = "；".join(existing[:3]) or f"我会先把问题放回 {job.title} 的业务目标，再说明输入、处理过程、输出和验证方式"
        return (
            f"针对这个问题，我会结合《{project_name}》直接回答。{project_fact} 我的核心判断是：{supporting}。\n\n"
            "回答时我会把已经完成的实现、实际观察到的结果和仍未验证的部分分开说明。能引用运行记录、测试或失败样例的地方就给出证据；"
            "没有数据支持的地方只讲判断方法和下一步验证，不把设计设想说成已经上线的能力。"
        )

    def _best_project(self, profile: Profile, question: dict[str, Any]) -> dict[str, Any] | None:
        projects = [item for item in (profile.structured_profile_json or {}).get("projects", []) or [] if isinstance(item, dict)]
        if not projects:
            return None
        terms = {str(item).lower(): 2 for item in question.get("skills") or [] if str(item).strip()}
        weights = {"chunk": 4, "rag": 3, "fastapi": 3, "sqlite": 3, "langgraph": 3, "redis": 3, "agent": 1}
        terms.update({term: weight for term, weight in weights.items() if term in self._question_blob(question)})

        def score(project: dict[str, Any]) -> tuple[int, int]:
            name = str(project.get("name") or "").lower()
            text = " ".join(
                [
                    name,
                    str(project.get("description") or ""),
                    str(project.get("impact") or ""),
                    " ".join(str(item) for item in project.get("tech_stack") or []),
                ]
            ).lower()
            relevance = sum(weight + (2 if term in name else 0) for term, weight in terms.items() if term and term in text)
            return relevance, len(text)

        return max(projects, key=score)

    def _question_kind(self, question: dict[str, Any]) -> str:
        text = str(question.get("question") or "").lower()
        source = str(question.get("source_perspective") or "")
        if (
            source == "jd_gap_drill"
            or any(term in text for term in ("缺口", "没有生产经验", "没有相关交付", "未明确体现", "待补强"))
        ):
            return "skill_gap"
        if any(term in text for term in ("为什么你想", "为什么想做", "为什么这个岗位", "岗位适合你")):
            return "motivation"
        if any(term in text for term in ("如何拆任务", "同步风险", "模糊需求", "协作", "冲突", "失败", "返工", "指标不达")):
            return "behavioral"
        if any(term in text for term in ("结合一个项目", "哪个简历项目", "回答主线")):
            return "project_overview"
        if source == "online_experience_research" or any(term in text for term in ("参考链接", "面经参考", "面经标题")):
            return "interview_research"
        if any(term in text for term in ("评测指标", "实验可复现", "保证实验", "如何评估", "如何验证效果")):
            return "evaluation"
        chunk_strategy = (
            "切分" in text
            or "分块" in text
            or ("chunk" in text and any(term in text for term in ("策略", "大小", "overlap", "怎么选")))
        )
        if chunk_strategy:
            return "chunk_strategy"
        if "fastapi" in text and any(term in text for term in ("并发", "trace", "异步", "队列", "请求变高")):
            return "fastapi_concurrency"
        if "fastapi" in text:
            return "fastapi_design"
        if any(term in text for term in ("sqlite", "向量库", "向量元数据", "存 jd", "存储边界")):
            return "storage_boundary"
        has_agent_topic = any(term in text for term in ("langgraph", "workflow", "工具调用", "react", "plan-execute"))
        if has_agent_topic or ("agent" in text and any(term in text for term in ("架构", "节点", "工作流", "怎么用", "如何编排"))):
            return "agent_workflow"
        if any(term in text for term in ("rag", "检索", "rerank", "embedding", "召回")):
            return "rag_retrieval"
        if "evaluation" in text or "评测" in text:
            return "evaluation"
        if "llm api" in text or "模型调用" in text:
            return "llm_integration"
        if "python" in text:
            return "python_design"
        return "general"

    def _project_answer_evidence(self, project: dict[str, Any] | None) -> tuple[str, str]:
        if project is None:
            return (
                "当前简历中的相关经历",
                "当前简历没有提供可直接引用的项目细节，因此这里只能说明技术判断和验证方法，不能声称已经交付。",
            )
        name = str(project.get("name") or "简历项目").strip()
        description = self._short_text(project.get("description"), 180).rstrip("。；; ")
        impact = self._short_text(project.get("impact"), 120).rstrip("。；; ")
        detail = "；".join(item for item in (description, impact) if item)
        if not detail:
            detail = "当前简历只记录了项目名称和技术栈，回答时不能补造未记录的实现或指标"
        return name, detail + "。"

    def _reference_answer_basis(
        self,
        question: dict[str, Any],
        *,
        project: dict[str, Any] | None,
        job: Job,
        question_kind: str,
    ) -> str:
        payload = "\n".join(
            [
                str(question.get("question") or ""),
                "|".join(str(item) for item in question.get("skills") or []),
                str(question.get("risk_level") or ""),
                question_kind,
                str((project or {}).get("name") or ""),
                str((project or {}).get("description") or ""),
                str((project or {}).get("impact") or ""),
                str(job.id or ""),
                str(job.title or ""),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

    def _project_guidance(self, project: dict[str, Any] | None) -> str:
        if project is None:
            return "当前简历没有找到可直接引用的项目证据。回答时应明确这一点，只说明理解、相邻经验和验证计划。"
        name = str(project.get("name") or "简历项目").strip()
        description = self._short_text(project.get("description"), 120).rstrip("。；; ")
        impact = self._short_text(project.get("impact"), 90).rstrip("。；; ")
        detail = "；".join(item for item in (description, impact) if item)
        return f"以《{name}》为回答主线，只引用简历已有事实：{detail or '项目名称与技术栈'}。没有写入简历的参数或指标不要临时补造。"

    def _project_evidence_ref(self, project: dict[str, Any] | None) -> dict[str, Any] | None:
        if project is None:
            return None
        name = str(project.get("name") or "简历项目").strip()
        description = self._short_text(project.get("description"), 140)
        return {
            "ref": f"resume_project:{name}",
            "source_type": "resume_project",
            "source_label": "简历项目",
            "preview": f"{name}：{description}" if description else name,
        }

    def _normalize_evidence_refs(self, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in refs:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            source_url = str(item.get("source_url") or item.get("url") or "").strip()
            if source_url and InterviewReferenceService.is_valid_public_url(source_url):
                item["source_url"] = source_url
            else:
                item.pop("source_url", None)
                item.pop("url", None)
            key = str(item.get("preview") or item.get("ref") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(item)
        return normalized

    def _question_source(self, question: dict[str, Any]) -> tuple[str, str]:
        source = str(question.get("source_perspective") or "")
        if source == "source_backed_interview_experience":
            return "imported_interview_experience", "由已导入面经生成题目"
        if source.startswith("llm_"):
            return "llm", "由 LLM 结合 JD 与简历生成题目"
        return "structured_rule", "由系统根据 JD 与简历生成题目"

    def _is_legacy(self, question: dict[str, Any]) -> bool:
        text = "\n".join(str(item) for item in question.get("answer_points") or [])
        return any(marker in text for marker in self._LEGACY_MARKERS)

    def _valid_framework(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        result: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                return []
            section = str(item.get("section") or "").strip()
            guidance = str(item.get("guidance") or "").strip()
            if not section or not guidance:
                return []
            result.append({"section": section, "guidance": guidance})
        return result

    def _question_blob(self, question: dict[str, Any]) -> str:
        values = [str(question.get("question") or ""), str(question.get("intent") or "")]
        values.extend(str(item) for item in question.get("skills") or [])
        values.extend(str(item) for item in question.get("follow_ups") or [])
        return "\n".join(values).lower()

    @staticmethod
    def _step(section: str, guidance: str) -> dict[str, str]:
        return {"section": section, "guidance": guidance}

    @staticmethod
    def _primary_skill(question: dict[str, Any]) -> str:
        return next((str(item).strip() for item in question.get("skills") or [] if str(item).strip()), "")

    @staticmethod
    def _short_text(value: Any, limit: int) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: max(limit - 1, 1)].rstrip() + "…"
