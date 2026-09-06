"""Deterministic, scope-aware benchmark case selection."""

from __future__ import annotations

from collections.abc import Sequence

from skilladam.benchmarks.deepplanning.manifest import SLICE_SPECS
from skilladam.types import BenchmarkCase


def select_cases(
    cases: Sequence[BenchmarkCase],
    *,
    benchmark: str,
    scope: str | None,
    case_ids: Sequence[str],
    limit: int | None,
    require_scope: bool = False,
) -> tuple[BenchmarkCase, ...]:
    """Select cases in source order without silently changing scope."""

    source = tuple(cases)
    if not source:
        raise ValueError("case selection requires at least one source case")
    if any(not isinstance(case, BenchmarkCase) for case in source):
        raise ValueError("source cases must be BenchmarkCase values")

    source_ids = tuple(_case_id(case) for case in source)
    duplicate_source = _first_duplicate(source_ids)
    if duplicate_source is not None:
        raise ValueError(
            f"duplicate source case ID {duplicate_source!r}"
        )

    requested = _requested_ids(case_ids)
    duplicate_requested = _first_duplicate(requested)
    if duplicate_requested is not None:
        raise ValueError(
            f"duplicate requested case ID {duplicate_requested!r}"
        )
    if requested and limit is not None:
        raise ValueError("case_ids and limit are mutually exclusive")
    if limit is not None and (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
    ):
        raise ValueError("limit must be a positive integer")

    if not isinstance(benchmark, str) or not benchmark.strip():
        raise ValueError("benchmark must be non-empty text")
    benchmark_name = benchmark.strip()
    selected_scope = _scope(scope)
    if benchmark_name != "deepplanning":
        if selected_scope is not None:
            raise ValueError(
                "scope is only supported for DeepPlanning"
            )
        eligible = source
    else:
        eligible = _deepplanning_cases(
            source,
            scope=selected_scope,
            require_scope=require_scope,
        )

    source_id_set = set(source_ids)
    unknown = tuple(
        case_id
        for case_id in requested
        if case_id not in source_id_set
    )
    if unknown:
        raise ValueError(f"unknown case IDs: {list(unknown)!r}")

    eligible_ids = {case.case_id for case in eligible}
    outside_scope = tuple(
        case_id
        for case_id in requested
        if case_id not in eligible_ids
    )
    if outside_scope:
        raise ValueError(
            f"requested case IDs are outside scope "
            f"{selected_scope!r}: {list(outside_scope)!r}"
        )

    if requested:
        requested_set = set(requested)
        selected = tuple(
            case for case in eligible if case.case_id in requested_set
        )
    else:
        selected = eligible
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise ValueError("case selection produced no cases")
    return selected


def _deepplanning_cases(
    cases: tuple[BenchmarkCase, ...],
    *,
    scope: str | None,
    require_scope: bool,
) -> tuple[BenchmarkCase, ...]:
    case_scopes = tuple(_case_scope(case) for case in cases)
    if scope is not None and scope not in SLICE_SPECS:
        raise ValueError(f"unknown DeepPlanning scope {scope!r}")
    if require_scope and scope is None:
        raise ValueError(
            "DeepPlanning optimization requires an explicit scope"
        )
    if scope is None:
        return cases
    return tuple(
        case
        for case, case_scope in zip(cases, case_scopes)
        if case_scope == scope
    )


def _case_scope(case: BenchmarkCase) -> str:
    value = case.metadata.get("slice", case.payload.get("slice"))
    if not isinstance(value, str) or value not in SLICE_SPECS:
        raise ValueError(
            f"DeepPlanning case {case.case_id!r} has invalid slice metadata"
        )
    return value


def _case_id(case: BenchmarkCase) -> str:
    value = case.case_id
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source case IDs must be non-empty text")
    return value


def _requested_ids(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("case_ids must be a sequence of IDs")
    result = tuple(values)
    if any(
        not isinstance(value, str) or not value.strip()
        for value in result
    ):
        raise ValueError("requested case IDs must be non-empty text")
    return result


def _scope(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("scope must be non-empty text or null")
    return value.strip()


def _first_duplicate(values: Sequence[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


__all__ = ["select_cases"]
