"""Configurable acceptance gate shared by public benchmark adapters."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from numbers import Real
from typing import Any

from skilladam.types import GateDecision, MetricResult


@dataclass(frozen=True)
class ImprovementRule:
    """Require a strict gain above ``min_gain`` for one metric."""

    metric: str
    min_gain: float = 0.0


@dataclass(frozen=True)
class NonRegressionRule:
    """Reject when one metric drops by more than ``max_drop``."""

    metric: str
    max_drop: float = 0.0


class AcceptanceGate:
    """Accept when any improvement rule passes and every guardrail holds."""

    def __init__(
        self,
        *,
        improvements: tuple[ImprovementRule, ...],
        non_regressions: tuple[NonRegressionRule, ...] = (),
    ) -> None:
        if not improvements:
            raise ValueError("at least one improvement rule is required")
        for rule in improvements:
            if (
                isinstance(rule.min_gain, bool)
                or not isinstance(rule.min_gain, Real)
                or not math.isfinite(float(rule.min_gain))
                or rule.min_gain < 0
            ):
                raise ValueError("min_gain must be finite and non-negative")
        for rule in non_regressions:
            if (
                isinstance(rule.max_drop, bool)
                or not isinstance(rule.max_drop, Real)
                or not math.isfinite(float(rule.max_drop))
                or rule.max_drop < 0
            ):
                raise ValueError("max_drop must be finite and non-negative")
        self.improvements = improvements
        self.non_regressions = non_regressions

    def judge(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        """Compare candidate and baseline metrics on the same cases."""

        _validate_comparable_results(baseline, candidate)
        metric_names = {
            rule.metric for rule in (*self.improvements, *self.non_regressions)
        }
        gains = {
            name: _metric_value(candidate, name) - _metric_value(baseline, name)
            for name in sorted(metric_names)
        }
        improved = [
            rule.metric
            for rule in self.improvements
            if gains[rule.metric] > rule.min_gain
        ]
        regressions = [
            rule.metric
            for rule in self.non_regressions
            if gains[rule.metric] < -rule.max_drop
        ]
        accepted = bool(improved) and not regressions

        if regressions:
            reason = "protected metric regressed: " + ", ".join(regressions)
        elif not improved:
            reason = "no metric exceeded its strict improvement threshold"
        else:
            reason = "improved: " + ", ".join(improved)

        metadata: dict[str, Any] = {
            "gains": gains,
            "improved_metrics": improved,
            "regressed_metrics": regressions,
            "config": self.to_dict(),
        }
        return GateDecision(
            accepted=accepted,
            reason=reason,
            baseline=baseline,
            candidate=candidate,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize gate rules for a run configuration snapshot."""

        return {
            "improvements": [asdict(rule) for rule in self.improvements],
            "non_regressions": [
                asdict(rule) for rule in self.non_regressions
            ],
        }


def _metric_value(result: MetricResult, name: str) -> float:
    if name == "primary":
        value = float(result.primary)
    else:
        if name not in result.metrics:
            raise ValueError(f"metric {name!r} is missing from MetricResult")
        value = float(result.metrics[name])
    if not math.isfinite(value):
        raise ValueError(f"metric {name!r} must be finite")
    return value


def _validate_comparable_results(
    baseline: MetricResult,
    candidate: MetricResult,
) -> None:
    for label, result in (("baseline", baseline), ("candidate", candidate)):
        if result.sample_count < 1:
            raise ValueError(f"{label} sample_count must be at least 1")
        _require_finite(result.primary, f"{label}.primary")
        for metric_name, value in result.metrics.items():
            _require_finite(value, f"{label}.metrics[{metric_name!r}]")
        for case_id, metrics in result.case_metrics.items():
            for metric_name, value in metrics.items():
                _require_finite(
                    value,
                    (
                        f"{label}.case_metrics[{case_id!r}]"
                        f"[{metric_name!r}]"
                    ),
                )
        if result.case_metrics and (
            len(result.case_metrics) != result.sample_count
        ):
            raise ValueError(
                f"{label} case_metrics must match sample_count"
            )

    if (
        baseline.sample_count != candidate.sample_count
        or set(baseline.case_metrics) != set(candidate.case_metrics)
    ):
        raise ValueError(
            "baseline and candidate must cover the same validation cases"
        )


def _require_finite(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_name} must be numeric")
    if not math.isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite")
