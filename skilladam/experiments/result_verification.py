"""Compare complete evaluation outputs with frozen paper references."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import math
from pathlib import Path
from typing import Any

from skilladam.experiments.main_results import load_main_result_profile


PUBLIC_RESULT_METHODS = ("baseline", "skillopt", "skilladam")
DEEPPLANNING_SCOPES = (
    "shopping_level1",
    "shopping_level2",
    "shopping_level3",
    "travel_en",
)


def verify_main_result_outputs(
    output_root: Path,
    *,
    benchmarks: Iterable[str] | None = None,
    methods: Iterable[str] = PUBLIC_RESULT_METHODS,
    max_delta_points: float | None = None,
) -> dict[str, Any]:
    """Validate coverage and report score deltas without rerunning models."""

    profile = load_main_result_profile()
    selected_benchmarks = tuple(
        profile.benchmarks if benchmarks is None else benchmarks
    )
    selected_methods = tuple(methods)
    if (
        not selected_benchmarks
        or len(set(selected_benchmarks)) != len(selected_benchmarks)
        or any(name not in profile.benchmarks for name in selected_benchmarks)
    ):
        raise ValueError("result verification benchmark selection is invalid")
    if (
        not selected_methods
        or len(set(selected_methods)) != len(selected_methods)
        or any(name not in PUBLIC_RESULT_METHODS for name in selected_methods)
    ):
        raise ValueError("result verification method selection is invalid")
    if max_delta_points is not None and (
        isinstance(max_delta_points, bool)
        or not isinstance(max_delta_points, (int, float))
        or not math.isfinite(float(max_delta_points))
        or max_delta_points < 0
    ):
        raise ValueError("max_delta_points must be finite and non-negative")

    rows: list[dict[str, Any]] = []
    for benchmark in selected_benchmarks:
        benchmark_profile = profile.benchmark(benchmark)
        for method in selected_methods:
            if benchmark == "deepplanning":
                measured, sample_count, scopes = _deepplanning_result(
                    Path(output_root),
                    method=method,
                    expected_counts=benchmark_profile.dataset[
                        "test_cases_by_scope"
                    ],
                    metric=str(benchmark_profile.evaluation["metric"]),
                )
                details: dict[str, Any] = {"scopes": scopes}
            else:
                path = (
                    Path(output_root)
                    / benchmark
                    / f"{method}_test"
                    / "metrics.json"
                )
                measured, sample_count = _read_metric(
                    path,
                    metric=str(benchmark_profile.evaluation["metric"]),
                )
                expected_count = int(
                    benchmark_profile.evaluation["test_cases"]
                )
                if sample_count != expected_count:
                    raise ValueError(
                        f"{benchmark}/{method} sample_count={sample_count}, "
                        f"expected {expected_count}"
                    )
                details = {}
            reference = float(
                benchmark_profile.evaluation[
                    "paper_results_percent"
                ][method]
            )
            measured_percent = measured * 100.0
            delta = measured_percent - reference
            within_tolerance = (
                None
                if max_delta_points is None
                else abs(delta) <= float(max_delta_points)
            )
            rows.append(
                {
                    "benchmark": benchmark,
                    "method": method,
                    "metric": benchmark_profile.evaluation["metric"],
                    "sample_count": sample_count,
                    "measured_percent": measured_percent,
                    "measured_rounded_1dp": round(measured_percent, 1),
                    "paper_reference_percent": reference,
                    "delta_points": delta,
                    "within_tolerance": within_tolerance,
                    **details,
                }
            )
    tolerance_passed = (
        None
        if max_delta_points is None
        else all(row["within_tolerance"] for row in rows)
    )
    return {
        "schema_version": 1,
        "profile_id": profile.profile_id,
        "comparison_semantics": (
            "configuration-aligned observed scores versus paper reference; "
            "generation is not expected to be byte-identical"
        ),
        "max_delta_points": max_delta_points,
        "tolerance_passed": tolerance_passed,
        "rows": rows,
    }


def _deepplanning_result(
    output_root: Path,
    *,
    method: str,
    expected_counts: Mapping[str, Any],
    metric: str,
) -> tuple[float, int, dict[str, Any]]:
    total = 0.0
    total_count = 0
    scope_rows: dict[str, Any] = {}
    for scope in DEEPPLANNING_SCOPES:
        path = (
            output_root
            / "deepplanning"
            / scope
            / f"{method}_test"
            / "metrics.json"
        )
        value, sample_count = _read_metric(path, metric=metric)
        expected = int(expected_counts[scope])
        if sample_count != expected:
            raise ValueError(
                f"deepplanning/{scope}/{method} "
                f"sample_count={sample_count}, expected {expected}"
            )
        total += value * sample_count
        total_count += sample_count
        scope_rows[scope] = {
            "sample_count": sample_count,
            "measured_percent": value * 100.0,
        }
    return total / total_count, total_count, scope_rows


def _read_metric(path: Path, *, metric: str) -> tuple[float, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"missing or invalid metrics file: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"metrics file must contain an object: {path}")
    overall = payload.get("overall")
    if not isinstance(overall, dict):
        raise ValueError(f"metrics file has no overall object: {path}")
    metrics = overall.get("metrics")
    if not isinstance(metrics, dict) or metric not in metrics:
        raise ValueError(f"metrics file has no {metric!r} metric: {path}")
    value = metrics[metric]
    count = overall.get("sample_count")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(f"metric {metric!r} is invalid: {path}")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 1
    ):
        raise ValueError(f"sample_count is invalid: {path}")
    return float(value), count


__all__ = [
    "DEEPPLANNING_SCOPES",
    "PUBLIC_RESULT_METHODS",
    "verify_main_result_outputs",
]
