"""Lazy public method bridges that preserve method-state isolation."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "BenchmarkPromptContext": (
        "skilladam.methods.skilladam_prompts",
        "BenchmarkPromptContext",
    ),
    "CondensedTrajectory": (
        "skilladam.methods.skilladam_prompts",
        "CondensedTrajectory",
    ),
    "IterationGeneration": (
        "skilladam.methods.skilladam_prompts",
        "IterationGeneration",
    ),
    "IterationPrompt": (
        "skilladam.methods.skilladam_prompts",
        "IterationPrompt",
    ),
    "IterationPromptBuilder": (
        "skilladam.methods.skilladam_prompts",
        "IterationPromptBuilder",
    ),
    "IterationPromptContext": (
        "skilladam.methods.skilladam_prompts",
        "IterationPromptContext",
    ),
    "SkillAdamRunConfig": (
        "skilladam.methods.skilladam",
        "SkillAdamRunConfig",
    ),
    "SkillAdamRunResult": (
        "skilladam.methods.skilladam",
        "SkillAdamRunResult",
    ),
    "SkillAdamRunner": (
        "skilladam.methods.skilladam",
        "SkillAdamRunner",
    ),
    "SkillOptExecutionBridge": (
        "skilladam.methods.skillopt",
        "SkillOptExecutionBridge",
    ),
    "SkillOptPrompt": (
        "skilladam.methods.skillopt_prompts",
        "SkillOptPrompt",
    ),
    "SkillOptPromptBuilder": (
        "skilladam.methods.skillopt_prompts",
        "SkillOptPromptBuilder",
    ),
    "load_benchmark_prompt_context": (
        "skilladam.methods.skilladam_prompts",
        "load_benchmark_prompt_context",
    ),
    "parse_skillopt_generation": (
        "skilladam.methods.skillopt_prompts",
        "parse_skillopt_generation",
    ),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
