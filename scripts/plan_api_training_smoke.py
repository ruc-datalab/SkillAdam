#!/usr/bin/env python3
"""Plan the low-cost three-unit API availability smoke offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from skilladam.benchmarks.deepplanning.runtime import preflight_runtime
from skilladam.benchmarks.registry import create_adapter
from skilladam.execution.backend import load_backend_config
from skilladam.experiments import build_api_training_smoke_commands


GENERAL_BACKEND = "skilladam.backends.openai_compatible:create_backend"
DEEPPLANNING_BACKEND = (
    "skilladam.backends.deepplanning_official:create_backend"
)
SEARCH_CASE = "00090d662c4c4db9b088922fef9fe061"
DEEPPLANNING_CASES = {
    "shopping_level1": "shopping_level1__case_002",
    "travel_en": "travel_en__case_000",
}
SEARCH_VALIDATION_CASE = "1758dc50625e46ee814e44a6061f091d"
DEEPPLANNING_VALIDATION_CASES = {
    "shopping_level1": "shopping_level1__case_011",
    "travel_en": "travel_en__case_007",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Derive a low-cost availability smoke from frozen paper "
            "prompts and method settings. This planner never calls an API."
        )
    )
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    report = _plan(_load_config(args.config), args.config)
    print(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        end="",
    )
    return 0


def _plan(
    config: Mapping[str, Any],
    config_path: Path,
) -> dict[str, Any]:
    base = config_path.resolve().parent
    output_root = _path(
        config["output_root"],
        base=base,
        label="output_root",
        must_exist=False,
    )
    _external_empty_output(output_root)
    search = _object(config["searchqa"], "searchqa")
    deep = _object(config["deepplanning"], "deepplanning")
    _exact_keys(search, {"data_root", "backend_config"}, "searchqa")
    _exact_keys(
        deep,
        {"runtime_root", "backend_config"},
        "deepplanning",
    )
    search_root = _path(
        search["data_root"],
        base=base,
        label="SearchQA data root",
    )
    deep_root = _path(
        deep["runtime_root"],
        base=base,
        label="DeepPlanning runtime root",
    )
    search_config = _path(
        search["backend_config"],
        base=base,
        label="SearchQA backend config",
    )
    deep_config = _path(
        deep["backend_config"],
        base=base,
        label="DeepPlanning backend config",
    )
    _validate_backend_contracts(search_config, deep_config)

    search_ids = {
        case.case_id
        for case in create_adapter("searchqa", search_root).load_cases(
            "test"
        )
    }
    if SEARCH_CASE not in search_ids:
        raise ValueError("configured SearchQA smoke case is absent")
    search_validation_ids = {
        case.case_id
        for case in create_adapter("searchqa", search_root).load_cases(
            "validation"
        )
    }
    if SEARCH_VALIDATION_CASE not in search_validation_ids:
        raise ValueError("SearchQA smoke validation case is absent")
    deep_preflight = preflight_runtime(deep_root)
    if not deep_preflight.git_revision_verified:
        raise ValueError(
            "DeepPlanning smoke requires a verified pinned Git checkout"
        )
    deep_ids = {
        case.case_id
        for case in create_adapter("deepplanning", deep_root).load_cases(
            "test"
        )
    }
    if not set(DEEPPLANNING_CASES.values()).issubset(deep_ids):
        raise ValueError("configured DeepPlanning smoke cases are absent")
    deep_validation_ids = {
        case.case_id
        for case in create_adapter("deepplanning", deep_root).load_cases(
            "validation"
        )
    }
    if not set(DEEPPLANNING_VALIDATION_CASES.values()).issubset(
        deep_validation_ids
    ):
        raise ValueError("DeepPlanning smoke validation cases are absent")

    commands = list(
        build_api_training_smoke_commands(
            benchmark="searchqa",
            exact_case_id=SEARCH_CASE,
            data_root=search_root,
            backend=GENERAL_BACKEND,
            backend_config=search_config,
            output_root=output_root,
            skillopt_validation_case_id=SEARCH_VALIDATION_CASE,
        )
    )
    for scope, case_id in DEEPPLANNING_CASES.items():
        commands.extend(
            build_api_training_smoke_commands(
                benchmark="deepplanning",
                scope=scope,
                exact_case_id=case_id,
                data_root=deep_root,
                backend=DEEPPLANNING_BACKEND,
                backend_config=deep_config,
                output_root=output_root,
                skillopt_validation_case_id=(
                    DEEPPLANNING_VALIDATION_CASES[scope]
                ),
            )
        )
    return {
        "schema_version": 1,
        "profile": "low-cost-api-availability-smoke-v2",
        "executes_api": False,
        "execution_status": "plan_only",
        "incomplete_smoke": True,
        "paper_profile_modified": False,
        "units": 3,
        "commands": [command.to_dict() for command in commands],
        "summary": {
            "commands": len(commands),
            "stage0": 3,
            "stage0_cases_per_unit": 1,
            "skilladam_one_iteration_train": 3,
            "skilladam_train_and_validation_cases_per_iteration": 1,
            "skillopt_one_case_batch_train": 3,
            "skillopt_validation_cases_per_candidate": 1,
            "exact_case_evaluations": 12,
            "seed": 42,
        },
        "cases": {
            "evaluation": {
                "searchqa": SEARCH_CASE,
                **DEEPPLANNING_CASES,
            },
            "skillopt_validation": {
                "searchqa": SEARCH_VALIDATION_CASE,
                **DEEPPLANNING_VALIDATION_CASES,
            },
        },
        "deepplanning_runtime": deep_preflight.to_public_dict(),
    }


def _validate_backend_contracts(
    search_config: Path,
    deep_config: Path,
) -> None:
    search = load_backend_config(search_config).values
    deep = load_backend_config(deep_config).values
    expected_search = {
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url_env": "OPENROUTER_BASE_URL",
        "reasoning_mode": "extra_body",
        "temperature": 1,
    }
    expected_deep = {
        "api_key_env": "VENUS_API_KEY",
        "base_url_env": "VENUS_BASE_URL",
        "reasoning_mode": "omit",
        "temperature": 0,
        "conversion_model": "gpt-4.1",
        "require_git_revision": True,
    }
    for label, values, expected in (
        ("SearchQA", search, expected_search),
        ("DeepPlanning", deep, expected_deep),
    ):
        for field, expected_value in expected.items():
            if values.get(field) != expected_value:
                raise ValueError(
                    f"{label} backend {field} must equal "
                    f"{expected_value!r}"
                )


def _load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    root = _object(payload, "training smoke config")
    _exact_keys(
        root,
        {
            "schema_version",
            "output_root",
            "searchqa",
            "deepplanning",
        },
        "training smoke config",
    )
    if root["schema_version"] != 1:
        raise ValueError("training smoke config schema is invalid")
    return root


def _external_empty_output(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(REPOSITORY_ROOT)
    except ValueError:
        pass
    else:
        raise ValueError("smoke output root must be outside the repository")
    if not resolved.parent.is_dir():
        raise ValueError("smoke output parent does not exist")
    if resolved.exists() and (
        not resolved.is_dir() or any(resolved.iterdir())
    ):
        raise ValueError("smoke output root must be absent or empty")


def _path(
    value: Any,
    *,
    base: Path,
    label: str,
    must_exist: bool = True,
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    raw = Path(value)
    path = raw if raw.is_absolute() else (base / raw).resolve()
    if must_exist and not path.exists():
        raise ValueError(f"{label} does not exist")
    return path


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} keys are invalid")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(" ".join(str(exc).splitlines()), file=sys.stderr)
        raise SystemExit(2) from exc
