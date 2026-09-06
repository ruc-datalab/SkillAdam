"""DeepPlanning implementation of the public benchmark contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from skilladam.benchmarks.deepplanning.evaluation import (
    metric_result,
    parse_evaluator_metrics,
)
from skilladam.benchmarks.deepplanning.gate import (
    ShoppingAcceptanceGate,
    TravelAcceptanceGate,
)
from skilladam.benchmarks.deepplanning.manifest import MANIFEST, load_cases
from skilladam.benchmarks.deepplanning.rollout import (
    build_bootstrap_metadata,
)
from skilladam.types import (
    BenchmarkCase,
    GateDecision,
    Method,
    MetricResult,
    PUBLIC_METHODS,
    PUBLIC_SPLITS,
    RolloutRequest,
    RolloutResult,
    Split,
)
from skilladam.usage import normalize_usage


class DeepPlanningAdapter:
    """Offline adapter plus an optional external execution boundary."""

    manifest = MANIFEST

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)

    def validate_dependencies(self) -> None:
        """Offline loading and result evaluation have no dependency."""

    def load_cases(self, split: Split) -> tuple[BenchmarkCase, ...]:
        return load_cases(self.data_root, split)

    def build_rollout_request(
        self,
        case: BenchmarkCase,
        *,
        method: Method,
        split: Split,
        skill: str | None,
        seed: int,
    ) -> RolloutRequest:
        if method not in PUBLIC_METHODS:
            raise ValueError(
                f"unsupported DeepPlanning method {method!r}"
            )
        if split not in PUBLIC_SPLITS:
            raise ValueError(f"unsupported DeepPlanning split {split!r}")
        if method == "baseline" and isinstance(skill, str) and skill.strip():
            raise ValueError("DeepPlanning baseline cannot include a skill")
        case_split = case.metadata.get("split")
        if case_split is not None and case_split != split:
            raise ValueError(
                f"DeepPlanning case belongs to {case_split!r}, "
                f"not {split!r} split"
            )
        return RolloutRequest(
            case=case,
            method=method,
            split=split,
            skill=skill,
            seed=seed,
            metadata=build_bootstrap_metadata(case, method=method),
        )

    def parse_result(
        self,
        case: BenchmarkCase,
        raw_result: Mapping[str, Any],
    ) -> RolloutResult:
        if not isinstance(raw_result, Mapping):
            raise ValueError("DeepPlanning raw result must be an object")
        metrics = parse_evaluator_metrics(case, raw_result)
        usage = (
            (normalize_usage(raw_result["usage"]),)
            if raw_result.get("usage") is not None
            else ()
        )
        output = raw_result.get("output", raw_result.get("plan"))
        return RolloutResult(
            case_id=case.case_id,
            output=output,
            trajectory=_portable_trajectory(
                raw_result.get("messages", ())
            ),
            usage=usage,
            metadata={
                "metrics": metrics,
                "domain": _case_text(case, "domain"),
                "slice": _case_text(case, "slice"),
            },
        )

    def evaluate(self, result: RolloutResult) -> MetricResult:
        raw_metrics = result.metadata.get("metrics")
        if not isinstance(raw_metrics, Mapping):
            raise ValueError(
                "DeepPlanning result must include evaluator metrics"
            )
        metrics = {
            str(name): float(value)
            for name, value in raw_metrics.items()
        }
        return metric_result(
            case_id=result.case_id,
            domain=_metadata_text(result.metadata, "domain"),
            slice_id=_metadata_text(result.metadata, "slice"),
            metrics=metrics,
        )

    def gate(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        baseline_domain = baseline.metadata.get("domain")
        candidate_domain = candidate.metadata.get("domain")
        if baseline_domain is None and candidate_domain is None:
            return _primary_gate(baseline, candidate)
        if baseline_domain != candidate_domain:
            raise ValueError(
                "DeepPlanning gate cannot compare different domains"
            )
        baseline_slice = baseline.metadata.get("slice")
        candidate_slice = candidate.metadata.get("slice")
        if baseline_slice != candidate_slice:
            raise ValueError(
                "DeepPlanning gate cannot compare different slices"
            )
        if baseline_domain == "shopping":
            return ShoppingAcceptanceGate().judge(baseline, candidate)
        if baseline_domain == "travel":
            return TravelAcceptanceGate().judge(baseline, candidate)
        raise ValueError("DeepPlanning gate requires domain metadata")


def _portable_trajectory(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value in (None, ()):
        return ()
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise ValueError(
            "DeepPlanning messages must be a sequence of objects"
        )
    return tuple(_sanitize_mapping(item) for item in value)


def _sanitize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        lowered = key.casefold()
        if (
            lowered.endswith("_path")
            or "endpoint" in lowered
            or "api_key" in lowered
            or "secret" in lowered
            or "base_url" in lowered
        ):
            continue
        if isinstance(raw_value, Mapping):
            sanitized[key] = _sanitize_mapping(raw_value)
        elif (
            isinstance(raw_value, Sequence)
            and not isinstance(raw_value, (str, bytes))
        ):
            sanitized[key] = [
                _sanitize_mapping(item)
                if isinstance(item, Mapping)
                else item
                for item in raw_value
            ]
        else:
            sanitized[key] = raw_value
    return sanitized


def _case_text(case: BenchmarkCase, field_name: str) -> str:
    value = case.metadata.get(field_name, case.payload.get(field_name))
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"DeepPlanning case is missing {field_name}"
        )
    return value


def _metadata_text(
    metadata: Mapping[str, Any],
    field_name: str,
) -> str:
    value = metadata.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"DeepPlanning result is missing {field_name}"
        )
    return value


def _primary_gate(
    baseline: MetricResult,
    candidate: MetricResult,
) -> GateDecision:
    if baseline.sample_count != candidate.sample_count:
        raise ValueError(
            "DeepPlanning baseline and candidate must cover the same cases"
        )
    if set(baseline.case_metrics) != set(candidate.case_metrics):
        raise ValueError(
            "DeepPlanning baseline and candidate must cover the same cases"
        )
    gain = float(candidate.primary) - float(baseline.primary)
    return GateDecision(
        accepted=gain > 0.0,
        reason=f"primary_gain={gain:+.3f}",
        baseline=baseline,
        candidate=candidate,
        metadata={"gains": {"primary": gain}},
    )
