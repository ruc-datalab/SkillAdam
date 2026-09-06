"""Deterministic method-to-method token and optional cost comparisons."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

from skilladam.cost.ledger import (
    UsageLedger,
    UsageLedgerEntry,
    aggregate_entries,
)
from skilladam.cost.pricing import (
    PriceBook,
    PromptWeightBook,
    estimate_cost,
    prompt_billing_equivalent_tokens,
)


METRICS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
    "requests",
)


@dataclass(frozen=True)
class ComparisonRow:
    """One metric row comparing two methods in the same scope."""

    benchmark: str
    stage: str
    metric: str
    left_method: str
    right_method: str
    left_value: int | Decimal | None
    right_value: int | Decimal | None
    delta_percent: Decimal | None


def build_comparison_rows(
    entries: Iterable[UsageLedgerEntry],
    *,
    left_method: str,
    right_method: str,
    price_book: PriceBook | None = None,
    prompt_weight_book: PromptWeightBook | None = None,
) -> list[ComparisonRow]:
    """Build rows grouped by benchmark and stage."""

    entry_list = list(entries)
    aggregated = aggregate_entries(
        entry_list,
        group_by=("method", "benchmark", "stage"),
    )
    by_scope = {
        item.key: item.usage
        for item in aggregated
    }
    scopes = sorted(
        {
            (benchmark, stage)
            for method, benchmark, stage in by_scope
            if method in {left_method, right_method}
        }
    )

    rows: list[ComparisonRow] = []
    for benchmark, stage in scopes:
        left = by_scope.get((left_method, benchmark, stage))
        right = by_scope.get((right_method, benchmark, stage))
        for metric in METRICS:
            left_value = _usage_metric(left, metric)
            right_value = _usage_metric(right, metric)
            rows.append(
                ComparisonRow(
                    benchmark=benchmark,
                    stage=stage,
                    metric=metric,
                    left_method=left_method,
                    right_method=right_method,
                    left_value=left_value,
                    right_value=right_value,
                    delta_percent=_delta_percent(
                        left_value,
                        right_value,
                    ),
                )
            )
        if prompt_weight_book is not None:
            left_equivalent = _scope_prompt_billing_equivalent(
                entry_list,
                method=left_method,
                benchmark=benchmark,
                stage=stage,
                weight_book=prompt_weight_book,
            )
            right_equivalent = _scope_prompt_billing_equivalent(
                entry_list,
                method=right_method,
                benchmark=benchmark,
                stage=stage,
                weight_book=prompt_weight_book,
            )
            rows.append(
                ComparisonRow(
                    benchmark=benchmark,
                    stage=stage,
                    metric="prompt_billing_equivalent_tokens",
                    left_method=left_method,
                    right_method=right_method,
                    left_value=left_equivalent,
                    right_value=right_equivalent,
                    delta_percent=_delta_percent(
                        left_equivalent,
                        right_equivalent,
                    ),
                )
            )
        if price_book is not None:
            left_cost = _scope_cost(
                entry_list,
                method=left_method,
                benchmark=benchmark,
                stage=stage,
                price_book=price_book,
            )
            right_cost = _scope_cost(
                entry_list,
                method=right_method,
                benchmark=benchmark,
                stage=stage,
                price_book=price_book,
            )
            rows.append(
                ComparisonRow(
                    benchmark=benchmark,
                    stage=stage,
                    metric="estimated_cost_usd",
                    left_method=left_method,
                    right_method=right_method,
                    left_value=left_cost,
                    right_value=right_cost,
                    delta_percent=_delta_percent(left_cost, right_cost),
                )
            )
    return rows


def write_comparison_csv(
    entries: Iterable[UsageLedgerEntry],
    output_path: Path,
    *,
    left_method: str,
    right_method: str,
    price_book: PriceBook | None = None,
    prompt_weight_book: PromptWeightBook | None = None,
) -> None:
    """Write a stable CSV suitable for byte-for-byte reproduction."""

    rows = build_comparison_rows(
        entries,
        left_method=left_method,
        right_method=right_method,
        price_book=price_book,
        prompt_weight_book=prompt_weight_book,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "benchmark",
                "stage",
                "metric",
                left_method,
                right_method,
                "delta_percent",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.benchmark,
                    row.stage,
                    row.metric,
                    _format_value(row.left_value),
                    _format_value(row.right_value),
                    (
                        f"{row.delta_percent}%"
                        if row.delta_percent is not None
                        else ""
                    ),
                ]
            )


def write_comparison_from_ledger(
    input_path: Path,
    output_path: Path,
    *,
    left_method: str,
    right_method: str,
    price_book: PriceBook | None = None,
    prompt_weight_book: PromptWeightBook | None = None,
) -> None:
    """Read a ledger and write a report without allowing self-overwrite."""

    write_comparison_from_ledgers(
        [input_path],
        output_path,
        left_method=left_method,
        right_method=right_method,
        price_book=price_book,
        prompt_weight_book=prompt_weight_book,
    )


def write_comparison_from_ledgers(
    input_paths: Iterable[Path],
    output_path: Path,
    *,
    left_method: str,
    right_method: str,
    price_book: PriceBook | None = None,
    prompt_weight_book: PromptWeightBook | None = None,
) -> None:
    """Combine distinct ledgers into one report without modifying inputs."""

    paths = tuple(Path(path) for path in input_paths)
    if not paths:
        raise ValueError("at least one input ledger is required")
    output_path = Path(output_path)
    seen: list[Path] = []
    for path in paths:
        _ensure_distinct_paths(path, output_path)
        for previous in seen:
            if (
                path.resolve(strict=True) == previous.resolve(strict=True)
                or os.path.samefile(path, previous)
            ):
                raise ValueError(
                    "duplicate input ledger paths are not allowed"
                )
        seen.append(path)

    entries = [
        entry
        for path in paths
        for entry in UsageLedger(path).read()
    ]
    write_comparison_csv(
        entries,
        output_path,
        left_method=left_method,
        right_method=right_method,
        price_book=price_book,
        prompt_weight_book=prompt_weight_book,
    )


def _ensure_distinct_paths(input_path: Path, output_path: Path) -> None:
    source = input_path.resolve(strict=True)
    destination = output_path.resolve(strict=False)
    if source == destination:
        raise ValueError(
            "input ledger and output report must use different paths"
        )
    if output_path.exists() and os.path.samefile(input_path, output_path):
        raise ValueError(
            "input ledger and output report must use different paths"
        )


def _usage_metric(usage: object, metric: str) -> int | None:
    if usage is None:
        return None
    return getattr(usage, metric)


def _delta_percent(
    left: int | Decimal | None,
    right: int | Decimal | None,
) -> Decimal | None:
    if left is None or right is None or right == 0:
        return None
    delta = (Decimal(left) - Decimal(right)) * 100 / Decimal(right)
    return delta.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _format_value(value: int | Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f") if isinstance(value, Decimal) else str(value)


def _scope_cost(
    entries: list[UsageLedgerEntry],
    *,
    method: str,
    benchmark: str,
    stage: str,
    price_book: PriceBook,
) -> Decimal | None:
    costs: list[Decimal] = []
    for entry in entries:
        if (
            entry.method != method
            or entry.benchmark != benchmark
            or entry.stage != stage
        ):
            continue
        pricing = price_book.models.get(entry.model)
        cost = estimate_cost(entry.usage, pricing)
        if cost is None:
            return None
        costs.append(cost)
    return sum(costs, Decimal(0)) if costs else None


def _scope_prompt_billing_equivalent(
    entries: list[UsageLedgerEntry],
    *,
    method: str,
    benchmark: str,
    stage: str,
    weight_book: PromptWeightBook,
) -> Decimal | None:
    values: list[Decimal] = []
    for entry in entries:
        if (
            entry.method != method
            or entry.benchmark != benchmark
            or entry.stage != stage
        ):
            continue
        weights = weight_book.models.get(entry.model)
        value = prompt_billing_equivalent_tokens(entry.usage, weights)
        if value is None:
            return None
        values.append(value)
    return sum(values, Decimal(0)) if values else None
