"""Historical SkillAdam acceptance gates for DeepPlanning."""

from __future__ import annotations

import math
from typing import Any

from skilladam.types import GateDecision, MetricResult


class ShoppingAcceptanceGate:
    """Accept shopping gains while enforcing strict regression boundaries."""

    def __init__(
        self,
        *,
        min_case_score_gain: float = 0.15,
        min_match_rate_gain: float = 0.05,
        max_case_score_drop: float = 0.15,
        max_match_rate_drop: float = 0.05,
    ) -> None:
        self.min_case_score_gain = _threshold(
            min_case_score_gain,
            "min_case_score_gain",
        )
        self.min_match_rate_gain = _threshold(
            min_match_rate_gain,
            "min_match_rate_gain",
        )
        self.max_case_score_drop = _threshold(
            max_case_score_drop,
            "max_case_score_drop",
        )
        self.max_match_rate_drop = _threshold(
            max_match_rate_drop,
            "max_match_rate_drop",
        )

    def judge(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        _validate_comparable(baseline, candidate, "shopping")
        case_gain = (
            _metric(candidate, "case_score")
            - _metric(baseline, "case_score")
        )
        match_gain = (
            _metric(candidate, "match_rate")
            - _metric(baseline, "match_rate")
        )
        improved = (
            _strictly_above(case_gain, self.min_case_score_gain)
            or _strictly_above(match_gain, self.min_match_rate_gain)
        )
        guarded = (
            _strictly_above(case_gain, -self.max_case_score_drop)
            and _strictly_above(match_gain, -self.max_match_rate_drop)
        )
        accepted = improved and guarded
        return GateDecision(
            accepted=accepted,
            reason=(
                f"case_score_gain={case_gain:+.3f}, "
                f"match_rate_gain={match_gain:+.3f}"
            ),
            baseline=baseline,
            candidate=candidate,
            metadata={
                "gains": {
                    "case_score": case_gain,
                    "match_rate": match_gain,
                },
                "config": self.to_dict(),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_class": type(self).__name__,
            "min_case_score_gain": self.min_case_score_gain,
            "min_match_rate_gain": self.min_match_rate_gain,
            "max_case_score_drop": self.max_case_score_drop,
            "max_match_rate_drop": self.max_match_rate_drop,
        }


class TravelAcceptanceGate:
    """Accept travel gains while enforcing strict regression boundaries."""

    def __init__(
        self,
        *,
        min_cs_gain: float = 0.03,
        min_ps_gain: float = 0.08,
        max_cs_drop: float = 0.05,
        max_ps_drop: float = 0.08,
    ) -> None:
        self.min_cs_gain = _threshold(min_cs_gain, "min_cs_gain")
        self.min_ps_gain = _threshold(min_ps_gain, "min_ps_gain")
        self.max_cs_drop = _threshold(max_cs_drop, "max_cs_drop")
        self.max_ps_drop = _threshold(max_ps_drop, "max_ps_drop")

    def judge(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        _validate_comparable(baseline, candidate, "travel")
        cs_gain = (
            _metric(candidate, "commonsense_weighted_score")
            - _metric(baseline, "commonsense_weighted_score")
        )
        ps_gain = (
            _metric(candidate, "personalized_score")
            - _metric(baseline, "personalized_score")
        )
        improved = (
            _strictly_above(cs_gain, self.min_cs_gain)
            or _strictly_above(ps_gain, self.min_ps_gain)
        )
        guarded = (
            _strictly_above(cs_gain, -self.max_cs_drop)
            and _strictly_above(ps_gain, -self.max_ps_drop)
        )
        accepted = improved and guarded
        return GateDecision(
            accepted=accepted,
            reason=f"cs_gain={cs_gain:+.3f}, ps_gain={ps_gain:+.3f}",
            baseline=baseline,
            candidate=candidate,
            metadata={
                "gains": {
                    "commonsense_weighted_score": cs_gain,
                    "personalized_score": ps_gain,
                },
                "config": self.to_dict(),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_class": type(self).__name__,
            "min_cs_gain": self.min_cs_gain,
            "min_ps_gain": self.min_ps_gain,
            "max_cs_drop": self.max_cs_drop,
            "max_ps_drop": self.max_ps_drop,
        }


def _validate_comparable(
    baseline: MetricResult,
    candidate: MetricResult,
    expected_domain: str,
) -> None:
    if baseline.sample_count != candidate.sample_count:
        raise ValueError(
            "DeepPlanning baseline and candidate must cover the same cases"
        )
    if set(baseline.case_metrics) != set(candidate.case_metrics):
        raise ValueError(
            "DeepPlanning baseline and candidate must cover the same cases"
        )
    for result in (baseline, candidate):
        domain = result.metadata.get("domain")
        if domain is not None and domain != expected_domain:
            raise ValueError(
                f"DeepPlanning gate expected {expected_domain} domain"
            )


def _metric(result: MetricResult, name: str) -> float:
    if name not in result.metrics:
        raise ValueError(f"DeepPlanning metric {name!r} is missing")
    value = float(result.metrics[name])
    if not math.isfinite(value):
        raise ValueError(f"DeepPlanning metric {name!r} must be finite")
    return value


def _strictly_above(value: float, boundary: float) -> bool:
    return value > boundary and not math.isclose(
        value,
        boundary,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def _threshold(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(
            f"DeepPlanning gate threshold {name} must be non-negative"
        )
    return number
