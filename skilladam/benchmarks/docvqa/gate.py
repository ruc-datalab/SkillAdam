"""Historical DocVQA hard/soft acceptance gates."""

from __future__ import annotations

import math
from numbers import Real

from skilladam.types import GateDecision, MetricResult


class DocVQAAcceptanceGate:
    """Accept either metric gain while guarding both against regression."""

    def __init__(
        self,
        *,
        min_hard_gain: float = 0.08,
        min_soft_gain: float = 0.05,
        max_hard_drop: float = 0.08,
        max_soft_drop: float = 0.05,
        profile: str = "v1",
    ) -> None:
        self.min_hard_gain = _threshold(min_hard_gain, "min_hard_gain")
        self.min_soft_gain = _threshold(min_soft_gain, "min_soft_gain")
        self.max_hard_drop = _threshold(max_hard_drop, "max_hard_drop")
        self.max_soft_drop = _threshold(max_soft_drop, "max_soft_drop")
        if not isinstance(profile, str) or not profile.strip():
            raise ValueError("DocVQA gate profile must be non-empty text")
        self.profile = profile.strip()

    @classmethod
    def for_v2(cls) -> "DocVQAAcceptanceGate":
        """Return the profile that produced the preserved checkpoint."""

        return cls(
            min_hard_gain=0.01,
            min_soft_gain=0.01,
            max_hard_drop=0.05,
            max_soft_drop=0.05,
            profile="v2",
        )

    def judge(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        _validate_comparable(baseline, candidate)
        hard_gain = (
            _metric_value(candidate, "hard")
            - _metric_value(baseline, "hard")
        )
        soft_gain = (
            _metric_value(candidate, "soft")
            - _metric_value(baseline, "soft")
        )
        has_improvement = (
            _at_least(hard_gain, self.min_hard_gain)
            or _at_least(soft_gain, self.min_soft_gain)
        )
        no_significant_drop = (
            _strictly_above_drop_boundary(
                hard_gain,
                self.max_hard_drop,
            )
            and _strictly_above_drop_boundary(
                soft_gain,
                self.max_soft_drop,
            )
        )
        thresholds = self.to_dict()
        accepted = has_improvement and no_significant_drop
        return GateDecision(
            accepted=accepted,
            reason=(
                f"hard_gain={hard_gain:+.3f}; "
                f"soft_gain={soft_gain:+.3f}; "
                f"profile={self.profile}; "
                f"{'accepted' if accepted else 'rejected'}"
            ),
            baseline=baseline,
            candidate=candidate,
            metadata={
                "profile": self.profile,
                "gains": {"hard": hard_gain, "soft": soft_gain},
                "thresholds": thresholds,
            },
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "min_hard_gain": self.min_hard_gain,
            "min_soft_gain": self.min_soft_gain,
            "max_hard_drop": self.max_hard_drop,
            "max_soft_drop": self.max_soft_drop,
        }


def _metric_value(result: MetricResult, name: str) -> float:
    value = result.metrics.get(name, result.primary)
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"DocVQA metric {name!r} must be finite")
    return float(value)


def _validate_comparable(
    baseline: MetricResult,
    candidate: MetricResult,
) -> None:
    if baseline.sample_count != candidate.sample_count:
        raise ValueError(
            "DocVQA baseline and candidate sample counts must match"
        )
    if set(baseline.case_metrics) != set(candidate.case_metrics):
        raise ValueError(
            "DocVQA baseline and candidate must cover the same cases"
        )


def _threshold(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise ValueError(
            f"DocVQA {name} must be finite and non-negative"
        )
    return float(value)


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
