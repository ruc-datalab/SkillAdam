"""Provider-neutral DeepPlanning rollout bootstrap metadata."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable

from skilladam.benchmarks.base import load_optional_dependency
from skilladam.benchmarks.deepplanning.manifest import SLICE_SPECS
from skilladam.types import BenchmarkCase, Method


_BACKEND_MODULE = "qwen_agent"
_INSTALL_HINT = (
    "install a pinned Qwen-Agent checkout and configure a "
    "DeepPlanningBackend"
)


def load_external_backend(
    *,
    importer: Callable[[str], Any] = import_module,
) -> Any:
    """Load the optional official execution dependency on demand."""

    return load_optional_dependency(
        "deepplanning",
        _BACKEND_MODULE,
        _INSTALL_HINT,
        importer=importer,
    )


def build_bootstrap_metadata(
    case: BenchmarkCase,
    *,
    method: Method,
) -> dict[str, Any]:
    """Describe one external task without copying benchmark prompts/data."""

    payload = case.payload
    slice_id = payload.get("slice", case.metadata.get("slice"))
    domain = payload.get("domain", case.metadata.get("domain"))
    case_number = payload.get("case_number")
    if not isinstance(slice_id, str) or not slice_id:
        raise ValueError("DeepPlanning case is missing a slice")
    if slice_id not in SLICE_SPECS:
        raise ValueError("DeepPlanning case has invalid slice")
    spec = SLICE_SPECS[slice_id]
    level = payload.get("level", spec["level"])
    language = payload.get("language", spec["language"])
    if domain != spec["domain"]:
        raise ValueError("DeepPlanning case has invalid domain")
    if level != spec["level"]:
        raise ValueError("DeepPlanning case has invalid level")
    if language != spec["language"]:
        raise ValueError("DeepPlanning case has invalid language")
    for field_name, value in (
        ("slice", slice_id),
        ("domain", domain),
        ("level", level),
        ("language", language),
    ):
        if (
            field_name in case.metadata
            and case.metadata[field_name] != value
        ):
            raise ValueError(
                "DeepPlanning case metadata has inconsistent "
                f"{field_name}"
            )
    first_case_id = int(spec["first_case_id"])
    past_last_case_id = first_case_id + int(spec["case_count"])
    if case_number is not None and (
        isinstance(case_number, bool)
        or not isinstance(case_number, int)
        or not first_case_id <= case_number < past_last_case_id
    ):
        raise ValueError(
            "DeepPlanning case_number is outside the slice's valid range"
        )
    if case_number is not None:
        expected_case_id = f"{slice_id}__case_{case_number:03d}"
        if case.case_id != expected_case_id:
            raise ValueError(
                "DeepPlanning case_id does not match its task identity"
            )

    task_identity = {
        "case_id": case.case_id,
        "slice": slice_id,
        "domain": domain,
        "level": level,
        "language": language,
    }
    return {
        "messages": (),
        "requires_external_deepplanning_backend": True,
        "task_identity": task_identity,
        "slice": slice_id,
        "domain": domain,
        "case_number": case_number,
        "level": level,
        "language": language,
        "max_interactions": 30 if domain == "shopping" else 60,
        "prompt_profile": f"deepplanning-external-system-{method}",
        "evaluator_profile": f"deepplanning-{domain}-official",
    }
