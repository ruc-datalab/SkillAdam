"""Pure edit-budget schedules retained from the MIT SkillOpt behavior."""

from __future__ import annotations

import math


AUTONOMOUS_BUDGET = 999
_MODES = frozenset({"constant", "linear", "cosine", "autonomous"})


class EditBudgetScheduler:
    """Validated stateful scheduler with one-indexed optimization steps."""

    def __init__(
        self,
        mode: str,
        *,
        max_budget: int,
        min_budget: int,
        total_steps: int,
    ) -> None:
        if mode not in _MODES:
            raise ValueError(
                f"unknown SkillOpt scheduler mode {mode!r}"
            )
        for name, value in (
            ("max_budget", max_budget),
            ("min_budget", min_budget),
            ("total_steps", total_steps),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise ValueError(
                    f"SkillOpt scheduler {name} must be a positive integer"
                )
        if max_budget < min_budget:
            raise ValueError(
                "SkillOpt max_budget must be at least min_budget"
            )
        self.mode = mode
        self.max_budget = max_budget
        self.min_budget = min_budget
        self.total_steps = total_steps
        self._current_step = 0

    def step(self) -> int:
        if self._current_step >= self.total_steps:
            raise ValueError("SkillOpt scheduler has no remaining steps")
        self._current_step += 1
        return self.get_budget(self._current_step)

    def get_budget(self, step: int) -> int:
        if (
            isinstance(step, bool)
            or not isinstance(step, int)
            or not 1 <= step <= self.total_steps
        ):
            raise ValueError(
                "SkillOpt scheduler step is outside the configured range"
            )
        if self.mode == "constant":
            return self.max_budget
        if self.mode == "autonomous":
            return AUTONOMOUS_BUDGET
        if self.total_steps == 1:
            return self.max_budget
        fraction = step / self.total_steps
        if self.mode == "linear":
            budget = self.max_budget + (
                self.min_budget - self.max_budget
            ) * fraction
        else:
            budget = self.min_budget + 0.5 * (
                self.max_budget - self.min_budget
            ) * (1 + math.cos(math.pi * fraction))
        return max(self.min_budget, round(budget))

    def state_dict(self) -> dict[str, int]:
        return {"current_step": self._current_step}

    def load_state_dict(self, payload: object) -> None:
        if not isinstance(payload, dict) or set(payload) != {
            "current_step"
        }:
            raise ValueError("invalid SkillOpt scheduler state")
        current_step = payload["current_step"]
        if (
            isinstance(current_step, bool)
            or not isinstance(current_step, int)
            or not 0 <= current_step <= self.total_steps
        ):
            raise ValueError("invalid SkillOpt scheduler current_step")
        self._current_step = current_step


def build_scheduler(
    mode: str = "constant",
    *,
    max_budget: int = 8,
    min_budget: int = 2,
    total_steps: int = 8,
) -> EditBudgetScheduler:
    """Build one exact historical edit-budget schedule."""

    return EditBudgetScheduler(
        mode,
        max_budget=max_budget,
        min_budget=min_budget,
        total_steps=total_steps,
    )
