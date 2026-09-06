"""Historical SearchQA dual-metric acceptance gate."""

from __future__ import annotations

import math
from numbers import Real

from skilladam.types import GateDecision, MetricResult


class SearchQAAcceptanceGate:
    """Accept EM or F1 gains while guarding both metrics from regression."""

    def __init__(
        self,
        *,
        min_em_gain: float = 0.08,
        min_f1_gain: float = 0.05,
        max_em_drop: float = 0.08,
        max_f1_drop: float = 0.05,
    ) -> None:
        self.min_em_gain = min_em_gain
        self.min_f1_gain = min_f1_gain
        self.max_em_drop = max_em_drop
        self.max_f1_drop = max_f1_drop

    def judge(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        _validate_comparable(baseline, candidate)
        baseline_em = _metric_value(baseline, "exact_match")
        candidate_em = _metric_value(candidate, "exact_match")
        baseline_f1 = _metric_value(baseline, "token_f1")
        candidate_f1 = _metric_value(candidate, "token_f1")
        em_gain = candidate_em - baseline_em
        f1_gain = candidate_f1 - baseline_f1

        has_improvement = (
            _at_least(em_gain, self.min_em_gain)
            or _at_least(f1_gain, self.min_f1_gain)
        )
        no_significant_drop = (
            _strictly_above_drop_boundary(em_gain, self.max_em_drop)
            and _strictly_above_drop_boundary(f1_gain, self.max_f1_drop)
        )
        accepted = has_improvement and no_significant_drop
        reason = (
            f"em_gain={em_gain:+.3f} | f1_gain={f1_gain:+.3f} "
            f"(thresholds: em_gain>=+{self.min_em_gain} or "
            f"f1_gain>=+{self.min_f1_gain}; "
            f"em_drop<{self.max_em_drop} and "
            f"f1_drop<{self.max_f1_drop})"
        )
        return GateDecision(
            accepted=accepted,
            reason=reason,
            baseline=baseline,
            candidate=candidate,
            metadata={
                "gains": {
                    "exact_match": em_gain,
                    "token_f1": f1_gain,
                },
                "thresholds": self.to_dict(),
            },
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "min_em_gain": self.min_em_gain,
            "min_f1_gain": self.min_f1_gain,
            "max_em_drop": self.max_em_drop,
            "max_f1_drop": self.max_f1_drop,
        }


def _metric_value(result: MetricResult, name: str) -> float:
    value = result.metrics.get(name, result.primary)
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"SearchQA metric {name!r} must be finite")
    return float(value)


def _validate_comparable(
    baseline: MetricResult,
    candidate: MetricResult,
) -> None:
    if baseline.sample_count != candidate.sample_count:
        raise ValueError(
            "SearchQA baseline and candidate sample counts must match"
        )
    if set(baseline.case_metrics) != set(candidate.case_metrics):
        raise ValueError(
            "SearchQA baseline and candidate must cover the same cases"
        )


def _at_least(value: float, threshold: float) -> bool:
    return value > threshold or math.isclose(
        value,
        threshold,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def _strictly_above_drop_boundary(value: float, max_drop: float) -> bool:
    boundary = -max_drop
    return value > boundary and not math.isclose(
        value,
        boundary,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
