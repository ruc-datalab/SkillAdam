"""Programmatic, reference, rubric, and hybrid evaluators."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any

from skilladam.product.models import (
    EvaluationSpec,
    OptimizationTask,
    PublicModel,
    _freeze,
)


class EvaluationConfigError(ValueError):
    """Invalid evaluation configuration; shared by planning and execution."""


class EvaluationExecutionError(EvaluationConfigError):
    """Invalid evaluator configuration or structured output."""


JudgeFunction = Callable[
    [OptimizationTask, Any, EvaluationSpec],
    Mapping[str, Any],
]


@dataclass(frozen=True, slots=True)
class EvaluationOutcome(PublicModel):
    task_id: str
    score: float
    passed: bool
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise EvaluationExecutionError(
                "evaluation score must be between 0 and 1"
            )
        object.__setattr__(self, "details", _freeze(self.details))


def evaluate_output(
    task: OptimizationTask,
    output: Any,
    *,
    judge: JudgeFunction | None = None,
) -> EvaluationOutcome:
    spec = task.evaluation
    if spec.kind == "programmatic":
        score, passed, details = _programmatic(output, spec)
    elif spec.kind == "reference":
        score, passed, details = _reference(output, spec)
    elif spec.kind == "rubric_judge":
        score, passed, details = _rubric(task, output, spec, judge)
    elif spec.kind == "hybrid":
        score, passed, details = _hybrid(task, output, spec, judge)
    else:  # pragma: no cover - EvaluationSpec already validates kind
        raise EvaluationExecutionError(
            f"unsupported evaluation kind {spec.kind!r}"
        )
    if not passed:
        feedback = str(spec.criteria.get("failure_feedback", "")).strip()
        if feedback:
            details = {**dict(details), "failure_feedback": feedback}
    return EvaluationOutcome(
        task_id=task.task_id,
        score=score,
        passed=passed,
        details=details,
    )


def _programmatic(
    output: Any,
    spec: EvaluationSpec,
) -> tuple[float, bool, Mapping[str, Any]]:
    checks = tuple(spec.criteria.get("checks", ()))
    if not checks:
        raise EvaluationExecutionError("programmatic spec has no checks")
    results = tuple(_run_check(output, check) for check in checks)
    score = sum(results) / len(results)
    return score, all(results), {"checks": results}


PROGRAMMATIC_CHECK_TYPES = frozenset(
    {
        "exact",
        "contains",
        "not_contains",
        "regex",
        "json_valid",
        "json_has_keys",
        "min_length",
        "max_length",
    }
)
REFERENCE_METHODS = frozenset(
    {
        "exact",
        "normalized_exact",
        "numeric_tolerance",
        "set_equality",
        "token_f1",
    }
)


def validate_check_config(check: Mapping[str, Any]) -> None:
    """Validate a programmatic check before execution.

    A valid type can still carry invalid values: malformed regex,
    non-integer lengths, or invalid json_has_keys collections. Reject these before freezing
    the plan so a session cannot become stuck on an unexecutable check.
    """

    kind = str(check.get("type", ""))
    if kind not in PROGRAMMATIC_CHECK_TYPES:
        raise EvaluationConfigError(
            f"unsupported programmatic check {kind!r}; "
            f"supported: {sorted(PROGRAMMATIC_CHECK_TYPES)}"
        )
    if "value" not in check and kind != "json_valid":
        raise EvaluationConfigError(
            f"programmatic check {kind!r} requires a value"
        )
    value = check.get("value")
    if kind == "regex":
        try:
            re.compile(str(value))
        except re.error as exc:
            raise EvaluationConfigError(
                f"invalid regex check {value!r}: {exc}"
            ) from exc
    elif kind in {"min_length", "max_length"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise EvaluationConfigError(
                f"programmatic check {kind!r} requires an integer value"
            )
    elif kind == "json_has_keys":
        if isinstance(value, (str, bytes)) or not isinstance(
            value, (Sequence, AbstractSet)
        ):
            raise EvaluationConfigError(
                "json_has_keys requires an array of key names"
            )


def validate_finite_float(value: Any, field: str) -> float:
    """Normalize weights and tolerances to finite floats; inf/nan invalidate aggregation."""

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationConfigError(f"{field} must be a number") from exc
    if not math.isfinite(number):
        raise EvaluationConfigError(f"{field} must be finite")
    return number


def _run_check(output: Any, check: Mapping[str, Any]) -> bool:
    kind = str(check.get("type", ""))
    text = (
        output
        if isinstance(output, str)
        else json.dumps(output, ensure_ascii=False)
    )
    value = check.get("value")
    if kind == "exact":
        return output == value
    if kind == "contains":
        return str(value) in text
    if kind == "not_contains":
        return str(value) not in text
    if kind == "regex":
        return re.search(str(value), text) is not None
    if kind == "json_valid":
        try:
            json.loads(text)
        except (TypeError, ValueError):
            return False
        return True
    if kind == "json_has_keys":
        try:
            parsed = output if isinstance(output, Mapping) else json.loads(text)
        except (TypeError, ValueError):
            return False
        return isinstance(parsed, Mapping) and set(value or ()).issubset(parsed)
    if kind == "min_length":
        return len(text) >= int(value)
    if kind == "max_length":
        return len(text) <= int(value)
    raise EvaluationExecutionError(
        f"unsupported programmatic check {kind!r}"
    )


def _reference(
    output: Any,
    spec: EvaluationSpec,
) -> tuple[float, bool, Mapping[str, Any]]:
    method = str(spec.criteria.get("method", "normalized_exact"))
    reference = spec.reference
    if method == "exact":
        score = float(output == reference)
    elif method == "normalized_exact":
        score = float(_normalize_text(output) == _normalize_text(reference))
    elif method == "numeric_tolerance":
        tolerance = float(spec.evaluator_config.get("tolerance", 1e-6))
        try:
            score = float(abs(float(output) - float(reference)) <= tolerance)
        except (TypeError, ValueError):
            score = 0.0
    elif method == "set_equality":
        score = float(_as_set(output) == _as_set(reference))
    elif method == "token_f1":
        score = _token_f1(_normalize_text(output), _normalize_text(reference))
    else:
        raise EvaluationExecutionError(
            f"unsupported reference method {method!r}"
        )
    threshold = float(spec.gate_metadata.get("pass_threshold", 1.0))
    return score, score >= threshold, {"method": method}


def _rubric(
    task: OptimizationTask,
    output: Any,
    spec: EvaluationSpec,
    judge: JudgeFunction | None,
) -> tuple[float, bool, Mapping[str, Any]]:
    if judge is None:
        raise EvaluationExecutionError(
            "rubric_judge evaluation requires a judge"
        )
    result = dict(judge(task, output, spec))
    try:
        score = float(result["score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationExecutionError(
            "judge must return a numeric score"
        ) from exc
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise EvaluationExecutionError("judge score must be between 0 and 1")
    threshold = float(spec.gate_metadata.get("pass_threshold", 0.5))
    return score, score >= threshold, {"judge": result}


def _hybrid(
    task: OptimizationTask,
    output: Any,
    spec: EvaluationSpec,
    judge: JudgeFunction | None,
) -> tuple[float, bool, Mapping[str, Any]]:
    component_results = []
    weights = []
    for index, component in enumerate(spec.components):
        component_task = OptimizationTask(
            task_id=task.task_id,
            prompt=task.prompt,
            capability=task.capability,
            source=task.source,
            difficulty=task.difficulty,
            split=task.split,
            evaluation=component,
            metadata=task.metadata,
        )
        component_results.append(
            evaluate_output(component_task, output, judge=judge)
        )
        weights.append(float(spec.weights.get(str(index), 1.0)))
    total_weight = sum(weights)
    if total_weight <= 0:
        raise EvaluationExecutionError(
            "hybrid weights must have a positive sum"
        )
    score = sum(
        outcome.score * weight
        for outcome, weight in zip(component_results, weights, strict=True)
    ) / total_weight
    threshold = float(spec.gate_metadata.get("pass_threshold", 0.5))
    return score, score >= threshold, {
        "components": tuple(
            outcome.to_dict() for outcome in component_results
        ),
        "weights": tuple(weights),
    }


def _normalize_text(value: Any) -> str:
    return " ".join(str(value).strip().lower().split())


def _as_set(value: Any) -> set[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = (value,)
    return {_normalize_text(item) for item in items}


def _token_f1(output: str, reference: str) -> float:
    output_tokens = output.split()
    reference_tokens = reference.split()
    if not output_tokens and not reference_tokens:
        return 1.0
    if not output_tokens or not reference_tokens:
        return 0.0
    remaining = list(reference_tokens)
    common = 0
    for token in output_tokens:
        if token in remaining:
            remaining.remove(token)
            common += 1
    precision = common / len(output_tokens)
    recall = common / len(reference_tokens)
    return (
        0.0
        if precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )


__all__ = [
    "EvaluationExecutionError",
    "EvaluationOutcome",
    "JudgeFunction",
    "PROGRAMMATIC_CHECK_TYPES",
    "REFERENCE_METHODS",
    "evaluate_output",
]
