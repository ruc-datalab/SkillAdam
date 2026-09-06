"""Packaged Stage0 and iteration prompts for the SkillAdam method."""

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
import json
from pathlib import Path
from typing import Any

from skilladam.benchmarks.registry import list_benchmarks
from skilladam.core.edit_budget import build_budget_section
from skilladam.core.stage0 import Stage0PromptContext
from skilladam.core.type_taxonomy import (
    TaxonomySpec,
    load_taxonomy,
    render_taxonomy_section,
)


CORE_PROMPTS_DIR = Path(__file__).parents[1] / "core" / "prompts"
ITERATION_KEYS = {"reasoning", "patch"}


@dataclass(frozen=True)
class BenchmarkPromptContext:
    """All packaged prompt material for one benchmark and optional scope."""

    benchmark: str
    scope: str | None
    stage0: Stage0PromptContext
    skill_writing_policy: str
    taxonomy: TaxonomySpec
    taxonomy_section: str
    iteration_example: str
    prompt_profile: str
    iteration_template_path: Path
    stage0_system_prompt_path: Path | None
    trajectory_compress_system_prompt: str | None


@dataclass(frozen=True)
class CondensedTrajectory:
    """One ordered training trajectory ready for the optimizer prompt."""

    case_id: str
    markdown: str

    def __post_init__(self) -> None:
        _non_empty(self.case_id, "trajectory case_id")
        _non_empty(self.markdown, "trajectory markdown")


@dataclass(frozen=True)
class IterationPromptContext:
    """Complete input for one SkillAdam patch-generation attempt."""

    benchmark: BenchmarkPromptContext
    current_skill: str
    trajectories: tuple[CondensedTrajectory, ...]
    momentum_memory: str
    previous_patch_error: str = ""
    edit_budget: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.benchmark, BenchmarkPromptContext):
            raise ValueError("benchmark prompt context is invalid")
        _non_empty(self.current_skill, "current_skill")
        trajectories = tuple(self.trajectories)
        if not trajectories or any(
            not isinstance(item, CondensedTrajectory)
            for item in trajectories
        ):
            raise ValueError(
                "iteration prompt requires condensed trajectories"
            )
        if len({item.case_id for item in trajectories}) != len(
            trajectories
        ):
            raise ValueError(
                "iteration trajectories must have unique case IDs"
            )
        object.__setattr__(self, "trajectories", trajectories)
        if not isinstance(self.momentum_memory, str):
            raise ValueError("momentum_memory must be text")
        if not isinstance(self.previous_patch_error, str):
            raise ValueError("previous_patch_error must be text")
        if self.edit_budget is not None and (
            isinstance(self.edit_budget, bool)
            or not isinstance(self.edit_budget, int)
            or self.edit_budget < 1
        ):
            raise ValueError("edit_budget must be a positive integer or null")


@dataclass(frozen=True)
class IterationPrompt:
    system_prompt: str
    user_prompt: str
    messages: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class IterationGeneration:
    """Strict JSON output from one SkillAdam iteration generation."""

    reasoning: str
    patch: str

    @classmethod
    def from_raw_text(cls, raw_text: str) -> "IterationGeneration":
        if not isinstance(raw_text, str):
            raise ValueError("iteration generation must be text")
        text = _strip_code_fence(raw_text)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"iteration generation is not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                "iteration generation must contain one JSON object"
            )
        if set(payload) != ITERATION_KEYS:
            raise ValueError(
                "iteration generation must use exactly the keys "
                "reasoning and patch"
            )
        reasoning = payload["reasoning"]
        patch = payload["patch"]
        if not isinstance(reasoning, str) or not isinstance(patch, str):
            raise ValueError(
                "iteration generation reasoning and patch must be strings"
            )
        if not reasoning.strip():
            raise ValueError(
                "iteration generation reasoning must be non-empty"
            )
        return cls(reasoning=reasoning.strip(), patch=patch)

    def to_dict(self) -> dict[str, str]:
        return {"reasoning": self.reasoning, "patch": self.patch}


class IterationPromptBuilder:
    """Render the frozen core prompt plus benchmark-specific resources."""

    def __init__(self, template_path: Path | None = None) -> None:
        self._template = (
            template_path
            or CORE_PROMPTS_DIR / "iteration_system_prompt.md"
        ).read_text(encoding="utf-8")

    def build(self, context: IterationPromptContext) -> IterationPrompt:
        benchmark = context.benchmark
        replacements = {
            "{vendor_prompt}": benchmark.stage0.vendor_prompt.strip(),
            "{metric_interpretation}": (
                benchmark.stage0.metric_interpretation.strip()
            ),
            "{skill_writing_policy}": (
                benchmark.skill_writing_policy.strip()
            ),
            "{type_taxonomy_section}": (
                benchmark.taxonomy_section.strip()
            ),
            "{diagnosis_output_section}": "",
            "{iteration_example}": benchmark.iteration_example.strip(),
        }
        system_prompt = self._template
        for placeholder, value in replacements.items():
            system_prompt = system_prompt.replace(placeholder, value)
        if any(placeholder in system_prompt for placeholder in replacements):
            raise ValueError("iteration prompt has unresolved placeholders")

        memory = (
            context.momentum_memory.strip()
            or "No problems tracked yet."
        )
        previous_error = (
            context.previous_patch_error.strip()
            or "(none)"
        )
        trajectory_sections = []
        for index, trajectory in enumerate(context.trajectories, start=1):
            trajectory_sections.append(
                f"## Trajectory {index}: {trajectory.case_id}\n\n"
                f"{trajectory.markdown.strip()}"
            )
        user_prompt = "\n\n".join(
            (
                "# Current Skill",
                context.current_skill.strip(),
                "# Ordered Condensed Training Trajectories",
                "\n\n".join(trajectory_sections),
                "# Optimization Memory",
                memory,
                "# Previous Patch Error",
                previous_error,
            )
        )
        user_prompt += build_budget_section(context.edit_budget)
        messages = (
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        )
        return IterationPrompt(
            system_prompt=messages[0]["content"],
            user_prompt=messages[1]["content"],
            messages=messages,
        )


def load_benchmark_prompt_context(
    benchmark: str,
    *,
    trajectory_count: int,
    benchmark_domain_info_md: str,
    scope: str | None = None,
    prompt_profile: str = "v2",
) -> BenchmarkPromptContext:
    """Load one benchmark's Stage0 function and packaged prompt resources."""

    if benchmark not in list_benchmarks():
        raise ValueError(f"unknown benchmark {benchmark!r}")
    if benchmark == "deepplanning" and scope is None:
        raise ValueError(
            "DeepPlanning prompt context requires an explicit scope"
        )
    if benchmark != "deepplanning" and scope is not None:
        raise ValueError("scope is only supported for DeepPlanning")

    module = import_module(f"skilladam.benchmarks.{benchmark}.stage0")
    build_context = getattr(module, "build_stage0_context")
    prompts_dir = Path(module.__file__).with_name("prompts")
    kwargs: dict[str, Any] = {
        "trajectory_count": trajectory_count,
        "benchmark_domain_info_md": benchmark_domain_info_md,
    }
    if benchmark == "deepplanning":
        kwargs["slice_id"] = scope
    stage0 = build_context(**kwargs)
    if not isinstance(stage0, Stage0PromptContext):
        raise ValueError(
            "benchmark Stage0 builder returned an invalid context"
        )
    taxonomy = load_taxonomy(prompts_dir)
    if benchmark == "deepplanning":
        assert scope is not None
        stage0 = replace(
            stage0,
            metric_interpretation=_read(
                prompts_dir / f"metric_interpretation_{scope}.md"
            ),
            outcome_label_format=_read(
                prompts_dir / f"trajectory_label_format_{scope}.md"
            ),
        )
    normalized_profile = prompt_profile
    if prompt_profile in {
        f"{benchmark}-main-result",
        f"{benchmark}-private-main-result",  # Compatibility with saved runs.
    }:
        normalized_profile = "v2"
    if (
        benchmark == "deepplanning"
        and prompt_profile in {
            "v2-r2-baseline-with-scope-prompts",
            "v2-r2-baseline-with-private-scope-prompts",  # Saved-run alias.
        }
    ):
        normalized_profile = "v2_r2_baseline"
    if benchmark == "deepplanning" and normalized_profile == "v2_r2_baseline":
        iteration_template_path = (
            prompts_dir / "iteration_system_prompt_v2_r2_baseline.md"
        )
        skill_policy_path = (
            prompts_dir / "skill_writing_policy_v2_r2_baseline.md"
        )
        iteration_example_path = prompts_dir / "iteration_example_v2.md"
    elif normalized_profile == "v2":
        iteration_template_path = (
            CORE_PROMPTS_DIR / "iteration_system_prompt.md"
        )
        skill_policy_path = prompts_dir / "skill_writing_policy.md"
        iteration_example_path = prompts_dir / "iteration_example.md"
    else:
        raise ValueError(
            f"unsupported prompt profile {prompt_profile!r} "
            f"for {benchmark!r}"
        )
    stage0_system_path = prompts_dir / "stage0_system_prompt.md"
    if not stage0_system_path.is_file():
        stage0_system_path = None
    compressor = _load_compressor(
        benchmark,
        prompts_dir,
        scope=scope,
    )
    return BenchmarkPromptContext(
        benchmark=benchmark,
        scope=scope,
        stage0=stage0,
        skill_writing_policy=_read_with_fallback(
            skill_policy_path,
            CORE_PROMPTS_DIR / "skill_writing_policy.md",
        ),
        taxonomy=taxonomy,
        taxonomy_section=render_taxonomy_section(taxonomy),
        iteration_example=_read(iteration_example_path),
        prompt_profile=prompt_profile,
        iteration_template_path=iteration_template_path,
        stage0_system_prompt_path=stage0_system_path,
        trajectory_compress_system_prompt=compressor,
    )


def _load_compressor(
    benchmark: str,
    prompts_dir: Path,
    *,
    scope: str | None,
) -> str | None:
    if benchmark in {"alfworld", "spreadsheetbench", "officeqa"}:
        system_path = prompts_dir / "trajectory_compress_system.md"
        example_path = prompts_dir / "trajectory_compress_example.md"
        word_min, word_max = 150, 250
    elif benchmark == "deepplanning":
        assert scope is not None
        domain = "travel" if scope == "travel_en" else "shopping"
        system_path = CORE_PROMPTS_DIR / "trajectory_compress_system.md"
        example_path = (
            prompts_dir / f"trajectory_compress_example_{domain}.md"
        )
        word_min, word_max = (
            (150, 350) if domain == "travel" else (150, 250)
        )
    else:
        return None
    system = _read(system_path)
    return (
        system.replace("{example}", _read(example_path))
        .replace("{word_min}", str(word_min))
        .replace("{word_max}", str(word_max))
    )


def _read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"could not load packaged prompt {path.name}") from exc
    return _non_empty(text, f"prompt {path.name}")


def _read_with_fallback(path: Path, fallback: Path) -> str:
    if path.is_file():
        return _read(path)
    return _read(fallback)


def _non_empty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()


def _strip_code_fence(value: str) -> str:
    lines = value.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


__all__ = [
    "BenchmarkPromptContext",
    "CondensedTrajectory",
    "IterationGeneration",
    "IterationPrompt",
    "IterationPromptBuilder",
    "IterationPromptContext",
    "load_benchmark_prompt_context",
]
