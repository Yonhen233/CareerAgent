from __future__ import annotations

import copy
import json
import math
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.entities import AgentDirective, AgentEvent, AgentRun, EvaluationRun
from app.models.schemas import AgentDirectiveRequest
from app.services.agent_directives import AgentDirectiveService
from app.services.pdf_extraction import PDFExtractionError, PDFExtractionService
from app.services.text_splitter import PDFPageText, ResumeTextSplitter


class _DirectiveEvaluationAgent:
    def __init__(self, *, mode: str = "completed") -> None:
        self.mode = mode
        self.requests: list[Any] = []

    async def run(self, db: Session, request: Any, *, tenant_id: str | None = None, user_id: str | None = None):
        self.requests.append(request)
        if self.mode == "raise":
            raise RuntimeError("synthetic directive executor failure")
        status = "failed" if self.mode == "failed" else "completed"
        run = AgentRun(
            tenant_id=tenant_id,
            user_id=user_id,
            task_type="natural_language_request",
            # The evaluator observes requested IDs without inventing referenced business rows.
            profile_id=None,
            job_id=None,
            status=status,
            input_json=request.model_dump(),
            output_json={
                "status": status,
                "user_message": "后续流程执行失败" if status == "failed" else "后续流程已完成",
                "result_json": {"error": "synthetic child failure"} if status == "failed" else {"updated": True},
            },
            error_message="synthetic child failure" if status == "failed" else None,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run


class CapabilityBadCaseEvaluationService:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def run_pdf_extraction(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "pdf_extraction_bad_cases.json"
        cases = self._load_cases(path)
        results = [self._run_pdf_case(case) for case in cases]
        summary = self._summarize_pdf(results, path)
        return self._persist(db, "pdf_extraction_bad_case_evaluation", summary, results)

    async def run_follow_up_directives(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "follow_up_directive_bad_cases.json"
        cases = self._load_cases(path)
        results = []
        for index, case in enumerate(cases):
            results.append(await self._run_directive_case(db, case, case_index=index))
        summary = self._summarize_directives(results, path)
        return self._persist(db, "follow_up_directive_bad_case_evaluation", summary, results)

    def _run_pdf_case(self, case: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        errors: list[str] = []
        observed: dict[str, Any] = {}
        try:
            if str(case["factory"]).startswith("bridge_"):
                pages = self._bridge_pages(case["factory"])
                chunks = ResumeTextSplitter().split_pdf_pages(pages, prefix=case["name"])
                bridges = [
                    chunk
                    for chunk in chunks
                    if (chunk.metadata or {}).get("strategy") == "cross_page_semantic_bridge"
                ]
                observed["bridge_created"] = bool(bridges)
                observed["bridge_count"] = len(bridges)
                expected_bridge = bool(case["expected_bridge"])
                if bool(bridges) != expected_bridge:
                    errors.append(f"bridge_created={bool(bridges)} expected={expected_bridge}")
                if bridges:
                    bridge_text = "\n".join(chunk.text for chunk in bridges)
                    missing = [token for token in case.get("expected_bridge_tokens", []) if token not in bridge_text]
                    if missing:
                        errors.append(f"bridge missing tokens: {missing}")
                return self._pdf_result(case, errors, observed, started)

            payload = self._pdf_payload(case["factory"])
            filename = str(case.get("filename") or "resume.pdf")
            settings = self.settings.model_copy(update=case.get("settings") or {})
            service = PDFExtractionService(settings=settings)
            try:
                extraction = service.extract(filename=filename, file_bytes=payload)
            except PDFExtractionError as exc:
                observed["error_code"] = exc.code
                expected_error = case.get("expected_error")
                if exc.code != expected_error:
                    errors.append(f"error_code={exc.code} expected={expected_error or 'success'}")
                return self._pdf_result(case, errors, observed, started)

            if case.get("expected_error"):
                errors.append(f"expected error {case['expected_error']} but extraction succeeded")
            diagnostics = extraction.as_dict()
            observed.update(
                {
                    "methods": [item.extraction_method for item in extraction.page_diagnostics],
                    "layouts": [item.layout_mode for item in extraction.page_diagnostics],
                    "coverage": diagnostics["text_page_coverage"],
                    "blank_pages": extraction.blank_page_count,
                    "ocr_pages": extraction.ocr_page_count,
                    "removed_margin_lines": len(extraction.repeated_margin_lines_removed),
                }
            )
            self._check_equal(errors, "methods", observed["methods"], case.get("expected_methods"))
            self._check_equal(errors, "layouts", observed["layouts"], case.get("expected_layouts"))
            self._check_equal(errors, "coverage", observed["coverage"], case.get("expected_coverage"))
            self._check_equal(errors, "blank_pages", observed["blank_pages"], case.get("expected_blank_pages"))

            lowered = extraction.raw_text.lower()
            missing_tokens = [token for token in case.get("expected_tokens", []) if token.lower() not in lowered]
            forbidden_tokens = [token for token in case.get("forbidden_tokens", []) if token.lower() in lowered]
            if missing_tokens:
                errors.append(f"missing tokens: {missing_tokens}")
            if forbidden_tokens:
                errors.append(f"forbidden tokens retained: {forbidden_tokens}")
            ordered_tokens = case.get("ordered_tokens") or []
            indexes = [lowered.find(str(token).lower()) for token in ordered_tokens]
            if ordered_tokens and (any(index < 0 for index in indexes) or indexes != sorted(indexes)):
                errors.append(f"reading order mismatch: {dict(zip(ordered_tokens, indexes, strict=False))}")
            minimum_confidence = case.get("min_ocr_confidence")
            confidences = [
                item.ocr_confidence
                for item in extraction.page_diagnostics
                if item.extraction_method == "ocr" and item.ocr_confidence is not None
            ]
            observed["ocr_confidences"] = [round(value, 4) for value in confidences]
            if minimum_confidence is not None and (
                not confidences or min(confidences) < float(minimum_confidence)
            ):
                errors.append(f"ocr confidence below {minimum_confidence}: {confidences}")
            minimum_removed = int(case.get("min_removed_margin_lines") or 0)
            if len(extraction.repeated_margin_lines_removed) < minimum_removed:
                errors.append(
                    f"removed_margin_lines={len(extraction.repeated_margin_lines_removed)} expected>={minimum_removed}"
                )
        except Exception as exc:  # noqa: BLE001
            observed["unexpected_exception"] = f"{type(exc).__name__}: {exc}"
            errors.append(observed["unexpected_exception"])
        return self._pdf_result(case, errors, observed, started)

    async def _run_directive_case(
        self,
        db: Session,
        case: dict[str, Any],
        *,
        case_index: int,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        errors: list[str] = []
        observed: dict[str, Any] = {}
        scenario = str(case["scenario"])
        if scenario == "unknown_action":
            try:
                AgentDirectiveRequest(
                    instruction="继续处理岗位",
                    selected_actions=["delete_database"],
                    client_request_id=f"eval-unknown-{case_index:04d}",
                )
                errors.append("unknown selected action was accepted")
            except ValidationError:
                observed["schema_rejected"] = True
            return self._directive_result(case, errors, observed, started)

        source = self._directive_source(db, case, case_index=case_index)
        before = {
            "status": source.status,
            "input_json": copy.deepcopy(source.input_json),
            "output_json": copy.deepcopy(source.output_json),
        }
        mode = "failed" if scenario == "target_failed" else "raise" if scenario == "executor_exception" else "completed"
        agent = _DirectiveEvaluationAgent(mode=mode)
        service = AgentDirectiveService(natural_language=agent)
        base_key = f"eval-directive-{case_index:04d}"

        try:
            if scenario in {"active_reject", "withdrawn_reject"}:
                try:
                    await service.append(
                        db,
                        source_run=source,
                        payload=AgentDirectiveRequest(
                            instruction="城市改为上海并重新搜索岗位",
                            client_request_id=base_key,
                        ),
                    )
                    errors.append("non-branchable source status was accepted")
                except ValueError:
                    observed["rejected"] = True
                if db.query(AgentDirective).filter(AgentDirective.source_run_id == source.id).count() != 0:
                    errors.append("rejected request persisted a directive")
            elif scenario == "idempotent_replay":
                payload = AgentDirectiveRequest(
                    instruction="补充生成面试准备",
                    client_request_id=base_key,
                )
                first = await service.append(db, source_run=source, payload=payload)
                second = await service.append(db, source_run=source, payload=payload)
                observed.update({"first_id": first.id, "second_id": second.id, "executor_calls": len(agent.requests)})
                if first.id != second.id or len(agent.requests) != 1:
                    errors.append("idempotent replay created duplicate work")
            elif scenario == "idempotency_collision":
                await service.append(
                    db,
                    source_run=source,
                    payload=AgentDirectiveRequest(instruction="城市改为上海", client_request_id=base_key),
                )
                try:
                    await service.append(
                        db,
                        source_run=source,
                        payload=AgentDirectiveRequest(instruction="城市改为北京", client_request_id=base_key),
                    )
                    errors.append("same idempotency key accepted a different payload")
                except ValueError:
                    observed["collision_rejected"] = True
            elif scenario == "same_text_new_key":
                first = await service.append(
                    db,
                    source_run=source,
                    payload=AgentDirectiveRequest(instruction="重新搜索岗位", client_request_id=base_key + "-a"),
                )
                second = await service.append(
                    db,
                    source_run=source,
                    payload=AgentDirectiveRequest(instruction="重新搜索岗位", client_request_id=base_key + "-b"),
                )
                if first.id == second.id or len(agent.requests) != 2:
                    errors.append("distinct explicit requests did not create distinct branches")
            elif scenario == "deduplicate_actions":
                directive = await service.append(
                    db,
                    source_run=source,
                    payload=AgentDirectiveRequest(
                        instruction="搜索岗位并准备面试",
                        selected_actions=["search_jobs", "search_jobs", "interview_prep"],
                        client_request_id=base_key,
                    ),
                )
                expected = ["search_jobs", "interview_prep"]
                observed["selected_actions"] = directive.selected_actions_json
                if directive.selected_actions_json != expected or agent.requests[0].selected_actions != expected:
                    errors.append("selected actions were not deduplicated in stable order")
            elif scenario == "root_lineage":
                directive = await self._append_default(service, db, source, base_key)
                target = db.get(AgentRun, directive.target_run_id)
                observed["conversation_root_run_id"] = (target.input_json or {}).get("conversation_root_run_id")
                if observed["conversation_root_run_id"] != 7001:
                    errors.append("existing conversation root was not inherited")
            elif scenario == "tenant_scope":
                directive = await self._append_default(service, db, source, base_key)
                target = db.get(AgentRun, directive.target_run_id)
                if (directive.tenant_id, directive.user_id, target.tenant_id, target.user_id) != (
                    "tenant-eval",
                    "user-eval",
                    "tenant-eval",
                    "user-eval",
                ):
                    errors.append("tenant or user scope was not propagated")
            elif scenario in {"context_minimization", "malformed_context", "limit_clamp", "approval_isolation"}:
                directive = await self._append_default(service, db, source, base_key)
                context = directive.context_json
                observed["context_keys"] = sorted(context)
                if scenario == "context_minimization":
                    allowed = {
                        "source_run_id",
                        "source_task_type",
                        "source_status",
                        "profile_id",
                        "job_id",
                        "resume_version_id",
                        "original_instruction",
                        "original_query",
                        "location",
                        "selected_job",
                        "prior_user_message",
                        "available_artifact_ids",
                    }
                    serialized = json.dumps(context, ensure_ascii=False)
                    if set(context) != allowed:
                        errors.append(f"context keys escaped allowlist: {set(context) - allowed}")
                    if "PRIVATE_INTERNAL_BLOB" in serialized:
                        errors.append("unapproved parent payload leaked into child context")
                    if len(context.get("original_instruction") or "") > 803 or len(context.get("prior_user_message") or "") > 603:
                        errors.append("source context truncation budget was exceeded")
                elif scenario == "malformed_context":
                    if context.get("selected_job") != {} or context.get("available_artifact_ids", {}).get("application_id") is not None:
                        errors.append("malformed legacy values were treated as valid entities")
                elif scenario == "limit_clamp":
                    observed["request_limit"] = agent.requests[0].limit
                    if agent.requests[0].limit != 8:
                        errors.append("invalid legacy limit was not defaulted to 8")
                elif scenario == "approval_isolation":
                    serialized = json.dumps(agent.requests[0].model_dump(), ensure_ascii=False)
                    if "approval-secret-77" in serialized or "approval_id" in serialized:
                        errors.append("parent approval leaked into the child request")
            elif scenario == "parent_immutable":
                await self._append_default(service, db, source, base_key)
                db.refresh(source)
                if source.status != before["status"] or source.input_json != before["input_json"] or source.output_json != before["output_json"]:
                    errors.append("parent run business payload was mutated")
            elif scenario == "target_failed":
                directive = await self._append_default(service, db, source, base_key)
                failed_events = db.query(AgentEvent).filter(
                    AgentEvent.run_id == source.id,
                    AgentEvent.event_type == "user_directive_failed",
                ).count()
                if directive.status != "failed" or not directive.error_message or failed_events != 1:
                    errors.append("failed child was not persisted and traced")
            elif scenario == "executor_exception":
                try:
                    await self._append_default(service, db, source, base_key)
                    errors.append("executor exception was swallowed")
                except RuntimeError:
                    directive = db.query(AgentDirective).filter(AgentDirective.source_run_id == source.id).one()
                    failed_events = db.query(AgentEvent).filter(
                        AgentEvent.run_id == source.id,
                        AgentEvent.event_type == "user_directive_failed",
                    ).count()
                    if directive.status != "failed" or failed_events != 1:
                        errors.append("executor exception did not leave an auditable failure")
            elif scenario == "nested_ids":
                directive = await self._append_default(service, db, source, base_key)
                request = agent.requests[0]
                observed.update(
                    {
                        "profile_id": request.profile_id,
                        "job_id": request.job_id,
                        "resume_version_id": request.resume_version_id,
                    }
                )
                if (request.profile_id, request.job_id, request.resume_version_id) != (301, 401, 501):
                    errors.append("nested artifact ids were not resolved")
            else:
                directive = await self._append_default(service, db, source, base_key)
                if directive.status != "completed" or directive.target_run_id is None:
                    errors.append("follow-up child was not completed")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            observed["unexpected_exception"] = f"{type(exc).__name__}: {exc}"
            errors.append(observed["unexpected_exception"])
        return self._directive_result(case, errors, observed, started)

    async def _append_default(
        self,
        service: AgentDirectiveService,
        db: Session,
        source: AgentRun,
        request_id: str,
    ) -> AgentDirective:
        return await service.append(
            db,
            source_run=source,
            payload=AgentDirectiveRequest(
                instruction="城市改为上海，只重新搜索岗位，不要投递",
                selected_actions=["search_jobs"],
                client_request_id=request_id,
            ),
        )

    def _directive_source(self, db: Session, case: dict[str, Any], *, case_index: int) -> AgentRun:
        scenario = str(case["scenario"])
        input_json: dict[str, Any] = {
            "query": "Agent 开发实习",
            "location": "深圳",
            "limit": 12,
            "instruction": "为目标岗位定制简历",
            "graph_thread_id": f"eval-source-{case_index}",
        }
        output_json: dict[str, Any] = {
            "resume_version_id": 91,
            "user_message": "上一轮流程已完成",
        }
        if scenario == "root_lineage":
            input_json["conversation_root_run_id"] = 7001
        elif scenario == "context_minimization":
            input_json["instruction"] = "原始要求" + "很长" * 1000
            input_json["private_internal_blob"] = "PRIVATE_INTERNAL_BLOB"
            output_json.update(
                {
                    "user_message": "结果" * 1000,
                    "selected_job": {
                        "job_id": 44,
                        "title": "Agent 工程师",
                        "company": "示例公司",
                        "location": "深圳",
                        "raw_jd_text": "PRIVATE_INTERNAL_BLOB",
                    },
                    "debug_trace": "PRIVATE_INTERNAL_BLOB",
                }
            )
        elif scenario == "malformed_context":
            output_json.update({"selected_job": "broken", "application": "broken"})
        elif scenario == "limit_clamp":
            input_json["limit"] = "legacy-invalid-limit"
        elif scenario == "approval_isolation":
            output_json.update(
                {
                    "approval_id": "approval-secret-77",
                    "application": {"application_id": 73, "approval_id": "approval-secret-77"},
                }
            )
        elif scenario == "nested_ids":
            output_json = {
                "result_json": {
                    "profile": {"id": 301},
                    "job": {"id": 401},
                    "tailor": {"resume_version_id": 501},
                },
                "user_message": "旧版嵌套输出",
            }
        run = AgentRun(
            tenant_id="tenant-eval",
            user_id="user-eval",
            task_type="tailor_resume_for_job",
            status=str(case.get("source_status") or "completed"),
            input_json=input_json,
            output_json=output_json,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def _pdf_payload(self, factory: str) -> bytes:
        import pymupdf as fitz

        if factory == "empty_upload":
            return b""
        if factory == "forged_signature":
            return b"this is not a pdf"
        if factory == "corrupt":
            return b"%PDF-1.7\ncorrupt"
        if factory == "plain_text":
            return self._text_pdf([["CareerAgent project uses LangGraph FastAPI and evaluation gates."]])
        if factory == "two_text_pages":
            return self._text_pdf([["First resume page with enough content for extraction."], ["Second project page."]])
        if factory == "oversized":
            return self._text_pdf([["CareerAgent resume with enough valid text."]]) + b"0" * (1024 * 1024 + 1)
        if factory == "short_text":
            return self._text_pdf([["x"]])
        if factory == "text_plus_blank":
            return self._text_pdf([["CareerAgent resume contains valid project experience."], []])
        if factory == "repeated_header":
            return self._text_pdf(
                [
                    ["Li Ming Resume - Page 1 of 2", "Project One implemented FastAPI and LangGraph."],
                    ["Li Ming Resume - Page 2 of 2", "Project Two implemented Redis recovery and tests."],
                ]
            )
        if factory == "text_two_column":
            return self._positioned_pdf(
                [
                    (50, 100, "Left Skills"),
                    (50, 160, "Left Education"),
                    (340, 100, "Right Project"),
                    (340, 160, "Right Impact"),
                ]
            )
        if factory == "pure_scan":
            return self._scan_pdf([["CareerAgent project", "Implemented LangGraph Redis recovery", "Measured retrieval quality"]])
        if factory == "mixed_text_scan":
            text = self._text_pdf([["Li Ming resume uses Python FastAPI for Agent development."]])
            scan = self._scan_pdf([["CareerAgent project", "Implemented checkpoint recovery and Redis"]])
            target = fitz.open(stream=text, filetype="pdf")
            scan_doc = fitz.open(stream=scan, filetype="pdf")
            target.insert_pdf(scan_doc)
            payload = target.tobytes()
            target.close()
            scan_doc.close()
            return payload
        if factory == "scan_two_column":
            positioned = self._positioned_pdf(
                [
                    (50, 100, "Left Skills"),
                    (50, 180, "Left Education"),
                    (650, 100, "Right Project"),
                    (650, 180, "Right Impact"),
                ],
                width=1200,
                height=500,
                font_size=30,
            )
            return self._rasterize_pdf(positioned)
        if factory == "blank_scan":
            blank = self._text_pdf([[]])
            return self._rasterize_pdf(blank)
        if factory == "huge_image_page":
            small_blank = self._rasterize_pdf(self._text_pdf([[]]))
            small_doc = fitz.open(stream=small_blank, filetype="pdf")
            image = small_doc[0].get_pixmap(dpi=72, alpha=False).tobytes("png")
            document = fitz.open()
            page = document.new_page(width=10000, height=10000)
            page.insert_image(page.rect, stream=image)
            payload = document.tobytes()
            document.close()
            small_doc.close()
            return payload
        if factory == "encrypted":
            reader = PdfReader(BytesIO(self._text_pdf([["Private CareerAgent resume content."]])))
            writer = PdfWriter()
            writer.append_pages_from_reader(reader)
            writer.encrypt("secret")
            output = BytesIO()
            writer.write(output)
            return output.getvalue()
        raise ValueError(f"Unknown PDF bad-case factory: {factory}")

    @staticmethod
    def _text_pdf(pages: list[list[str]]) -> bytes:
        import pymupdf as fitz

        document = fitz.open()
        for lines in pages:
            page = document.new_page(width=595, height=842)
            for index, line in enumerate(lines):
                page.insert_text((50, 70 + index * 54), line, fontsize=14)
        payload = document.tobytes()
        document.close()
        return payload

    @staticmethod
    def _positioned_pdf(
        rows: list[tuple[float, float, str]],
        *,
        width: float = 595,
        height: float = 842,
        font_size: float = 14,
    ) -> bytes:
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=width, height=height)
        for x, y, text in rows:
            page.insert_text((x, y), text, fontsize=font_size)
        payload = document.tobytes()
        document.close()
        return payload

    def _scan_pdf(self, pages: list[list[str]]) -> bytes:
        return self._rasterize_pdf(self._text_pdf(pages))

    @staticmethod
    def _rasterize_pdf(payload: bytes) -> bytes:
        import pymupdf as fitz

        source = fitz.open(stream=payload, filetype="pdf")
        target = fitz.open()
        for source_page in source:
            pixmap = source_page.get_pixmap(dpi=180, alpha=False)
            page = target.new_page(width=source_page.rect.width, height=source_page.rect.height)
            page.insert_image(page.rect, stream=pixmap.tobytes("png"))
        result = target.tobytes()
        source.close()
        target.close()
        return result

    @staticmethod
    def _bridge_pages(factory: str) -> list[PDFPageText]:
        if factory == "bridge_stranded_heading":
            return [
                PDFPageText(1, "Delivered retrieval evaluation and worker recovery.\nCareerAgent Project"),
                PDFPageText(2, "Implemented LangGraph checkpoints and measurable RAG gates."),
            ]
        if factory == "bridge_bullet_continuation":
            return [
                PDFPageText(1, "Project delivery completed."),
                PDFPageText(2, "• Redis recovery and idempotent application writes"),
            ]
        if factory == "bridge_split_sentence":
            return [
                PDFPageText(1, "Designed a durable Agent workflow with"),
                PDFPageText(2, "checkpoint recovery, Redis queueing and approval isolation."),
            ]
        if factory == "bridge_independent_pages":
            return [
                PDFPageText(1, "Education\nSoftware Engineering degree completed."),
                PDFPageText(2, "Project Experience\nBuilt CareerAgent with LangGraph."),
            ]
        if factory == "bridge_unrelated_sections":
            return [
                PDFPageText(1, "Languages\nEnglish CET-6."),
                PDFPageText(2, "Awards\nNational software competition finalist."),
            ]
        raise ValueError(f"Unknown bridge factory: {factory}")

    def _summarize_pdf(self, results: list[dict[str, Any]], path: Path) -> dict[str, Any]:
        bridge_rows = [row for row in results if row["category"] == "cross_page_bridge"]
        true_positive = sum(row["expected_bridge"] and row["observed"].get("bridge_created") for row in bridge_rows)
        false_positive = sum(not row["expected_bridge"] and row["observed"].get("bridge_created") for row in bridge_rows)
        false_negative = sum(row["expected_bridge"] and not row["observed"].get("bridge_created") for row in bridge_rows)
        true_negative = sum(not row["expected_bridge"] and not row["observed"].get("bridge_created") for row in bridge_rows)
        expected_error_rows = [row for row in results if row.get("expected_error")]
        critical_rows = [row for row in results if row["critical"]]
        summary = {
            "evaluation_type": "pdf_extraction_bad_cases",
            "dataset": path.name,
            "case_count": len(results),
            "pass_rate": self._rate(results),
            "critical_case_pass_rate": self._rate(critical_rows),
            "expected_error_accuracy": self._rate(expected_error_rows),
            "bridge_precision": self._ratio(true_positive, true_positive + false_positive),
            "bridge_recall": self._ratio(true_positive, true_positive + false_negative),
            "bridge_false_positive_rate": self._ratio(false_positive, false_positive + true_negative),
            "category_breakdown": self._category_breakdown(results),
            "latency_ms": self._latency_summary(results),
            "failed_cases": [row["name"] for row in results if not row["passed"]],
        }
        summary["release_gate"] = self._release_gate(
            [
                ("pass_rate", summary["pass_rate"], ">=", 0.95),
                ("critical_case_pass_rate", summary["critical_case_pass_rate"], "==", 1.0),
                ("expected_error_accuracy", summary["expected_error_accuracy"], "==", 1.0),
                ("bridge_precision", summary["bridge_precision"], "==", 1.0),
                ("bridge_recall", summary["bridge_recall"], "==", 1.0),
            ]
        )
        return summary

    def _summarize_directives(self, results: list[dict[str, Any]], path: Path) -> dict[str, Any]:
        critical_rows = [row for row in results if row["critical"]]
        category = self._category_breakdown(results)
        summary = {
            "evaluation_type": "follow_up_directive_bad_cases",
            "dataset": path.name,
            "case_count": len(results),
            "pass_rate": self._rate(results),
            "critical_case_pass_rate": self._rate(critical_rows),
            "concurrency_guard_accuracy": category.get("concurrency_guard", {}).get("pass_rate", 0.0),
            "idempotency_safety_rate": category.get("idempotency_safety", {}).get("pass_rate", 0.0),
            "lineage_integrity_rate": category.get("lineage_integrity", {}).get("pass_rate", 0.0),
            "context_minimization_rate": category.get("context_minimization", {}).get("pass_rate", 0.0),
            "failure_audit_rate": category.get("failure_audit", {}).get("pass_rate", 0.0),
            "category_breakdown": category,
            "latency_ms": self._latency_summary(results),
            "failed_cases": [row["name"] for row in results if not row["passed"]],
        }
        summary["release_gate"] = self._release_gate(
            [
                ("pass_rate", summary["pass_rate"], "==", 1.0),
                ("critical_case_pass_rate", summary["critical_case_pass_rate"], "==", 1.0),
                ("concurrency_guard_accuracy", summary["concurrency_guard_accuracy"], "==", 1.0),
                ("idempotency_safety_rate", summary["idempotency_safety_rate"], "==", 1.0),
                ("lineage_integrity_rate", summary["lineage_integrity_rate"], "==", 1.0),
                ("context_minimization_rate", summary["context_minimization_rate"], "==", 1.0),
                ("failure_audit_rate", summary["failure_audit_rate"], "==", 1.0),
            ]
        )
        return summary

    @staticmethod
    def _pdf_result(
        case: dict[str, Any],
        errors: list[str],
        observed: dict[str, Any],
        started: float,
    ) -> dict[str, Any]:
        return {
            "name": case["name"],
            "category": case["category"],
            "difficulty": case["difficulty"],
            "critical": bool(case.get("critical")),
            "expected_error": case.get("expected_error"),
            "expected_bridge": case.get("expected_bridge"),
            "passed": not errors,
            "errors": errors,
            "observed": observed,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    @staticmethod
    def _directive_result(
        case: dict[str, Any],
        errors: list[str],
        observed: dict[str, Any],
        started: float,
    ) -> dict[str, Any]:
        return {
            "name": case["name"],
            "category": case["category"],
            "difficulty": case["difficulty"],
            "critical": bool(case.get("critical")),
            "scenario": case["scenario"],
            "passed": not errors,
            "errors": errors,
            "observed": observed,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    @staticmethod
    def _check_equal(errors: list[str], field: str, actual: Any, expected: Any) -> None:
        if expected is not None and actual != expected:
            errors.append(f"{field}={actual!r} expected={expected!r}")

    @staticmethod
    def _load_cases(path: Path) -> list[dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"Bad-case dataset must be a list: {path}")
        return payload

    @staticmethod
    def _persist(
        db: Session,
        name: str,
        summary: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> EvaluationRun:
        run = EvaluationRun(name=name, summary_json=summary, case_results_json=results)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    def _rate(rows: list[dict[str, Any]]) -> float:
        return CapabilityBadCaseEvaluationService._ratio(sum(bool(row["passed"]) for row in rows), len(rows))

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    @staticmethod
    def _category_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
        categories: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            categories.setdefault(str(row["category"]), []).append(row)
        return {
            key: {
                "case_count": len(items),
                "pass_rate": CapabilityBadCaseEvaluationService._rate(items),
                "failed_cases": [item["name"] for item in items if not item["passed"]],
            }
            for key, items in sorted(categories.items())
        }

    @staticmethod
    def _latency_summary(rows: list[dict[str, Any]]) -> dict[str, float]:
        values = sorted(float(row["latency_ms"]) for row in rows)
        if not values:
            return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}

        def percentile(value: float) -> float:
            index = min(math.ceil(len(values) * value) - 1, len(values) - 1)
            return round(values[max(index, 0)], 2)

        return {
            "avg": round(sum(values) / len(values), 2),
            "p50": percentile(0.5),
            "p95": percentile(0.95),
            "max": round(values[-1], 2),
        }

    @staticmethod
    def _release_gate(checks: list[tuple[str, float, str, float]]) -> dict[str, Any]:
        rows = []
        for metric, actual, operator, threshold in checks:
            passed = actual >= threshold if operator == ">=" else actual == threshold
            rows.append(
                {
                    "metric": metric,
                    "actual": actual,
                    "operator": operator,
                    "threshold": threshold,
                    "passed": passed,
                }
            )
        return {"passed": all(row["passed"] for row in rows), "checks": rows}
