"""Execution entry for a frozen EvaluationPlan."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from skilladam.product.evaluators import (
    EvaluationOutcome,
    JudgeFunction,
    evaluate_output,
)
from skilladam.product.feedback import extract_final_output
from skilladam.product.models import (
    EvaluationPlan,
    OptimizationTask,
    TaskSplit,
    ValidationResult,
    digest_value,
)


class EvaluationError(ValueError):
    """Evaluation execution violated the frozen protocol."""


RolloutFunction = Callable[[str, OptimizationTask], Any]


class EvaluationRunner:
    """Use the same frozen EvaluationPlan for baseline and candidate."""

    def __init__(
        self,
        tasks: tuple[OptimizationTask, ...],
        plan: EvaluationPlan,
        rollout: RolloutFunction,
        *,
        judge: JudgeFunction | None = None,
    ) -> None:
        self.tasks = {task.task_id: task for task in tasks}
        self.plan = plan
        self.rollout = rollout
        self.judge = judge
        if set(self.tasks) != set(plan.task_specs):
            raise EvaluationError("tasks must exactly match frozen plan task_specs")
        if any(
            task.evaluation.digest != plan.task_specs[task_id].digest
            for task_id, task in self.tasks.items()
        ):
            raise EvaluationError("task EvaluationSpec differs from frozen plan")

    def evaluate(
        self,
        skill: str,
        *,
        candidate_digest: str = "",
        split: TaskSplit = "validation",
        task_ids: Sequence[str] | None = None,
    ) -> ValidationResult:
        if task_ids is None:
            selected = tuple(
                task for task in self.tasks.values() if task.split == split
            )
            selection_kind = split
        else:
            ordered_ids = tuple(str(task_id) for task_id in task_ids)
            if not ordered_ids or len(set(ordered_ids)) != len(ordered_ids):
                raise EvaluationError("task_ids must be non-empty and unique")
            unknown = set(ordered_ids).difference(self.tasks)
            if unknown:
                raise EvaluationError(
                    f"frozen plan contains no tasks {sorted(unknown)}"
                )
            selected = tuple(self.tasks[task_id] for task_id in ordered_ids)
            selection_kind = "explicit_task_ids"
        if not selected:
            raise EvaluationError(f"frozen plan contains no {split} tasks")
        outcomes = tuple(
            evaluate_output(
                task,
                extract_final_output(self.rollout(skill, task)),
                judge=self.judge,
            )
            for task in selected
        )
        primary = sum(outcome.score for outcome in outcomes) / len(outcomes)
        passed = sum(outcome.passed for outcome in outcomes)
        source_scores: dict[str, list[float]] = {}
        for task, outcome in zip(selected, outcomes, strict=True):
            source_scores.setdefault(task.source, []).append(outcome.score)
        metrics = {
            "score": primary,
            "pass_rate": passed / len(outcomes),
            **{
                f"source.{source}.score": sum(scores) / len(scores)
                for source, scores in source_scores.items()
            },
        }
        return ValidationResult(
            candidate_digest=candidate_digest or digest_value(skill),
            evaluation_plan_digest=self.plan.digest,
            primary=primary,
            metrics=metrics,
            case_results={
                outcome.task_id: outcome.to_dict() for outcome in outcomes
            },
            metadata={
                "plan_id": self.plan.plan_id,
                "split": split,
                "selection_kind": selection_kind,
                "task_ids": tuple(
                    outcome.task_id for outcome in outcomes
                ),
            },
        )


def assert_same_evaluation_plan(
    baseline: ValidationResult,
    candidate: ValidationResult,
) -> None:
    if baseline.evaluation_plan_digest != candidate.evaluation_plan_digest:
        raise EvaluationError(
            "baseline and candidate must use the same frozen EvaluationPlan"
        )


__all__ = [
    "EvaluationError",
    "EvaluationOutcome",
    "EvaluationRunner",
    "JudgeFunction",
    "RolloutFunction",
    "assert_same_evaluation_plan",
    "evaluate_output",
]
