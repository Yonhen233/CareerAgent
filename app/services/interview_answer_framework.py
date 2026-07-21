from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models.entities import Job, Profile
from app.services.interview_references import InterviewReferenceService


class InterviewAnswerFrameworkService:
    """Build evidence-aware answer frameworks and upgrade legacy interview questions."""

    FRAMEWORK_VERSION = "evidence_framework_v1"
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

        existing_framework = self._valid_framework(question.get("answer_framework"))
        if existing_framework and not self._is_legacy(question):
            question["answer_framework"] = existing_framework
            question.setdefault("answer_framework_source", "llm_generated")
            question.setdefault("answer_framework_source_label", "LLM 生成，引用证据由系统校验")
            question.setdefault("answer_framework_version", self.FRAMEWORK_VERSION)
            return

        framework = self._build_framework(question, profile=profile, job=job, project=project)
        question["answer_framework"] = framework
        question["answer_points"] = [f"{item['section']}：{item['guidance']}" for item in framework]
        question["answer_framework_source"] = "evidence_rule_enriched"
        question["answer_framework_source_label"] = "系统根据 JD、简历证据和题目类型生成"
        question["answer_framework_version"] = self.FRAMEWORK_VERSION

    def _build_framework(
        self,
        question: dict[str, Any],
        *,
        profile: Profile,
        job: Job,
        project: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        blob = self._question_blob(question)
        project_guidance = self._project_guidance(project)
        if "chunk" in blob or "切分" in blob or "分块" in blob:
            return [
                self._step("先给结论", "没有对所有文档都最优的固定长度。先按标题、经历和职责等语义边界切分，超长段落再按 token 滑窗，并保留页码、章节和来源元数据。"),
                self._step("说明选择依据", "比较固定长度、递归字符和语义/章节切分；重点说明 PDF 断行、列表、表格、跨页标题及 overlap 对上下文完整性的影响。"),
                self._step("绑定项目证据", project_guidance),
                self._step("用评测做决定", "在带噪声样本上比较 Recall@K、MRR/nDCG、证据命中率、引用正确率和检索延迟；根据坏样本决定 chunk 大小与 overlap，而不是凭经验拍参数。"),
                self._step("说明取舍边界", "短 chunk 容易丢上下文，长 chunk 会稀释相关信息，overlap 过大会增加索引和重复召回；没有真实测过的参数和指标要明确说未验证。"),
            ]
        if any(term in blob for term in ("fastapi", "并发", "trace", "异步", "队列", "redis")):
            return [
                self._step("先界定场景", "先区分请求内 IO 并发、CPU 密集任务和长耗时工作流，再说明同步返回、异步任务或外部队列分别适用什么情况。"),
                self._step("讲清实现链路", "说明 FastAPI async 入口、任务入队、worker 消费、状态持久化、幂等键和超时/重试的边界。"),
                self._step("绑定项目证据", project_guidance),
                self._step("说明可观测性", "用 run_id、trace_id 和 stage heartbeat 串联 API、队列、worker、数据库与 LLM 调用，并观察吞吐、P95 延迟、失败率和积压量。"),
                self._step("说明失败处理", "明确连接池、重复消费、worker 崩溃和 stale run 的处理；没有压测结果时只说明设计与待验证项。"),
            ]
        if any(term in blob for term in ("sqlite", "向量库", "向量元数据", "存 jd", "存储边界")):
            return [
                self._step("先划分职责", "SQLite 保存岗位、JD chunk 元数据、业务状态和审计记录；向量索引保存 embedding 并负责近邻召回，两者通过稳定 chunk_id 关联。"),
                self._step("解释查询链路", "先做租户、城市、岗位类型等元数据过滤，再进行 BM25/向量召回和 reranker，最后按 chunk_id 回表取原文与引用。"),
                self._step("绑定项目证据", project_guidance),
                self._step("验证一致性", "写入使用业务幂等键和事务边界；索引失败要记录可重建状态，避免业务库已有 JD 而向量索引不可追踪。"),
                self._step("说明选型边界", "SQLite 适合单机和中小规模开发验证；并发写入、索引规模或高可用要求提升后，需要迁移到 PostgreSQL/专用向量库并重新压测。"),
            ]
        has_rag_topic = any(term in blob for term in ("rag", "检索", "rerank", "embedding", "召回"))
        has_agent_topic = any(term in blob for term in ("langgraph", "workflow", "工具调用", "react", "plan-execute"))
        if has_agent_topic or ("agent" in blob and not has_rag_topic):
            return [
                self._step("先画出状态流", f"围绕 {job.title} 说明输入状态、规划节点、工具节点、校验节点、人工审批和最终产物，不把 Agent 简化成一次 Prompt 调用。"),
                self._step("解释决策依据", "说明哪些步骤由确定性工作流控制，哪些交给 LLM 决策；高风险外发动作必须 interrupt 并等待用户审批。"),
                self._step("绑定项目证据", project_guidance),
                self._step("验证恢复与追踪", "用持久化 checkpoint、幂等键、事件流和 run artifact 支持跨进程恢复，并能定位每次模型、工具和写库结果。"),
                self._step("说明质量边界", "结构化输出校验、证据引用、guardrail 和 repair loop 只能降低错误率；无法确认的事实要报错或请求用户确认。"),
            ]
        if has_rag_topic:
            return [
                self._step("先说明检索目标", "明确 query、候选文档、需要召回的证据类型和最终回答用途，避免只描述向量库 API。"),
                self._step("拆解检索链路", "说明结构化解析、元数据过滤、BM25 与向量混合召回、TopK reranker、上下文压缩和带引用生成。"),
                self._step("绑定项目证据", project_guidance),
                self._step("给出评测方法", "离线比较 Recall@K、MRR/nDCG、reranker 增益、引用正确率和延迟，并按来源与噪声类型查看失败样本。"),
                self._step("说明取舍边界", "区分召回失败、排序失败和生成幻觉；未被检索证据支持的结论不能写进答案。"),
            ]
        if str(question.get("risk_level") or "") == "high" or "缺口" in blob or "没有生产经验" in blob:
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
