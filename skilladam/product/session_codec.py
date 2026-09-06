"""Decode public models from product session checkpoints."""

from __future__ import annotations

from typing import Any, Mapping

from skilladam.core.diff_apply import PatchHunk
from skilladam.product.feedback import IterationFeedback, ProductTrajectory
from skilladam.product.models import GateResult, ValidationResult
from skilladam.product.patch_review import (
    CandidateSnapshot,
    PatchProposal,
    PatchSelection,
)


def proposal_from_dict(payload: Mapping[str, Any]) -> PatchProposal:
    return PatchProposal(
        proposal_id=str(payload["proposal_id"]),
        base_digest=str(payload["base_digest"]),
        raw_diff=str(payload["raw_diff"]),
        hunks=tuple(
            PatchHunk(
                hunk_id=str(item["hunk_id"]),
                target_path=str(item["target_path"]),
                old_start=int(item["old_start"]),
                old_count=int(item["old_count"]),
                new_start=int(item["new_start"]),
                new_count=int(item["new_count"]),
                operations=tuple(
                    (str(operation), str(line))
                    for operation, line in item["operations"]
                ),
                order=int(item["order"]),
                schema_version=str(item.get("schema_version", "1")),
            )
            for item in payload["hunks"]
        ),
        mutable_paths=tuple(str(item) for item in payload["mutable_paths"]),
        parser_version=str(payload.get("parser_version", "1")),
        schema_version=str(payload.get("schema_version", "1")),
    )


def selection_from_dict(payload: Mapping[str, Any]) -> PatchSelection:
    return PatchSelection(
        proposal_id=str(payload["proposal_id"]),
        accepted_hunk_ids=tuple(payload["accepted_hunk_ids"]),
        rejected_hunk_ids=tuple(payload["rejected_hunk_ids"]),
        actor=str(payload.get("actor", "user")),
        reason=str(payload.get("reason", "")),
        idempotency_key=str(payload.get("idempotency_key", "")),
        schema_version=str(payload.get("schema_version", "1")),
    )


def candidate_from_dict(payload: Mapping[str, Any]) -> CandidateSnapshot:
    return CandidateSnapshot(
        base_digest=str(payload["base_digest"]),
        proposal_id=str(payload["proposal_id"]),
        applied_hunk_ids=tuple(payload["applied_hunk_ids"]),
        files=dict(payload["files"]),
        metadata=dict(payload.get("metadata", {})),
        schema_version=str(payload.get("schema_version", "1")),
    )


def validation_from_dict(payload: Mapping[str, Any]) -> ValidationResult:
    return ValidationResult(
        candidate_digest=str(payload["candidate_digest"]),
        evaluation_plan_digest=str(payload["evaluation_plan_digest"]),
        primary=float(payload["primary"]),
        metrics={str(key): float(value) for key, value in payload["metrics"].items()},
        case_results=dict(payload["case_results"]),
        metadata=dict(payload.get("metadata", {})),
        schema_version=str(payload.get("schema_version", "1")),
    )


def feedback_from_dict(payload: Mapping[str, Any]) -> IterationFeedback:
    return IterationFeedback(
        task_ids=tuple(str(item) for item in payload["task_ids"]),
        trajectories=tuple(
            ProductTrajectory(
                task_id=str(item["task_id"]),
                query=str(item["query"]),
                messages=tuple(dict(message) for message in item["messages"]),
                final_output=item.get("final_output"),
                evaluation=dict(item["evaluation"]),
                outcome_label=str(item["outcome_label"]),
                condensed_markdown=str(item["condensed_markdown"]),
                metadata=dict(item.get("metadata", {})),
            )
            for item in payload["trajectories"]
        ),
        validation=validation_from_dict(payload["validation"]),
        metadata=dict(payload.get("metadata", {})),
    )


def gate_from_dict(payload: Mapping[str, Any]) -> GateResult:
    return GateResult(
        accepted=bool(payload["accepted"]),
        reason=str(payload["reason"]),
        baseline_validation_digest=str(payload["baseline_validation_digest"]),
        candidate_validation_digest=str(payload["candidate_validation_digest"]),
        metadata=dict(payload.get("metadata", {})),
        schema_version=str(payload.get("schema_version", "1")),
    )


__all__ = [
    "candidate_from_dict",
    "feedback_from_dict",
    "gate_from_dict",
    "proposal_from_dict",
    "selection_from_dict",
    "validation_from_dict",
]
