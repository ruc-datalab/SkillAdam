"""DeepPlanning-specific Stage0 prompt context."""

from __future__ import annotations

from pathlib import Path

from skilladam.benchmarks.deepplanning.manifest import SLICE_SPECS
from skilladam.core.stage0 import Stage0PromptContext


PROMPTS_DIR = Path(__file__).with_name("prompts")


def build_stage0_context(
    *,
    slice_id: str,
    trajectory_count: int,
    benchmark_domain_info_md: str,
) -> Stage0PromptContext:
    """Build Stage0 context without copying an official system prompt."""

    if slice_id not in SLICE_SPECS:
        raise ValueError(
            f"unsupported DeepPlanning slice {slice_id!r}"
        )
    vendor_boundary = (
        f"DeepPlanning external backend prompt boundary for {slice_id}. "
        "The configured benchmark backend supplies its official system "
        "prompt and tool definitions at execution time."
    )
    return Stage0PromptContext(
        trajectory_count=trajectory_count,
        benchmark_domain_info_md=benchmark_domain_info_md,
        vendor_prompt=vendor_boundary,
        metric_interpretation=_read("metric_interpretation.md"),
        outcome_label_format=_read("trajectory_label_format.md"),
        extra_section=_read("stage0_extra_section.md"),
        example=_read("stage0_example.md"),
    )


def _read(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(
        encoding="utf-8"
    ).strip()
