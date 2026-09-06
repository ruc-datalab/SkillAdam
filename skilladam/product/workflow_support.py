"""Pure state, view, and core metric adapters for OptimizationWorkflow."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from skilladam.core.case_sampler import IterationCases
from skilladam.core.edit_budget import (
    compute_edit_budget,
    compute_per_case_deltas,
    compute_sigma_sq,
    update_v_ema,
)
from skilladam.core.feedback_loop import FeedbackLoopConfig
from skilladam.core.momentum import MomentumTracker
from skilladam.product.models import (
    EvaluationPlan,
    GateResult,
    OptimizationSession,
    SkillPackageSnapshot,
    ValidationResult,
    digest_value,
)
from skilladam.product.patch_review import CandidateSnapshot
from skilladam.product.session_codec import (
    candidate_from_dict,
    gate_from_dict,
    proposal_from_dict,
    validation_from_dict,
)
from skilladam.types import MetricResult


def build_run_signature(
    *,
    package: SkillPackageSnapshot,
    plan: EvaluationPlan,
    batches: Sequence[IterationCases],
    loop_config: FeedbackLoopConfig,
    gate_config: Mapping[str, Any],
    momentum_update_enabled: bool = False,
) -> str:
    signature = {
        "package_digest": package.package_digest,
        "entrypoint": package.entrypoint,
        "mutable_paths": package.mutable_paths,
        "evaluation_plan_digest": plan.digest,
        "batches": [
            {
                "training": batch.training_case_ids,
                "validation": batch.validation_case_ids,
            }
            for batch in batches
        ],
        "loop_config": asdict(loop_config),
        "gate": gate_config,
    }
    if momentum_update_enabled:
        signature["momentum_update_enabled"] = True
    return digest_value(signature)


def new_session_payload(
    *,
    schema_version: int,
    run_signature: str,
    session_id: str,
    package: SkillPackageSnapshot,
    momentum: MomentumTracker,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "run_signature": run_signature,
        "session_id": session_id,
        "state": "ready",
        "iteration": 0,
        "current_files": dict(package.files),
        "consecutive_failures": 0,
        "skill_ever_accepted": False,
        "stop_reason": "",
        "v_ema": 0.0,
        "records": [],
        "proposal": None,
        "iteration_feedback": None,
        "baseline_validation": None,
        "selection": None,
        "candidate": None,
        "candidate_validation": None,
        "patch_attempts": 0,
        "edit_budget": None,
        "momentum_update": None,
        "last_proposal": None,
        "last_baseline_validation": None,
        "last_candidate_validation": None,
        "last_gate": None,
        "last_selection_digest": "",
        "last_idempotency_key": "",
        "momentum": momentum.to_dict(),
    }


def build_session_view(
    *,
    payload: Mapping[str, Any],
    plan: EvaluationPlan,
    checkpoint_path: Path,
    run_signature: str,
    view_type,
):
    active = payload["state"] in {"awaiting_review", "validating"}
    proposal_payload = (
        payload.get("proposal") if active else payload.get("last_proposal")
    )
    baseline_payload = (
        payload.get("baseline_validation")
        if active
        else payload.get("last_baseline_validation")
    )
    candidate_validation_payload = (
        payload.get("candidate_validation")
        if active
        else payload.get("last_candidate_validation")
    )
    gate_payload = None if active else payload.get("last_gate")
    proposal = (
        proposal_from_dict(proposal_payload)
        if proposal_payload is not None
        else None
    )
    candidate = payload.get("candidate")
    session = OptimizationSession(
        session_id=str(payload["session_id"]),
        state=payload["state"],
        iteration=int(payload["iteration"]),
        package_digest=digest_value(payload["current_files"]),
        task_suite_digest=plan.suite_digest,
        evaluation_plan_digest=plan.digest,
        proposal_id=proposal.proposal_id if proposal else "",
        candidate_digest=(
            candidate_from_dict(candidate).candidate_digest
            if candidate is not None
            else ""
        ),
        stop_reason=str(payload["stop_reason"]),
        artifacts={
            "checkpoint": str(checkpoint_path),
            "final_validation": str(
                checkpoint_path.parent / "final_validation.json"
            ),
            "iterations": str(checkpoint_path.parent / "iterations"),
        },
        metadata={
            "consecutive_failures": payload["consecutive_failures"],
            "skill_ever_accepted": payload["skill_ever_accepted"],
        },
    )
    return view_type(
        session=session,
        proposal=proposal,
        baseline_validation=(
            validation_from_dict(baseline_payload)
            if baseline_payload is not None
            else None
        ),
        candidate_validation=(
            validation_from_dict(candidate_validation_payload)
            if candidate_validation_payload is not None
            else None
        ),
        gate=(
            gate_from_dict(gate_payload) if gate_payload is not None else None
        ),
        records=tuple(payload["records"]),
        metadata={"run_signature": run_signature},
    )


def metric_result(validation: ValidationResult) -> MetricResult:
    case_metrics = {
        task_id: {
            "score": float(result["score"]),
            "primary": float(result["score"]),
        }
        for task_id, result in validation.case_results.items()
    }
    return MetricResult(
        primary=validation.primary,
        metrics={key: float(value) for key, value in validation.metrics.items()},
        case_metrics=case_metrics,
        sample_count=len(case_metrics),
        metadata={
            **dict(validation.metadata),
            "evaluation_plan_digest": validation.evaluation_plan_digest,
            "candidate_digest": validation.candidate_digest,
        },
    )


def append_iteration_record(
    payload: dict[str, Any],
    batch: IterationCases,
    *,
    accepted: bool,
    reason: str,
    candidate: CandidateSnapshot | None,
    candidate_validation: ValidationResult | None,
    gate: GateResult | None,
    sigma_sq: float | None,
    v_ema_after: float | None,
    momentum_update: Mapping[str, Any] | None = None,
) -> None:
    proposal = payload.get("proposal")
    selection = payload.get("selection")
    payload["records"].append(
        {
            "iteration": payload["iteration"],
            "training_case_ids": list(batch.training_case_ids),
            "validation_case_ids": list(batch.validation_case_ids),
            "accepted": accepted,
            "reason": reason,
            "patch_attempts": payload["patch_attempts"],
            "edit_budget": payload["edit_budget"],
            "proposal_id": proposal["proposal_id"] if proposal else "",
            "selection": selection,
            "candidate_digest": (
                candidate.candidate_digest if candidate else ""
            ),
            "baseline_validation": payload.get("baseline_validation"),
            "iteration_feedback": payload.get("iteration_feedback"),
            "candidate_validation": (
                candidate_validation.to_dict()
                if candidate_validation is not None
                else None
            ),
            "gate": gate.to_dict() if gate is not None else None,
            "sigma_sq": sigma_sq,
            "v_ema_after": v_ema_after,
            "momentum_update": (
                dict(momentum_update) if momentum_update is not None else None
            ),
        }
    )
    payload["last_proposal"] = proposal
    payload["last_baseline_validation"] = payload.get(
        "baseline_validation"
    )
    payload["proposal"] = None
    payload["selection"] = None
    payload["candidate"] = None
    payload["candidate_validation"] = None
    payload["baseline_validation"] = None
    payload["iteration_feedback"] = None
    payload["momentum_update"] = None


def current_edit_budget(
    payload: Mapping[str, Any],
    config: FeedbackLoopConfig,
) -> int | None:
    budget = config.edit_budget
    if budget is None:
        return None
    return compute_edit_budget(
        payload["v_ema"],
        v_max=budget.v_max,
        base=budget.base,
        minimum=budget.minimum,
    )


def update_edit_budget(
    payload: dict[str, Any],
    config: FeedbackLoopConfig,
    baseline: MetricResult,
    candidate: MetricResult,
) -> tuple[float | None, float | None]:
    budget = config.edit_budget
    if budget is None:
        return None, None
    deltas = compute_per_case_deltas(
        baseline,
        candidate,
        metric_key=budget.metric_key,
    )
    sigma_sq = compute_sigma_sq(deltas)
    payload["v_ema"] = update_v_ema(
        payload["v_ema"],
        sigma_sq,
        beta=budget.beta,
    )
    return sigma_sq, payload["v_ema"]


def advance_iteration(
    payload: dict[str, Any],
    config: FeedbackLoopConfig,
    batch_count: int,
) -> None:
    payload["iteration"] += 1
    completed = payload["iteration"]
    if (
        payload["skill_ever_accepted"]
        and completed >= config.min_iterations
        and payload["consecutive_failures"]
        >= config.max_consecutive_failures
    ):
        payload["state"] = "completed"
        payload["stop_reason"] = "early_stop_consecutive_failures"
    elif completed >= min(config.max_iterations, batch_count):
        complete_for_limit(payload, config, batch_count)
    else:
        payload["state"] = "ready"


def complete_for_limit(
    payload: dict[str, Any],
    config: FeedbackLoopConfig,
    batch_count: int,
) -> None:
    payload["state"] = "completed"
    payload["stop_reason"] = (
        "batches_exhausted"
        if batch_count < config.max_iterations
        else "max_iterations"
    )


__all__ = [
    "advance_iteration",
    "append_iteration_record",
    "build_run_signature",
    "build_session_view",
    "complete_for_limit",
    "current_edit_budget",
    "metric_result",
    "new_session_payload",
    "update_edit_budget",
]
