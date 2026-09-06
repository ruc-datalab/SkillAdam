"""DocVQA-specific Stage0 prompt context."""

from __future__ import annotations

from pathlib import Path

from skilladam.benchmarks.docvqa.rollout import (
    ROLLOUT_SYSTEM_TEMPLATE,
)
from skilladam.core.stage0 import Stage0PromptContext


PROMPTS_DIR = Path(__file__).with_name("prompts")
OUTCOME_LABEL_FORMAT = (
    "outcome=success/partial/failure | hard={0|1}, "
    "soft={0.00..1.00}"
)


def build_stage0_context(
    *,
    trajectory_count: int,
    benchmark_domain_info_md: str,
) -> Stage0PromptContext:
    """Build shared Stage0 context with DocVQA prompt resources."""

    vendor_prompt = ROLLOUT_SYSTEM_TEMPLATE.replace(
        "{skill_section}",
        "",
    )
    return Stage0PromptContext(
        trajectory_count=trajectory_count,
        benchmark_domain_info_md=benchmark_domain_info_md,
        vendor_prompt=vendor_prompt,
        metric_interpretation=_read("metric_interpretation.md"),
        outcome_label_format=OUTCOME_LABEL_FORMAT,
        extra_section=_read("stage0_extra_section.md"),
        example=_read("stage0_example.md"),
        max_skill_words=None,
        min_when_to_use=1,
    )


def _read(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()
