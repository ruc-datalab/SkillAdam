"""Historical ALFWorld hard-success acceptance gate."""

from __future__ import annotations

import math
from numbers import Real

from skilladam.types import GateDecision, MetricResult


class ALFWorldAcceptanceGate:
    """Reproduce relaxed r3/r4 or strict r2 hard-success gating."""

    def __init__(self, *, min_hard_gain: float = 0.0) -> None:
        if (
            isinstance(min_hard_gain, bool)
            or not isinstance(min_hard_gain, Real)
            or not math.isfinite(float(min_hard_gain))
            or float(min_hard_gain) < 0.0
        ):
            raise ValueError(
                "ALFWorld min_hard_gain must be finite and non-negative"
            )
        self.min_hard_gain = float(min_hard_gain)

    def judge(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        _validate_comparable(baseline, candidate)
        baseline_hard = _metric_value(baseline)
        candidate_hard = _metric_value(candidate)
        delta = candidate_hard - baseline_hard
        accepted = delta > self.min_hard_gain or math.isclose(
            delta,
            self.min_hard_gain,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        strict = self.min_hard_gain > 0.0
        if strict:
            reason = (
                f"hard {delta:+.3f} "
                + (
                    f"(>= {self.min_hard_gain:.2f})"
                    if accepted
                    else "(no improvement)"
                )
            )
            rule = "require-hard-gain"
        else:
            reason = (
                f"hard {delta:+.3f} "
                f"({'no regression' if accepted else 'regression'})"
            )
            rule = "accept-hard-non-regression"
        return GateDecision(
            accepted=accepted,
            reason=reason,
            baseline=baseline,
            candidate=candidate,
            metadata={
                "gains": {"hard": delta},
                "rule": rule,
                "min_hard_gain": self.min_hard_gain,
            },
        )

    def to_dict(self) -> dict[str, float]:
        """Return the explicit public gate configuration."""

        return {"min_hard_gain": self.min_hard_gain}


def _metric_value(result: MetricResult) -> float:
    value = result.metrics.get("hard", result.primary)
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
    ):
        raise ValueError("ALFWorld hard metric must be finite")
    return float(value)


def _validate_comparable(
    baseline: MetricResult,
    candidate: MetricResult,
) -> None:
    if baseline.sample_count != candidate.sample_count:
        raise ValueError(
            "ALFWorld baseline and candidate sample counts must match"
        )
    if set(baseline.case_metrics) != set(candidate.case_metrics):
        raise ValueError(
            "ALFWorld baseline and candidate must cover the same cases"
        )
