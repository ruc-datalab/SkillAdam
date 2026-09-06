"""SkillAdam user-facing product entry point."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Sequence

from skilladam.product.commands import (
    apply_default_review_policy,
    continue_product,
    prepare_product,
    product_status,
    submit_product_selection,
)
from skilladam.product.cli_model import DEFAULT_PLATFORM_CLI_TIMEOUT_SECONDS
from skilladam.product.history_adapters import HISTORY_PLATFORMS
from skilladam.product.history_discovery import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MAX_CANDIDATES,
    discover_history_tasks,
    validate_history_discovery,
)
from skilladam.product.runtime import (
    build_runtime_config,
    default_runtime_config,
    load_task_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skilladam-product")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover-history")
    discover.add_argument("--output-dir", required=True, type=Path)
    discover.add_argument("--skill", required=True, type=Path)
    discover.add_argument("--intent", required=True)
    discover.add_argument("--platform", required=True, choices=sorted(HISTORY_PLATFORMS))
    discover.add_argument(
        "--lookback-days",
        type=_positive_int,
        default=DEFAULT_LOOKBACK_DAYS,
    )
    discover.add_argument(
        "--max-candidates",
        type=_positive_int,
        default=DEFAULT_MAX_CANDIDATES,
    )

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--output-dir", required=True, type=Path)
    prepare.add_argument("--skill", type=Path)
    prepare.add_argument("--intent")
    _add_provider_arguments(prepare)
    prepare.add_argument("--task-manifest", type=Path)
    prepare.add_argument("--task-count", type=_positive_int, default=8)
    prepare.add_argument("--validation-size", type=_positive_int)
    prepare.add_argument("--batch-size", type=_positive_int)
    prepare.add_argument("--iterations", type=_positive_int, default=3)
    prepare.add_argument("--min-iterations", type=_non_negative_int, default=0)
    prepare.add_argument(
        "--max-consecutive-failures",
        type=_positive_int,
        default=3,
    )
    prepare.add_argument("--patch-attempts", type=_positive_int, default=3)
    prepare.add_argument("--edit-budget-base", type=_positive_int, default=4)
    prepare.add_argument("--edit-budget-minimum", type=_positive_int, default=1)
    prepare.add_argument("--edit-budget-beta", type=_unit_float, default=0.9)
    prepare.add_argument("--edit-budget-v-max", type=_positive_float, default=0.1)
    prepare.add_argument(
        "--review",
        action="store_true",
        help="Pause at awaiting_review before applying patches; by default, accept all and validate.",
    )

    status = subparsers.add_parser("status")
    status.add_argument("--output-dir", required=True, type=Path)

    submit = subparsers.add_parser("submit")
    submit.add_argument("--output-dir", required=True, type=Path)
    selection = submit.add_mutually_exclusive_group(required=True)
    selection.add_argument("--accept", action="append", default=[])
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--none", action="store_true")
    submit.add_argument("--idempotency-key", required=True)
    submit.add_argument("--reason", default="")

    resume = subparsers.add_parser("continue")
    resume.add_argument("--output-dir", required=True, type=Path)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "discover-history":
            payload = discover_history_tasks(
                skill_path=args.skill,
                intent=args.intent,
                output_dir=args.output_dir,
                platform=args.platform,
                lookback_days=args.lookback_days,
                max_candidates=args.max_candidates,
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if args.command == "prepare":
            view = _prepare(args)
        elif args.command == "status":
            view = product_status(args.output_dir)
        elif args.command == "submit":
            view = submit_product_selection(
                output_dir=args.output_dir,
                accepted_hunk_ids=(
                    None if args.all else () if args.none else tuple(args.accept)
                ),
                idempotency_key=args.idempotency_key,
                reason=args.reason,
            )
        elif args.command == "continue":
            view = continue_product(args.output_dir)
        else:  # pragma: no cover - argparse restricts valid commands
            raise ValueError(f"unsupported command {args.command!r}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(view.to_json())
    return 0


def _prepare(args: argparse.Namespace):
    runtime = default_runtime_config()
    if args.provider is not None or args.platform is not None:
        runtime = build_runtime_config(
            provider=args.provider or "platform-cli",
            fixture=args.fixture,
            model=args.model,
            api_key_env=args.api_key_env,
            base_url_env=args.base_url_env,
            wire_api=args.wire_api,
            reasoning_effort=args.reasoning_effort,
            platform=args.platform,
            executable=args.executable,
            timeout_seconds=args.timeout_seconds,
        )
    task_manifest = load_task_manifest(args.task_manifest)
    if (
        args.platform in HISTORY_PLATFORMS
        and args.provider != "fixture"
        and args.skill is not None
        and args.intent is not None
        and task_manifest is not None
    ):
        validate_history_discovery(
            output_dir=args.output_dir,
            skill_path=args.skill,
            intent=args.intent,
            platform=args.platform,
            task_manifest=task_manifest,
        )
    view = prepare_product(
        output_dir=args.output_dir,
        skill_path=args.skill,
        intent=args.intent,
        runtime=runtime,
        task_count=args.task_count,
        validation_size=args.validation_size,
        batch_size=args.batch_size,
        task_manifest=task_manifest,
        max_iterations=args.iterations,
        min_iterations=args.min_iterations,
        max_consecutive_failures=args.max_consecutive_failures,
        patch_attempts=args.patch_attempts,
        edit_budget_base=args.edit_budget_base,
        edit_budget_minimum=args.edit_budget_minimum,
        edit_budget_beta=args.edit_budget_beta,
        edit_budget_v_max=args.edit_budget_v_max,
    )
    return apply_default_review_policy(
        output_dir=args.output_dir,
        view=view,
        review_mode=args.review,
    )


def _add_provider_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=("fixture", "openai-compatible", "platform-cli"),
    )
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url-env", default="OPENAI_BASE_URL")
    parser.add_argument(
        "--wire-api",
        choices=("completions", "responses"),
        default="completions",
    )
    parser.add_argument("--reasoning-effort")
    parser.add_argument(
        "--platform",
        choices=("claude-code", "codex", "cursor", "github-copilot"),
    )
    parser.add_argument("--executable")
    parser.add_argument(
        "--timeout-seconds",
        type=_timeout_seconds,
        default=DEFAULT_PLATFORM_CLI_TIMEOUT_SECONDS,
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _timeout_seconds(value: str) -> int:
    parsed = int(value)
    if not 10 <= parsed <= 1800:
        raise argparse.ArgumentTypeError("must be between 10 and 1800")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _unit_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def entrypoint() -> None:
    raise SystemExit(main())


def _configure_stdio() -> None:
    """Windows default code pages cannot reliably encode arbitrary history text."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


if __name__ == "__main__":
    entrypoint()
