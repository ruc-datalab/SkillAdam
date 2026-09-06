#!/usr/bin/env python3
"""Plan the complete seven-benchmark, ten-unit API smoke offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from skilladam.benchmarks.deepplanning.runtime import preflight_runtime
from skilladam.benchmarks.registry import create_adapter
from skilladam.artifacts import load_best_skill
from skilladam.experiments import (
    PlannedCommand,
    build_api_training_smoke_commands,
    load_main_result_profile,
    preflight_main_result_matrix,
)


GENERAL_BACKEND = "skilladam.backends.openai_compatible:create_backend"
DEEPPLANNING_BACKEND = (
    "skilladam.backends.deepplanning_official:create_backend"
)
FIXED_TEST_CASES: Mapping[str, str | Mapping[str, str]] = {
    "alfworld": "test:0000",
    "deepplanning": {
        "shopping_level1": "shopping_level1__case_002",
        "shopping_level2": "shopping_level2__case_002",
        "shopping_level3": "shopping_level3__case_002",
        "travel_en": "travel_en__case_000",
    },
    "docvqa": "1014",
    "lmb": "202511:10",
    "officeqa": "UID0003",
    "searchqa": "00090d662c4c4db9b088922fef9fe061",
    "spreadsheetbench": "109-21",
}
SMOKE_VALIDATION_CASES: Mapping[str, str | Mapping[str, str]] = {
    "alfworld": "val:0000",
    "deepplanning": {
        "shopping_level1": "shopping_level1__case_011",
        "shopping_level2": "shopping_level2__case_011",
        "shopping_level3": "shopping_level3__case_005",
        "travel_en": "travel_en__case_007",
    },
    "docvqa": "15382",
    "lmb": "202511:1",
    "officeqa": "UID0001",
    "searchqa": "1758dc50625e46ee814e44a6061f091d",
    "spreadsheetbench": "12864",
}
DEEPPLANNING_SCOPES = (
    "shopping_level1",
    "shopping_level2",
    "shopping_level3",
    "travel_en",
)
CALIBRATION_BENCHMARKS = (
    "alfworld",
    "docvqa",
    "lmb",
    "officeqa",
    "spreadsheetbench",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate data and derive the low-cost 70-command, ten-unit "
            "API availability smoke. This planner never calls an API."
        )
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = _plan(args.config)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        if not args.report.parent.is_dir():
            raise ValueError("report parent directory does not exist")
        try:
            args.report.resolve().relative_to(REPOSITORY_ROOT)
        except ValueError:
            pass
        else:
            raise ValueError("report path must be outside the repository")
        if args.report.exists():
            raise FileExistsError("refusing to overwrite existing report")
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


def _plan(config_path: Path) -> dict[str, Any]:
    preflight = preflight_main_result_matrix(
        config_path,
        require_secrets=False,
        environ={},
    )
    config = _load_config(config_path)
    base = config_path.resolve().parent
    output_root = _path(
        config["output_root"],
        base=base,
        label="output_root",
    )
    raw_benchmarks = _object(config["benchmarks"], "benchmarks")
    commands = _build_commands(
        raw_benchmarks,
        base=base,
        output_root=output_root,
    )
    profile = load_main_result_profile()
    units: list[dict[str, Any]] = []
    for benchmark in profile.benchmarks:
        raw = _object(
            raw_benchmarks[benchmark],
            f"benchmarks.{benchmark}",
        )
        data_root = _path(
            raw["data_root"],
            base=base,
            label=f"{benchmark}.data_root",
        )
        test_ids = {
            case.case_id
            for case in create_adapter(benchmark, data_root).load_cases(
                "test"
            )
        }
        validation_ids = {
            case.case_id
            for case in create_adapter(benchmark, data_root).load_cases(
                "validation"
            )
        }
        scopes = (
            DEEPPLANNING_SCOPES
            if benchmark == "deepplanning"
            else (None,)
        )
        for scope in scopes:
            case_id = _fixed_case_id(benchmark, scope)
            if case_id not in test_ids:
                raise ValueError(
                    f"{benchmark}/{scope or 'all'} fixed test case is absent"
                )
            validation_case_id = _smoke_validation_case_id(
                benchmark,
                scope,
            )
            if validation_case_id not in validation_ids:
                raise ValueError(
                    f"{benchmark}/{scope or 'all'} smoke validation case "
                    "is absent"
                )
            selected_skill_sha256 = hashlib.sha256(
                load_best_skill(
                    benchmark,
                    scope=scope,
                ).encode("utf-8")
            ).hexdigest()
            expected_skill_sha256 = (
                profile.benchmark(benchmark).selected_artifacts[
                    scope or "all"
                ]
            )
            if selected_skill_sha256 != expected_skill_sha256:
                raise ValueError(
                    f"{benchmark}/{scope or 'all'} selected skill "
                    "does not match the frozen profile"
                )
            units.append(
                {
                    "benchmark": benchmark,
                    "scope": scope,
                    "provider": profile.benchmark(benchmark).provider,
                    "model": profile.benchmark(benchmark).model,
                    "case_id": case_id,
                    "skillopt_validation_case_id": validation_case_id,
                    "commands": 7,
                    "selected_skill_sha256": selected_skill_sha256,
                }
            )
    if len(units) != 10 or len(commands) != 70:
        raise ValueError("full API smoke matrix shape is invalid")
    calibration_commands = _calibration_commands(commands)
    deep_raw = _object(
        raw_benchmarks["deepplanning"],
        "benchmarks.deepplanning",
    )
    deep_root = _path(
        deep_raw["data_root"],
        base=base,
        label="deepplanning.data_root",
    )
    deep_preflight = preflight_runtime(deep_root)
    if not deep_preflight.git_revision_verified:
        raise ValueError(
            "full API smoke requires a verified DeepPlanning runtime"
        )
    return {
        "schema_version": 1,
        "profile": "seven-benchmark-low-cost-api-availability-smoke-v2",
        "executes_api": False,
        "execution_status": "plan_only",
        "budget_status": "not_projected",
        "incomplete_smoke": True,
        "paper_profile_modified": False,
        "runtime_credentials_checked": False,
        "output_root": str(output_root),
        "summary": {
            "benchmarks": 7,
            "units": 10,
            "commands": 70,
            "stage0": 10,
            "stage0_cases_per_unit": 1,
            "skilladam_one_iteration_train": 10,
            "skilladam_train_and_validation_cases_per_iteration": 1,
            "skillopt_one_case_batch_train": 10,
            "skillopt_validation_cases_per_candidate": 1,
            "exact_case_evaluations": 40,
            "seed": 42,
        },
        "units": units,
        "commands": [command.to_dict() for command in commands],
        "optional_eval_calibration": {
            "execution_status": "plan_only",
            "budget_status": "not_projected",
            "benchmarks": list(CALIBRATION_BENCHMARKS),
            "units": 5,
            "commands": 10,
            "methods": ["baseline", "public_selected_skilladam"],
            "command_plan": [
                {
                    **command.to_dict(),
                    "budget": {
                        "status": "not_set",
                        "max_requests": None,
                        "max_input_tokens": None,
                        "max_output_tokens": None,
                    },
                }
                for command in calibration_commands
            ],
        },
        "structural_preflight": {
            "profile_id": preflight["profile_id"],
            "dataset_and_prompt_matrix_valid": True,
            "missing_environment_names": preflight[
                "missing_environment_names"
            ],
            "runtime_preflight_deferred": True,
        },
        "deepplanning_runtime": deep_preflight.to_public_dict(),
    }


def _build_commands(
    raw_benchmarks: Mapping[str, Any],
    *,
    base: Path,
    output_root: Path,
) -> tuple[PlannedCommand, ...]:
    profile = load_main_result_profile()
    commands: list[PlannedCommand] = []
    for benchmark in profile.benchmarks:
        raw = _object(
            raw_benchmarks[benchmark],
            f"benchmarks.{benchmark}",
        )
        data_root = _path(
            raw["data_root"],
            base=base,
            label=f"{benchmark}.data_root",
        )
        backend = _text(
            raw["backend"],
            f"{benchmark}.backend",
        )
        backend_config = _path(
            raw["backend_config"],
            base=base,
            label=f"{benchmark}.backend_config",
        )
        scopes = (
            DEEPPLANNING_SCOPES
            if benchmark == "deepplanning"
            else (None,)
        )
        for scope in scopes:
            commands.extend(
                build_api_training_smoke_commands(
                    benchmark=benchmark,
                    scope=scope,
                    exact_case_id=_fixed_case_id(benchmark, scope),
                    data_root=data_root,
                    backend=backend,
                    backend_config=backend_config,
                    output_root=output_root,
                    skillopt_validation_case_id=(
                        _smoke_validation_case_id(benchmark, scope)
                    ),
                )
            )
    labels = [command.label for command in commands]
    if len(labels) != len(set(labels)):
        raise ValueError("full API smoke command labels are duplicated")
    return tuple(commands)


def _calibration_commands(
    commands: Sequence[PlannedCommand],
) -> tuple[PlannedCommand, ...]:
    selected = tuple(
        command
        for command in commands
        if command.benchmark in CALIBRATION_BENCHMARKS
        and command.phase == "evaluate"
        and (
            command.label.endswith(":baseline:evaluate:exact-case")
            or command.label.endswith(
                ":skilladam:evaluate:"
                "public-selected-skill-exact-case"
            )
        )
    )
    if len(selected) != 10:
        raise ValueError(
            "remaining benchmark calibration must contain 10 commands"
        )
    counts = {
        benchmark: sum(
            command.benchmark == benchmark for command in selected
        )
        for benchmark in CALIBRATION_BENCHMARKS
    }
    if any(count != 2 for count in counts.values()):
        raise ValueError(
            "remaining benchmark calibration must contain two commands "
            "per benchmark"
        )
    return selected


def _fixed_case_id(benchmark: str, scope: str | None) -> str:
    value = FIXED_TEST_CASES[benchmark]
    if isinstance(value, Mapping):
        if scope is None:
            raise ValueError("DeepPlanning fixed case requires a scope")
        return value[scope]
    if scope is not None:
        raise ValueError(f"{benchmark} does not support a scope")
    return value


def _smoke_validation_case_id(
    benchmark: str,
    scope: str | None,
) -> str:
    value = SMOKE_VALIDATION_CASES[benchmark]
    if isinstance(value, Mapping):
        if scope is None:
            raise ValueError(
                "DeepPlanning smoke validation case requires a scope"
            )
        return value[scope]
    if scope is not None:
        raise ValueError(f"{benchmark} does not support a scope")
    return value


def _load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    root = _object(payload, "full API smoke config")
    if root.get("schema_version") != 1:
        raise ValueError("full API smoke config schema is invalid")
    if set(root) != {"schema_version", "output_root", "benchmarks"}:
        raise ValueError("full API smoke config keys are invalid")
    return root


def _path(value: Any, *, base: Path, label: str) -> Path:
    text = _text(value, label)
    raw = Path(text)
    return raw if raw.is_absolute() else (base / raw).resolve()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(" ".join(str(exc).splitlines()), file=sys.stderr)
        raise SystemExit(2) from exc
