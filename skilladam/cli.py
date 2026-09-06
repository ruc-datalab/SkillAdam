"""Unified, dependency-free command line interface for SkillAdam."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Sequence

from skilladam.cost.pricing import PriceBook, PromptWeightBook
from skilladam.cost.report import write_comparison_from_ledgers
from skilladam.registry import BenchmarkSpec, get_benchmark, list_benchmarks
from skilladam.types import (
    PUBLIC_METHODS,
    PUBLIC_REASONING_EFFORTS,
    PUBLIC_SPLITS,
)


_SKILLADAM_OPTIONS = (
    "iterations",
    "train_size",
    "validation_size",
    "stage0_size",
    "stage0_attempts",
    "min_iterations",
    "max_consecutive_failures",
    "patch_attempts",
    "sampling_strategy",
    "validation_mode",
    "merge_validation_into_train",
    "include_partial_final_batch",
    "shuffle_optimization_pool",
    "optimization_case_id",
    "stage0_case_id",
    "stage0_only",
    "gate_profile",
    "prompt_profile",
    "trajectory_compression",
    "optimizer_history_turns",
    "optimizer_context_limit",
    "edit_budget_metric",
    "edit_budget_v_max",
    "edit_budget_base",
    "edit_budget_minimum",
    "edit_budget_beta",
)
_SKILLOPT_OPTIONS = (
    "epochs",
    "scheduler",
    "max_edit_budget",
    "min_edit_budget",
    "gate_metric",
    "mixed_weight",
    "slow_update",
    "slow_update_mode",
    "slow_update_start_epoch",
    "slow_update_samples",
    "batch_size",
    "reflection_minibatch_size",
    "merge_batch_size",
    "max_analyst_rounds",
    "analyst_workers",
    "shuffle_each_epoch",
    "max_batches",
    "smoke_train_case_id",
    "smoke_validation_case_id",
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return parsed


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("must be finite")
    return parsed


def _unit_float(value: str) -> float:
    parsed = _finite_float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def _add_execution_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_split: str,
) -> None:
    parser.add_argument(
        "--benchmark",
        required=True,
        choices=list_benchmarks(),
        help="Benchmark adapter to use.",
    )
    parser.add_argument(
        "--method",
        required=True,
        choices=PUBLIC_METHODS,
        help="Method to run: skilladam, skillopt, or baseline.",
    )
    parser.add_argument(
        "--split",
        choices=PUBLIC_SPLITS,
        default=default_split,
        help=f"Dataset split (default: {default_split}).",
    )
    parser.add_argument(
        "--data-root",
        required=True,
        type=Path,
        help="Benchmark data root or manifest path.",
    )
    parser.add_argument(
        "--backend",
        required=True,
        help="Explicit fixture or module:factory execution backend.",
    )
    parser.add_argument(
        "--backend-config",
        type=Path,
        help="Optional backend JSON config; only its hash is recorded.",
    )
    parser.add_argument(
        "--scope",
        help="DeepPlanning slice; unsupported by other benchmarks.",
    )
    case_group = parser.add_mutually_exclusive_group()
    case_group.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Exact case ID to include; repeat for multiple cases.",
    )
    case_group.add_argument(
        "--limit",
        type=_positive_int,
        help="Use the first N selected cases in adapter order.",
    )
    skill_group = parser.add_mutually_exclusive_group()
    skill_group.add_argument(
        "--skill",
        type=Path,
        help=(
            "Explicit Markdown skill file; without one, resolution depends "
            "on the selected method and adapter."
        ),
    )
    skill_group.add_argument(
        "--skill-map",
        type=Path,
        help="DeepPlanning scope-to-Markdown JSON map.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print a portable plan without execution.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for reproducible run artifacts.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Provider model identifier; no provider is selected implicitly.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=PUBLIC_REASONING_EFFORTS,
        default="medium",
        help="Reasoning effort when supported (default: medium).",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=_positive_int, default=1)


def _add_optimization_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--initial-skill",
        type=Path,
        help=(
            "Explicit initial Markdown skill. Required for SkillOpt; "
            "SkillAdam runs Stage0 when omitted."
        ),
    )
    parser.add_argument(
        "--validation-split",
        choices=("train", "validation"),
        help="Validation case pool (optimization methods only).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a compatible method checkpoint.",
    )

    skilladam = parser.add_argument_group("SkillAdam options")
    skilladam.add_argument("--iterations", type=_positive_int)
    skilladam.add_argument("--train-size", type=_positive_int)
    skilladam.add_argument("--validation-size", type=_positive_int)
    skilladam.add_argument("--stage0-size", type=_positive_int)
    skilladam.add_argument("--stage0-attempts", type=_positive_int)
    skilladam.add_argument(
        "--min-iterations",
        type=_non_negative_int,
    )
    skilladam.add_argument(
        "--max-consecutive-failures",
        type=_positive_int,
    )
    skilladam.add_argument("--patch-attempts", type=_positive_int)
    skilladam.add_argument(
        "--sampling-strategy",
        choices=("random", "sequential"),
    )
    skilladam.add_argument(
        "--validation-mode",
        choices=("disjoint", "same-batch"),
        help=(
            "Use a disjoint validation batch or gate on the same cases as "
            "the iteration rollout."
        ),
    )
    skilladam.add_argument(
        "--merge-validation-into-train",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Merge the original validation/selection split into the "
            "SkillAdam optimization pool while retaining source metadata."
        ),
    )
    skilladam.add_argument(
        "--include-partial-final-batch",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    skilladam.add_argument(
        "--shuffle-optimization-pool",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    skilladam.add_argument(
        "--optimization-case-id",
        action="append",
        default=None,
        help=(
            "Exact optimization-pool case ID; repeat in the frozen source "
            "order used by the experiment."
        ),
    )
    skilladam.add_argument(
        "--stage0-case-id",
        action="append",
        default=None,
        help="Exact shared Stage0 case ID; repeat in desired order.",
    )
    skilladam.add_argument(
        "--stage0-only",
        action="store_true",
        default=None,
        help=(
            "Generate the shared initial skill and stop before feedback "
            "iterations."
        ),
    )
    skilladam.add_argument(
        "--gate-profile",
        default=None,
        help="Named benchmark gate profile recorded in the run manifest.",
    )
    skilladam.add_argument(
        "--prompt-profile",
        default=None,
        help="Named optimizer prompt profile recorded in the run manifest.",
    )
    skilladam.add_argument(
        "--trajectory-compression",
        choices=("deterministic", "llm"),
        default=None,
    )
    skilladam.add_argument(
        "--optimizer-history-turns",
        type=_positive_int,
        default=None,
        help=(
            "Retain only the latest N completed optimizer iterations in "
            "the multi-turn context; omit to retain the full conversation."
        ),
    )
    skilladam.add_argument(
        "--optimizer-context-limit",
        type=_positive_int,
        default=None,
        help=(
            "Reset only the optimizer conversation after this archived "
            "char-based token estimate is exceeded; current skill and "
            "Momentum remain intact."
        ),
    )
    skilladam.add_argument("--edit-budget-metric")
    skilladam.add_argument(
        "--edit-budget-v-max",
        type=_finite_float,
    )
    skilladam.add_argument("--edit-budget-base", type=_positive_int)
    skilladam.add_argument(
        "--edit-budget-minimum",
        type=_positive_int,
    )
    skilladam.add_argument(
        "--edit-budget-beta",
        type=_unit_float,
    )

    skillopt = parser.add_argument_group("SkillOpt options")
    skillopt.add_argument("--epochs", type=_positive_int)
    skillopt.add_argument(
        "--scheduler",
        choices=("constant", "linear", "cosine", "autonomous"),
    )
    skillopt.add_argument("--max-edit-budget", type=_positive_int)
    skillopt.add_argument("--min-edit-budget", type=_positive_int)
    skillopt.add_argument(
        "--gate-metric",
        choices=("hard", "soft", "mixed"),
    )
    skillopt.add_argument("--mixed-weight", type=_unit_float)
    skillopt.add_argument(
        "--slow-update",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    skillopt.add_argument(
        "--slow-update-mode",
        choices=("force", "gated"),
    )
    skillopt.add_argument(
        "--slow-update-start-epoch",
        type=_positive_int,
    )
    skillopt.add_argument(
        "--slow-update-samples",
        type=_positive_int,
    )
    skillopt.add_argument("--batch-size", type=_positive_int)
    skillopt.add_argument(
        "--reflection-minibatch-size",
        type=_positive_int,
    )
    skillopt.add_argument("--merge-batch-size", type=_positive_int)
    skillopt.add_argument("--max-analyst-rounds", type=_positive_int)
    skillopt.add_argument("--analyst-workers", type=_positive_int)
    skillopt.add_argument(
        "--shuffle-each-epoch",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    skillopt.add_argument(
        "--max-batches",
        type=_positive_int,
        help=(
            "Stop after at most N SkillOpt batches and mark the run as an "
            "incomplete smoke test."
        ),
    )
    skillopt.add_argument(
        "--smoke-train-case-id",
        action="append",
        default=None,
        help=(
            "Exact SkillOpt train case for an incomplete max-batches smoke; "
            "repeat only when more than one case is strictly required."
        ),
    )
    skillopt.add_argument(
        "--smoke-validation-case-id",
        action="append",
        default=None,
        help=(
            "Exact SkillOpt validation case for an incomplete max-batches "
            "smoke; repeat only when more than one case is strictly required."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the public CLI parser without importing benchmark dependencies."""

    parser = argparse.ArgumentParser(
        prog="skilladam",
        description="Reproduce SkillAdam and SkillOpt benchmark experiments.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run optimization or a baseline rollout.",
    )
    _add_execution_arguments(run_parser, default_split="train")
    _add_optimization_arguments(run_parser)

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Evaluate a saved skill on a benchmark split.",
    )
    _add_execution_arguments(evaluate_parser, default_split="test")

    cost_parser = subparsers.add_parser(
        "cost-report",
        help="Build a token-usage and cost comparison report.",
    )
    cost_parser.add_argument(
        "--input",
        required=True,
        type=Path,
        action="append",
        help=(
            "Provider-neutral usage ledger in JSONL format; repeat to "
            "compare method runs stored in separate output directories."
        ),
    )
    cost_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output comparison CSV path.",
    )
    cost_parser.add_argument(
        "--pricing",
        type=Path,
        help="Optional explicit model pricing JSON.",
    )
    cost_parser.add_argument(
        "--prompt-weights",
        type=Path,
        help=(
            "Optional explicit cache weighting JSON for billing-equivalent "
            "prompt tokens."
        ),
    )
    cost_parser.add_argument(
        "--left-method",
        choices=PUBLIC_METHODS,
        default="skilladam",
    )
    cost_parser.add_argument(
        "--right-method",
        choices=PUBLIC_METHODS,
        default="skillopt",
    )

    download_parser = subparsers.add_parser(
        "download-data",
        help="Download or prepare one benchmark dataset.",
    )
    download_parser.add_argument(
        "--benchmark",
        required=True,
        choices=list_benchmarks(),
    )

    materialize_parser = subparsers.add_parser(
        "materialize-data",
        help="Copy an audited local dataset into the strict public layout.",
    )
    materialize_parser.add_argument(
        "--benchmark",
        required=True,
        choices=list_benchmarks(),
    )
    materialize_parser.add_argument(
        "--source-root",
        required=True,
        type=Path,
    )
    materialize_parser.add_argument(
        "--destination",
        required=True,
        type=Path,
    )
    materialize_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate through a disposable copy without retaining the "
            "destination."
        ),
    )

    environment_parser = subparsers.add_parser(
        "check-environment",
        help="Check installed dependencies for one benchmark without API use.",
    )
    environment_parser.add_argument(
        "--benchmark",
        required=True,
        choices=list_benchmarks(),
    )
    environment_parser.add_argument(
        "--spreadsheet-execution-mode",
        choices=("local-subprocess", "container"),
        default="local-subprocess",
        help=(
            "SpreadsheetBench execution mode to check; local-subprocess "
            "requires no container engine (default: local-subprocess)."
        ),
    )
    environment_parser.add_argument(
        "--container-engine",
        choices=("docker", "podman"),
        default="docker",
        help="Optional SpreadsheetBench container engine (default: docker).",
    )
    download_parser.add_argument(
        "--destination",
        required=True,
        type=Path,
    )
    execution_group = download_parser.add_mutually_exclusive_group()
    execution_group.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the source/revision status, license boundary, and "
            "required layout without network or filesystem writes."
        ),
    )
    execution_group.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Explicitly permit the pinned download or local-source "
            "materialization."
        ),
    )
    download_parser.add_argument(
        "--source-root",
        type=Path,
        help=(
            "Use an already authorized local source instead of network "
            "acquisition."
        ),
    )
    download_parser.add_argument(
        "--cache-dir",
        type=Path,
        help="Optional external Hugging Face cache directory.",
    )
    download_parser.add_argument(
        "--accept-terms",
        action="store_true",
        help=(
            "Confirm that the caller reviewed and accepted applicable "
            "gated or license-review dataset terms."
        ),
    )

    list_parser = subparsers.add_parser(
        "list-benchmarks",
        help="List public benchmark identifiers.",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit benchmark metadata as JSON.",
    )

    return parser


def _spec_as_dict(spec: BenchmarkSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "display_name": spec.display_name,
        "description": spec.description,
        "requirements": list(spec.requirements),
        "available": spec.adapter_factory is not None,
    }


def _list_command(*, as_json: bool) -> int:
    specs = [get_benchmark(name) for name in list_benchmarks()]
    if as_json:
        print(
            json.dumps(
                [_spec_as_dict(spec) for spec in specs],
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for spec in specs:
            print(spec.name)
    return 0


def _validate_output_root(path: Path, *, resume: bool = False) -> None:
    root = Path(path)
    if root.is_symlink():
        raise ValueError("output directory must not be a symlink")
    if resume:
        if not root.is_dir():
            raise ValueError(
                "resume output directory must already exist"
            )
        return
    if root.exists():
        if not root.is_dir():
            raise ValueError("output directory must be a directory")
        if any(root.iterdir()):
            raise ValueError(
                "new run refuses a non-empty output directory"
            )
        return
    if not root.parent.is_dir():
        raise ValueError(
            "output directory parent must already exist"
        )


def _option_was_set(
    args: argparse.Namespace,
    names: Sequence[str],
) -> bool:
    return any(getattr(args, name, None) is not None for name in names)


def _validate_run_method_options(args: argparse.Namespace) -> None:
    if args.method == "baseline":
        if (
            args.initial_skill is not None
            or args.validation_split is not None
            or args.resume
            or _option_was_set(args, _SKILLADAM_OPTIONS)
            or _option_was_set(args, _SKILLOPT_OPTIONS)
        ):
            raise ValueError(
                "baseline run does not accept optimization options"
            )
        return

    if args.split != "train":
        raise ValueError("optimization requires --split train")
    if args.skill is not None or args.skill_map is not None:
        raise ValueError(
            "optimization uses --initial-skill, not --skill or --skill-map"
        )
    if args.case_id or args.limit is not None:
        raise ValueError(
            "optimization case pools cannot use --case-id or --limit"
        )

    if args.method == "skilladam":
        if _option_was_set(args, _SKILLOPT_OPTIONS):
            raise ValueError(
                "SkillOpt options are incompatible with SkillAdam"
            )
        adaptive = (
            args.edit_budget_metric,
            args.edit_budget_v_max,
            args.edit_budget_base,
            args.edit_budget_minimum,
            args.edit_budget_beta,
        )
        if any(value is not None for value in adaptive) and (
            args.edit_budget_metric is None
            or args.edit_budget_v_max is None
        ):
            raise ValueError(
                "SkillAdam adaptive edit budget requires both "
                "--edit-budget-metric and --edit-budget-v-max"
            )
        if (
            args.edit_budget_v_max is not None
            and args.edit_budget_v_max <= 0
        ):
            raise ValueError("--edit-budget-v-max must be greater than 0")
        if (
            args.validation_mode == "same-batch"
            and args.validation_split not in {None, "train", "validation"}
        ):
            raise ValueError("invalid same-batch validation split")
        if args.stage0_only and (
            args.initial_skill is not None or args.resume
        ):
            raise ValueError(
                "--stage0-only cannot use --initial-skill or --resume"
            )
        return

    if args.method == "skillopt":
        if _option_was_set(args, _SKILLADAM_OPTIONS):
            raise ValueError(
                "SkillAdam options are incompatible with SkillOpt"
            )
        if args.initial_skill is None:
            raise ValueError(
                "SkillOpt requires an explicit --initial-skill"
            )
        if args.validation_split == "train":
            raise ValueError(
                "SkillOpt requires a validation split disjoint from train"
            )
        if args.max_batches is not None and args.resume:
            raise ValueError(
                "SkillOpt max-batches smoke runs cannot use --resume"
            )
        smoke_train = tuple(args.smoke_train_case_id or ())
        smoke_validation = tuple(args.smoke_validation_case_id or ())
        if args.max_batches is None and (smoke_train or smoke_validation):
            raise ValueError(
                "SkillOpt smoke case IDs require --max-batches"
            )
        if args.max_batches is not None and (
            not smoke_train or not smoke_validation
        ):
            raise ValueError(
                "SkillOpt max-batches smoke runs require explicit "
                "--smoke-train-case-id and --smoke-validation-case-id"
            )
        return

    raise ValueError(f"unsupported run method {args.method!r}")


def _optimization_cases(
    *,
    adapter,
    args: argparse.Namespace,
    method: str,
) -> tuple[tuple[object, ...], str]:
    from skilladam.execution import select_cases

    validation_split = args.validation_split or "validation"
    train_cases = select_cases(
        adapter.load_cases("train"),
        benchmark=args.benchmark,
        scope=args.scope,
        case_ids=(),
        limit=None,
    )
    if validation_split == "train":
        validation_cases = train_cases
    else:
        validation_cases = select_cases(
            adapter.load_cases(validation_split),
            benchmark=args.benchmark,
            scope=args.scope,
            case_ids=(),
            limit=None,
        )
    if method == "skilladam":
        if args.merge_validation_into_train:
            from skilladam.types import BenchmarkCase

            merged_validation = tuple(
                BenchmarkCase(
                    case_id=case.case_id,
                    payload=case.payload,
                    reference=case.reference,
                    metadata={
                        **case.metadata,
                        "source_split": case.metadata.get(
                            "split",
                            validation_split,
                        ),
                        "split": "train",
                    },
                )
                for case in validation_cases
            )
            selected = (*train_cases, *merged_validation)
            validation_split = "train"
        else:
            selected = (
                train_cases
                if validation_split == "train"
                else (*train_cases, *validation_cases)
            )
    else:
        test_cases = select_cases(
            adapter.load_cases("test"),
            benchmark=args.benchmark,
            scope=args.scope,
            case_ids=(),
            limit=None,
        )
        selected = (*train_cases, *validation_cases, *test_cases)
    ids = tuple(case.case_id for case in selected)
    if len(set(ids)) != len(ids):
        raise ValueError(
            "optimization train, validation, and test IDs must be disjoint"
        )
    return tuple(selected), validation_split


def _initial_skill_resolver(
    *,
    args: argparse.Namespace,
    cases,
):
    from skilladam.execution import SkillResolver

    if args.initial_skill is None:
        return None, None
    resolver = SkillResolver.build(
        benchmark=args.benchmark,
        method=args.method,
        cases=cases,
        scope=args.scope,
        skill_path=args.initial_skill,
    )
    resolved = resolver.resolve(cases[0])
    if resolved is None:
        raise ValueError("initial skill resolution failed")
    return resolver, resolved.text


def _load_backend(
    *,
    args: argparse.Namespace,
    context,
    config,
):
    from skilladam.execution import (
        BackendInit,
        load_execution_backend,
    )

    return load_execution_backend(
        args.backend,
        init=BackendInit(
            data_root=args.data_root,
            output_dir=args.output_dir,
            config=config.values,
            context=context,
            config_sha256=config.sha256,
        ),
    )


def _execute_command(args: argparse.Namespace) -> int:
    if args.command == "run":
        _validate_run_method_options(args)
        if args.method != "baseline":
            return _execute_optimization_command(args)

    from skilladam import __version__
    from skilladam.benchmarks.registry import create_adapter
    from skilladam.cost.ledger import UsageLedgerEntry
    from skilladam.execution import (
        BackendContext,
        BackendInit,
        EvaluationRunner,
        ExecutionPlan,
        RunStore,
        SkillResolver,
        load_backend_config,
        load_execution_backend,
        select_cases,
    )

    _validate_output_root(args.output_dir)
    adapter = create_adapter(args.benchmark, args.data_root)
    adapter.validate_dependencies()
    selected = select_cases(
        adapter.load_cases(args.split),
        benchmark=args.benchmark,
        scope=args.scope,
        case_ids=args.case_id,
        limit=args.limit,
    )
    resolver = SkillResolver.build(
        benchmark=args.benchmark,
        method=args.method,
        cases=selected,
        scope=args.scope,
        skill_path=args.skill,
        skill_map_path=args.skill_map,
    )
    context = BackendContext(
        benchmark=args.benchmark,
        method=args.method,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        workers=args.workers,
        seed=args.seed,
    )
    config = load_backend_config(args.backend_config)
    loaded = load_execution_backend(
        args.backend,
        init=BackendInit(
            data_root=args.data_root,
            output_dir=args.output_dir,
            config=config.values,
            context=context,
            config_sha256=config.sha256,
        ),
    )
    plan = ExecutionPlan(
        command=args.command,
        benchmark=args.benchmark,
        method=args.method,
        split=args.split,
        scope=args.scope,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        workers=args.workers,
        seed=args.seed,
        case_ids=tuple(case.case_id for case in selected),
        skill_sources=resolver.sources,
        backend_spec=loaded.spec,
        backend_config_sha256=loaded.config_sha256,
        backend_metadata=loaded.public_metadata,
    )
    plan_payload = plan.to_dict()
    if args.dry_run:
        print(
            json.dumps(
                plan_payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    stage = (
        "baseline_rollout"
        if args.command == "run"
        else "evaluation_rollout"
    )
    evaluation = EvaluationRunner().evaluate(
        adapter=adapter,
        cases=selected,
        method=args.method,
        split=args.split,
        skill_resolver=resolver,
        backend=loaded.backend,
        context=context,
        stage=stage,
    )
    result_payload = evaluation.to_dict()
    result_rows = tuple(
        {"stage": evaluation.stage, **item.to_dict()}
        for item in evaluation.cases
    )
    usage_rows = tuple(
        UsageLedgerEntry(
            method=args.method,
            benchmark=args.benchmark,
            stage=stage,
            model=args.model,
            usage=usage,
        ).to_dict()
        for usage in evaluation.usage
    )
    manifest = {
        "schema_version": 1,
        "package_version": __version__,
        "status": "completed",
        "execution": plan_payload,
    }

    store = RunStore(
        args.output_dir,
        resume=args.output_dir.exists(),
    )
    store.write_json("execution_plan.json", plan_payload)
    store.write_json("run_manifest.json", manifest)
    store.write_jsonl("results.jsonl", result_rows)
    store.write_json("metrics.json", result_payload["metrics"])
    store.write_jsonl("usage.jsonl", usage_rows)
    print(
        f"Completed {args.command} for {args.benchmark} "
        f"({len(selected)} cases)."
    )
    return 0


def _execute_optimization_command(args: argparse.Namespace) -> int:
    from skilladam import __version__
    from skilladam.benchmarks.registry import create_adapter
    from skilladam.execution import (
        BackendContext,
        ExecutionPlan,
        RunStore,
        load_backend_config,
        validate_public_metadata,
    )

    _validate_output_root(args.output_dir, resume=args.resume)
    adapter = create_adapter(args.benchmark, args.data_root)
    adapter.validate_dependencies()
    selected, validation_split = _optimization_cases(
        adapter=adapter,
        args=args,
        method=args.method,
    )
    resolver, initial_skill = _initial_skill_resolver(
        args=args,
        cases=selected,
    )
    context = BackendContext(
        benchmark=args.benchmark,
        method=args.method,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        workers=args.workers,
        seed=args.seed,
    )
    backend_config = load_backend_config(args.backend_config)

    if args.method == "skilladam":
        (
            method_config,
            optimization,
        ) = _skilladam_method_config(
            args=args,
            cases=selected,
            validation_split=validation_split,
        )
    else:
        (
            method_config,
            optimization,
        ) = _skillopt_method_config(
            args=args,
            cases=selected,
            validation_split=validation_split,
            initial_skill=initial_skill,
        )
    execution_cases = selected
    if args.method == "skillopt" and args.max_batches is not None:
        by_id = {case.case_id: case for case in selected}
        execution_case_ids = (
            *method_config.train_case_ids,
            *method_config.validation_case_ids,
            *method_config.test_case_ids,
        )
        execution_cases = tuple(
            by_id[case_id] for case_id in execution_case_ids
        )

    loaded = _load_backend(
        args=args,
        context=context,
        config=backend_config,
    )
    baseline_loaded = None
    baseline_context = None
    if args.method == "skilladam" and initial_skill is None:
        baseline_context = BackendContext(
            benchmark=args.benchmark,
            method="baseline",
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            workers=args.workers,
            seed=args.seed,
        )
        baseline_loaded = _load_backend(
            args=args,
            context=baseline_context,
            config=backend_config,
        )

    plan = ExecutionPlan(
        command="run",
        benchmark=args.benchmark,
        method=args.method,
        split="train",
        scope=args.scope,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        workers=args.workers,
        seed=args.seed,
        case_ids=tuple(case.case_id for case in execution_cases),
        skill_sources=() if resolver is None else resolver.sources,
        backend_spec=loaded.spec,
        backend_config_sha256=loaded.config_sha256,
        backend_metadata=loaded.public_metadata,
    )
    validate_public_metadata(optimization)
    plan_payload = plan.to_dict()
    plan_payload["optimization"] = optimization
    if args.dry_run:
        print(
            json.dumps(
                plan_payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.method == "skilladam":
        from skilladam.core.feedback_loop import (
            CheckpointExistsError,
            ResumeMismatchError,
        )
        from skilladam.methods.skilladam import SkillAdamRunner

        try:
            runner = SkillAdamRunner(
                config=method_config,
                adapter=adapter,
                backend=loaded.backend,
                context=context,
                output_dir=args.output_dir,
                baseline_backend=(
                    None
                    if baseline_loaded is None
                    else baseline_loaded.backend
                ),
                baseline_context=baseline_context,
            )
            if args.stage0_only:
                runner.run_stage0_only(cases=selected)
            else:
                runner.run(
                    cases=selected,
                    initial_skill=initial_skill,
                    resume=args.resume,
                )
        except (CheckpointExistsError, ResumeMismatchError) as exc:
            raise ValueError(str(exc)) from exc
    else:
        from skilladam.baselines.skillopt.runner import (
            CheckpointExistsError,
            ResumeMismatchError,
        )
        from skilladam.methods.skillopt import SkillOptExecutionBridge

        assert initial_skill is not None
        try:
            SkillOptExecutionBridge(
                config=method_config,
                adapter=adapter,
                backend=loaded.backend,
                context=context,
                output_dir=args.output_dir,
            ).run(
                cases=execution_cases,
                initial_skill=initial_skill,
                resume=args.resume,
            )
        except (CheckpointExistsError, ResumeMismatchError) as exc:
            raise ValueError(str(exc)) from exc

    manifest = {
        "schema_version": 1,
        "package_version": __version__,
        "status": "completed",
        "execution": plan_payload,
    }
    store = RunStore(args.output_dir, resume=True)
    try:
        store.write_json("execution_plan.json", plan_payload)
        store.write_json("run_manifest.json", manifest)
    except FileExistsError as exc:
        raise ValueError(str(exc)) from exc
    print(f"Completed run for {args.benchmark} ({args.method}).")
    return 0


def _skilladam_method_config(
    *,
    args: argparse.Namespace,
    cases,
    validation_split: str,
):
    from skilladam.core.feedback_loop import (
        EditBudgetConfig,
        FeedbackLoopConfig,
    )
    from skilladam.methods.skilladam import SkillAdamRunConfig

    iterations = args.iterations or 10
    train_size = args.train_size or 10
    validation_size = args.validation_size or 10
    stage0_size = args.stage0_size or train_size
    min_iterations = (
        min(3, iterations)
        if args.min_iterations is None
        else args.min_iterations
    )
    edit_budget = None
    if args.edit_budget_metric is not None:
        metric = args.edit_budget_metric.strip()
        if not metric:
            raise ValueError("--edit-budget-metric must be non-empty")
        base = args.edit_budget_base or 4
        minimum = args.edit_budget_minimum or 1
        if minimum > base:
            raise ValueError(
                "--edit-budget-minimum cannot exceed --edit-budget-base"
            )
        edit_budget = EditBudgetConfig(
            metric_key=metric,
            v_max=args.edit_budget_v_max,
            base=base,
            minimum=minimum,
            beta=(
                0.9
                if args.edit_budget_beta is None
                else args.edit_budget_beta
            ),
        )
    feedback = FeedbackLoopConfig(
        max_iterations=iterations,
        min_iterations=min_iterations,
        max_consecutive_failures=(
            args.max_consecutive_failures or 3
        ),
        patch_attempts=args.patch_attempts or 3,
        edit_budget=edit_budget,
    )
    config = SkillAdamRunConfig(
        benchmark=args.benchmark,
        split="train",
        validation_split=validation_split,
        scope=args.scope,
        train_size=train_size,
        validation_size=validation_size,
        stage0_size=stage0_size,
        feedback_loop=feedback,
        sampling_strategy=args.sampling_strategy or "random",
        seed=args.seed,
        stage0_attempts=args.stage0_attempts or 2,
        validation_mode=(
            "same_batch"
            if args.validation_mode == "same-batch"
            else "disjoint"
        ),
        include_partial_final_batch=bool(
            args.include_partial_final_batch
        ),
        shuffle_optimization_pool=bool(
            args.shuffle_optimization_pool
        ),
        optimization_case_ids=tuple(args.optimization_case_id or ()),
        stage0_case_ids=tuple(args.stage0_case_id or ()),
        gate_profile=args.gate_profile or "default",
        prompt_profile=args.prompt_profile or "v2",
        trajectory_compression=(
            args.trajectory_compression or "deterministic"
        ),
        optimizer_history_turns=args.optimizer_history_turns,
        optimizer_context_limit=args.optimizer_context_limit,
    )
    _validate_skilladam_pool(config, cases)
    return (
        config,
        {
            "method": "skilladam",
            "initial_skill": (
                "explicit"
                if args.initial_skill is not None
                else "stage0"
            ),
            "phase": (
                "stage0_only" if args.stage0_only else "optimization"
            ),
            "validation_split": validation_split,
            "config": config.to_dict(),
        },
    )


def _validate_skilladam_pool(config, cases) -> None:
    if config.validation_split == config.split:
        required = (
            config.train_size
            if config.validation_mode == "same_batch"
            else config.train_size + config.validation_size
        )
        if len(cases) < required:
            raise ValueError(
                f"SkillAdam requires at least {required} train cases"
            )
        if config.stage0_size > len(cases):
            raise ValueError("stage0 size exceeds the train case pool")
        return
    train_count = sum(
        case.metadata.get("split") == config.split for case in cases
    )
    validation_count = sum(
        case.metadata.get("split") == config.validation_split
        for case in cases
    )
    if train_count < config.train_size:
        raise ValueError("SkillAdam train case pool is too small")
    if validation_count < config.validation_size:
        raise ValueError("SkillAdam validation case pool is too small")
    if train_count < config.stage0_size:
        raise ValueError("SkillAdam Stage0 case pool is too small")


def _skillopt_method_config(
    *,
    args: argparse.Namespace,
    cases,
    validation_split: str,
    initial_skill: str | None,
):
    from hashlib import sha256

    from skilladam.baselines.skillopt.contracts import SkillOptConfig

    if initial_skill is None:
        raise ValueError("SkillOpt requires an explicit initial skill")
    case_ids = {
        split: tuple(
            case.case_id
            for case in cases
            if case.metadata.get("split") == split
        )
        for split in ("train", validation_split, "test")
    }
    train_case_ids = case_ids["train"]
    validation_case_ids = case_ids[validation_split]
    test_case_ids = case_ids["test"]
    if args.max_batches is not None:
        train_case_ids = _exact_smoke_case_ids(
            available=train_case_ids,
            requested=tuple(args.smoke_train_case_id or ()),
            label="train",
        )
        validation_case_ids = _exact_smoke_case_ids(
            available=validation_case_ids,
            requested=tuple(args.smoke_validation_case_id or ()),
            label="validation",
        )
        test_case_ids = ()
    config = SkillOptConfig(
        benchmark=args.benchmark,
        slice_id=args.scope or "all",
        epochs=args.epochs or 4,
        train_case_ids=train_case_ids,
        validation_case_ids=validation_case_ids,
        test_case_ids=test_case_ids,
        seed=args.seed,
        model=args.model,
        initial_skill_sha256=sha256(
            initial_skill.encode("utf-8")
        ).hexdigest(),
        scheduler_mode=args.scheduler or "cosine",
        max_edit_budget=args.max_edit_budget or 4,
        min_edit_budget=args.min_edit_budget or 2,
        gate_metric=args.gate_metric or "soft",
        mixed_weight=(
            0.5 if args.mixed_weight is None else args.mixed_weight
        ),
        use_slow_update=(
            True if args.slow_update is None else args.slow_update
        ),
        slow_update_mode=args.slow_update_mode or "force",
        slow_update_start_epoch=args.slow_update_start_epoch or 2,
        slow_update_samples=args.slow_update_samples or 20,
        batch_size=args.batch_size or 0,
        reflection_minibatch_size=(
            args.reflection_minibatch_size or 0
        ),
        merge_batch_size=args.merge_batch_size or 8,
        max_analyst_rounds=args.max_analyst_rounds or 3,
        analyst_workers=args.analyst_workers or 16,
        shuffle_each_epoch=(
            True
            if args.shuffle_each_epoch is None
            else args.shuffle_each_epoch
        ),
        max_batches=args.max_batches,
    )
    return (
        config,
        {
            "method": "skillopt",
            "initial_skill": "explicit",
            "validation_split": validation_split,
            "config": config.signature(),
            "incomplete_smoke": args.max_batches is not None,
        },
    )


def _exact_smoke_case_ids(
    *,
    available: tuple[str, ...],
    requested: tuple[str, ...],
    label: str,
) -> tuple[str, ...]:
    if len(set(requested)) != len(requested):
        raise ValueError(
            f"SkillOpt smoke {label} case IDs must be unique"
        )
    missing = [case_id for case_id in requested if case_id not in available]
    if missing:
        raise ValueError(
            f"SkillOpt smoke {label} case IDs are unavailable: {missing!r}"
        )
    return requested


def _public_execution_command(args: argparse.Namespace) -> int:
    from skilladam.benchmarks.base import (
        DatasetUnavailableError,
        DependencyUnavailableError,
    )
    from skilladam.execution import BackendError

    try:
        return _execute_command(args)
    except (
        BackendError,
        DatasetUnavailableError,
        DependencyUnavailableError,
        ValueError,
    ) as exc:
        message = " ".join(str(exc).splitlines())
        print(message, file=sys.stderr)
        return 2


def _download_data_command(args: argparse.Namespace) -> int:
    from skilladam.data.download import (
        create_download_plan,
    )
    from skilladam.data.prepare import (
        DataPreparationError,
        prepare_benchmark_data,
    )

    try:
        plan = create_download_plan(args.benchmark, args.destination)
        if not args.execute:
            payload: object = plan.to_dict()
        else:
            result = prepare_benchmark_data(
                args.benchmark,
                args.destination,
                source_root=args.source_root,
                accept_terms=args.accept_terms,
                cache_dir=args.cache_dir,
                token=os.environ.get("HF_TOKEN"),
            )
            payload = {
                "plan": plan.to_dict(),
                "result": result.to_dict(),
            }
        print(json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
    except (DataPreparationError, OSError, ValueError) as exc:
        print(" ".join(str(exc).splitlines()), file=sys.stderr)
        return 2
    return 0


def _materialize_data_command(args: argparse.Namespace) -> int:
    from skilladam.data.materialize import (
        MaterializationError,
        materialize_dataset,
    )

    try:
        result = materialize_dataset(
            args.benchmark,
            args.source_root,
            args.destination,
            dry_run=args.dry_run,
        )
    except MaterializationError as exc:
        print(" ".join(str(exc).splitlines()), file=sys.stderr)
        return 2
    print(
        json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _check_environment_command(args: argparse.Namespace) -> int:
    from skilladam.environment import inspect_benchmark_environment

    try:
        report = inspect_benchmark_environment(
            args.benchmark,
            spreadsheet_execution_mode=args.spreadsheet_execution_mode,
            container_engine=args.container_engine,
        )
    except ValueError as exc:
        print(" ".join(str(exc).splitlines()), file=sys.stderr)
        return 2
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["dependencies_ready"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    args = build_parser().parse_args(argv)

    if args.command == "list-benchmarks":
        return _list_command(as_json=args.json)
    if args.command == "evaluate":
        return _public_execution_command(args)
    if args.command == "run":
        return _public_execution_command(args)
    if args.command == "download-data":
        return _download_data_command(args)
    if args.command == "materialize-data":
        return _materialize_data_command(args)
    if args.command == "check-environment":
        return _check_environment_command(args)
    if args.command == "cost-report":
        price_book = (
            PriceBook.load(args.pricing) if args.pricing else None
        )
        prompt_weight_book = (
            PromptWeightBook.load(args.prompt_weights)
            if args.prompt_weights
            else None
        )
        write_comparison_from_ledgers(
            args.input,
            args.output,
            left_method=args.left_method,
            right_method=args.right_method,
            price_book=price_book,
            prompt_weight_book=prompt_weight_book,
        )
        print(f"Wrote cost comparison to {args.output}")
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")


def entrypoint() -> None:
    """Console-script entry point."""

    raise SystemExit(main())
