"""Pausable and resumable skill optimization workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from skilladam.core.acceptance_gate import AcceptanceGate, ImprovementRule
from skilladam.core.case_sampler import CaseId, IterationCases
from skilladam.core.feedback_loop import (
    FeedbackLoopConfig,
    IterationContext,
)
from skilladam.core.momentum import MomentumTracker
from skilladam.product.evaluation import assert_same_evaluation_plan
from skilladam.product.feedback import (
    IterationFeedback,
    MomentumUpdateContext,
    PatchGeneration,
)
from skilladam.product.models import (
    EvaluationPlan,
    GateResult,
    OptimizationSession,
    PublicModel,
    SkillPackageSnapshot,
    ValidationResult,
    _freeze,
    digest_value,
)
from skilladam.product.patch_review import (
    CandidateSnapshot,
    PatchProposal,
    PatchReviewError,
    PatchSelection,
    apply_patch_selection,
    prepare_patch_proposal,
    selected_patch_diff,
    select_patch_hunks,
)
from skilladam.product.session_codec import (
    candidate_from_dict,
    feedback_from_dict,
    proposal_from_dict,
    validation_from_dict,
)
from skilladam.product.session_store import SessionStore
from skilladam.product.workflow_support import (
    advance_iteration,
    append_iteration_record,
    build_run_signature,
    build_session_view,
    complete_for_limit,
    current_edit_budget,
    metric_result,
    new_session_payload,
    update_edit_budget,
)
from skilladam.types import GateDecision, MetricResult


class OptimizationWorkflowError(RuntimeError):
    """Optimization session state or resume signature mismatch."""


class GateProtocol(Protocol):
    def judge(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision: ...

    def to_dict(self) -> dict[str, Any]: ...


ProposalFunction = Callable[[IterationContext], str | PatchGeneration]
ValidationFunction = Callable[
    [Mapping[str, str], tuple[CaseId, ...]],
    ValidationResult,
]
MomentumUpdateFunction = Callable[
    [MomentumUpdateContext, str],
    Sequence[Mapping[str, Any]],
]


@dataclass(frozen=True, slots=True)
class ProductSessionView(PublicModel):
    session: OptimizationSession
    proposal: PatchProposal | None = None
    baseline_validation: ValidationResult | None = None
    candidate_validation: ValidationResult | None = None
    gate: GateResult | None = None
    records: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(_freeze(item) for item in self.records))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


class OptimizationWorkflow:
    """Provide a persistent human review boundary between proposal and validation."""

    SCHEMA_VERSION = 2

    def __init__(
        self,
        *,
        store: SessionStore,
        session_id: str,
        package: SkillPackageSnapshot,
        plan: EvaluationPlan,
        batches: tuple[IterationCases, ...],
        loop_config: FeedbackLoopConfig,
        propose_patch: ProposalFunction,
        validate: ValidationFunction,
        gate: GateProtocol | None = None,
        momentum: MomentumTracker | None = None,
        update_momentum: MomentumUpdateFunction | None = None,
        resume: bool = False,
    ) -> None:
        if not batches:
            raise ValueError("optimization workflow requires iteration batches")
        self.store = store
        self.package = package
        self.plan = plan
        self.batches = tuple(batches)
        self.loop_config = loop_config
        self.propose_patch = propose_patch
        self.validate = validate
        self.gate = gate or AcceptanceGate(
            improvements=(ImprovementRule(metric="primary"),),
        )
        self.momentum = momentum or MomentumTracker()
        self.update_momentum = update_momentum
        self._signature = build_run_signature(
            package=package,
            plan=plan,
            batches=self.batches,
            loop_config=loop_config,
            gate_config=self.gate.to_dict(),
            momentum_update_enabled=update_momentum is not None,
        )

        if store.exists:
            if not resume:
                raise OptimizationWorkflowError(
                    f"session checkpoint already exists: {store.path}"
                )
            self._payload = store.load()
            self._restore(session_id)
        else:
            if resume:
                raise FileNotFoundError(
                    f"session checkpoint does not exist: {store.path}"
                )
            self._payload = new_session_payload(
                schema_version=self.SCHEMA_VERSION,
                run_signature=self._signature,
                session_id=session_id,
                package=package,
                momentum=self.momentum,
            )
            self._save()

    @property
    def view(self) -> ProductSessionView:
        return self._view()

    @property
    def current_files(self) -> Mapping[str, str]:
        return _freeze(self._payload["current_files"])

    def prepare(self) -> ProductSessionView:
        state = self._payload["state"]
        if state == "awaiting_review":
            return self._view()
        if state == "completed":
            return self._view()
        if state == "validating":
            raise OptimizationWorkflowError(
                "session has pending candidate validation; call continue_pending"
            )
        if state not in {"ready", "preparing"}:
            raise OptimizationWorkflowError(
                f"cannot prepare session from state {state!r}"
            )

        iteration = int(self._payload["iteration"])
        if iteration >= self._iteration_limit:
            complete_for_limit(
                self._payload,
                self.loop_config,
                len(self.batches),
            )
            self._save()
            return self._view()
        self._payload["state"] = "preparing"
        self._save()

        batch = self.batches[iteration]
        self.momentum.set_iteration(iteration)
        current_files = dict(self._payload["current_files"])
        current_budget = current_edit_budget(
            self._payload,
            self.loop_config,
        )
        previous_error = ""
        proposal: PatchProposal | None = None
        baseline: ValidationResult | None = None
        feedback: IterationFeedback | None = None
        attempts = 0
        for attempts in range(1, self.loop_config.patch_attempts + 1):
            context = IterationContext(
                iteration=iteration,
                current_skill=current_files[self.package.entrypoint],
                training_case_ids=batch.training_case_ids,
                patch_attempt=attempts,
                previous_patch_error=previous_error,
                edit_budget=current_budget,
            )
            try:
                generation = self.propose_patch(context)
                if isinstance(generation, PatchGeneration):
                    self._assert_validation_exact(
                        generation.baseline_validation,
                        current_files,
                        batch.validation_case_ids,
                    )
                    if generation.feedback.task_ids != tuple(
                        str(item) for item in batch.training_case_ids
                    ):
                        raise OptimizationWorkflowError(
                            "trajectory feedback does not match training batch"
                        )
                    if baseline is not None and (
                        baseline.digest
                        != generation.baseline_validation.digest
                    ):
                        raise OptimizationWorkflowError(
                            "patch retries changed frozen baseline feedback"
                        )
                    baseline = generation.baseline_validation
                    feedback = generation.feedback
                    raw_patch = generation.raw_patch
                else:
                    if baseline is None:
                        baseline = self._validate_exact(
                            current_files,
                            batch.validation_case_ids,
                        )
                    raw_patch = generation
                proposal = prepare_patch_proposal(
                    current_files,
                    raw_patch,
                    mutable_paths=self.package.mutable_paths,
                )
                self._preflight_hunks(current_files, proposal)
                break
            except (PatchReviewError, ValueError) as exc:
                proposal = None
                previous_error = str(exc)

        if baseline is None:
            baseline = self._validate_exact(
                current_files,
                batch.validation_case_ids,
            )
        if proposal is None:
            self._payload["baseline_validation"] = baseline.to_dict()
            self._payload["iteration_feedback"] = (
                feedback.to_dict() if feedback is not None else None
            )
            self._finish_without_candidate(
                reason=f"patch preparation failed: {previous_error}",
                patch_attempts=attempts,
                edit_budget=current_budget,
            )
            return self._view()

        self._payload.update(
            {
                "state": "awaiting_review",
                "proposal": proposal.to_dict(),
                "baseline_validation": baseline.to_dict(),
                "iteration_feedback": (
                    feedback.to_dict() if feedback is not None else None
                ),
                "candidate": None,
                "candidate_validation": None,
                "selection": None,
                "patch_attempts": attempts,
                "edit_budget": current_budget,
            }
        )
        self._save()
        return self._view()

    def submit_selection(
        self,
        selection: PatchSelection,
    ) -> ProductSessionView:
        if self._payload["state"] != "awaiting_review":
            return self._idempotent_submission(selection)
        staged = self.stage_selection(selection)
        if staged.session.state != "validating":
            return staged
        return self.continue_pending()

    def stage_selection(
        self,
        selection: PatchSelection,
    ) -> ProductSessionView:
        """Persist choices and the exact candidate; defer validation to resume."""

        if not selection.idempotency_key.strip():
            raise OptimizationWorkflowError(
                "selection requires a non-empty idempotency_key"
            )
        if self._payload["state"] != "awaiting_review":
            return self._idempotent_submission(selection)

        proposal = proposal_from_dict(self._payload["proposal"])
        candidate = apply_patch_selection(
            self._payload["current_files"],
            proposal,
            selection,
        )
        self._payload["selection"] = selection.to_dict()
        self._payload["last_selection_digest"] = selection.digest
        self._payload["last_idempotency_key"] = selection.idempotency_key
        if candidate is None:
            self._finish_without_candidate(
                reason="user rejected all patch hunks",
                patch_attempts=int(self._payload["patch_attempts"]),
                edit_budget=self._payload["edit_budget"],
            )
            return self._view()

        self._payload["candidate"] = candidate.to_dict()
        self._payload["candidate_validation"] = None
        self._payload["state"] = "validating"
        self._save()
        return self._view()

    def continue_pending(self) -> ProductSessionView:
        if self._payload["state"] != "validating":
            return self._view()
        candidate = candidate_from_dict(self._payload["candidate"])
        baseline = validation_from_dict(
            self._payload["baseline_validation"]
        )
        raw_validation = self._payload.get("candidate_validation")
        if raw_validation is None:
            batch = self.batches[int(self._payload["iteration"])]
            candidate_validation = self._validate_exact(
                candidate.files,
                batch.validation_case_ids,
            )
            assert_same_evaluation_plan(baseline, candidate_validation)
            self._payload["candidate_validation"] = (
                candidate_validation.to_dict()
            )
            self._save()
        else:
            candidate_validation = validation_from_dict(raw_validation)
        self._finish_candidate(candidate, baseline, candidate_validation)
        return self._view()

    def _finish_candidate(
        self,
        candidate: CandidateSnapshot,
        baseline: ValidationResult,
        candidate_validation: ValidationResult,
    ) -> None:
        baseline_metric = metric_result(baseline)
        candidate_metric = metric_result(candidate_validation)
        decision = self.gate.judge(baseline_metric, candidate_metric)
        gate_result = GateResult(
            accepted=decision.accepted,
            reason=decision.reason,
            baseline_validation_digest=baseline.digest,
            candidate_validation_digest=candidate_validation.digest,
            metadata=dict(decision.metadata),
        )
        budget_state = {"v_ema": self._payload["v_ema"]}
        sigma_sq, v_ema_after = update_edit_budget(
            budget_state,
            self.loop_config,
            baseline_metric,
            candidate_metric,
        )
        momentum_update = self._update_momentum_after_gate(
            candidate=candidate,
            baseline=baseline,
            candidate_validation=candidate_validation,
            gate=gate_result,
        )
        self._payload["v_ema"] = budget_state["v_ema"]
        if decision.accepted:
            self._payload["current_files"] = dict(candidate.files)
            self._payload["skill_ever_accepted"] = True
            self._payload["consecutive_failures"] = 0
        else:
            self._payload["consecutive_failures"] += 1
        append_iteration_record(
            self._payload,
            self.batches[self._payload["iteration"]],
            accepted=decision.accepted,
            reason=decision.reason,
            candidate=candidate,
            candidate_validation=candidate_validation,
            gate=gate_result,
            sigma_sq=sigma_sq,
            v_ema_after=v_ema_after,
            momentum_update=momentum_update,
        )
        self._payload["last_candidate_validation"] = (
            candidate_validation.to_dict()
        )
        self._payload["last_gate"] = gate_result.to_dict()
        advance_iteration(
            self._payload,
            self.loop_config,
            len(self.batches),
        )
        self._save()

    def _update_momentum_after_gate(
        self,
        *,
        candidate: CandidateSnapshot,
        baseline: ValidationResult,
        candidate_validation: ValidationResult,
        gate: GateResult,
    ) -> Mapping[str, Any] | None:
        raw_feedback = self._payload.get("iteration_feedback")
        if self.update_momentum is None or raw_feedback is None:
            self.momentum.fill_gate_result(gate.accepted)
            return None

        feedback = feedback_from_dict(raw_feedback)
        proposal = proposal_from_dict(self._payload["proposal"])
        tracker = MomentumTracker.from_dict(self.momentum.to_dict())
        iteration = int(self._payload["iteration"])
        tracker.set_iteration(iteration)
        context = MomentumUpdateContext(
            iteration=iteration,
            current_skill=self._payload["current_files"][
                self.package.entrypoint
            ],
            candidate_skill=candidate.files[self.package.entrypoint],
            raw_patch=selected_patch_diff(
                proposal,
                candidate.applied_hunk_ids,
            ),
            feedback=feedback,
            baseline_validation=baseline,
            candidate_validation=candidate_validation,
            gate=gate,
        )
        calls = self.update_momentum(
            context,
            tracker.render_for_agent_input(),
        )
        normalized_calls: list[dict[str, Any]] = []
        responses: list[str] = []
        for index, call in enumerate(calls):
            if not isinstance(call, Mapping):
                raise OptimizationWorkflowError(
                    f"momentum tool call {index} must be an object"
                )
            name = str(call.get("name", ""))
            arguments = call.get("arguments")
            if not isinstance(arguments, Mapping):
                raise OptimizationWorkflowError(
                    f"momentum tool {name!r} arguments must be an object"
                )
            response = tracker.execute_tool_call(name, dict(arguments))
            if response.startswith("Unknown tool:") or " call failed:" in response:
                raise OptimizationWorkflowError(response)
            normalized_calls.append(
                {"name": name, "arguments": dict(arguments)}
            )
            responses.append(response)
        tracker.fill_gate_result(gate.accepted)
        self.momentum = tracker
        return {
            "tool_calls": normalized_calls,
            "responses": responses,
        }

    def _finish_without_candidate(
        self,
        *,
        reason: str,
        patch_attempts: int,
        edit_budget: int | None,
    ) -> None:
        self._payload["patch_attempts"] = patch_attempts
        self._payload["edit_budget"] = edit_budget
        self._payload["last_candidate_validation"] = None
        self._payload["last_gate"] = None
        self._payload["consecutive_failures"] += 1
        self.momentum.fill_gate_result(False)
        append_iteration_record(
            self._payload,
            self.batches[self._payload["iteration"]],
            accepted=False,
            reason=reason,
            candidate=None,
            candidate_validation=None,
            gate=None,
            sigma_sq=None,
            v_ema_after=None,
        )
        advance_iteration(
            self._payload,
            self.loop_config,
            len(self.batches),
        )
        self._save()

    def _preflight_hunks(
        self,
        current_files: Mapping[str, str],
        proposal: PatchProposal,
    ) -> None:
        """Check that each hunk and the complete auto-accept-all set can be applied.

        The full set is the adapter default; checking only individual hunks lets
        overlapping proposals pass preflight and become stuck during review.
        """

        for hunk in proposal.hunks:
            selection = select_patch_hunks(
                proposal,
                (hunk.hunk_id,),
                idempotency_key="preflight",
            )
            apply_patch_selection(current_files, proposal, selection)
        apply_patch_selection(
            current_files,
            proposal,
            select_patch_hunks(
                proposal,
                tuple(hunk.hunk_id for hunk in proposal.hunks),
                idempotency_key="preflight_all",
            ),
        )

    def _validate_exact(
        self,
        files: Mapping[str, str],
        case_ids: tuple[CaseId, ...],
    ) -> ValidationResult:
        result = self.validate(_freeze(files), case_ids)
        self._assert_validation_exact(result, files, case_ids)
        return result

    def _assert_validation_exact(
        self,
        result: ValidationResult,
        files: Mapping[str, str],
        case_ids: tuple[CaseId, ...],
    ) -> None:
        expected_digest = digest_value(files)
        if result.candidate_digest != expected_digest:
            raise OptimizationWorkflowError(
                "validation result does not match exact candidate digest"
            )
        if result.evaluation_plan_digest != self.plan.digest:
            raise OptimizationWorkflowError(
                "validation result does not use frozen EvaluationPlan"
            )
        expected_task_ids = tuple(str(item) for item in case_ids)
        actual_task_ids = tuple(
            str(item) for item in result.metadata.get("task_ids", ())
        )
        if actual_task_ids != expected_task_ids:
            raise OptimizationWorkflowError(
                "validation result does not use the exact iteration batch"
            )
        if tuple(result.case_results) != expected_task_ids:
            raise OptimizationWorkflowError(
                "validation case results do not preserve iteration batch order"
            )

    def _idempotent_submission(
        self,
        selection: PatchSelection,
    ) -> ProductSessionView:
        if (
            selection.idempotency_key
            == self._payload.get("last_idempotency_key")
            and selection.digest
            == self._payload.get("last_selection_digest")
        ):
            return self._view()
        raise OptimizationWorkflowError(
            "session is not awaiting review for this selection"
        )

    def _view(self) -> ProductSessionView:
        return build_session_view(
            payload=self._payload,
            plan=self.plan,
            checkpoint_path=self.store.path,
            run_signature=self._signature,
            view_type=ProductSessionView,
        )

    @property
    def _iteration_limit(self) -> int:
        return min(self.loop_config.max_iterations, len(self.batches))

    def _restore(self, expected_session_id: str) -> None:
        migrated = False
        if self._payload.get("schema_version") == 1:
            self._migrate_v1_session()
            migrated = True
        if self._payload.get("schema_version") != self.SCHEMA_VERSION:
            raise OptimizationWorkflowError(
                "unsupported product session schema version"
            )
        if self._payload.get("run_signature") != self._signature:
            raise OptimizationWorkflowError(
                "resume inputs do not match product session signature"
            )
        if self._payload.get("session_id") != expected_session_id:
            raise OptimizationWorkflowError("resume session_id does not match")
        self.momentum = MomentumTracker.from_dict(self._payload["momentum"])
        if migrated:
            self._save()

    def _migrate_v1_session(self) -> None:
        self._payload["schema_version"] = self.SCHEMA_VERSION
        self._payload.setdefault("iteration_feedback", None)
        self._payload.setdefault("momentum_update", None)
        for record in self._payload.get("records", ()):
            record.setdefault("iteration_feedback", None)
            record.setdefault("momentum_update", None)

    def _save(self) -> None:
        self._payload["momentum"] = self.momentum.to_dict()
        self.store.save(self._payload)
__all__ = [
    "GateProtocol",
    "MomentumUpdateFunction",
    "OptimizationWorkflow",
    "OptimizationWorkflowError",
    "ProductSessionView",
    "ProposalFunction",
    "ValidationFunction",
]
