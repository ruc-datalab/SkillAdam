"""Independent packaged prompt rendering for SkillOpt optimizer roles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from skilladam.baselines.skillopt.contracts import (
    OptimizerCall,
    OptimizerResult,
)
from skilladam.execution.contracts import GenerationResult


PROMPTS_DIR = (
    Path(__file__).parents[1] / "baselines" / "skillopt" / "prompts"
)
_BENCHMARK_REFLECTION_PROMPTS = frozenset(
    {
        "alfworld",
        "docvqa",
        "lmb",
        "officeqa",
        "searchqa",
        "spreadsheetbench",
    }
)
_HARD_METRIC_NAMES = {
    "alfworld": "hard",
    "deepplanning": "hard",
    "docvqa": "hard",
    "lmb": "exact_match",
    "officeqa": "em",
    "searchqa": "exact_match",
    "spreadsheetbench": "hard",
}
_STAGES = {
    "optimizer_reflection": (
        "reflection.md",
        frozenset({"metric", "traces"}),
    ),
    "optimizer_merge": (
        "merge.md",
        frozenset({"reflection"}),
    ),
    "optimizer_select": (
        "select.md",
        frozenset({"reflection", "merged"}),
    ),
    "slow_update_generation": (
        "slow_update.md",
        frozenset({"pairs", "existing_guidance"}),
    ),
    "optimizer_memory": (
        "memory.md",
        frozenset(
            {
                "candidate_action",
                "slow_update_action",
                "previous_memory",
            }
        ),
    ),
}


@dataclass(frozen=True)
class SkillOptPrompt:
    """One deterministic two-message SkillOpt optimizer prompt."""

    stage: str
    system_prompt: str
    user_prompt: str
    messages: tuple[dict[str, str], ...]


class SkillOptPromptBuilder:
    """Render only SkillOpt-owned prompt resources and call state."""

    def __init__(
        self,
        prompts_dir: Path | None = None,
        *,
        benchmark: str = "deepplanning",
    ) -> None:
        if benchmark not in _HARD_METRIC_NAMES:
            raise ValueError(
                f"unsupported SkillOpt benchmark {benchmark!r}"
            )
        root = prompts_dir or PROMPTS_DIR
        self._benchmark = benchmark
        self._prompts = {
            stage: _read(root / filename)
            for stage, (filename, _) in _STAGES.items()
        }
        self._reflection_prompts = self._load_reflection_prompts(root)

    def build(self, call: OptimizerCall) -> SkillOptPrompt:
        if not isinstance(call, OptimizerCall):
            raise ValueError("SkillOpt prompt input must be OptimizerCall")
        try:
            _, required_inputs = _STAGES[call.stage]
        except KeyError as exc:
            raise ValueError(
                f"unknown SkillOpt optimizer stage {call.stage!r}"
            ) from exc
        _validate_call(call, required_inputs)

        ordered_ids = "\n".join(
            f"{index}. {case_id}"
            for index, case_id in enumerate(call.case_ids, start=1)
        )
        budget = (
            str(call.edit_budget)
            if call.edit_budget is not None
            else "autonomous"
        )
        memory = call.memory.strip() or "(empty)"
        inputs = json.dumps(
            _portable(call.inputs),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        user_prompt = "\n\n".join(
            (
                "# SkillOpt Optimizer Call",
                f"Stage: {call.stage}\nEpoch: {call.epoch}",
                "# Current Skill",
                call.current_skill,
                "# Ordered Case IDs",
                ordered_ids,
                "# Scheduled Edit Budget",
                f"Scheduled edit budget: {budget}",
                "# Separate Optimizer Memory",
                memory,
                "# Stage-Specific Inputs",
                inputs,
            )
        )
        system_prompt = self._system_prompt(call)
        messages = (
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        )
        return SkillOptPrompt(
            stage=call.stage,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            messages=messages,
        )

    def _load_reflection_prompts(
        self,
        root: Path,
    ) -> dict[str, str]:
        if self._benchmark not in _BENCHMARK_REFLECTION_PROMPTS:
            return {}
        benchmark_root = (
            root / "benchmarks" / self._benchmark
        )
        return {
            "failure": _read(benchmark_root / "analyst_error.md"),
            "success": _read(benchmark_root / "analyst_success.md"),
        }

    def _system_prompt(self, call: OptimizerCall) -> str:
        if (
            call.stage != "optimizer_reflection"
            or not self._reflection_prompts
        ):
            return self._prompts[call.stage]
        metric = call.inputs.get("metric")
        if not isinstance(metric, Mapping):
            raise ValueError(
                "SkillOpt reflection metric summary must be an object"
            )
        metrics = metric.get("metrics")
        if not isinstance(metrics, Mapping):
            raise ValueError(
                "SkillOpt reflection metrics must be an object"
            )
        hard_name = _HARD_METRIC_NAMES[self._benchmark]
        hard_value = metrics.get(
            hard_name,
            metrics.get("hard"),
        )
        if (
            isinstance(hard_value, bool)
            or not isinstance(hard_value, (int, float))
        ):
            raise ValueError(
                "SkillOpt reflection metric is missing its hard score"
            )
        kind = "success" if float(hard_value) > 1e-9 else "failure"
        return self._reflection_prompts[kind]


def parse_skillopt_generation(
    stage: str,
    result: GenerationResult,
) -> OptimizerResult:
    """Validate one text-only SkillOpt generation."""

    if stage not in _STAGES:
        raise ValueError(f"unknown SkillOpt optimizer stage {stage!r}")
    if not isinstance(result, GenerationResult):
        raise ValueError(
            "SkillOpt backend must return GenerationResult"
        )
    if result.tool_calls:
        raise ValueError("SkillOpt optimizer generations cannot use tool calls")
    if not isinstance(result.text, str) or not result.text.strip():
        raise ValueError(
            "SkillOpt optimizer generation text must be non-empty"
        )
    return OptimizerResult(
        text=result.text.strip(),
        usage=result.usage,
    )


def _validate_call(
    call: OptimizerCall,
    required_inputs: frozenset[str],
) -> None:
    if (
        isinstance(call.epoch, bool)
        or not isinstance(call.epoch, int)
        or call.epoch < 1
    ):
        raise ValueError("SkillOpt optimizer epoch must be positive")
    if not isinstance(call.current_skill, str) or not call.current_skill.strip():
        raise ValueError("SkillOpt current skill must be non-empty")
    case_ids = tuple(call.case_ids)
    if not case_ids or any(
        not isinstance(case_id, str) or not case_id.strip()
        for case_id in case_ids
    ):
        raise ValueError("SkillOpt optimizer case IDs must be non-empty")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("SkillOpt optimizer case IDs must be unique")
    if call.edit_budget is not None and (
        isinstance(call.edit_budget, bool)
        or not isinstance(call.edit_budget, int)
        or call.edit_budget < 1
    ):
        raise ValueError(
            "SkillOpt scheduled edit budget must be positive or null"
        )
    if not isinstance(call.memory, str):
        raise ValueError("SkillOpt optimizer memory must be text")
    if not isinstance(call.inputs, Mapping):
        raise ValueError("SkillOpt optimizer inputs must be an object")
    if set(call.inputs) != required_inputs:
        raise ValueError(
            f"SkillOpt {call.stage} inputs must be exactly "
            f"{sorted(required_inputs)!r}"
        )


def _portable(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    "SkillOpt prompt input keys must be strings"
                )
            result[key] = _portable(item)
        return result
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_portable(item) for item in value]
    return value


def _read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(
            f"could not load SkillOpt prompt {path.name}"
        ) from exc
    if not text:
        raise ValueError(f"SkillOpt prompt {path.name} is empty")
    return text


__all__ = [
    "SkillOptPrompt",
    "SkillOptPromptBuilder",
    "parse_skillopt_generation",
]
