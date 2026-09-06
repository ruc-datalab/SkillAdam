"""Historical LMB exact-match acceptance gate."""

from __future__ import annotations

import math
from numbers import Real

from skilladam.types import GateDecision, MetricResult


class LMBAcceptanceGate:
    """Accept an EM gain while rejecting the historical drop boundary."""

    def __init__(
        self,
        *,
        min_em_gain: float = 0.08,
        max_em_drop: float = 0.08,
    ) -> None:
        self.min_em_gain = min_em_gain
        self.max_em_drop = max_em_drop

    def judge(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        _validate_comparable(baseline, candidate)
        baseline_em = _metric_value(baseline)
        candidate_em = _metric_value(candidate)
        em_gain = candidate_em - baseline_em
        accepted = (
            _at_least(em_gain, self.min_em_gain)
            and _strictly_above_drop_boundary(em_gain, self.max_em_drop)
        )
        return GateDecision(
            accepted=accepted,
            reason=(
                f"exact_match_gain={em_gain:+.3f} "
                f"(thresholds: gain>=+{self.min_em_gain}; "
                f"drop<{self.max_em_drop})"
            ),
            baseline=baseline,
            candidate=candidate,
            metadata={
                "gains": {"exact_match": em_gain},
                "thresholds": self.to_dict(),
            },
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "min_em_gain": self.min_em_gain,
            "max_em_drop": self.max_em_drop,
        }


def _metric_value(result: MetricResult) -> float:
    value = result.metrics.get("exact_match", result.primary)
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
    ):
        raise ValueError("LMB exact_match metric must be finite")
    return float(value)


def _validate_comparable(
    baseline: MetricResult,
    candidate: MetricResult,
) -> None:
    if baseline.sample_count != candidate.sample_count:
        raise ValueError("LMB baseline and candidate sample counts must match")
    if set(baseline.case_metrics) != set(candidate.case_metrics):
        raise ValueError(
            "LMB baseline and candidate must cover the same cases"
        )


def _at_least(value: float, threshold: float) -> bool:
    return value > threshold or math.isclose(
        value,
        threshold,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def _strictly_above_drop_boundary(
    value: float,
    max_drop: float,
) -> bool:
    boundary = -max_drop
    return value > boundary and not math.isclose(
        value,
        boundary,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
