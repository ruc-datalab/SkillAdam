"""Provider-neutral, exact-coverage benchmark evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any

from skilladam.benchmarks.base import BenchmarkAdapter
from skilladam.execution.aggregation import (
    AggregationResult,
    aggregate_benchmark_metrics,
)
from skilladam.execution.contracts import (
    BackendContext,
    BatchExecution,
    ExecutionBackend,
    RawRollout,
)
from skilladam.execution.skills import SkillResolver
from skilladam.types import (
    BenchmarkCase,
    Method,
    MetricResult,
    PUBLIC_METHODS,
    PUBLIC_SPLITS,
    RolloutRequest,
    RolloutResult,
    Split,
    UsageRecord,
)


_SENSITIVE_PARTS = (
    "apikey",
    "authorization",
    "baseurl",
    "credential",
    "endpoint",
    "password",
    "secret",
    "token",
)


@dataclass(frozen=True, slots=True)
class CaseEvaluation:
    """One parsed rollout and its one-case metric result."""

    case_id: str
    result: RolloutResult
    metric: MetricResult

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise ValueError("case evaluation requires a case ID")
        if not isinstance(self.result, RolloutResult):
            raise ValueError("case evaluation result must be RolloutResult")
        if not isinstance(self.metric, MetricResult):
            raise ValueError("case evaluation metric must be MetricResult")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "output": _portable_value(self.result.output),
            "trajectory": [
                _sanitize_metadata(item)
                for item in self.result.trajectory
            ],
            "metadata": _sanitize_metadata(self.result.metadata),
            "metrics": _metric_dict(self.metric),
        }


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """In-memory evaluation result; no files are written here."""

    stage: str
    cases: tuple[CaseEvaluation, ...]
    metrics: AggregationResult
    usage: tuple[UsageRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.stage, str) or not self.stage.strip():
            raise ValueError("evaluation stage must be non-empty text")
        cases = tuple(self.cases)
        usage = tuple(self.usage)
        if not cases or any(
            not isinstance(item, CaseEvaluation) for item in cases
        ):
            raise ValueError("evaluation cases must not be empty")
        if not isinstance(self.metrics, AggregationResult):
            raise ValueError("evaluation metrics are invalid")
        if any(not isinstance(item, UsageRecord) for item in usage):
            raise ValueError("evaluation usage must contain UsageRecord values")
        object.__setattr__(self, "cases", cases)
        object.__setattr__(self, "usage", usage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "results": [item.to_dict() for item in self.cases],
            "metrics": {
                "overall": _metric_dict(self.metrics.overall),
                "by_scope": {
                    scope: _metric_dict(result)
                    for scope, result in self.metrics.by_scope.items()
                },
            },
            "usage": [_usage_dict(item) for item in self.usage],
        }


class EvaluationRunner:
    """Execute, parse, and aggregate one exact ordered case batch."""

    def evaluate(
        self,
        *,
        adapter: BenchmarkAdapter,
        cases: Sequence[BenchmarkCase],
        method: Method,
        split: Split,
        skill_resolver: SkillResolver,
        backend: ExecutionBackend,
        context: BackendContext,
        stage: str,
    ) -> EvaluationResult:
        selected = _selected_cases(cases)
        if method not in PUBLIC_METHODS:
            raise ValueError(f"unsupported evaluation method {method!r}")
        if split not in PUBLIC_SPLITS:
            raise ValueError(f"unsupported evaluation split {split!r}")
        if not isinstance(context, BackendContext):
            raise ValueError("evaluation context is invalid")
        if (
            context.method != method
            or context.benchmark != adapter.manifest.name
        ):
            raise ValueError(
                "evaluation context does not match method and benchmark"
            )
        if not isinstance(skill_resolver, SkillResolver):
            raise ValueError("evaluation requires a SkillResolver")
        if not isinstance(stage, str) or not stage.strip():
            raise ValueError("evaluation stage must be non-empty text")

        requests = tuple(
            self._request(
                adapter=adapter,
                case=case,
                method=method,
                split=split,
                skill_resolver=skill_resolver,
                context=context,
            )
            for case in selected
        )
        raw_values = backend.execute_batch(
            BatchExecution(
                requests=requests,
                context=context,
                stage=stage,
            )
        )
        ordered = _exact_results(selected, raw_values)

        evaluated: list[CaseEvaluation] = []
        usage: list[UsageRecord] = []
        metrics: list[MetricResult] = []
        for case, raw in zip(selected, ordered):
            parsed = adapter.parse_result(case, raw.result)
            if not isinstance(parsed, RolloutResult):
                raise ValueError(
                    "adapter parse_result must return RolloutResult"
                )
            if parsed.case_id != case.case_id:
                raise ValueError(
                    "adapter parsed result case ID does not match request"
                )
            metric = adapter.evaluate(parsed)
            if not isinstance(metric, MetricResult):
                raise ValueError(
                    "adapter evaluate must return MetricResult"
                )
            if (
                metric.sample_count != 1
                or tuple(metric.case_metrics) != (case.case_id,)
            ):
                raise ValueError(
                    "adapter one-case metrics must contain the request "
                    "case ID exactly once"
                )
            evaluated.append(
                CaseEvaluation(
                    case_id=case.case_id,
                    result=parsed,
                    metric=metric,
                )
            )
            metrics.append(metric)
            usage.extend(parsed.usage)

        scopes = {
            metric.metadata.get("slice")
            for metric in metrics
            if metric.metadata.get("slice") is not None
        }
        aggregate = aggregate_benchmark_metrics(
            adapter.manifest.name,
            metrics,
            allow_mixed_deepplanning=len(scopes) > 1,
        )
        return EvaluationResult(
            stage=stage.strip(),
            cases=tuple(evaluated),
            metrics=aggregate,
            usage=tuple(usage),
        )

    def _request(
        self,
        *,
        adapter: BenchmarkAdapter,
        case: BenchmarkCase,
        method: Method,
        split: Split,
        skill_resolver: SkillResolver,
        context: BackendContext,
    ) -> RolloutRequest:
        resolved = skill_resolver.resolve(case)
        if method == "baseline" and resolved is not None:
            raise ValueError("baseline evaluation cannot include a skill")
        if method != "baseline" and resolved is None:
            raise ValueError(f"{method} evaluation requires a skill")
        request = adapter.build_rollout_request(
            case,
            method=method,
            split=split,
            skill=None if resolved is None else resolved.text,
            seed=context.seed,
        )
        if not isinstance(request, RolloutRequest):
            raise ValueError(
                "adapter must build a RolloutRequest"
            )
        if request.case.case_id != case.case_id:
            raise ValueError("rollout request case ID drift")
        if request.method != method or request.method != context.method:
            raise ValueError("rollout request method drift")
        if request.split != split:
            raise ValueError("rollout request split drift")
        return request


def _selected_cases(
    cases: Sequence[BenchmarkCase],
) -> tuple[BenchmarkCase, ...]:
    selected = tuple(cases)
    if not selected or any(
        not isinstance(case, BenchmarkCase) for case in selected
    ):
        raise ValueError("evaluation requires BenchmarkCase values")
    ids = tuple(case.case_id for case in selected)
    if any(not isinstance(case_id, str) or not case_id for case_id in ids):
        raise ValueError("evaluation case IDs must be non-empty")
    if len(set(ids)) != len(ids):
        raise ValueError("evaluation case IDs must be unique")
    return selected


def _exact_results(
    cases: tuple[BenchmarkCase, ...],
    raw_values: object,
) -> tuple[RawRollout, ...]:
    if (
        isinstance(raw_values, (str, bytes))
        or not isinstance(raw_values, Sequence)
    ):
        raise ValueError("backend results must be a sequence")
    values = tuple(raw_values)
    if any(not isinstance(value, RawRollout) for value in values):
        raise ValueError("backend results must contain RawRollout values")
    if any(
        not isinstance(value.case_id, str) or not value.case_id.strip()
        for value in values
    ):
        raise ValueError("backend returned a blank case ID")
    if any(not isinstance(value.result, Mapping) for value in values):
        raise ValueError("backend raw results must be mappings")
    ids = tuple(value.case_id for value in values)
    if len(set(ids)) != len(ids):
        raise ValueError("backend returned duplicate case IDs")
    expected = tuple(case.case_id for case in cases)
    expected_set = set(expected)
    actual_set = set(ids)
    missing = tuple(
        case_id for case_id in expected if case_id not in actual_set
    )
    extra = tuple(
        case_id for case_id in ids if case_id not in expected_set
    )
    if missing or extra:
        raise ValueError(
            "backend result coverage mismatch: "
            f"missing={list(missing)!r}, extra={list(extra)!r}"
        )
    by_id = {value.case_id: value for value in values}
    return tuple(by_id[case_id] for case_id in expected)


def _sanitize_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, item in value.items():
        if not isinstance(raw_key, str):
            raise ValueError("evaluation mapping keys must be strings")
        key = raw_key
        normalized = re.sub(r"[^a-z0-9]+", "", key.casefold())
        if (
            any(part in normalized for part in _SENSITIVE_PARTS)
            or normalized.endswith("path")
        ):
            continue
        if isinstance(item, str) and _is_absolute_path(item):
            continue
        if isinstance(item, Mapping):
            result[key] = _sanitize_metadata(item)
        elif isinstance(item, Sequence) and not isinstance(
            item,
            (str, bytes, bytearray),
        ):
            entries: list[Any] = []
            for entry in item:
                if isinstance(entry, str) and _is_absolute_path(entry):
                    continue
                entries.append(
                    _sanitize_metadata(entry)
                    if isinstance(entry, Mapping)
                    else _portable_value(entry)
                )
            result[key] = entries
        else:
            result[key] = _portable_value(item)
    return result


def _portable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    "evaluation mapping keys must be strings"
                )
            result[key] = _portable_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_portable_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("evaluation result contains a non-portable value")


def _is_absolute_path(value: str) -> bool:
    return (
        PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    )


def _metric_dict(result: MetricResult) -> dict[str, Any]:
    return {
        "primary": result.primary,
        "metrics": dict(result.metrics),
        "case_metrics": {
            case_id: dict(values)
            for case_id, values in result.case_metrics.items()
        },
        "sample_count": result.sample_count,
        "metadata": _sanitize_metadata(result.metadata),
    }


def _usage_dict(usage: UsageRecord) -> dict[str, Any]:
    return {
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "cache_creation_input_tokens": (
            usage.cache_creation_input_tokens
        ),
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "total_tokens": usage.total_tokens,
        "requests": usage.requests,
        "cost_usd": usage.cost_usd,
        "metadata": _sanitize_metadata(usage.metadata),
    }


__all__ = [
    "CaseEvaluation",
    "EvaluationResult",
    "EvaluationRunner",
]
