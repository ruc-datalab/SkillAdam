"""OfficeQA implementation of the public benchmark adapter contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from skilladam.benchmarks.officeqa.evaluation import (
    exact_match,
    token_f1,
)
from skilladam.benchmarks.officeqa.gate import (
    OfficeQAAcceptanceGate,
)
from skilladam.benchmarks.officeqa.manifest import MANIFEST, load_cases
from skilladam.benchmarks.officeqa.rollout import (
    TOOL_SCHEMAS,
    build_messages,
    extract_answer,
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
    UsageRecord,
)
from skilladam.usage import normalize_usage


class OfficeQAAdapter:
    """Dependency-free OfficeQA loader, prompt, evaluator, and gate."""

    manifest = MANIFEST

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)
        self._gate = OfficeQAAcceptanceGate()

    def validate_dependencies(self) -> None:
        """The public offline contract uses only the Python standard library."""

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
            raise ValueError(f"unsupported OfficeQA method {method!r}")
        if split not in PUBLIC_SPLITS:
            raise ValueError(f"unsupported OfficeQA split {split!r}")
        if method == "baseline" and isinstance(skill, str) and skill.strip():
            raise ValueError("OfficeQA baseline cannot include a skill")
        case_split = case.metadata.get("split")
        if case_split is not None and case_split != split:
            raise ValueError(
                f"OfficeQA case belongs to {case_split!r}, "
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
                "tools": TOOL_SCHEMAS,
                "prompt_profile": prompt_profile_for_method(method),
                "output_protocol": "tool-calls-then-answer-tags",
                "max_tool_turns": 24,
            },
        )

    def parse_result(
        self,
        case: BenchmarkCase,
        raw_result: Mapping[str, Any],
    ) -> RolloutResult:
        answer, raw_response = extract_answer(raw_result)
        usage = _usage_records(raw_result)
        trajectory = _trajectory(raw_result.get("messages", ()))
        return RolloutResult(
            case_id=case.case_id,
            output=answer,
            trajectory=trajectory,
            usage=usage,
            metadata={
                "gold_answers": _gold_answers(case.reference),
                "question": case.payload.get("question", ""),
                "raw_response": raw_response,
                "n_tool_calls": _non_negative_integer(
                    raw_result.get("n_tool_calls", 0),
                    "n_tool_calls",
                ),
                "n_turns": _non_negative_integer(
                    raw_result.get("n_turns", 0),
                    "n_turns",
                ),
                "termination_reason": _optional_text(
                    raw_result.get("termination_reason"),
                    "termination_reason",
                ),
                "fail_reason": _optional_text(
                    raw_result.get("fail_reason"),
                    "fail_reason",
                ),
            },
        )

    def evaluate(self, result: RolloutResult) -> MetricResult:
        prediction = result.output
        if not isinstance(prediction, str):
            raise ValueError("OfficeQA prediction must be text")
        references = _gold_answers(result.metadata.get("gold_answers"))
        em = max(exact_match(prediction, answer) for answer in references)
        f1 = max(token_f1(prediction, answer) for answer in references)
        metrics = {"em": em, "f1": f1}
        return MetricResult(
            primary=em,
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


def _gold_answers(value: Any) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(
            "OfficeQA reference answers must be a non-empty string sequence"
        )
    return tuple(item.strip() for item in value)


def _trajectory(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value in (None, ()):
        return ()
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise ValueError("OfficeQA messages must be a sequence of objects")
    return tuple(dict(item) for item in value)


def _usage_records(
    raw_result: Mapping[str, Any],
) -> tuple[UsageRecord, ...]:
    if "usage_records" in raw_result:
        values = raw_result["usage_records"]
        if (
            isinstance(values, (str, bytes))
            or not isinstance(values, Sequence)
        ):
            raise ValueError(
                "OfficeQA usage_records must be a sequence"
            )
        return tuple(normalize_usage(item) for item in values)
    if raw_result.get("usage") is not None:
        return (normalize_usage(raw_result["usage"]),)
    return ()


def _non_negative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"OfficeQA {field_name} must be a non-negative integer"
        )
    return value


def _optional_text(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"OfficeQA {field_name} must be text")
    return value.strip()
