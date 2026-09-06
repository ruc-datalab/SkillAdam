"""Dependency-free SkillAdam feedback-loop state machine."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from skilladam.core.acceptance_gate import AcceptanceGate
from skilladam.core.case_sampler import CaseId, IterationCases
from skilladam.core.diff_apply import ApplyReport, apply_patch_with_fallback
from skilladam.core.edit_budget import (
    compute_edit_budget,
    compute_per_case_deltas,
    compute_sigma_sq,
    update_v_ema,
)
from skilladam.core.momentum import MomentumTracker
from skilladam.types import GateDecision, MetricResult


class CheckpointExistsError(RuntimeError):
    """Raised when a new run would overwrite an existing checkpoint."""


class ResumeMismatchError(RuntimeError):
    """Raised when resume inputs differ from the checkpointed experiment."""


@dataclass(frozen=True)
class EditBudgetConfig:
    metric_key: str
    v_max: float
    base: int = 4
    minimum: int = 1
    beta: float = 0.9


@dataclass(frozen=True)
class FeedbackLoopConfig:
    max_iterations: int
    min_iterations: int = 0
    max_consecutive_failures: int = 3
    patch_attempts: int = 3
    edit_budget: EditBudgetConfig | None = None

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if not 0 <= self.min_iterations <= self.max_iterations:
            raise ValueError(
                "min_iterations must be between 0 and max_iterations"
            )
        if self.max_consecutive_failures < 1:
            raise ValueError(
                "max_consecutive_failures must be at least 1"
            )
        if self.patch_attempts < 1:
            raise ValueError("patch_attempts must be at least 1")


@dataclass(frozen=True)
class IterationContext:
    iteration: int
    current_skill: str
    training_case_ids: tuple[CaseId, ...]
    patch_attempt: int
    previous_patch_error: str = ""
    edit_budget: int | None = None


@dataclass(frozen=True)
class IterationRecord:
    iteration: int
    training_case_ids: tuple[CaseId, ...]
    validation_case_ids: tuple[CaseId, ...]
    accepted: bool
    reason: str
    patch_attempts: int
    patch_mode: str
    edit_budget: int | None
    sigma_sq: float | None
    v_ema_after: float | None
    baseline: MetricResult
    candidate: MetricResult

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["training_case_ids"] = list(self.training_case_ids)
        payload["validation_case_ids"] = list(self.validation_case_ids)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IterationRecord":
        value = dict(payload)
        value["training_case_ids"] = tuple(value["training_case_ids"])
        value["validation_case_ids"] = tuple(value["validation_case_ids"])
        value["baseline"] = _metric_from_dict(value["baseline"])
        value["candidate"] = _metric_from_dict(value["candidate"])
        return cls(**value)


@dataclass(frozen=True)
class FeedbackLoopResult:
    final_skill: str
    iterations: tuple[IterationRecord, ...]
    stop_reason: str
    resumed_from_iteration: int


@dataclass(frozen=True)
class PostDecisionContext:
    """Immutable context for one checkpoint-safe post-decision action."""

    iteration_id: str
    record: IterationRecord
    current_skill: str


PostDecisionGenerate = Callable[
    [PostDecisionContext],
    Mapping[str, Any],
]
PostDecisionApply = Callable[
    [PostDecisionContext, Mapping[str, Any]],
    None,
]


@dataclass(frozen=True)
class PostDecisionHook:
    """Two-phase hook whose generation can be cached before application."""

    run_signature: Mapping[str, Any]
    generate: PostDecisionGenerate
    apply: PostDecisionApply

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "run_signature",
            _json_object(
                self.run_signature,
                field_name="post-decision run signature",
            ),
        )
        if not callable(self.generate) or not callable(self.apply):
            raise ValueError(
                "post-decision generate and apply must be callable"
            )


class CheckpointStore:
    """Atomically persist one feedback-loop checkpoint."""

    def __init__(
        self,
        output_dir: Path,
        *,
        filename: str = "checkpoint.json",
    ) -> None:
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / filename

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def load_payload(self) -> dict[str, Any]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("checkpoint must contain a JSON object")
        return payload

    def save_payload(self, payload: dict[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        temporary = self.path.with_name(
            f".{self.path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
        )
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(self.output_dir, directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)


ProposePatch = Callable[[IterationContext], str]
Evaluate = Callable[[str, tuple[CaseId, ...]], MetricResult]


class FeedbackLoop:
    """Run patch, validation gate, momentum, checkpoint, and resume logic."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        config: FeedbackLoopConfig,
        *,
        gate: AcceptanceGate,
        checkpoint_store: CheckpointStore,
        momentum: MomentumTracker | None = None,
    ) -> None:
        self.config = config
        self.gate = gate
        self.checkpoint_store = checkpoint_store
        self.momentum = momentum

    def run(
        self,
        *,
        initial_skill: str,
        batches: Iterable[IterationCases],
        propose_patch: ProposePatch,
        evaluate: Evaluate,
        post_decision_hook: PostDecisionHook | None = None,
        resume: bool = False,
    ) -> FeedbackLoopResult:
        batch_list = list(batches)
        run_signature = self._run_signature(post_decision_hook)
        if self.checkpoint_store.exists:
            if not resume:
                raise CheckpointExistsError(
                    f"checkpoint already exists: {self.checkpoint_store.path}"
                )
            state = self._restore_state(
                self.checkpoint_store.load_payload(),
                expected_signature=run_signature,
                batches=batch_list,
            )
        else:
            if resume:
                raise FileNotFoundError(
                    f"checkpoint does not exist: {self.checkpoint_store.path}"
                )
            state = _LoopState(current_skill=initial_skill)

        start_iteration = state.next_iteration
        if state.pending_post_decision is not None:
            if post_decision_hook is None:
                raise ResumeMismatchError(
                    "checkpoint has a pending post-decision action but "
                    "the current run has no hook"
                )
            self._complete_post_decision(
                state,
                hook=post_decision_hook,
                run_signature=run_signature,
            )
        if state.terminal_stop_reason:
            return FeedbackLoopResult(
                final_skill=state.current_skill,
                iterations=tuple(state.records),
                stop_reason=state.terminal_stop_reason,
                resumed_from_iteration=start_iteration,
            )

        stop_reason = "max_iterations"
        limit = min(self.config.max_iterations, len(batch_list))

        for iteration in range(start_iteration, limit):
            batch = batch_list[iteration]
            if self.momentum is not None:
                self.momentum.set_iteration(iteration)

            current_budget = self._current_budget(state.v_ema)
            report, attempts, error = self._generate_candidate(
                iteration=iteration,
                current_skill=state.current_skill,
                batch=batch,
                edit_budget=current_budget,
                propose_patch=propose_patch,
            )
            baseline = evaluate(
                state.current_skill,
                batch.validation_case_ids,
            )

            if report is None:
                candidate = baseline
                decision = GateDecision(
                    accepted=False,
                    reason=f"patch application failed: {error}",
                    baseline=baseline,
                    candidate=candidate,
                )
                patch_mode = "failed"
            else:
                candidate = evaluate(
                    report.text,
                    batch.validation_case_ids,
                )
                decision = self.gate.judge(baseline, candidate)
                patch_mode = _patch_mode(report)

            sigma_sq, v_ema_after = self._update_edit_budget(
                state,
                baseline=baseline,
                candidate=candidate,
                applied=report is not None,
            )
            if decision.accepted and report is not None:
                state.current_skill = report.text
                state.skill_ever_accepted = True
                state.consecutive_failures = 0
            else:
                state.consecutive_failures += 1
            if self.momentum is not None:
                self.momentum.fill_gate_result(decision.accepted)

            record = IterationRecord(
                iteration=iteration,
                training_case_ids=batch.training_case_ids,
                validation_case_ids=batch.validation_case_ids,
                accepted=decision.accepted,
                reason=decision.reason,
                patch_attempts=attempts,
                patch_mode=patch_mode,
                edit_budget=current_budget,
                sigma_sq=sigma_sq,
                v_ema_after=v_ema_after,
                baseline=baseline,
                candidate=candidate,
            )
            state.records.append(record)
            state.next_iteration = iteration + 1

            completed = iteration + 1
            if (
                state.skill_ever_accepted
                and completed >= self.config.min_iterations
                and state.consecutive_failures
                >= self.config.max_consecutive_failures
            ):
                stop_reason = "early_stop_consecutive_failures"
                state.terminal_stop_reason = stop_reason

            if post_decision_hook is not None:
                state.pending_post_decision = {
                    "iteration_id": f"iter_{iteration:03d}",
                    "iteration": iteration,
                    "generation": None,
                }
                self._save_state(state, run_signature=run_signature)
                self._complete_post_decision(
                    state,
                    hook=post_decision_hook,
                    run_signature=run_signature,
                )
            else:
                self._save_state(state, run_signature=run_signature)
            if state.terminal_stop_reason:
                break
        else:
            if limit < self.config.max_iterations:
                stop_reason = "batches_exhausted"

        return FeedbackLoopResult(
            final_skill=state.current_skill,
            iterations=tuple(state.records),
            stop_reason=stop_reason,
            resumed_from_iteration=start_iteration,
        )

    def _generate_candidate(
        self,
        *,
        iteration: int,
        current_skill: str,
        batch: IterationCases,
        edit_budget: int | None,
        propose_patch: ProposePatch,
    ) -> tuple[ApplyReport | None, int, str]:
        previous_error = ""
        for attempt in range(1, self.config.patch_attempts + 1):
            context = IterationContext(
                iteration=iteration,
                current_skill=current_skill,
                training_case_ids=batch.training_case_ids,
                patch_attempt=attempt,
                previous_patch_error=previous_error,
                edit_budget=edit_budget,
            )
            patch = propose_patch(context)
            report = apply_patch_with_fallback(current_skill, patch)
            malformed = bool(patch.strip()) and report.hunks_total == 0
            if not malformed and not report.all_failed:
                return report, attempt, ""
            diagnostics = "; ".join(report.fail_diagnostics)
            previous_error = diagnostics or "patch contained no applicable hunks"
        return None, self.config.patch_attempts, previous_error

    def _current_budget(self, v_ema: float) -> int | None:
        config = self.config.edit_budget
        if config is None:
            return None
        return compute_edit_budget(
            v_ema,
            v_max=config.v_max,
            base=config.base,
            minimum=config.minimum,
        )

    def _update_edit_budget(
        self,
        state: "_LoopState",
        *,
        baseline: MetricResult,
        candidate: MetricResult,
        applied: bool,
    ) -> tuple[float | None, float | None]:
        config = self.config.edit_budget
        if config is None or not applied:
            return None, None
        deltas = compute_per_case_deltas(
            baseline,
            candidate,
            metric_key=config.metric_key,
        )
        sigma_sq = compute_sigma_sq(deltas)
        state.v_ema = update_v_ema(
            state.v_ema,
            sigma_sq,
            beta=config.beta,
        )
        return sigma_sq, state.v_ema

    def _run_signature(
        self,
        post_decision_hook: PostDecisionHook | None,
    ) -> dict[str, Any]:
        signature = {
            "gate": self.gate.to_dict(),
            "min_iterations": self.config.min_iterations,
            "max_consecutive_failures": (
                self.config.max_consecutive_failures
            ),
            "patch_attempts": self.config.patch_attempts,
            "momentum_enabled": self.momentum is not None,
            "edit_budget": (
                asdict(self.config.edit_budget)
                if self.config.edit_budget is not None
                else None
            ),
        }
        if post_decision_hook is not None:
            signature["post_decision"] = _json_object(
                post_decision_hook.run_signature,
                field_name="post-decision run signature",
            )
        return signature

    def _complete_post_decision(
        self,
        state: "_LoopState",
        *,
        hook: PostDecisionHook,
        run_signature: dict[str, Any],
    ) -> None:
        pending = state.pending_post_decision
        if pending is None:
            return
        iteration = int(pending["iteration"])
        if (
            not state.records
            or iteration != state.records[-1].iteration
            or state.next_iteration != iteration + 1
        ):
            raise ResumeMismatchError(
                "checkpoint pending post-decision action does not match "
                "the latest iteration"
            )
        iteration_id = str(pending["iteration_id"])
        expected_id = f"iter_{iteration:03d}"
        if iteration_id != expected_id:
            raise ResumeMismatchError(
                "checkpoint pending post-decision iteration ID is invalid"
            )
        context = PostDecisionContext(
            iteration_id=iteration_id,
            record=state.records[-1],
            current_skill=state.current_skill,
        )
        if self.momentum is not None:
            self.momentum.set_iteration(iteration)
        generation = pending.get("generation")
        if generation is None:
            generation = _json_object(
                hook.generate(context),
                field_name="post-decision generation",
            )
            pending["generation"] = generation
            self._save_state(state, run_signature=run_signature)
        else:
            generation = _json_object(
                generation,
                field_name="cached post-decision generation",
            )
        hook.apply(context, generation)
        if self.momentum is not None:
            self.momentum.fill_gate_result(context.record.accepted)
        state.pending_post_decision = None
        self._save_state(state, run_signature=run_signature)

    def _save_state(
        self,
        state: "_LoopState",
        *,
        run_signature: dict[str, Any],
    ) -> None:
        self.checkpoint_store.save_payload(
            {
                "schema_version": self.SCHEMA_VERSION,
                "run_signature": run_signature,
                "next_iteration": state.next_iteration,
                "current_skill": state.current_skill,
                "consecutive_failures": state.consecutive_failures,
                "skill_ever_accepted": state.skill_ever_accepted,
                "terminal_stop_reason": state.terminal_stop_reason,
                "v_ema": state.v_ema,
                "iterations": [
                    record.to_dict() for record in state.records
                ],
                "pending_post_decision": state.pending_post_decision,
                "momentum": (
                    self.momentum.to_dict()
                    if self.momentum is not None
                    else None
                ),
            }
        )

    def _restore_state(
        self,
        payload: dict[str, Any],
        *,
        expected_signature: dict[str, Any],
        batches: list[IterationCases],
    ) -> "_LoopState":
        if payload.get("schema_version") != self.SCHEMA_VERSION:
            raise ValueError("unsupported feedback-loop checkpoint schema")
        if payload.get("run_signature") != expected_signature:
            raise ResumeMismatchError(
                "checkpoint run signature does not match current gate/config"
            )
        if self.momentum is not None and payload.get("momentum") is not None:
            restored = MomentumTracker.from_dict(payload["momentum"])
            self.momentum.problems = restored.problems
        records = [
            IterationRecord.from_dict(item)
            for item in payload.get("iterations", [])
        ]
        next_iteration = int(payload.get("next_iteration", 0))
        if next_iteration != len(records):
            raise ResumeMismatchError(
                "checkpoint state next_iteration must equal record count"
            )
        if next_iteration > len(batches):
            raise ResumeMismatchError(
                "checkpoint state exceeds the current batch plan"
            )
        for expected_iteration, record in enumerate(records):
            if record.iteration != expected_iteration:
                raise ResumeMismatchError(
                    "checkpoint state records must be contiguous from zero"
                )
            if record.iteration >= len(batches):
                raise ResumeMismatchError(
                    "checkpoint run signature exceeds the current batch plan"
                )
            batch = batches[record.iteration]
            if (
                record.training_case_ids != batch.training_case_ids
                or record.validation_case_ids != batch.validation_case_ids
            ):
                raise ResumeMismatchError(
                    "checkpoint run signature does not match the current "
                    "batch plan"
                )
        terminal_stop_reason = str(
            payload.get("terminal_stop_reason", "")
        )
        if terminal_stop_reason not in {
            "",
            "early_stop_consecutive_failures",
        }:
            raise ResumeMismatchError(
                "checkpoint state has an unsupported terminal stop reason"
            )
        pending = payload.get("pending_post_decision")
        if pending is not None:
            pending = _json_object(
                pending,
                field_name="pending post-decision action",
            )
        return _LoopState(
            next_iteration=next_iteration,
            current_skill=str(payload.get("current_skill", "")),
            consecutive_failures=int(
                payload.get("consecutive_failures", 0)
            ),
            skill_ever_accepted=bool(
                payload.get("skill_ever_accepted", False)
            ),
            terminal_stop_reason=terminal_stop_reason,
            v_ema=float(payload.get("v_ema", 0.0)),
            records=records,
            pending_post_decision=pending,
        )


@dataclass
class _LoopState:
    next_iteration: int = 0
    current_skill: str = ""
    consecutive_failures: int = 0
    skill_ever_accepted: bool = False
    terminal_stop_reason: str = ""
    v_ema: float = 0.0
    records: list[IterationRecord] = field(default_factory=list)
    pending_post_decision: dict[str, Any] | None = None


def _patch_mode(report: ApplyReport) -> str:
    if report.hunks_total == 0:
        return "empty"
    if report.hunks_failed:
        return "partial"
    if report.hunks_intent:
        return "intent"
    return "strict"


def _metric_from_dict(payload: dict[str, Any]) -> MetricResult:
    return MetricResult(
        primary=float(payload["primary"]),
        metrics=dict(payload.get("metrics", {})),
        case_metrics={
            str(case_id): dict(metrics)
            for case_id, metrics in payload.get("case_metrics", {}).items()
        },
        sample_count=int(payload.get("sample_count", 1)),
        metadata=dict(payload.get("metadata", {})),
    )


def _json_object(
    value: object,
    *,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must contain JSON-safe values"
        ) from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return decoded
