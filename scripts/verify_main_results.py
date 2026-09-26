#!/usr/bin/env python3
"""Validate current-run metric values and aggregate evaluation sample counts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skilladam.benchmarks.registry import list_benchmarks
from skilladam.experiments.result_verification import (
    PUBLIC_RESULT_METHODS,
    verify_main_result_outputs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate saved metric values and aggregate sample counts, then "
            "summarize the current run. Case identities are not verified. "
            "No API is called."
        )
    )
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--benchmark",
        action="append",
        choices=list_benchmarks(),
        help="Repeat to select benchmarks; default is all seven.",
    )
    parser.add_argument(
        "--method",
        action="append",
        choices=PUBLIC_RESULT_METHODS,
        help="Repeat to select methods; default is all three.",
    )
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = verify_main_result_outputs(
        args.output_root,
        benchmarks=args.benchmark,
        methods=args.method or PUBLIC_RESULT_METHODS,
    )
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        if not args.report.parent.is_dir():
            raise ValueError("report parent directory does not exist")
        if args.report.exists():
            raise FileExistsError("refusing to overwrite existing report")
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
