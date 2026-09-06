"""Build explicit CLI commands for the frozen paper main-result profile."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from skilladam.experiments.main_results import (
    BenchmarkMainResultProfile,
    load_main_result_profile,
)


_METHODS = frozenset(
    {"baseline", "skilladam", "skillopt", "both", "all"}
)
_PHASES = frozenset({"stage0", "train", "evaluate", "all"})
_DP_SCOPES = (
    "shopping_level1",
    "shopping_level2",
    "shopping_level3",
    "travel_en",
)


@dataclass(frozen=True, slots=True)
class PlannedCommand:
    """One auditable main-result subprocess invocation."""

    label: str
    argv: tuple[str, ...]
    phase: str
    method: str
    benchmark: str
    scope: str | None
    uses_api: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "argv": list(self.argv),
            "phase": self.phase,
            "method": self.method,
            "benchmark": self.benchmark,
            "scope": self.scope,
            "uses_api": self.uses_api,
        }


def build_main_result_commands(
    *,
    benchmark: str,
    method: str,
    phase: str,
    data_root: Path,
    backend: str,
    backend_config: Path,
    output_root: Path,
    scope: str | None = None,
) -> tuple[PlannedCommand, ...]:
    """Translate one frozen profile into explicit public CLI commands."""

    profile = load_main_result_profile()
    benchmark_profile = profile.benchmark(benchmark)
    if method not in _METHODS:
        raise ValueError(f"unsupported main-result method {method!r}")
    if phase not in _PHASES:
        raise ValueError(f"unsupported main-result phase {phase!r}")
    scopes = _scopes(benchmark, scope)
    commands: list[PlannedCommand] = []
    for selected_scope in scopes:
        paths = _output_paths(
            Path(output_root),
            benchmark,
            selected_scope,
        )
        if phase in {"stage0", "all"} and method != "baseline":
            commands.append(
                _stage0_command(
                    benchmark_profile,
                    data_root=Path(data_root),
                    backend=backend,
                    backend_config=Path(backend_config),
                    output_dir=paths["stage0"],
                    scope=selected_scope,
                )
            )
        if phase in {"train", "all"}:
            if method in {"skilladam", "both", "all"}:
                commands.append(
                    _skilladam_train_command(
                        benchmark_profile,
                        data_root=Path(data_root),
                        backend=backend,
                        backend_config=Path(backend_config),
                        output_dir=paths["skilladam"],
                        initial_skill=paths["stage0"] / "initial_skill.md",
                        scope=selected_scope,
                    )
                )
            if method in {"skillopt", "both", "all"}:
                commands.append(
                    _skillopt_train_command(
                        benchmark_profile,
                        data_root=Path(data_root),
                        backend=backend,
                        backend_config=Path(backend_config),
                        output_dir=paths["skillopt"],
                        initial_skill=paths["stage0"] / "initial_skill.md",
                        scope=selected_scope,
                    )
                )
        if phase in {"evaluate", "all"}:
            methods = (
                ("baseline",)
                if method == "baseline"
                else (
                    ("baseline", "skilladam", "skillopt")
                    if method == "all"
                    else ("skilladam", "skillopt")
                    if method == "both"
                    else (method,)
                )
            )
            for selected_method in methods:
                commands.append(
                    _evaluation_command(
                        benchmark_profile,
                        method=selected_method,
                        data_root=Path(data_root),
                        backend=backend,
                        backend_config=Path(backend_config),
                        output_dir=paths[
                            f"{selected_method}_evaluation"
                        ],
                        skill_path=(
                            None
                            if selected_method == "baseline"
                            else paths[selected_method]
                            / (
                                "final_skill.md"
                                if selected_method == "skilladam"
                                else "best_skill.md"
                            )
                        ),
                        scope=selected_scope,
                    )
                )
    return tuple(commands)


def _stage0_command(
    profile: BenchmarkMainResultProfile,
    *,
    data_root: Path,
    backend: str,
    backend_config: Path,
    output_dir: Path,
    scope: str | None,
) -> PlannedCommand:
    case_ids = _stage0_case_ids(profile, scope)
    args = _common_run_args(
        profile,
        method="skilladam",
        phase="stage0",
        data_root=data_root,
        backend=backend,
        backend_config=backend_config,
        output_dir=output_dir,
        scope=scope,
        workers=int(profile.stage0["workers"]),
    )
    args.extend(
        [
            "--iterations",
            "1",
            "--min-iterations",
            "0",
            "--train-size",
            str(int(profile.skilladam["batch_size"])),
            "--validation-size",
            str(int(profile.skilladam["batch_size"])),
            "--stage0-size",
            str(len(case_ids)),
            "--validation-mode",
            "same-batch",
            "--sampling-strategy",
            str(profile.skilladam["sampling_strategy"]),
            "--prompt-profile",
            str(profile.skilladam["prompt_profile"]),
            "--gate-profile",
            "main-result",
            "--stage0-only",
        ]
    )
    _add_optimization_pool_flags(args, profile)
    for case_id in case_ids:
        args.extend(["--stage0-case-id", case_id])
    return _planned(
        profile,
        scope,
        "stage0",
        "skilladam",
        args,
    )


def _skilladam_train_command(
    profile: BenchmarkMainResultProfile,
    *,
    data_root: Path,
    backend: str,
    backend_config: Path,
    output_dir: Path,
    initial_skill: Path,
    scope: str | None,
) -> PlannedCommand:
    config = profile.skilladam
    limits = _skilladam_limits(config, scope)
    batch_size = int(config["batch_size"])
    args = _common_run_args(
        profile,
        method="skilladam",
        phase="train",
        data_root=data_root,
        backend=backend,
        backend_config=backend_config,
        output_dir=output_dir,
        scope=scope,
        workers=int(config["workers"]),
    )
    args.extend(
        [
            "--initial-skill",
            str(initial_skill),
            "--iterations",
            str(limits["max_iterations"]),
            "--min-iterations",
            str(limits.get("min_iterations", 0)),
            "--max-consecutive-failures",
            str(limits.get("max_consecutive_failures", 3)),
            "--train-size",
            str(batch_size),
            "--validation-size",
            str(batch_size),
            "--stage0-size",
            str(len(_stage0_case_ids(profile, scope))),
            "--validation-mode",
            "same-batch",
            "--sampling-strategy",
            str(config["sampling_strategy"]),
            "--prompt-profile",
            str(config["prompt_profile"]),
            "--gate-profile",
            "main-result",
            "--trajectory-compression",
            str(config["trajectory_compression"]),
        ]
    )
    context_limit = config.get("optimizer_context_limit")
    if context_limit is not None:
        args.extend(
            [
                "--optimizer-context-limit",
                str(context_limit),
            ]
        )
    history_turns = config.get("history_turns")
    if history_turns is not None:
        args.extend(
            [
                "--optimizer-history-turns",
                str(history_turns),
            ]
        )
    _add_optimization_pool_flags(args, profile)
    if bool(config.get("include_partial_final_batch", False)):
        args.append("--include-partial-final-batch")
    if bool(config.get("shuffle_optimization_pool", False)):
        args.append("--shuffle-optimization-pool")
    if profile.name == "deepplanning":
        for case_id in _deepplanning_optimization_case_ids(scope):
            args.extend(["--optimization-case-id", case_id])
    budget = config.get("adaptive_edit_budget")
    if isinstance(budget, Mapping):
        metric, v_max = _edit_budget_scope_values(budget, scope)
        args.extend(
            [
                "--edit-budget-metric",
                metric,
                "--edit-budget-v-max",
                str(v_max),
                "--edit-budget-base",
                str(budget["base"]),
                "--edit-budget-minimum",
                str(budget["minimum"]),
                "--edit-budget-beta",
                str(budget["beta"]),
            ]
        )
    return _planned(
        profile,
        scope,
        "train",
        "skilladam",
        args,
    )


def _skillopt_train_command(
    profile: BenchmarkMainResultProfile,
    *,
    data_root: Path,
    backend: str,
    backend_config: Path,
    output_dir: Path,
    initial_skill: Path,
    scope: str | None,
) -> PlannedCommand:
    config = profile.skillopt
    batch_size = _scoped_int(config, "batch_size", scope)
    reflection_size = _scoped_int(
        config,
        "reflection_minibatch_size",
        scope,
    )
    slow_samples = _scoped_int(
        config,
        "slow_update_samples",
        scope,
    )
    args = _common_run_args(
        profile,
        method="skillopt",
        phase="train",
        data_root=data_root,
        backend=backend,
        backend_config=backend_config,
        output_dir=output_dir,
        scope=scope,
        workers=int(config["rollout_workers"]),
    )
    args.extend(
        [
            "--initial-skill",
            str(initial_skill),
            "--validation-split",
            "validation",
            "--epochs",
            str(config["epochs"]),
            "--batch-size",
            str(batch_size),
            "--reflection-minibatch-size",
            str(reflection_size),
            "--merge-batch-size",
            str(config["merge_batch_size"]),
            "--max-analyst-rounds",
            str(config["max_analyst_rounds"]),
            "--analyst-workers",
            str(config["analyst_workers"]),
            "--scheduler",
            str(config["scheduler"]),
            "--max-edit-budget",
            str(config["max_edit_budget"]),
            "--min-edit-budget",
            str(config["min_edit_budget"]),
            "--slow-update-samples",
            str(slow_samples),
        ]
    )
    args.append(
        "--shuffle-each-epoch"
        if bool(config["shuffle_each_epoch"])
        else "--no-shuffle-each-epoch"
    )
    return _planned(
        profile,
        scope,
        "train",
        "skillopt",
        args,
    )


def _evaluation_command(
    profile: BenchmarkMainResultProfile,
    *,
    method: str,
    data_root: Path,
    backend: str,
    backend_config: Path,
    output_dir: Path,
    skill_path: Path | None,
    scope: str | None,
) -> PlannedCommand:
    workers = int(profile.evaluation["workers"])
    args = [
        "skilladam",
        "evaluate",
        "--benchmark",
        profile.name,
        "--method",
        method,
        "--split",
        "test",
        "--data-root",
        str(data_root),
        "--backend",
        backend,
        "--backend-config",
        str(backend_config),
        "--output-dir",
        str(output_dir),
        "--model",
        profile.model,
        "--reasoning-effort",
        _reasoning_effort(
            profile,
            method=method,
            phase="evaluate",
        ),
        "--seed",
        "42",
        "--workers",
        str(workers),
    ]
    if scope is not None:
        args.extend(["--scope", scope])
    if skill_path is not None:
        args.extend(["--skill", str(skill_path)])
    return _planned(profile, scope, "evaluate", method, args)


def _common_run_args(
    profile: BenchmarkMainResultProfile,
    *,
    method: str,
    phase: str,
    data_root: Path,
    backend: str,
    backend_config: Path,
    output_dir: Path,
    scope: str | None,
    workers: int,
) -> list[str]:
    args = [
        "skilladam",
        "run",
        "--benchmark",
        profile.name,
        "--method",
        method,
        "--split",
        "train",
        "--data-root",
        str(data_root),
        "--backend",
        backend,
        "--backend-config",
        str(backend_config),
        "--output-dir",
        str(output_dir),
        "--model",
        profile.model,
        "--reasoning-effort",
        _reasoning_effort(
            profile,
            method=method,
            phase=phase,
        ),
        "--seed",
        "42",
        "--workers",
        str(workers),
    ]
    if scope is not None:
        args.extend(["--scope", scope])
    return args


def _add_optimization_pool_flags(
    args: list[str],
    profile: BenchmarkMainResultProfile,
) -> None:
    args.extend(
        [
            "--validation-split",
            "validation",
            "--merge-validation-into-train",
        ]
    )


def _skilladam_limits(
    config,
    scope: str | None,
):
    scoped = config.get("scope_limits")
    return scoped[scope] if isinstance(scoped, Mapping) else config


def _scoped_int(config, key: str, scope: str | None) -> int:
    scoped = config.get(f"{key}_by_scope")
    if isinstance(scoped, Mapping):
        return int(scoped[scope])
    return int(config[key])


def _stage0_case_ids(
    profile: BenchmarkMainResultProfile,
    scope: str | None,
) -> tuple[str, ...]:
    scoped = profile.stage0.get("case_ids_by_scope")
    values = scoped[scope] if isinstance(scoped, Mapping) else profile.stage0[
        "case_ids"
    ]
    if profile.name == "deepplanning":
        assert scope is not None
        return tuple(
            f"{scope}__case_{int(value):03d}"
            for value in values
        )
    return tuple(str(value) for value in values)


def _deepplanning_optimization_case_ids(
    scope: str | None,
) -> tuple[str, ...]:
    from skilladam.benchmarks.deepplanning.manifest import SLICE_SPECS

    if scope not in SLICE_SPECS:
        raise ValueError("DeepPlanning optimization requires a valid scope")
    spec = SLICE_SPECS[scope]
    first = int(spec["first_case_id"])
    stop = first + int(spec["case_count"])
    return tuple(
        f"{scope}__case_{case_id:03d}"
        for case_id in range(first, stop)
        if case_id % 2 == 1
    )


def _edit_budget_scope_values(
    budget,
    scope: str | None,
) -> tuple[str, float]:
    if "metric" in budget:
        return str(budget["metric"]), float(budget["v_max"])
    domain = "travel" if scope == "travel_en" else "shopping"
    return (
        str(budget[f"{domain}_metric"]),
        float(budget[f"{domain}_v_max"]),
    )


def _reasoning_effort(
    profile: BenchmarkMainResultProfile,
    *,
    method: str,
    phase: str,
) -> str:
    if phase == "stage0":
        source = profile.stage0
    elif method == "skillopt":
        source = profile.skillopt
    else:
        source = profile.skilladam
    value = source.get("reasoning_effort")
    return "none" if value is None else str(value)


def _scopes(benchmark: str, scope: str | None) -> tuple[str | None, ...]:
    if benchmark != "deepplanning":
        if scope is not None:
            raise ValueError("scope is only supported for DeepPlanning")
        return (None,)
    if scope is None:
        return _DP_SCOPES
    if scope not in _DP_SCOPES:
        raise ValueError(f"unsupported DeepPlanning scope {scope!r}")
    return (scope,)


def _output_paths(
    root: Path,
    benchmark: str,
    scope: str | None,
) -> dict[str, Path]:
    base = root / benchmark
    if scope is not None:
        base /= scope
    return {
        "stage0": base / "shared_stage0",
        "skilladam": base / "skilladam_train",
        "skillopt": base / "skillopt_train",
        "baseline_evaluation": base / "baseline_test",
        "skilladam_evaluation": base / "skilladam_test",
        "skillopt_evaluation": base / "skillopt_test",
    }


def _planned(
    profile: BenchmarkMainResultProfile,
    scope: str | None,
    phase: str,
    method: str,
    argv: Iterable[str],
) -> PlannedCommand:
    suffix = f"/{scope}" if scope else ""
    return PlannedCommand(
        label=f"{profile.name}{suffix}:{method}:{phase}",
        argv=tuple(argv),
        phase=phase,
        method=method,
        benchmark=profile.name,
        scope=scope,
    )


__all__ = ["PlannedCommand", "build_main_result_commands"]
