"""Historical SpreadsheetBench hard/per-cell acceptance gate."""

from __future__ import annotations

import math
from numbers import Real

from skilladam.types import GateDecision, MetricResult


class SpreadsheetBenchAcceptanceGate:
    """Accept a gain on either metric while guarding both from regression."""

    def __init__(
        self,
        *,
        min_hard_gain: float = 0.06,
        min_per_cell_gain: float = 0.03,
        max_hard_drop: float = 0.08,
        max_per_cell_drop: float = 0.06,
    ) -> None:
        self.min_hard_gain = min_hard_gain
        self.min_per_cell_gain = min_per_cell_gain
        self.max_hard_drop = max_hard_drop
        self.max_per_cell_drop = max_per_cell_drop

    def judge(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        _validate_comparable(baseline, candidate)
        baseline_hard = _metric_value(baseline, "hard")
        candidate_hard = _metric_value(candidate, "hard")
        baseline_per_cell = _metric_value(
            baseline,
            "per_cell_pass_rate",
        )
        candidate_per_cell = _metric_value(
            candidate,
            "per_cell_pass_rate",
        )
        hard_gain = candidate_hard - baseline_hard
        per_cell_gain = candidate_per_cell - baseline_per_cell
        has_improvement = (
            _at_least(hard_gain, self.min_hard_gain)
            or _at_least(per_cell_gain, self.min_per_cell_gain)
        )
        no_significant_drop = (
            _strictly_above_drop_boundary(
                hard_gain,
                self.max_hard_drop,
            )
            and _strictly_above_drop_boundary(
                per_cell_gain,
                self.max_per_cell_drop,
            )
        )
        reason = (
            f"hard_gain={hard_gain:+.3f} | "
            f"per_cell_gain={per_cell_gain:+.3f} "
            f"(thresholds: hard_gain>=+{self.min_hard_gain} or "
            f"per_cell_gain>=+{self.min_per_cell_gain}; "
            f"hard_drop<{self.max_hard_drop} and "
            f"per_cell_drop<{self.max_per_cell_drop})"
        )
        return GateDecision(
            accepted=has_improvement and no_significant_drop,
            reason=reason,
            baseline=baseline,
            candidate=candidate,
            metadata={
                "gains": {
                    "hard": hard_gain,
                    "per_cell_pass_rate": per_cell_gain,
                },
                "thresholds": self.to_dict(),
            },
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "min_hard_gain": self.min_hard_gain,
            "min_per_cell_gain": self.min_per_cell_gain,
            "max_hard_drop": self.max_hard_drop,
            "max_per_cell_drop": self.max_per_cell_drop,
        }


def _metric_value(result: MetricResult, name: str) -> float:
    value = result.metrics.get(name, result.primary)
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
    ):
        raise ValueError(
            f"SpreadsheetBench metric {name!r} must be finite"
        )
    return float(value)


def _validate_comparable(
    baseline: MetricResult,
    candidate: MetricResult,
) -> None:
    if baseline.sample_count != candidate.sample_count:
        raise ValueError(
            "SpreadsheetBench baseline and candidate sample counts must match"
        )
    if set(baseline.case_metrics) != set(candidate.case_metrics):
        raise ValueError(
            "SpreadsheetBench baseline and candidate must cover "
            "the same cases"
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
