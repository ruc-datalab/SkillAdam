"""Plan and freeze EvaluationSpec before candidate generation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from skilladam.product.evaluators import (
    REFERENCE_METHODS,
    EvaluationConfigError,
    validate_check_config,
    validate_finite_float,
)
from skilladam.product.evaluation_semantics import (
    open_ended_exact_text_reason,
)
from skilladam.product.models import (
    EvaluationPlan,
    EvaluationSpec,
    OptimizationTask,
    TaskDraft,
    TaskPool,
    TaskSplit,
    TaskSuite,
    digest_value,
)


class EvaluationPlanningError(ValueError):
    """Invalid evaluation planning input or routing."""


RubricGenerator = Callable[[TaskDraft], Mapping[str, Any]]


class EvaluationPlanner:
    """Plan evaluators in deterministic, reference, then rubric order."""

    def __init__(
        self,
        rubric_generator: RubricGenerator | None = None,
        *,
        evaluator_version: str = "1",
    ) -> None:
        self.rubric_generator = rubric_generator
        self.evaluator_version = evaluator_version

    def freeze_plan(
        self,
        pool: TaskPool,
        suite: TaskSuite,
        *,
        plan_id: str = "",
        version: str = "1",
        gate: Mapping[str, Any] | None = None,
    ) -> tuple[tuple[OptimizationTask, ...], EvaluationPlan]:
        if suite.pool_digest != pool.digest:
            raise EvaluationPlanningError(
                "TaskSuite does not belong to TaskPool"
            )
        split_by_id = _split_by_id(suite)
        if set(split_by_id) != {task.task_id for task in pool.tasks}:
            raise EvaluationPlanningError(
                "TaskSuite must classify every TaskPool task"
            )
        tasks = tuple(
            OptimizationTask(
                task_id=draft.task_id,
                prompt=draft.prompt,
                capability=draft.capability,
                source=draft.source,
                difficulty=draft.difficulty,
                split=split_by_id[draft.task_id],
                evaluation=self.plan_task(draft),
                metadata={
                    **dict(draft.metadata),
                    "draft_digest": draft.digest,
                },
            )
            for draft in pool.tasks
        )
        task_specs = {task.task_id: task.evaluation for task in tasks}
        identity = {
            "suite_digest": suite.digest,
            "task_specs": task_specs,
            "version": version,
        }
        plan = EvaluationPlan(
            plan_id=plan_id or f"plan_{digest_value(identity)}",
            suite_digest=suite.digest,
            task_specs=task_specs,
            aggregation={"kind": "mean", "missing_value_policy": "fail"},
            gate=dict(gate or {}),
            version=version,
        )
        return tasks, plan

    def plan_task(self, task: TaskDraft) -> EvaluationSpec:
        hint = dict(task.evaluation_hint)
        requested_kind = str(hint.get("kind", "")).strip()
        if requested_kind:
            return self._from_hint(task, requested_kind, hint)
        if hint.get("checks"):
            return self._programmatic(hint, task=task)
        if task.expected_output is not None:
            method = _reference_method(hint.get("method", "normalized_exact"))
            semantic_error = open_ended_exact_text_reason(
                prompt=task.prompt,
                reference=task.expected_output,
                method=method,
            )
            if semantic_error:
                raise EvaluationPlanningError(semantic_error)
            return EvaluationSpec(
                kind="reference",
                criteria={
                    "method": method,
                    **_failure_feedback(hint),
                },
                reference=task.expected_output,
                evaluator_version=self.evaluator_version,
                evaluator_config=dict(hint.get("config", {})),
            )
        rubric = (
            dict(self.rubric_generator(task))
            if self.rubric_generator is not None
            else _default_rubric(task)
        )
        return EvaluationSpec(
            kind="rubric_judge",
            criteria={"task_capability": task.capability},
            rubric=rubric,
            evaluator_version=self.evaluator_version,
            gate_metadata={"pass_threshold": 0.5},
        )

    def _from_hint(
        self,
        task: TaskDraft,
        kind: str,
        hint: Mapping[str, Any],
    ) -> EvaluationSpec:
        if kind == "programmatic":
            return self._programmatic(hint, task=task)
        if kind == "reference":
            if task.expected_output is None and "reference" not in hint:
                raise EvaluationPlanningError(
                    "reference evaluation requires expected output"
                )
            method = _reference_method(hint.get("method", "normalized_exact"))
            reference = hint.get("reference", task.expected_output)
            semantic_error = open_ended_exact_text_reason(
                prompt=task.prompt,
                reference=reference,
                method=method,
            )
            if semantic_error:
                raise EvaluationPlanningError(semantic_error)
            config = dict(hint.get("config", {}))
            if method == "numeric_tolerance" and "tolerance" in config:
                config["tolerance"] = _finite(config["tolerance"], "tolerance")
            return EvaluationSpec(
                kind="reference",
                criteria={"method": method, **_failure_feedback(hint)},
                reference=reference,
                evaluator_version=self.evaluator_version,
                evaluator_config=config,
            )
        if kind == "rubric_judge":
            rubric = dict(hint.get("rubric", {}))
            if not rubric:
                rubric = (
                    dict(self.rubric_generator(task))
                    if self.rubric_generator is not None
                    else _default_rubric(task)
                )
            return EvaluationSpec(
                kind="rubric_judge",
                criteria={
                    **dict(hint.get("criteria", {})),
                    **_failure_feedback(hint),
                },
                rubric=rubric,
                evaluator_version=self.evaluator_version,
                evaluator_config=dict(hint.get("config", {})),
                gate_metadata={
                    "pass_threshold": hint.get("pass_threshold", 0.5),
                },
            )
        if kind == "hybrid":
            components = tuple(
                self._from_component_hint(task, component)
                for component in hint.get("components", ())
            )
            weights = tuple(
                hint.get("weights", [1.0] * len(components))
            )
            if len(weights) != len(components):
                raise EvaluationPlanningError(
                    "hybrid weights must match component count"
                )
            return EvaluationSpec(
                kind="hybrid",
                criteria={
                    **dict(hint.get("criteria", {})),
                    **_failure_feedback(hint),
                },
                evaluator_version=self.evaluator_version,
                weights={
                    str(index): _finite(weight, f"hybrid weight {index}")
                    for index, weight in enumerate(weights)
                },
                components=components,
                gate_metadata={
                    "pass_threshold": hint.get("pass_threshold", 0.5),
                },
            )
        raise EvaluationPlanningError(
            f"unsupported evaluation kind {kind!r}"
        )

    def _from_component_hint(
        self,
        task: TaskDraft,
        component: Mapping[str, Any],
    ) -> EvaluationSpec:
        kind = str(component.get("kind", "")).strip()
        if kind == "hybrid":
            raise EvaluationPlanningError(
                "nested hybrid evaluation is not supported"
            )
        return self._from_hint(task, kind, component)

    def _programmatic(
        self,
        hint: Mapping[str, Any],
        *,
        task: TaskDraft,
    ) -> EvaluationSpec:
        checks = tuple(hint.get("checks", ()))
        if not checks:
            raise EvaluationPlanningError(
                "programmatic evaluation requires checks"
            )
        # Validate before freezing; unsupported checks would otherwise fail only at evaluation,
        # leaving a persisted plan that cannot complete.
        for check in checks:
            if not isinstance(check, Mapping):
                raise EvaluationPlanningError(
                    "programmatic checks must be objects"
                )
            try:
                validate_check_config(check)
            except EvaluationConfigError as exc:
                raise EvaluationPlanningError(str(exc)) from exc
            semantic_error = open_ended_exact_text_reason(
                prompt=task.prompt,
                reference=check.get("value"),
                method=str(check.get("type", "")),
            )
            if semantic_error:
                raise EvaluationPlanningError(semantic_error)
        return EvaluationSpec(
            kind="programmatic",
            criteria={"checks": checks, **_failure_feedback(hint)},
            evaluator_version=self.evaluator_version,
            gate_metadata={"pass_threshold": 1.0},
        )


def _finite(value: Any, field: str) -> float:
    try:
        return validate_finite_float(value, field)
    except EvaluationConfigError as exc:
        raise EvaluationPlanningError(str(exc)) from exc


def _failure_feedback(hint: Mapping[str, Any]) -> dict[str, str]:
    feedback = str(hint.get("failure_feedback", "")).strip()
    return {"failure_feedback": feedback} if feedback else {}


def _reference_method(value: Any) -> str:
    method = str(value)
    if method not in REFERENCE_METHODS:
        raise EvaluationPlanningError(
            f"unsupported reference method {method!r}; "
            f"supported: {sorted(REFERENCE_METHODS)}"
        )
    return method


def _split_by_id(suite: TaskSuite) -> dict[str, TaskSplit]:
    return {
        **{task_id: "train" for task_id in suite.train_task_ids},
        **{task_id: "validation" for task_id in suite.validation_task_ids},
        **{task_id: "test" for task_id in suite.test_task_ids},
    }


def _default_rubric(task: TaskDraft) -> Mapping[str, Any]:
    return {
        "scale": {"minimum": 0.0, "maximum": 1.0},
        "dimensions": (
            {
                "name": "task_completion",
                "description": "输出是否完整满足任务要求",
                "weight": 0.6,
            },
            {
                "name": "constraint_following",
                "description": "输出是否遵循明确约束且没有无关内容",
                "weight": 0.4,
            },
        ),
        "task_digest": task.digest,
    }


__all__ = [
    "EvaluationPlanner",
    "EvaluationPlanningError",
    "RubricGenerator",
]
