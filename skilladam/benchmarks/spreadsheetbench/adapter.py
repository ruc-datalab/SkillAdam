"""SpreadsheetBench implementation of the public adapter contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from numbers import Real
from pathlib import Path
from typing import Any

from skilladam.benchmarks.spreadsheetbench.evaluation import (
    score_cell_mapping,
)
from skilladam.benchmarks.spreadsheetbench.gate import (
    SpreadsheetBenchAcceptanceGate,
)
from skilladam.benchmarks.spreadsheetbench.manifest import (
    MANIFEST,
    load_cases,
)
from skilladam.benchmarks.spreadsheetbench.rollout import (
    build_messages,
    extract_python_code,
    prompt_profile_for_method,
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


class SpreadsheetBenchAdapter:
    """Offline loader, request builder, result parser, evaluator, and gate."""

    manifest = MANIFEST

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)
        self._gate = SpreadsheetBenchAcceptanceGate()

    def validate_dependencies(self) -> None:
        """Metadata, fixtures, and precomputed results need no extras.

        The execution runner calls ``load_execution_dependencies`` before
        it opens a workbook, keeping registry import and base tests clean.
        """

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
                f"unsupported SpreadsheetBench method {method!r}"
            )
        if split not in PUBLIC_SPLITS:
            raise ValueError(
                f"unsupported SpreadsheetBench split {split!r}"
            )
        if method == "baseline" and isinstance(skill, str) and skill.strip():
            raise ValueError(
                "SpreadsheetBench baseline cannot include a skill"
            )
        case_split = case.metadata.get("split")
        if case_split is not None and case_split != split:
            raise ValueError(
                f"SpreadsheetBench case belongs to {case_split!r}, "
                f"not {split!r} split"
            )
        return RolloutRequest(
            case=case,
            method=method,
            split=split,
            skill=skill,
            seed=seed,
            metadata={
                "messages": build_messages(
                    case,
                    method=method,
                    skill=skill,
                ),
                "prompt_profile": prompt_profile_for_method(method),
                "output_protocol": "python-fenced-xlsx",
                "input_path": case.payload.get("input_path"),
            },
        )

    def parse_result(
        self,
        case: BenchmarkCase,
        raw_result: Mapping[str, Any],
    ) -> RolloutResult:
        usage = _usage_records(raw_result)
        trajectory = _trajectory(raw_result.get("messages", ()))
        cells = raw_result.get("cells")
        if cells is not None:
            if not isinstance(cells, Mapping):
                raise ValueError(
                    "SpreadsheetBench cells result must be an object"
                )
            return RolloutResult(
                case_id=case.case_id,
                output=dict(cells),
                trajectory=trajectory,
                usage=usage,
                metadata={
                    "reference": case.reference,
                    "result_shape": "synthetic-cells",
                },
            )

        hard = _unit_metric(raw_result.get("hard"), "hard")
        per_cell = _unit_metric(
            raw_result.get("per_cell_pass_rate"),
            "per_cell_pass_rate",
        )
        exec_pass = _unit_metric(
            raw_result.get("exec_pass"),
            "exec_pass",
        )
        turns = _non_negative_integer(
            raw_result.get("n_turns"),
            "n_turns",
        )
        code = raw_result.get("code", "")
        if not isinstance(code, str):
            raise ValueError("SpreadsheetBench code must be text")
        return RolloutResult(
            case_id=case.case_id,
            output=extract_python_code(code),
            trajectory=trajectory,
            usage=usage,
            metadata={
                "hard": hard,
                "per_cell_pass_rate": per_cell,
                "exec_pass": exec_pass,
                "n_turns": turns,
                "n_cells_total": raw_result.get("n_cells_total"),
                "n_cells_match": raw_result.get("n_cells_match"),
                "fail_reason": raw_result.get("fail_reason", ""),
                "reference": case.reference,
                "result_shape": "rollout-result",
            },
        )

    def evaluate(self, result: RolloutResult) -> MetricResult:
        if result.metadata.get("result_shape") == "synthetic-cells":
            if not isinstance(result.output, Mapping):
                raise ValueError(
                    "SpreadsheetBench synthetic output must be a cell object"
                )
            reference = result.metadata.get("reference")
            if not isinstance(reference, Mapping):
                raise ValueError(
                    "SpreadsheetBench synthetic reference must be an object"
                )
            scores = score_cell_mapping(result.output, reference)
            hard = scores["hard"]
            per_cell = scores["per_cell_pass_rate"]
            exec_pass = 1.0
            turns = 1.0
        else:
            hard = _unit_metric(result.metadata.get("hard"), "hard")
            per_cell = _unit_metric(
                result.metadata.get("per_cell_pass_rate"),
                "per_cell_pass_rate",
            )
            exec_pass = _unit_metric(
                result.metadata.get("exec_pass"),
                "exec_pass",
            )
            turns = float(
                _non_negative_integer(
                    result.metadata.get("n_turns"),
                    "n_turns",
                )
            )
        metrics = {
            "hard": hard,
            "per_cell_pass_rate": per_cell,
            "exec_pass": exec_pass,
            "avg_turns": turns,
        }
        return MetricResult(
            primary=hard,
            metrics=metrics,
            case_metrics={result.case_id: metrics},
            sample_count=1,
        )

    def gate(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        return self._gate.judge(baseline, candidate)


def _trajectory(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value in (None, ()):
        return ()
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise ValueError(
            "SpreadsheetBench messages must be a sequence of objects"
        )
    return tuple(dict(item) for item in value)


def _usage_records(
    raw_result: Mapping[str, Any],
) -> tuple[Any, ...]:
    single = raw_result.get("usage")
    multiple = raw_result.get("usage_records")
    if single is not None and multiple is not None:
        raise ValueError(
            "SpreadsheetBench result cannot contain both usage and "
            "usage_records"
        )
    if multiple is not None:
        if (
            isinstance(multiple, (str, bytes))
            or not isinstance(multiple, Sequence)
            or not multiple
        ):
            raise ValueError(
                "SpreadsheetBench usage_records must be a non-empty sequence"
            )
        try:
            return tuple(normalize_usage(item) for item in multiple)
        except ValueError:
            raise ValueError(
                "SpreadsheetBench usage_records contain invalid usage"
            ) from None
    if single is None:
        return ()
    try:
        return (normalize_usage(single),)
    except ValueError:
        raise ValueError("SpreadsheetBench usage is invalid") from None


def _unit_metric(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(
            f"SpreadsheetBench {name} must be finite and within [0, 1]"
        )
    return float(value)


def _non_negative_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"SpreadsheetBench {name} must be a non-negative integer"
        )
    return value
