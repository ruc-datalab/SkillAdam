"""Pure protected-block and longitudinal slow-update behavior."""

from __future__ import annotations

from dataclasses import dataclass
import math

from skilladam.baselines.skillopt.contracts import SkillVersion
from skilladam.baselines.skillopt.gate import (
    GateTransition,
    evaluate_gate,
)
from skilladam.types import MetricResult


SLOW_UPDATE_START = "<!-- SLOW_UPDATE_START -->"
SLOW_UPDATE_END = "<!-- SLOW_UPDATE_END -->"


@dataclass(frozen=True)
class LongitudinalPair:
    case_id: str
    category: str
    previous_hard: float
    previous_soft: float
    current_hard: float
    current_soft: float


def inject_slow_update_block(skill: str) -> str:
    bounds = _block_bounds(skill)
    if bounds is not None:
        return skill
    return (
        skill.rstrip()
        + "\n\n"
        + SLOW_UPDATE_START
        + "\n"
        + SLOW_UPDATE_END
        + "\n"
    )


def extract_slow_update_block(skill: str) -> str:
    bounds = _block_bounds(skill)
    if bounds is None:
        return ""
    start, end = bounds
    inner_start = start + len(SLOW_UPDATE_START)
    return skill[inner_start:end].strip()


def replace_slow_update_block(skill: str, guidance: str) -> str:
    if not isinstance(guidance, str):
        raise ValueError("SkillOpt slow-update guidance must be text")
    bounds = _block_bounds(skill)
    if bounds is None:
        base = skill.rstrip()
    else:
        start, end = bounds
        after = end + len(SLOW_UPDATE_END)
        base = (skill[:start] + skill[after:]).strip()
    return (
        base
        + "\n\n"
        + SLOW_UPDATE_START
        + "\n"
        + guidance.strip()
        + "\n"
        + SLOW_UPDATE_END
        + "\n"
    )


def build_longitudinal_pairs(
    previous: MetricResult,
    current: MetricResult,
) -> tuple[LongitudinalPair, ...]:
    """Compare the same ordered cases across adjacent epochs."""

    previous_ids = tuple(previous.case_metrics)
    current_ids = tuple(current.case_metrics)
    if (
        previous.sample_count != current.sample_count
        or previous_ids != current_ids
        or previous.sample_count != len(previous_ids)
    ):
        raise ValueError(
            "SkillOpt longitudinal metrics must cover the same cases"
        )
    pairs: list[LongitudinalPair] = []
    for case_id in previous_ids:
        previous_values = previous.case_metrics[case_id]
        current_values = current.case_metrics[case_id]
        previous_hard = _unit_value(previous_values, "hard")
        previous_soft = _unit_value(previous_values, "soft")
        current_hard = _unit_value(current_values, "hard")
        current_soft = _unit_value(current_values, "soft")
        previous_ok = bool(previous_hard)
        current_ok = bool(current_hard)
        if not previous_ok and current_ok:
            category = "improved"
        elif previous_ok and not current_ok:
            category = "regressed"
        elif not previous_ok and not current_ok:
            category = "persistent_failure"
        else:
            category = "stable_success"
        pairs.append(
            LongitudinalPair(
                case_id=case_id,
                category=category,
                previous_hard=previous_hard,
                previous_soft=previous_soft,
                current_hard=current_hard,
                current_soft=current_soft,
            )
        )
    return tuple(pairs)


def apply_force_slow_update(
    *,
    current: SkillVersion,
    best: SkillVersion,
    guidance: str,
    origin: str,
) -> GateTransition:
    """Replace guidance in current and best without changing base scores."""

    return GateTransition(
        action="force_accept",
        current=_guided(current, guidance, origin),
        best=_guided(best, guidance, origin),
    )


def apply_gated_slow_update(
    *,
    current: SkillVersion,
    best: SkillVersion,
    guidance: str,
    candidate_score: float,
    origin: str,
) -> GateTransition:
    """Run a protected guidance candidate through the strict gate."""

    candidate = SkillVersion(
        text=replace_slow_update_block(current.text, guidance),
        score=candidate_score,
        base_origin=current.base_origin,
        slow_update_origin=origin,
    )
    return evaluate_gate(
        current=current,
        best=best,
        candidate=candidate,
    )


def _guided(
    version: SkillVersion,
    guidance: str,
    origin: str,
) -> SkillVersion:
    return SkillVersion(
        text=replace_slow_update_block(version.text, guidance),
        score=version.score,
        base_origin=version.base_origin,
        slow_update_origin=origin,
    )


def _block_bounds(skill: str) -> tuple[int, int] | None:
    if not isinstance(skill, str):
        raise ValueError("SkillOpt skill must be text")
    starts = skill.count(SLOW_UPDATE_START)
    ends = skill.count(SLOW_UPDATE_END)
    if starts == 0 and ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise ValueError(
            "SkillOpt SLOW_UPDATE block must contain exactly one marker pair"
        )
    start = skill.find(SLOW_UPDATE_START)
    end = skill.find(SLOW_UPDATE_END)
    if end < start + len(SLOW_UPDATE_START):
        raise ValueError("SkillOpt SLOW_UPDATE markers are malformed")
    return start, end


def _unit_value(values: object, name: str) -> float:
    if not isinstance(values, dict) and not hasattr(values, "get"):
        raise ValueError("SkillOpt case metrics must be objects")
    value = values.get(name)
    if isinstance(value, bool):
        raise ValueError(f"SkillOpt case metric {name!r} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"SkillOpt case metric {name!r} must be numeric"
        ) from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(
            f"SkillOpt case metric {name!r} must be finite in [0, 1]"
        )
    return number
