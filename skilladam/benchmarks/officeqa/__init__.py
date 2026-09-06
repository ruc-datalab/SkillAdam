"""Public OfficeQA adapter and offline document-tool helpers."""

from skilladam.benchmarks.officeqa.adapter import OfficeQAAdapter
from skilladam.benchmarks.officeqa.evaluation import (
    exact_match,
    normalize_answer,
    token_f1,
)
from skilladam.benchmarks.officeqa.manifest import MANIFEST
from skilladam.benchmarks.officeqa.stage0 import (
    PROMPTS_DIR,
    build_stage0_context,
)
from skilladam.benchmarks.officeqa.tool_runtime import (
    build_oracle_parsed_pages_context,
    resolve_candidate_files,
    resolve_docs_roots,
    run_tool,
)

__all__ = [
    "MANIFEST",
    "PROMPTS_DIR",
    "OfficeQAAdapter",
    "build_oracle_parsed_pages_context",
    "build_stage0_context",
    "exact_match",
    "normalize_answer",
    "resolve_candidate_files",
    "resolve_docs_roots",
    "run_tool",
    "token_f1",
]
