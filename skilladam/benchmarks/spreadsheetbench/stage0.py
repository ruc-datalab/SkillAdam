"""SpreadsheetBench-specific Stage0 prompt context."""

from __future__ import annotations

from pathlib import Path

from skilladam.benchmarks.spreadsheetbench.rollout import VENDOR_PROMPT
from skilladam.core.stage0 import Stage0PromptContext


PROMPTS_DIR = Path(__file__).with_name("prompts")


def build_stage0_context(
    *,
    trajectory_count: int,
    benchmark_domain_info_md: str,
) -> Stage0PromptContext:
    """Build shared Stage0 input with SpreadsheetBench semantics."""

    return Stage0PromptContext(
        trajectory_count=trajectory_count,
        benchmark_domain_info_md=benchmark_domain_info_md,
        vendor_prompt=VENDOR_PROMPT,
        metric_interpretation=_read("metric_interpretation.md"),
        outcome_label_format=_read("trajectory_label_format.md"),
        extra_section=_read("stage0_extra_section.md"),
        example=_read("stage0_example.md"),
        expected_skill_sections=(
            "Workflow",
            "Code Patterns",
            "Error Avoidance",
        ),
        max_skill_words=None,
    )


def _read(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()
