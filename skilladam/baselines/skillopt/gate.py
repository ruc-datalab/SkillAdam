"""Strict current/best selection for the SkillOpt reproduction."""

from __future__ import annotations

from dataclasses import dataclass
import math

from skilladam.baselines.skillopt.contracts import SkillVersion
from skilladam.types import MetricResult


@dataclass(frozen=True)
class GateConfig:
    metric: str = "hard"
    mixed_weight: float = 0.5

    def __post_init__(self) -> None:
        if self.metric not in {"hard", "soft", "mixed"}:
            raise ValueError("unknown SkillOpt gate metric")
        if (
            not math.isfinite(self.mixed_weight)
            or not 0.0 <= self.mixed_weight <= 1.0
        ):
            raise ValueError(
                "SkillOpt mixed_weight must be in range [0, 1]"
            )


@dataclass(frozen=True)
class GateTransition:
    action: str
    current: SkillVersion
    best: SkillVersion


def select_gate_score(
    metric: MetricResult,
    config: GateConfig,
) -> float:
    """Project a shared hard/soft metric into gate score space."""

    hard = _unit_metric(metric, "hard")
    soft = _unit_metric(metric, "soft")
    if config.metric == "hard":
        return hard
    if config.metric == "soft":
        return soft
    return (
        (1.0 - config.mixed_weight) * hard
        + config.mixed_weight * soft
    )


def evaluate_gate(
    *,
    current: SkillVersion,
    best: SkillVersion,
    candidate: SkillVersion,
) -> GateTransition:
    """Accept only a candidate strictly better than current."""

    if candidate.score > current.score:
        if candidate.score > best.score:
            return GateTransition(
                action="accept_new_best",
                current=candidate,
                best=candidate,
            )
        return GateTransition(
            action="accept",
            current=candidate,
            best=best,
        )
    return GateTransition(
        action="reject",
        current=current,
        best=best,
    )


def _unit_metric(metric: MetricResult, name: str) -> float:
    if name not in metric.metrics:
        raise ValueError(f"SkillOpt metric {name!r} is missing")
    value = metric.metrics[name]
    if isinstance(value, bool):
        raise ValueError(f"SkillOpt metric {name!r} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"SkillOpt metric {name!r} must be numeric"
        ) from exc
    if not math.isfinite(number):
        raise ValueError(f"SkillOpt metric {name!r} must be finite")
    if not 0.0 <= number <= 1.0:
        raise ValueError(
            f"SkillOpt metric {name!r} must be in range [0, 1]"
        )
    return number
