"""Dependency-only environment checks for public benchmark runtimes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata, util
from pathlib import Path
import platform
import re
import shutil
import sys
from typing import Any


@dataclass(frozen=True, slots=True)
class DependencyRequirement:
    """One declared distribution and its importable top-level module."""

    requirement: str
    distribution: str
    module: str


_OPENAI = DependencyRequirement("openai>=2.49,<3", "openai", "openai")
_DATA = (
    DependencyRequirement(
        "huggingface-hub>=1,<2",
        "huggingface-hub",
        "huggingface_hub",
    ),
    DependencyRequirement("pillow>=12,<13", "pillow", "PIL"),
    DependencyRequirement("pyarrow>=23,<24", "pyarrow", "pyarrow"),
)
_BENCHMARK_DEPENDENCIES: dict[str, tuple[DependencyRequirement, ...]] = {
    "alfworld": (
        _OPENAI,
        DependencyRequirement(
            "alfworld==0.4.2",
            "alfworld",
            "alfworld",
        ),
    ),
    "deepplanning": (
        DependencyRequirement(
            "qwen-agent==0.0.34",
            "qwen-agent",
            "qwen_agent",
        ),
        _OPENAI,
        DependencyRequirement("dashscope>=1.11", "dashscope", "dashscope"),
        DependencyRequirement("pandas>=1.5", "pandas", "pandas"),
        DependencyRequirement("numpy>=1.24", "numpy", "numpy"),
        DependencyRequirement(
            "rank-bm25>=0.2.2",
            "rank-bm25",
            "rank_bm25",
        ),
        DependencyRequirement("requests>=2.28", "requests", "requests"),
        DependencyRequirement(
            "python-dotenv>=1",
            "python-dotenv",
            "dotenv",
        ),
        DependencyRequirement("json5>=0.9", "json5", "json5"),
        DependencyRequirement("jsonlines>=3", "jsonlines", "jsonlines"),
        DependencyRequirement(
            "jsonschema>=4",
            "jsonschema",
            "jsonschema",
        ),
        DependencyRequirement("pydantic>=2.3", "pydantic", "pydantic"),
        DependencyRequirement("tiktoken>=0.5", "tiktoken", "tiktoken"),
        DependencyRequirement(
            "eval-type-backport",
            "eval-type-backport",
            "eval_type_backport",
        ),
        DependencyRequirement("pillow>=9", "pillow", "PIL"),
        DependencyRequirement("tabulate>=0.9", "tabulate", "tabulate"),
        DependencyRequirement(
            "huggingface-hub>=1,<2",
            "huggingface-hub",
            "huggingface_hub",
        ),
    ),
    "docvqa": (_OPENAI, *_DATA),
    "lmb": (_OPENAI, *_DATA),
    "officeqa": (_OPENAI, *_DATA),
    "searchqa": (_OPENAI, *_DATA),
    "spreadsheetbench": (
        _OPENAI,
        *_DATA,
        DependencyRequirement("openpyxl>=3.1", "openpyxl", "openpyxl"),
        DependencyRequirement("pandas", "pandas", "pandas"),
    ),
}


def benchmark_dependency_names() -> tuple[str, ...]:
    """Return benchmark names with an explicit installation profile."""

    return tuple(sorted(_BENCHMARK_DEPENDENCIES))


def benchmark_requirements_file(benchmark: str) -> str:
    """Return the repository-relative requirements file for *benchmark*."""

    _requirements_for(benchmark)
    return f"skilladam/benchmarks/{benchmark}/requirements.txt"


def inspect_benchmark_environment(
    benchmark: str,
    *,
    spreadsheet_execution_mode: str = "local-subprocess",
    container_engine: str = "docker",
    module_finder: Callable[[str], object | None] = util.find_spec,
    version_resolver: Callable[[str], str] = metadata.version,
    executable_finder: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Inspect imports and optional tools without importing or installing.

    This deliberately does not inspect datasets, credentials, provider
    connectivity, fixed Git revisions, or benchmark correctness. Those remain
    the responsibility of the benchmark materializer and runtime preflight.
    """

    requirements = _requirements_for(benchmark)
    if spreadsheet_execution_mode not in {"local-subprocess", "container"}:
        raise ValueError(
            "spreadsheet execution mode must be local-subprocess or container"
        )
    if benchmark != "spreadsheetbench" and (
        spreadsheet_execution_mode != "local-subprocess"
    ):
        raise ValueError(
            "spreadsheet execution mode only applies to spreadsheetbench"
        )
    if container_engine not in {"docker", "podman"}:
        raise ValueError("container engine must be docker or podman")

    dependency_rows = [
        _inspect_dependency(
            requirement,
            module_finder=module_finder,
            version_resolver=version_resolver,
        )
        for requirement in requirements
    ]
    tool_rows: list[dict[str, object]] = []
    if (
        benchmark == "spreadsheetbench"
        and spreadsheet_execution_mode == "container"
    ):
        resolved_engine = executable_finder(container_engine)
        tool_rows.append(
            {
                "name": container_engine,
                "available": bool(resolved_engine),
                "path": str(Path(resolved_engine).resolve())
                if resolved_engine
                else None,
                "required_for": "optional-container-mode",
            }
        )

    python_compatible = sys.version_info >= (3, 10)
    dependencies_ready = (
        python_compatible
        and all(
            bool(row["importable"]) and bool(row["version_satisfied"])
            for row in dependency_rows
        )
        and all(bool(row["available"]) for row in tool_rows)
    )
    return {
        "schema_version": 1,
        "benchmark": benchmark,
        "requirements_file": benchmark_requirements_file(benchmark),
        "python": {
            "executable": str(Path(sys.executable).resolve()),
            "version": platform.python_version(),
            "requires": ">=3.10",
            "compatible": python_compatible,
        },
        "spreadsheet_execution_mode": (
            spreadsheet_execution_mode
            if benchmark == "spreadsheetbench"
            else None
        ),
        "dependencies": dependency_rows,
        "tools": tool_rows,
        "dependencies_ready": dependencies_ready,
        "data_checked": False,
        "credentials_checked": False,
        "provider_connectivity_checked": False,
        "version_note": (
            "The requirements file and pyproject.toml are authoritative. "
            "This command verifies installed distribution versions and import "
            "availability without importing packages."
        ),
    }


def _requirements_for(
    benchmark: str,
) -> tuple[DependencyRequirement, ...]:
    try:
        return _BENCHMARK_DEPENDENCIES[benchmark]
    except KeyError:
        raise ValueError(f"unknown benchmark: {benchmark}") from None


def _inspect_dependency(
    requirement: DependencyRequirement,
    *,
    module_finder: Callable[[str], object | None],
    version_resolver: Callable[[str], str],
) -> dict[str, object]:
    try:
        importable = module_finder(requirement.module) is not None
    except (AttributeError, ImportError, ValueError):
        importable = False
    try:
        version: str | None = version_resolver(requirement.distribution)
    except metadata.PackageNotFoundError:
        version = None
    version_satisfied = _version_satisfies_requirement(
        version,
        requirement,
    )
    return {
        "requirement": requirement.requirement,
        "distribution": requirement.distribution,
        "module": requirement.module,
        "installed_version": version,
        "importable": importable,
        "version_satisfied": version_satisfied,
    }


def _version_satisfies_requirement(
    installed_version: str | None,
    requirement: DependencyRequirement,
) -> bool:
    """Evaluate the deliberately small specifier grammar used by this repo."""

    if installed_version is None:
        return False
    installed = _numeric_version(installed_version)
    if installed is None:
        return False
    specifier = requirement.requirement[len(requirement.distribution) :]
    if not specifier:
        return True
    for clause in specifier.split(","):
        match = re.fullmatch(r"(==|>=|<=|>|<)([0-9]+(?:\.[0-9]+)*)", clause)
        if match is None:
            return False
        expected = _numeric_version(match.group(2))
        if expected is None:
            return False
        left, right = _pad_versions(installed, expected)
        operator = match.group(1)
        satisfied = {
            "==": left == right,
            ">=": left >= right,
            "<=": left <= right,
            ">": left > right,
            "<": left < right,
        }[operator]
        if not satisfied:
            return False
    return True


def _numeric_version(value: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def _pad_versions(
    left: tuple[int, ...],
    right: tuple[int, ...],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    return padded_left, padded_right


__all__ = [
    "DependencyRequirement",
    "benchmark_dependency_names",
    "benchmark_requirements_file",
    "inspect_benchmark_environment",
]
