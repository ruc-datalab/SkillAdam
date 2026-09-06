"""Reproduce a method cost-comparison CSV from a local usage ledger."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skilladam.cost.pricing import PriceBook, PromptWeightBook
from skilladam.cost.report import write_comparison_from_ledgers
from skilladam.types import PUBLIC_METHODS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a deterministic SkillAdam versus SkillOpt token/cost "
            "table from a provider-neutral JSONL ledger."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        action="append",
        help="Repeat for method ledgers stored in separate run directories.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pricing", type=Path)
    parser.add_argument(
        "--prompt-weights",
        type=Path,
        help=(
            "Optional explicit cache weighting JSON for billing-equivalent "
            "prompt tokens."
        ),
    )
    parser.add_argument(
        "--left-method",
        choices=PUBLIC_METHODS,
        default="skilladam",
    )
    parser.add_argument(
        "--right-method",
        choices=PUBLIC_METHODS,
        default="skillopt",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    price_book = PriceBook.load(args.pricing) if args.pricing else None
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


if __name__ == "__main__":
    raise SystemExit(main())
