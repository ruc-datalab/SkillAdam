"""Adam-style variance-driven edit-budget helpers."""

from __future__ import annotations

import math
import statistics
from numbers import Real

from skilladam.types import MetricResult


def compute_per_case_deltas(
    baseline: MetricResult,
    candidate: MetricResult,
    *,
    metric_key: str,
) -> list[float]:
    """Return candidate-minus-baseline values aligned by case ID."""

    if not metric_key.strip():
        raise ValueError("metric_key must be non-empty")

    deltas: list[float] = []
    for case_id, baseline_metrics in baseline.case_metrics.items():
        if case_id not in candidate.case_metrics:
            continue
        candidate_metrics = candidate.case_metrics[case_id]
        baseline_value = _strict_metric(
            baseline_metrics,
            metric_key=metric_key,
            case_id=case_id,
            side="baseline",
        )
        candidate_value = _strict_metric(
            candidate_metrics,
            metric_key=metric_key,
            case_id=case_id,
            side="candidate",
        )
        deltas.append(candidate_value - baseline_value)
    return deltas


def compute_sigma_sq(deltas: list[float]) -> float:
    """Return sample variance, or zero when fewer than two cases are paired."""

    if len(deltas) < 2:
        return 0.0
    return float(statistics.variance(deltas))


def compute_edit_budget(
    v_ema: float,
    *,
    v_max: float,
    base: int,
    minimum: int,
) -> int:
    """Shrink the hunk budget as the validation-delta EMA grows."""

    if base < 1:
        raise ValueError("base must be at least 1")
    if minimum < 1:
        raise ValueError("minimum must be at least 1")
    if minimum > base:
        raise ValueError("minimum cannot exceed base")
    if not math.isfinite(float(v_ema)):
        raise ValueError("v_ema must be finite")
    if not math.isfinite(float(v_max)):
        raise ValueError("v_max must be finite")
    if v_max <= 0:
        return base

    ratio = max(0.0, min(float(v_ema) / float(v_max), 1.0))
    return max(minimum, round(base * (1.0 - ratio)))


def update_v_ema(v_ema: float, sigma_sq: float, *, beta: float) -> float:
    """Apply ``v <- beta*v + (1-beta)*sigma_sq``."""

    if not math.isfinite(float(beta)) or not 0.0 <= beta <= 1.0:
        raise ValueError("beta must be in [0, 1]")
    if not math.isfinite(float(v_ema)):
        raise ValueError("v_ema must be finite")
    if not math.isfinite(float(sigma_sq)):
        raise ValueError("sigma_sq must be finite")
    if sigma_sq < 0:
        raise ValueError("sigma_sq must be non-negative")
    return beta * float(v_ema) + (1.0 - beta) * float(sigma_sq)


def build_budget_section(current_budget: int | None) -> str:
    """Render an optional prompt section without forcing a patch."""

    if current_budget is None:
        return ""
    if current_budget < 1:
        raise ValueError("current_budget must be at least 1")
    return (
        "\n\n## Edit Budget for This Iteration\n\n"
        f"If a patch is warranted, include at most **{current_budget} edits** "
        "(hunks in unified diff). Returning an empty patch remains valid when "
        "no recurring pattern justifies a change."
    )


def _strict_metric(
    metrics: object,
    *,
    metric_key: str,
    case_id: str,
    side: str,
) -> float:
    if not isinstance(metrics, dict) and not hasattr(metrics, "__getitem__"):
        raise ValueError(f"{side} metrics for case {case_id!r} are invalid")
    try:
        value = metrics[metric_key]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"{side} metric {metric_key!r} is missing for case {case_id!r}"
        ) from exc
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(
            f"{side} metric {metric_key!r} for case {case_id!r} "
            "must be numeric"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(
            f"{side} metric {metric_key!r} for case {case_id!r} "
            "must be finite"
        )
    return number
