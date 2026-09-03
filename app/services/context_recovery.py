from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import AgentApproval, AgentArtifact, AgentRun, Job, Profile, ResumeVersion
from app.models.schemas import TaskState
from app.services.context_runtime import (
    ContextIntegrityError,
    ContextJITLoader,
    ContextRequest,
    ContextRuntimeV2,
    ContextScope,
)


SIDE_EFFECT_ARTIFACTS = {"browser_apply_result", "email_draft_result", "email_send_result"}


@dataclass(frozen=True)
class ContextRecoveryResult:
    context_refs: dict[str, Any]
    packet: dict[str, Any]
    next_node: str
    executed_side_effect_receipts: list[str]
    task_state: dict[str, Any]


class ContextRecoveryService:
    """Rebuild next-node context from checkpoint references and authoritative tables."""

    NODE_ALIASES = {
        "plan_task": "natural_language_planner",
        "find_jobs": "job_matcher",
        "select_job": "job_matcher",
        "match_job": "job_matcher",
        "tailor_resume": "resume_tailor",
        "verify_tailored_resume": "guardrail",
        "create_application": "application_packet",
        "prepare_interview": "interview_question_generator",
        "completion_gate": "completion_gate",
    }

    def __init__(self, runtime: ContextRuntimeV2 | None = None) -> None:
        self.runtime = runtime or ContextRuntimeV2()

    def build_refs(
        self,
        db: Session,
        *,
        run: AgentRun,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        prior = dict(state.get("context_refs") or {})
        profile_id = state.get("profile_id") or run.profile_id or prior.get("profile_id")
        job_id = state.get("selected_job_id") or state.get("job_id") or run.job_id or prior.get("job_id")
        resume_version_id = state.get("resume_version_id") or prior.get("resume_version_id")
        self._validate_business_scope(
            db,
            run=run,
            profile_id=profile_id,
            job_id=job_id,
            resume_version_id=resume_version_id,
        )
        artifacts = (
            db.query(AgentArtifact)
            .filter(AgentArtifact.run_id == run.id)
            .order_by(AgentArtifact.id.asc())
            .all()
        )
        artifact_ids = [row.id for row in artifacts]
        summary = next(
            (row for row in reversed(artifacts) if row.artifact_type == "conversation_summary"),
            None,
        )
        approvals = (
            db.query(AgentApproval)
            .filter(AgentApproval.run_id == run.id)
            .order_by(AgentApproval.id.asc())
            .all()
        )
        receipts = self._side_effect_receipts(artifacts)
        evidence_citations = list(
            dict.fromkeys(
                [
                    *[str(item) for item in prior.get("evidence_citations") or [] if item],
                    *[
                        str(item.get("citation_id") or item.get("chunk_uid"))
                        for item in state.get("evidence_chunks") or []
                        if item.get("citation_id") or item.get("chunk_uid")
                    ],
                ]
            )
        )
        return {
            "profile_id": profile_id,
            "job_id": job_id,
            "resume_version_id": resume_version_id,
            "evidence_citations": evidence_citations,
            "artifact_ids": artifact_ids,
            "approval_id": approvals[-1].id if approvals else prior.get("approval_id"),
            "tool_receipt_ids": receipts,
            "conversation_summary_artifact_id": (
                summary.id if summary is not None else prior.get("conversation_summary_artifact_id")
            ),
            "task_state_version": TaskState.model_validate(state.get("task_state") or {}).version,
            "data_versions": self._data_versions(db, profile_id=profile_id, job_id=job_id),
        }

    def rebuild_for_next_node(
        self,
        db: Session,
        *,
        run: AgentRun,
        state: dict[str, Any],
        next_node: str,
    ) -> ContextRecoveryResult:
        refs = self.build_refs(db, run=run, state=state)
        task_state = TaskState.model_validate(state.get("task_state") or {})
        if refs.get("task_state_version") != task_state.version:
            raise ContextIntegrityError("Checkpoint task state version does not match context refs.")
        contract_node = self.NODE_ALIASES.get(next_node, next_node)
        try:
            contract = self.runtime.contract_for(contract_node)
        except KeyError:
            contract = self.runtime.contract_for("completion_gate")
            contract_node = "completion_gate"
        scope = ContextScope(
            tenant_id=str(run.tenant_id or "default"),
            user_id=str(run.user_id or "runtime"),
            profile_id=refs.get("profile_id"),
        )
        working = self._working_for_contract(db, refs=refs, contract_name=contract.name, state=state)
        evidence = self._load_evidence(db, scope=scope, citation_ids=refs["evidence_citations"])
        artifacts = self._artifact_refs(db, run=run, artifact_ids=refs["artifact_ids"])
        loader = ContextJITLoader(
            db,
            scope=scope,
            allowed_operations={
                "load_profile_fragment",
                "load_job_fragment",
                "load_evidence_fragment",
                "load_artifact_excerpt",
            },
        )
        packet = self.runtime.build(
            ContextRequest(
                run_id=run.id,
                node=contract_node,
                task_type=run.task_type,
                scope=scope,
                control={
                    "recovery": "Resume from checkpoint without replaying successful side effects.",
                    "next_node": next_node,
                },
                working=working,
                evidence=evidence,
                artifacts=artifacts,
                query=str(state.get("query") or ""),
                prompt_version="checkpoint-context-recovery-v1",
                data_version=self._hash(refs.get("data_versions") or {}),
                jit_loader=loader,
            )
        ).packet
        return ContextRecoveryResult(
            context_refs=refs,
            packet=packet,
            next_node=next_node,
            executed_side_effect_receipts=list(refs["tool_receipt_ids"]),
            task_state=task_state.model_dump(),
        )

    @staticmethod
    def side_effect_already_executed(context_refs: dict[str, Any], receipt_id: str) -> bool:
        return receipt_id in {str(item) for item in context_refs.get("tool_receipt_ids") or []}

    def _working_for_contract(
        self,
        db: Session,
        *,
        refs: dict[str, Any],
        contract_name: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        profile = db.get(Profile, refs.get("profile_id")) if refs.get("profile_id") else None
        job = db.get(Job, refs.get("job_id")) if refs.get("job_id") else None
        resume = (
            db.get(ResumeVersion, refs.get("resume_version_id"))
            if refs.get("resume_version_id")
            else None
        )
        if contract_name == "completion_gate":
            return {
                "goal": state.get("task_contract") or state.get("request") or {},
                "steps": state.get("goal_ledger") or [],
                "artifact_refs": refs["artifact_ids"],
                "profile_id": refs.get("profile_id"),
                "job_id": refs.get("job_id"),
                "resume_version_id": refs.get("resume_version_id"),
                "approval_status": state.get("human_confirmation") or {},
                "business_terminal_state": state.get("completion_verification") or {},
                "tool_receipts": refs["tool_receipt_ids"],
            }
        if contract_name == "application_packet":
            return {
                "profile": (profile.structured_profile_json or {}) if profile else {},
                "job": (job.structured_jd_json or {}) if job else {},
                "verified_resume": resume.tailored_resume_markdown if resume else "",
                "profile_id": refs.get("profile_id"),
                "job_id": refs.get("job_id"),
                "resume_version_id": refs.get("resume_version_id"),
                "approval_status": state.get("human_confirmation") or {},
                "tool_receipts": refs["tool_receipt_ids"],
            }
        working = {
            "profile": (profile.structured_profile_json or {}) if profile else {},
            "job": (job.structured_jd_json or {}) if job else {},
            "query": state.get("query") or "",
        }
        if contract_name == "resume_tailor":
            working["evidence"] = refs["evidence_citations"]
        if contract_name == "guardrail":
            working = {
                "candidate_output": resume.tailored_resume_markdown if resume else "",
                "source_facts": (profile.structured_profile_json or {}) if profile else {},
                "citations": refs["evidence_citations"],
            }
        return working

    @staticmethod
    def _load_evidence(
        db: Session,
        *,
        scope: ContextScope,
        citation_ids: list[str],
    ) -> list[dict[str, Any]]:
        loader = ContextJITLoader(
            db,
            scope=scope,
            max_calls=max(len(citation_ids), 1),
            allowed_operations={"load_evidence_fragment"},
        )
        output = []
        for citation_id in citation_ids:
            receipt = loader.load_evidence_fragment(citation_id)
            output.append(
                {
                    "citation_id": citation_id,
                    "evidence_type": receipt["source_type"],
                    "text": receipt["value"],
                    "source_id": receipt["source_id"],
                    "trust": 1.0,
                }
            )
        return output

    @staticmethod
    def _artifact_refs(
        db: Session,
        *,
        run: AgentRun,
        artifact_ids: list[int],
    ) -> list[dict[str, Any]]:
        rows = (
            db.query(AgentArtifact)
            .filter(AgentArtifact.run_id == run.id, AgentArtifact.id.in_(artifact_ids or [-1]))
            .all()
        )
        return [
            {
                "artifact_id": row.id,
                "artifact_type": row.artifact_type,
                "status": "available",
                "sha256": ContextRecoveryService._hash(row.artifact_json),
                "summary": ContextRecoveryService._artifact_summary(row),
            }
            for row in rows
        ]

    @staticmethod
    def _artifact_summary(row: AgentArtifact) -> str:
        payload = row.artifact_json or {}
        return str(payload.get("status") or payload.get("summary") or row.artifact_type)[:240]

    @staticmethod
    def _side_effect_receipts(artifacts: list[AgentArtifact]) -> list[str]:
        receipts = []
        for row in artifacts:
            if row.artifact_type not in SIDE_EFFECT_ARTIFACTS:
                continue
            payload = row.artifact_json or {}
            result = payload.get("tool_result") or payload
            if result.get("status") not in {"tool_execution_completed", "email_sent", "completed"} and not result.get(
                "submitted"
            ):
                continue
            receipt_id = result.get("receipt_id") or payload.get("receipt_id") or f"artifact:{row.id}"
            receipts.append(str(receipt_id))
        return list(dict.fromkeys(receipts))

    @staticmethod
    def _validate_business_scope(
        db: Session,
        *,
        run: AgentRun,
        profile_id: int | None,
        job_id: int | None,
        resume_version_id: int | None,
    ) -> None:
        if profile_id:
            profile = db.get(Profile, profile_id)
            if profile is None or profile.tenant_id != run.tenant_id:
                raise ContextIntegrityError("Checkpoint Profile is outside the Run tenant.")
        if job_id:
            job = db.get(Job, job_id)
            if job is None or job.tenant_id not in {None, run.tenant_id}:
                raise ContextIntegrityError("Checkpoint Job is outside the Run tenant.")
        if resume_version_id:
            resume = db.get(ResumeVersion, resume_version_id)
            if resume is None or resume.profile_id != profile_id or resume.job_id != job_id:
                raise ContextIntegrityError("Checkpoint ResumeVersion does not match Profile/Job refs.")

    @staticmethod
    def _data_versions(
        db: Session, *, profile_id: int | None, job_id: int | None
    ) -> dict[str, str]:
        profile = db.get(Profile, profile_id) if profile_id else None
        job = db.get(Job, job_id) if job_id else None
        return {
            "profile": ContextRecoveryService._hash(
                (profile.structured_profile_json or {}) if profile else {}
            ),
            "job": ContextRecoveryService._hash((job.structured_jd_json or {}) if job else {}),
        }

    @staticmethod
    def _hash(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
