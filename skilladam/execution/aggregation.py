"""Deterministic generic and DeepPlanning metric aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from numbers import Real
from types import MappingProxyType

from skilladam.benchmarks.deepplanning.evaluation import (
    aggregate_metric_results as aggregate_deepplanning_results,
)
from skilladam.types import MetricResult


@dataclass(frozen=True, slots=True)
class AggregationResult:
    """One overall result plus optional deterministic scope summaries."""

    overall: MetricResult
    by_scope: Mapping[str, MetricResult]

    def __post_init__(self) -> None:
        if not isinstance(self.overall, MetricResult):
            raise ValueError("overall must be a MetricResult")
        if not isinstance(self.by_scope, Mapping):
            raise ValueError("by_scope must be a mapping")
        if any(
            not isinstance(scope, str)
            or not scope
            or not isinstance(result, MetricResult)
            for scope, result in self.by_scope.items()
        ):
            raise ValueError("by_scope contains an invalid summary")
        object.__setattr__(
            self,
            "by_scope",
            MappingProxyType(dict(self.by_scope)),
        )


def aggregate_metric_results(
    results: Sequence[MetricResult],
) -> MetricResult:
    """Aggregate compatible metrics with sample-count weighting."""

    normalized = _validated_results(results, require_same_metrics=True)
    metric_names = tuple(sorted(normalized[0].metrics))
    total_samples = sum(result.sample_count for result in normalized)
    primary_total = sum(
        _finite(result.primary, "primary") * result.sample_count
        for result in normalized
    )
    metric_totals = {
        name: sum(
            _finite(result.metrics[name], name) * result.sample_count
            for result in normalized
        )
        for name in metric_names
    }
    case_metrics: dict[str, Mapping[str, float]] = {}
    for result in normalized:
        case_metrics.update(result.case_metrics)
    return MetricResult(
        primary=primary_total / total_samples,
        metrics={
            name: metric_totals[name] / total_samples
            for name in metric_names
        },
        case_metrics=case_metrics,
        sample_count=total_samples,
    )


def aggregate_benchmark_metrics(
    benchmark: str,
    results: Sequence[MetricResult],
    *,
    allow_mixed_deepplanning: bool = False,
) -> AggregationResult:
    """Aggregate one benchmark, preserving DeepPlanning scope semantics."""

    normalized = tuple(results)
    if benchmark != "deepplanning":
        return AggregationResult(
            overall=aggregate_metric_results(normalized),
            by_scope={},
        )

    _validated_results(normalized, require_same_metrics=False)
    grouped: dict[str, list[MetricResult]] = {}
    for result in normalized:
        scope = result.metadata.get("slice")
        if not isinstance(scope, str) or not scope:
            raise ValueError(
                "DeepPlanning aggregation requires slice metadata"
            )
        grouped.setdefault(scope, []).append(result)
    if len(grouped) > 1 and not allow_mixed_deepplanning:
        raise ValueError(
            "DeepPlanning gate aggregation cannot use mixed slices"
        )

    by_scope = {
        scope: aggregate_deepplanning_results(grouped[scope])
        for scope in sorted(grouped)
    }
    if len(by_scope) == 1:
        overall = next(iter(by_scope.values()))
    else:
        overall = _deepplanning_overall(normalized)
    return AggregationResult(overall=overall, by_scope=by_scope)


def _validated_results(
    results: Sequence[MetricResult],
    *,
    require_same_metrics: bool,
) -> tuple[MetricResult, ...]:
    normalized = tuple(results)
    if not normalized:
        raise ValueError("metric aggregation requires results")
    if any(not isinstance(result, MetricResult) for result in normalized):
        raise ValueError("aggregation values must be MetricResult records")

    expected_names = set(normalized[0].metrics)
    if not expected_names:
        raise ValueError("aggregate metrics must not be empty")
    seen_cases: set[str] = set()
    for result in normalized:
        if (
            isinstance(result.sample_count, bool)
            or not isinstance(result.sample_count, int)
            or result.sample_count < 1
        ):
            raise ValueError("sample_count must be a positive integer")
        if require_same_metrics and set(result.metrics) != expected_names:
            raise ValueError(
                "aggregate results must use the same metric fields"
            )
        _finite(result.primary, "primary")
        for name, value in result.metrics.items():
            _finite(value, str(name))
        if len(result.case_metrics) != result.sample_count:
            raise ValueError(
                "case metrics must exactly match sample_count"
            )
        for case_id, values in result.case_metrics.items():
            if not isinstance(case_id, str) or not case_id:
                raise ValueError("case metric IDs must be non-empty text")
            if case_id in seen_cases:
                raise ValueError(
                    f"duplicate case metric ID {case_id!r}"
                )
            seen_cases.add(case_id)
            if not isinstance(values, Mapping):
                raise ValueError("case metrics must be mappings")
            if set(values) != set(result.metrics):
                raise ValueError(
                    "case metric fields must match aggregate metric fields"
                )
            for name, value in values.items():
                _finite(value, str(name))
    return normalized


def _deepplanning_overall(
    results: tuple[MetricResult, ...],
) -> MetricResult:
    total_samples = sum(result.sample_count for result in results)
    totals = {"hard": 0.0, "soft": 0.0}
    cases: dict[str, Mapping[str, float]] = {}
    for result in results:
        for name in totals:
            if name not in result.metrics:
                raise ValueError(
                    "DeepPlanning all-slice metrics require hard and soft"
                )
            totals[name] += (
                _finite(result.metrics[name], name) * result.sample_count
            )
        for case_id, values in result.case_metrics.items():
            cases[case_id] = {
                name: _finite(values[name], name)
                for name in ("hard", "soft")
            }
    means = {
        name: value / total_samples
        for name, value in totals.items()
    }
    return MetricResult(
        primary=means["soft"],
        metrics=means,
        case_metrics=cases,
        sample_count=total_samples,
        metadata={
            "slices": tuple(
                sorted(
                    {
                        str(result.metadata["slice"])
                        for result in results
                    }
                )
            )
        },
    )


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"metric {field_name!r} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"metric {field_name!r} must be finite")
    return number


__all__ = [
    "AggregationResult",
    "aggregate_benchmark_metrics",
    "aggregate_metric_results",
]
