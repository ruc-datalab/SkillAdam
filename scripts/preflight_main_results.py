#!/usr/bin/env python3
"""Read-only preflight for the complete seven-benchmark API matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skilladam.experiments import preflight_main_result_matrix


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate all data/config/runtime inputs and print the exact "
            "60-command paper main-result matrix. This never calls an API."
        )
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--allow-missing-secrets",
        action="store_true",
        help=(
            "Allow an offline structural audit to finish while reporting "
            "missing environment variable names."
        ),
    )
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = preflight_main_result_matrix(
        args.config,
        require_secrets=not args.allow_missing_secrets,
    )
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        if not args.report.parent.is_dir():
            raise ValueError("report parent directory does not exist")
        if args.report.exists():
            raise FileExistsError("refusing to overwrite existing report")
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["ready_for_api"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
