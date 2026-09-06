"""Paper-aligned product rollout trajectory and evaluation feedback contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from skilladam.core.trajectory_condenser import condense_trajectory_md
from skilladam.product.models import (
    GateResult,
    OptimizationTask,
    PublicModel,
    ValidationResult,
    _freeze,
    digest_value,
)


@dataclass(frozen=True, slots=True)
class ProductTrajectory(PublicModel):
    """One task's ordered trajectory, final output, and case-level feedback."""

    task_id: str
    query: str
    messages: tuple[Mapping[str, Any], ...]
    final_output: Any
    evaluation: Mapping[str, Any]
    outcome_label: str
    condensed_markdown: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("product trajectory requires task_id")
        if not self.query.strip():
            raise ValueError("product trajectory requires query")
        if self.outcome_label not in {"success", "partial", "failure"}:
            raise ValueError("unsupported product trajectory outcome_label")
        object.__setattr__(
            self,
            "messages",
            tuple(_freeze(dict(message)) for message in self.messages),
        )
        object.__setattr__(self, "final_output", _freeze(self.final_output))
        object.__setattr__(self, "evaluation", _freeze(self.evaluation))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class IterationFeedback(PublicModel):
    """The paper's ``(T_t, F_t^roll)``, stably aligned by task ID."""

    task_ids: tuple[str, ...]
    trajectories: tuple[ProductTrajectory, ...]
    validation: ValidationResult
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_ids:
            raise ValueError("iteration feedback requires task_ids")
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("iteration feedback task_ids must be unique")
        trajectory_ids = tuple(item.task_id for item in self.trajectories)
        if trajectory_ids != self.task_ids:
            raise ValueError(
                "iteration feedback trajectories must match ordered task_ids"
            )
        if set(self.validation.case_results) != set(self.task_ids):
            raise ValueError(
                "iteration feedback validation must cover the same tasks"
            )
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    @property
    def feedback_digest(self) -> str:
        return digest_value(self.to_dict())


@dataclass(frozen=True, slots=True)
class PatchGeneration(PublicModel):
    """One paper Stage 1 input and its unified diff output."""

    raw_patch: str
    baseline_validation: ValidationResult
    feedback: IterationFeedback

    def __post_init__(self) -> None:
        if not isinstance(self.raw_patch, str):
            raise ValueError("patch generation raw_patch must be text")
        if self.baseline_validation.digest != self.feedback.validation.digest:
            raise ValueError(
                "patch generation baseline must be the feedback validation"
            )


@dataclass(frozen=True, slots=True)
class MomentumUpdateContext(PublicModel):
    """Complete input for the paper's EIT update ``U(M,T,Froll,g,Fval,a)``."""

    iteration: int
    current_skill: str
    candidate_skill: str
    raw_patch: str
    feedback: IterationFeedback
    baseline_validation: ValidationResult
    candidate_validation: ValidationResult
    gate: GateResult

    def __post_init__(self) -> None:
        if self.iteration < 0:
            raise ValueError("momentum update iteration must be non-negative")
        if not self.current_skill.strip():
            raise ValueError("momentum update requires current_skill")
        if not self.candidate_skill.strip():
            raise ValueError("momentum update requires candidate_skill")
        if self.baseline_validation.digest != self.feedback.validation.digest:
            raise ValueError(
                "momentum update baseline must be the feedback validation"
            )


def build_iteration_feedback(
    *,
    tasks: Mapping[str, OptimizationTask],
    validation: ValidationResult,
    outputs: Mapping[str, Any],
    ordered_task_ids: Sequence[str],
) -> IterationFeedback:
    """Normalize product rollouts into trajectory feedback consumed by SkillAdam."""

    task_ids = tuple(str(task_id) for task_id in ordered_task_ids)
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("ordered_task_ids must be unique")
    if set(outputs) != set(task_ids):
        raise ValueError("rollout outputs must exactly cover ordered_task_ids")
    if set(validation.case_results) != set(task_ids):
        raise ValueError("validation must exactly cover ordered_task_ids")

    trajectories = tuple(
        _build_trajectory(
            task=tasks[task_id],
            output=outputs[task_id],
            evaluation=validation.case_results[task_id],
        )
        for task_id in task_ids
    )
    return IterationFeedback(
        task_ids=task_ids,
        trajectories=trajectories,
        validation=validation,
        metadata={
            "evaluation_plan_digest": validation.evaluation_plan_digest,
            "candidate_digest": validation.candidate_digest,
        },
    )


def _build_trajectory(
    *,
    task: OptimizationTask,
    output: Any,
    evaluation: Any,
) -> ProductTrajectory:
    safe_output = _json_safe(output)
    safe_evaluation = _evaluation_mapping(evaluation)
    messages = _trajectory_messages(task.prompt, output)
    final_output = extract_final_output(safe_output)
    score = float(safe_evaluation.get("score", 0.0))
    passed = bool(safe_evaluation.get("passed", False))
    label = "success" if passed else "partial" if score > 0.0 else "failure"
    judgment = json.dumps(
        safe_evaluation,
        ensure_ascii=False,
        sort_keys=True,
    )
    condensed = condense_trajectory_md(
        messages=[dict(message) for message in messages],
        outcome_label=label,
        query=task.prompt,
        final_answer=_portable_text(final_output),
        judgment_reason=judgment,
    )
    return ProductTrajectory(
        task_id=task.task_id,
        query=task.prompt,
        messages=messages,
        final_output=final_output,
        evaluation=safe_evaluation,
        outcome_label=label,
        condensed_markdown=condensed,
        metadata={
            "capability": task.capability,
            "difficulty": task.difficulty,
            "source": task.source,
        },
    )


def _trajectory_messages(
    query: str,
    output: Any,
) -> tuple[Mapping[str, Any], ...]:
    raw_messages: Any = None
    if isinstance(output, Mapping):
        raw_messages = output.get("trajectory", output.get("messages"))
    messages: list[Mapping[str, Any]] = [{"role": "user", "content": query}]
    if isinstance(raw_messages, Sequence) and not isinstance(
        raw_messages,
        (str, bytes),
    ):
        for item in raw_messages:
            if isinstance(item, Mapping):
                messages.append(dict(item))
    if len(messages) == 1:
        messages.append(
            {
                "role": "assistant",
                "content": _portable_text(extract_final_output(output)),
            }
        )
    return tuple(messages)


def extract_final_output(output: Any) -> Any:
    """Extract a scoreable final output from a trajectory-preserving rollout wrapper."""

    if isinstance(output, Mapping):
        for field_name in ("output", "final_output", "result"):
            if field_name in output:
                return _json_safe(output[field_name])
    return _json_safe(output)


def _evaluation_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    raise ValueError("case evaluation must be a mapping")


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        return _portable_text(value)


def _portable_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


__all__ = [
    "IterationFeedback",
    "MomentumUpdateContext",
    "PatchGeneration",
    "ProductTrajectory",
    "build_iteration_feedback",
    "extract_final_output",
]
