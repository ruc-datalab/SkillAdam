#!/usr/bin/env python3
"""Plan or execute the frozen paper main-result command matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skilladam.benchmarks.registry import list_benchmarks
from skilladam.experiments import build_main_result_commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build explicit Stage0/train/evaluate commands from the frozen "
            "paper main-result profile. Planning is offline by default."
        )
    )
    parser.add_argument(
        "--benchmark",
        required=True,
        choices=list_benchmarks(),
    )
    parser.add_argument(
        "--method",
        required=True,
        choices=("baseline", "skilladam", "skillopt", "both", "all"),
    )
    parser.add_argument(
        "--phase",
        required=True,
        choices=("stage0", "train", "evaluate", "all"),
    )
    parser.add_argument("--scope")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--backend-config", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--format",
        choices=("json", "shell"),
        default="json",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the API-capable plan instead of only printing it.",
    )
    parser.add_argument(
        "--confirm-api-costs",
        action="store_true",
        help=(
            "Required with --execute. Confirm that you have reviewed "
            "the generated plan and its API cost exposure."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands = build_main_result_commands(
        benchmark=args.benchmark,
        method=args.method,
        phase=args.phase,
        data_root=args.data_root,
        backend=args.backend,
        backend_config=args.backend_config,
        output_root=args.output_root,
        scope=args.scope,
    )
    if not commands:
        raise ValueError("the selected method/phase produced no commands")
    if args.format == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "profile": "paper-main-results-v1",
                    "executes_api": bool(args.execute),
                    "commands": [
                        command.to_dict() for command in commands
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for command in commands:
            print(shlex.join(command.argv))
    if not args.execute:
        return 0
    if not args.confirm_api_costs:
        raise ValueError(
            "--execute requires the explicit --confirm-api-costs interlock"
        )
    _preflight(args.data_root, args.backend_config)
    for command in commands:
        executable = (
            sys.executable,
            "-m",
            "skilladam",
            *command.argv[1:],
        )
        _prepare_output_parent(command.argv)
        subprocess.run(executable, check=True)
    return 0


def _preflight(data_root: Path, backend_config: Path) -> None:
    if not data_root.exists():
        raise FileNotFoundError(f"data root does not exist: {data_root}")
    if not backend_config.is_file():
        raise FileNotFoundError(
            f"backend config does not exist: {backend_config}"
        )


def _prepare_output_parent(argv: tuple[str, ...]) -> None:
    index = argv.index("--output-dir") + 1
    Path(argv[index]).parent.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
