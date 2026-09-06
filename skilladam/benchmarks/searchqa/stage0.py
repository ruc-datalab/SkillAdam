"""SearchQA-specific Stage0 prompt context."""

from __future__ import annotations

from pathlib import Path

from skilladam.core.stage0 import Stage0PromptContext


PROMPTS_DIR = Path(__file__).with_name("prompts")

VENDOR_PROMPT = (
    "You are a question-answering agent. You will receive a search context "
    "and a question. Read the context carefully, identify the answer, and "
    "output it inside <answer>...</answer> tags. Be concise — output only "
    "the answer text, no explanation."
)


def build_stage0_context(
    *,
    trajectory_count: int,
    benchmark_domain_info_md: str,
) -> Stage0PromptContext:
    """Build the shared Stage0 context with SearchQA prompt resources."""

    return Stage0PromptContext(
        trajectory_count=trajectory_count,
        benchmark_domain_info_md=benchmark_domain_info_md,
        vendor_prompt=VENDOR_PROMPT,
        metric_interpretation=_read("metric_interpretation.md"),
        outcome_label_format=_read("trajectory_label_format.md"),
        extra_section=_read("stage0_extra_section.md"),
        example=_read("stage0_example.md"),
    )


def _read(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()
