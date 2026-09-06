"""Read-only preflight for the complete paper API command matrix."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
from typing import Any

from skilladam.benchmarks.registry import create_adapter
from skilladam.execution.backend import load_backend_config
from skilladam.experiments.main_results import load_main_result_profile
from skilladam.experiments.planning import build_main_result_commands
from skilladam.experiments.prompt_resources import verify_prompt_resources


GENERAL_BACKEND = "skilladam.backends.openai_compatible:create_backend"
DEEPPLANNING_BACKEND = (
    "skilladam.backends.deepplanning_official:create_backend"
)


def preflight_main_result_matrix(
    config_path: Path,
    *,
    require_secrets: bool = True,
    environ: Mapping[str, str] | None = None,
    adapter_factory: Callable[[str, Path], Any] = create_adapter,
    spreadsheet_sandbox_preflight: (
        Callable[[str, str, str, float, Path], None] | None
    ) = None,
    spreadsheet_local_preflight: (
        Callable[[float, Path], None] | None
    ) = None,
    officeqa_corpus_preflight: (
        Callable[
            [Path, Mapping[str, tuple[Any, ...]]],
            Mapping[str, int],
        ]
        | None
    ) = None,
) -> dict[str, Any]:
    """Validate data, configs, credentials, and all 60 planned commands."""

    path = Path(config_path)
    root = _load_object(path)
    if root.get("schema_version") != 1:
        raise ValueError("unsupported API preflight config schema")
    _exact_keys(root, {"schema_version", "output_root", "benchmarks"}, "root")
    base = path.resolve().parent
    output_root = _resolve_path(root.get("output_root"), base, "output_root")
    _validate_new_output_root(output_root)
    raw_benchmarks = _object(root.get("benchmarks"), "benchmarks")
    profile = load_main_result_profile()
    prompt_resources = verify_prompt_resources()
    if set(raw_benchmarks) != set(profile.benchmarks):
        raise ValueError("API preflight config must cover all seven benchmarks")

    environment = os.environ if environ is None else environ
    rows: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    missing_environment: set[str] = set()
    infrastructure_blockers: list[dict[str, str]] = []
    for benchmark in profile.benchmarks:
        raw = _object(raw_benchmarks[benchmark], f"benchmarks.{benchmark}")
        _exact_keys(
            raw,
            {"data_root", "backend", "backend_config"},
            f"benchmarks.{benchmark}",
        )
        data_root = _resolve_path(
            raw.get("data_root"),
            base,
            f"{benchmark}.data_root",
        )
        if not data_root.exists():
            raise ValueError(f"{benchmark} data_root does not exist")
        backend = _text(raw.get("backend"), f"{benchmark}.backend")
        expected_backend = (
            DEEPPLANNING_BACKEND
            if benchmark == "deepplanning"
            else GENERAL_BACKEND
        )
        if backend != expected_backend:
            raise ValueError(
                f"{benchmark} must use the audited first-party backend"
            )
        backend_config_path = _resolve_path(
            raw.get("backend_config"),
            base,
            f"{benchmark}.backend_config",
        )
        if not backend_config_path.is_file():
            raise ValueError(f"{benchmark} backend_config is not a file")
        backend_config = load_backend_config(backend_config_path)
        env_names = _validate_backend_config(
            benchmark,
            backend_config.values,
            profile.benchmark(benchmark),
        )
        missing = sorted(
            name
            for name in env_names
            if not environment.get(name, "").strip()
        )
        missing_environment.update(missing)
        if benchmark == "alfworld" and not missing:
            data_env = str(backend_config.values["alfworld_data_env"])
            if not Path(environment[data_env]).is_dir():
                raise ValueError(
                    "ALFWorld data environment must name a directory"
                )
        if benchmark == "spreadsheetbench" and not missing:
            from skilladam.benchmarks.spreadsheetbench.runtime import (
                SpreadsheetSandboxRuntimeError,
                validate_image_reference,
            )

            try:
                mode = str(
                    backend_config.values["spreadsheet_execution_mode"]
                )
                if mode == "container":
                    engine = str(
                        backend_config.values["spreadsheet_sandbox_engine"]
                    )
                    image_env = str(
                        backend_config.values[
                            "spreadsheet_sandbox_image_env"
                        ]
                    )
                    validate_image_reference(environment[image_env])
                    sandbox_preflight = (
                        spreadsheet_sandbox_preflight
                        or _preflight_spreadsheet_sandbox
                    )
                    sandbox_preflight(
                        engine,
                        environment[image_env],
                        str(
                            backend_config.values[
                                "spreadsheet_sandbox_security_profile"
                            ]
                        ),
                        float(
                            backend_config.values[
                                "spreadsheet_exec_timeout_seconds"
                            ]
                        ),
                        output_root / ".spreadsheet-sandbox-preflight",
                    )
                else:
                    local_preflight = (
                        spreadsheet_local_preflight
                        or _preflight_spreadsheet_local
                    )
                    local_preflight(
                        float(
                            backend_config.values[
                                "spreadsheet_exec_timeout_seconds"
                            ]
                        ),
                        output_root / ".spreadsheet-local-preflight",
                    )
            except SpreadsheetSandboxRuntimeError as exc:
                infrastructure_blockers.append(
                    {
                        "benchmark": benchmark,
                        "type": (
                            "spreadsheet_sandbox"
                            if mode == "container"
                            else "spreadsheet_local_subprocess"
                        ),
                        "reason": str(exc),
                    }
                )
        if benchmark == "deepplanning" and not missing:
            runtime_env = str(
                backend_config.values["runtime_root_env"]
            )
            runtime_path = Path(environment[runtime_env]).resolve()
            if runtime_path != data_root.resolve():
                raise ValueError(
                    "DeepPlanning runtime env does not match data_root"
                )
            bridge_env = str(
                backend_config.values["bridge_python_env"]
            )
            bridge = Path(environment[bridge_env])
            if not bridge.is_file() or not os.access(bridge, os.X_OK):
                raise ValueError(
                    "DeepPlanning bridge Python path is not executable"
                )

        adapter = adapter_factory(benchmark, data_root)
        adapter.validate_dependencies()
        split_cases = {
            split: tuple(adapter.load_cases(split))
            for split in ("train", "validation", "test")
        }
        counts = _validate_dataset(
            benchmark,
            split_cases,
            profile.benchmark(benchmark),
        )
        data_dependencies: dict[str, Any] = {}
        if benchmark == "officeqa":
            corpus_preflight = (
                officeqa_corpus_preflight
                or _preflight_officeqa_corpus
            )
            data_dependencies["treasury_corpus"] = dict(
                corpus_preflight(data_root, split_cases)
            )
        planned = build_main_result_commands(
            benchmark=benchmark,
            method="all",
            phase="all",
            data_root=data_root,
            backend=backend,
            backend_config=backend_config_path,
            output_root=output_root,
        )
        commands.extend(command.to_dict() for command in planned)
        rows.append(
            {
                "benchmark": benchmark,
                "provider": profile.benchmark(benchmark).provider,
                "model": profile.benchmark(benchmark).model,
                "backend": backend,
                "backend_config_sha256": backend_config.sha256,
                "dataset_counts": counts,
                "data_dependencies": data_dependencies,
                "environment": {
                    name: "present" if name not in missing else "missing"
                    for name in env_names
                },
                "ready_for_api": (
                    not missing
                    and not any(
                        blocker["benchmark"] == benchmark
                        for blocker in infrastructure_blockers
                    )
                ),
                "planned_commands": len(planned),
            }
        )

    if require_secrets and missing_environment:
        raise ValueError(
            "required environment variables are missing: "
            + ", ".join(sorted(missing_environment))
        )
    if len(commands) != 60:
        raise ValueError(
            f"complete main-result matrix must contain 60 commands, "
            f"found {len(commands)}"
        )
    return {
        "schema_version": 1,
        "profile_id": profile.profile_id,
        "executes_api": False,
        "ready_for_api": (
            not missing_environment and not infrastructure_blockers
        ),
        "missing_environment_names": sorted(missing_environment),
        "infrastructure_blockers": infrastructure_blockers,
        "matrix_summary": {
            "benchmarks": 7,
            "scoped_experiments": 10,
            "commands": len(commands),
            "stage0": 10,
            "skilladam_train": 10,
            "skillopt_train": 10,
            "baseline_test": 10,
            "skilladam_test": 10,
            "skillopt_test": 10,
            "seed": 42,
        },
        "prompt_resources": prompt_resources,
        "benchmarks": rows,
        "commands": commands,
    }


def _preflight_spreadsheet_sandbox(
    engine: str,
    image_reference: str,
    security_profile: str,
    timeout_seconds: float,
    artifact_root: Path,
) -> None:
    from skilladam.benchmarks.spreadsheetbench.runtime import (
        OCISandboxExecutor,
    )

    OCISandboxExecutor(
        artifact_root=artifact_root,
        engine=engine,
        image_reference=image_reference,
        security_profile=security_profile,
        timeout_seconds=timeout_seconds,
    ).preflight()


def _preflight_spreadsheet_local(
    timeout_seconds: float,
    artifact_root: Path,
) -> None:
    from skilladam.benchmarks.spreadsheetbench.runtime import (
        LocalSubprocessExecutor,
    )

    LocalSubprocessExecutor(
        artifact_root=artifact_root,
        timeout_seconds=timeout_seconds,
    ).preflight()


def _preflight_officeqa_corpus(
    data_root: Path,
    split_cases: Mapping[str, tuple[Any, ...]],
) -> dict[str, int]:
    corpus = (
        data_root
        / "raw"
        / "treasury_bulletins_parsed"
    )
    transformed = corpus / "transformed"
    parsed = corpus / "jsons"
    for label, directory in (
        ("transformed", transformed),
        ("parsed JSON", parsed),
    ):
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(
                f"OfficeQA {label} corpus directory is missing or unsafe"
            )

    case_count = 0
    names: set[str] = set()
    for split in ("train", "validation", "test"):
        for case in split_cases[split]:
            case_count += 1
            payload = getattr(case, "payload", None)
            if not isinstance(payload, Mapping):
                raise ValueError(
                    f"OfficeQA case {case.case_id!r} has no payload"
                )
            source_files = payload.get("source_files")
            if (
                not isinstance(source_files, (list, tuple))
                or not source_files
            ):
                raise ValueError(
                    f"OfficeQA case {case.case_id!r} has no source_files"
                )
            for value in source_files:
                if not isinstance(value, str):
                    raise ValueError(
                        f"OfficeQA case {case.case_id!r} has an invalid "
                        "source filename"
                    )
                name = Path(value)
                if (
                    name.name != value
                    or name.suffix.lower() != ".txt"
                    or value in {"", ".", ".."}
                ):
                    raise ValueError(
                        f"OfficeQA source filename is unsafe: {value!r}"
                    )
                names.add(value)

    if case_count != 246 or len(names) != 285:
        raise ValueError(
            "OfficeQA corpus identity must cover 246 cases and 285 "
            "referenced documents"
        )
    for name in sorted(names):
        text_path = transformed / name
        json_path = parsed / f"{Path(name).stem}.json"
        if (
            not text_path.is_file()
            or text_path.is_symlink()
            or text_path.stat().st_size == 0
        ):
            raise ValueError(
                f"OfficeQA referenced document is missing or unsafe: {name}"
            )
        if not json_path.is_file() or json_path.is_symlink():
            raise ValueError(
                f"OfficeQA parsed document is missing or unsafe: {name}"
            )
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"OfficeQA parsed document is unreadable: {name}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"OfficeQA parsed document is invalid: {name}"
            )
    return {
        "cases": case_count,
        "referenced_documents": len(names),
        "text_files": len(names),
        "parsed_json_files": len(names),
    }


def _validate_backend_config(
    benchmark: str,
    values: Mapping[str, Any],
    profile: Any,
) -> tuple[str, ...]:
    expected = {
        "max_completion_tokens": 16384,
        "token_limit_field": (
            "max_tokens"
            if benchmark == "deepplanning"
            else "max_completion_tokens"
        ),
        "temperature": 0 if benchmark == "deepplanning" else 1,
        "reasoning_mode": (
            "omit" if benchmark == "deepplanning" else "extra_body"
        ),
        "timeout_seconds": int(
            profile.stage0["request_timeout_seconds"]
        ),
        "max_retries": 2,
    }
    for key, value in expected.items():
        if values.get(key) != value:
            raise ValueError(
                f"{benchmark} backend config {key} must equal {value!r}"
            )
    names = [
        _environment_name(values.get("api_key_env"), "api_key_env"),
        _environment_name(values.get("base_url_env"), "base_url_env"),
    ]
    expected_environment = (
        ("VENUS_API_KEY", "VENUS_BASE_URL")
        if profile.provider == "venus"
        else ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL")
    )
    if tuple(names) != expected_environment:
        raise ValueError(
            f"{benchmark} backend provider environment must equal "
            f"{expected_environment!r}"
        )
    if benchmark == "deepplanning":
        deep_expected = {
            "enable_prompt_cache": True,
            "prompt_cache_min_tokens": 1024,
            "conversion_model": "gpt-4.1",
            "conversion_max_tokens": 10240,
            "conversion_token_limit_field": "max_tokens",
            "conversion_temperature": 0,
            "conversion_max_attempts": 30,
        }
        for key, value in deep_expected.items():
            if values.get(key) != value:
                raise ValueError(
                    f"deepplanning backend config {key} "
                    f"must equal {value!r}"
                )
        prefix = values.get("cache_session_prefix")
        expected_prefix = profile.skilladam.get(
            "cache_session_prefix"
        )
        if prefix != expected_prefix:
            raise ValueError(
                "deepplanning cache_session_prefix must match the "
                "frozen main-result profile"
            )
        names.extend(
            (
                _environment_name(
                    values.get("runtime_root_env"),
                    "runtime_root_env",
                ),
                _environment_name(
                    values.get("bridge_python_env"),
                    "bridge_python_env",
                ),
            )
        )
    elif benchmark == "alfworld":
        names.append(
            _environment_name(
                values.get("alfworld_data_env"),
                "alfworld_data_env",
            )
        )
    elif benchmark == "spreadsheetbench":
        spreadsheet_expected = {
            "spreadsheet_execution_mode": "local-subprocess",
            "spreadsheet_exec_timeout_seconds": 600,
            "spreadsheet_task_timeout_seconds": 600,
            "spreadsheet_max_turns": 30,
        }
        for key, value in spreadsheet_expected.items():
            if values.get(key) != value:
                raise ValueError(
                    f"spreadsheetbench backend config {key} "
                    f"must equal {value!r}"
                )
        if values.get("spreadsheet_execution_mode") == "container":
            names.append(
                _environment_name(
                    values.get("spreadsheet_sandbox_image_env"),
                    "spreadsheet_sandbox_image_env",
                )
            )
    return tuple(names)


def _validate_dataset(
    benchmark: str,
    split_cases: Mapping[str, tuple[Any, ...]],
    profile,
) -> dict[str, Any]:
    all_ids = [
        str(case.case_id)
        for cases in split_cases.values()
        for case in cases
    ]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError(f"{benchmark} dataset splits overlap")
    if benchmark != "deepplanning":
        expected = {
            "train": int(profile.dataset["train_cases"]),
            "validation": int(profile.dataset["selection_cases"]),
            "test": int(profile.dataset["test_cases"]),
        }
        actual = {
            split: len(split_cases[split])
            for split in ("train", "validation", "test")
        }
        if actual != expected:
            raise ValueError(
                f"{benchmark} dataset counts {actual!r} != {expected!r}"
            )
        stage0_ids = set(str(item) for item in profile.stage0["case_ids"])
        optimization_ids = {
            str(case.case_id)
            for split in ("train", "validation")
            for case in split_cases[split]
        }
        if not stage0_ids.issubset(optimization_ids):
            raise ValueError(f"{benchmark} Stage0 IDs are absent from data")
        return {
            **actual,
            "skilladam_optimization": actual["train"]
            + actual["validation"],
        }

    counts: dict[str, dict[str, int]] = {}
    stage0_by_scope = profile.stage0["case_ids_by_scope"]
    for scope in profile.dataset["test_cases_by_scope"]:
        actual = {
            split: sum(
                case.metadata.get("slice") == scope
                for case in split_cases[split]
            )
            for split in ("train", "validation", "test")
        }
        expected_train, expected_selection = profile.dataset[
            "skillopt_train_selection_by_scope"
        ][scope]
        expected = {
            "train": int(expected_train),
            "validation": int(expected_selection),
            "test": int(
                profile.dataset["test_cases_by_scope"][scope]
            ),
        }
        if actual != expected:
            raise ValueError(
                f"deepplanning/{scope} counts {actual!r} != {expected!r}"
            )
        scope_ids = {
            str(case.case_id)
            for split in ("train", "validation")
            for case in split_cases[split]
            if case.metadata.get("slice") == scope
        }
        stage0_ids = {
            f"{scope}__case_{int(case_id):03d}"
            for case_id in stage0_by_scope[scope]
        }
        if not stage0_ids.issubset(scope_ids):
            raise ValueError(
                f"deepplanning/{scope} Stage0 IDs are absent from data"
            )
        counts[scope] = {
            **actual,
            "skilladam_optimization": (
                actual["train"] + actual["validation"]
            ),
        }
    return counts


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("API preflight config is missing or invalid") from exc
    return _object(payload, "root")


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    field_name: str,
) -> None:
    if set(value) != expected:
        raise ValueError(
            f"{field_name} keys must equal {sorted(expected)!r}"
        )


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()


def _resolve_path(value: Any, base: Path, field_name: str) -> Path:
    raw = Path(_text(value, field_name))
    return raw if raw.is_absolute() else (base / raw).resolve()


def _validate_new_output_root(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise ValueError(
                "output_root must be absent or an empty directory"
            )
    elif not path.parent.is_dir():
        raise ValueError("output_root parent directory does not exist")


def _environment_name(value: Any, field_name: str) -> str:
    name = _text(value, field_name)
    if not name.replace("_", "").isalnum() or not name[0].isalpha():
        raise ValueError(f"{field_name} is not a valid environment name")
    return name


__all__ = [
    "DEEPPLANNING_BACKEND",
    "GENERAL_BACKEND",
    "preflight_main_result_matrix",
]
