"""DeepPlanning saved-result parsing and shared metric aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from numbers import Real
from typing import Any

from skilladam.types import BenchmarkCase, MetricResult


_SHOPPING_REQUIRED = (
    "case_score",
    "matched_count",
    "expected_count",
    "extra_products_count",
    "coupon_score",
)
_TRAVEL_REQUIRED = (
    "case_acc",
    "composite_score",
    "commonsense_weighted_score",
    "personalized_score",
)


def parse_evaluator_metrics(
    case: BenchmarkCase,
    raw_result: Mapping[str, Any],
) -> dict[str, float]:
    """Normalize one official or synthetic evaluator result."""

    domain = _case_domain(case)
    raw_metrics = _find_metric_mapping(raw_result)
    if raw_metrics is None:
        return _synthetic_metrics(case, raw_result)
    if domain == "shopping":
        return _shopping_metrics(raw_metrics)
    return _travel_metrics(raw_metrics)


def metric_result(
    *,
    case_id: str,
    domain: str,
    slice_id: str,
    metrics: Mapping[str, float],
) -> MetricResult:
    """Build the public one-case metric shape."""

    normalized = dict(metrics)
    return MetricResult(
        primary=normalized["soft"],
        metrics=normalized,
        case_metrics={case_id: normalized},
        sample_count=1,
        metadata={"domain": domain, "slice": slice_id},
    )


def aggregate_metric_results(
    results: Sequence[MetricResult],
) -> MetricResult:
    """Aggregate one non-empty, single-slice result sequence."""

    if not results:
        raise ValueError("DeepPlanning aggregation requires results")
    first = results[0]
    domain = first.metadata.get("domain")
    slice_id = first.metadata.get("slice")
    if domain not in {"shopping", "travel"} or not isinstance(
        slice_id,
        str,
    ):
        raise ValueError(
            "DeepPlanning results require domain and slice metadata"
        )
    metric_names = set(first.metrics)
    merged_cases: dict[str, Mapping[str, float]] = {}
    totals = {name: 0.0 for name in metric_names}
    total_samples = 0
    for result in results:
        if (
            result.metadata.get("domain") != domain
            or result.metadata.get("slice") != slice_id
        ):
            raise ValueError(
                "DeepPlanning aggregation cannot mix domains or slices"
            )
        if set(result.metrics) != metric_names:
            raise ValueError(
                "DeepPlanning aggregate metrics must use the same fields"
            )
        if result.sample_count < 1:
            raise ValueError(
                "DeepPlanning sample_count must be at least one"
            )
        if len(result.case_metrics) != result.sample_count:
            raise ValueError(
                "DeepPlanning case metrics must match sample_count"
            )
        overlap = set(merged_cases) & set(result.case_metrics)
        if overlap:
            raise ValueError(
                "duplicate DeepPlanning case IDs in aggregation"
            )
        merged_cases.update(result.case_metrics)
        for name in metric_names:
            totals[name] += _finite_number(
                result.metrics[name],
                name,
            ) * result.sample_count
        total_samples += result.sample_count
    means = {
        name: totals[name] / total_samples for name in sorted(metric_names)
    }
    return MetricResult(
        primary=means["soft"],
        metrics=means,
        case_metrics=merged_cases,
        sample_count=total_samples,
        metadata={"domain": domain, "slice": slice_id},
    )


def _find_metric_mapping(
    raw_result: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    direct = raw_result.get("metrics")
    if direct is not None:
        if not isinstance(direct, Mapping):
            raise ValueError("DeepPlanning metrics must be an object")
        return direct
    evaluation = raw_result.get("evaluation")
    if evaluation is not None:
        if not isinstance(evaluation, Mapping):
            raise ValueError("DeepPlanning evaluation must be an object")
        scores = evaluation.get("scores")
        if scores is not None:
            if not isinstance(scores, Mapping):
                raise ValueError(
                    "DeepPlanning evaluation scores must be an object"
                )
            return scores
        return evaluation
    return None


def _shopping_metrics(raw: Mapping[str, Any]) -> dict[str, float]:
    _require_fields(raw, _SHOPPING_REQUIRED, "shopping")
    soft_name = "score" if "score" in raw else "match_rate"
    if soft_name not in raw:
        raise ValueError(
            "missing required shopping metric: score or match_rate"
        )
    result = {
        "hard": _unit_score(raw["case_score"], "case_score"),
        "soft": _unit_score(raw[soft_name], soft_name),
        "case_score": _unit_score(raw["case_score"], "case_score"),
    }
    if "score" in raw:
        result["score"] = _unit_score(raw["score"], "score")
    if "match_rate" in raw:
        result["match_rate"] = _unit_score(
            raw["match_rate"],
            "match_rate",
        )
    else:
        result["match_rate"] = result["soft"]
    result.update(
        {
            "matched_count": _count(
                raw["matched_count"],
                "matched_count",
            ),
            "expected_count": _count(
                raw["expected_count"],
                "expected_count",
            ),
            "extra_products_count": _count(
                raw["extra_products_count"],
                "extra_products_count",
            ),
            "coupon_score": _unit_score(
                raw["coupon_score"],
                "coupon_score",
            ),
        }
    )
    return result


def _travel_metrics(raw: Mapping[str, Any]) -> dict[str, float]:
    _require_fields(raw, _TRAVEL_REQUIRED, "travel")
    return {
        "hard": _unit_score(raw["case_acc"], "case_acc"),
        "soft": _unit_score(
            raw["composite_score"],
            "composite_score",
        ),
        "case_acc": _unit_score(raw["case_acc"], "case_acc"),
        "composite_score": _unit_score(
            raw["composite_score"],
            "composite_score",
        ),
        "commonsense_weighted_score": _unit_score(
            raw["commonsense_weighted_score"],
            "commonsense_weighted_score",
        ),
        "personalized_score": _unit_score(
            raw["personalized_score"],
            "personalized_score",
        ),
    }


def _synthetic_metrics(
    case: BenchmarkCase,
    raw_result: Mapping[str, Any],
) -> dict[str, float]:
    plan = raw_result.get("plan")
    reference = case.reference
    if (
        isinstance(plan, (str, bytes))
        or not isinstance(plan, Sequence)
        or isinstance(reference, (str, bytes))
        or not isinstance(reference, Sequence)
    ):
        raise ValueError(
            "DeepPlanning synthetic result requires plan/reference arrays"
        )
    predicted = tuple(_normalized_text(item) for item in plan)
    expected = tuple(_normalized_text(item) for item in reference)
    success = float(predicted == expected)
    if _case_domain(case) == "shopping":
        matched = sum(
            min(predicted.count(item), expected.count(item))
            for item in set(expected)
        )
        match_rate = (
            matched / len(expected) if expected else float(not predicted)
        )
        extra = max(0, len(predicted) - matched)
        return {
            "hard": success,
            "soft": success,
            "case_score": success,
            "score": success,
            "match_rate": float(match_rate),
            "matched_count": float(matched),
            "expected_count": float(len(expected)),
            "extra_products_count": float(extra),
            "coupon_score": success,
        }
    return {
        "hard": success,
        "soft": success,
        "case_acc": success,
        "composite_score": success,
        "commonsense_weighted_score": success,
        "personalized_score": success,
    }


def _case_domain(case: BenchmarkCase) -> str:
    domain = case.metadata.get("domain", case.payload.get("domain"))
    if domain not in {"shopping", "travel"}:
        raise ValueError("DeepPlanning case has invalid domain")
    return str(domain)


def _require_fields(
    raw: Mapping[str, Any],
    fields: tuple[str, ...],
    label: str,
) -> None:
    missing = [field for field in fields if field not in raw]
    if missing:
        raise ValueError(
            f"missing required {label} metrics: {', '.join(missing)}"
        )


def _unit_score(value: Any, name: str) -> float:
    score = _finite_number(value, name)
    if not 0.0 <= score <= 1.0:
        raise ValueError(
            f"DeepPlanning metric {name} must be in range [0, 1]"
        )
    return score


def _count(value: Any, name: str) -> float:
    number = _finite_number(value, name)
    if number < 0 or not number.is_integer():
        raise ValueError(
            f"DeepPlanning metric {name} must be a non-negative integer"
        )
    return number


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"DeepPlanning metric {name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"DeepPlanning metric {name} must be finite")
    return number


def _normalized_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "DeepPlanning synthetic plan entries must be non-empty text"
        )
    return " ".join(value.casefold().split())
