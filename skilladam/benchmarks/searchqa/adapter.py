"""SearchQA implementation of the public benchmark adapter contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from skilladam.benchmarks.searchqa.evaluation import (
    best_token_f1,
    exact_match,
)
from skilladam.benchmarks.searchqa.gate import SearchQAAcceptanceGate
from skilladam.benchmarks.searchqa.manifest import MANIFEST, load_cases
from skilladam.benchmarks.searchqa.rollout import (
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
)
from skilladam.usage import normalize_usage


class SearchQAAdapter:
    """Dependency-free SearchQA loader, prompt, evaluator, and gate."""

    manifest = MANIFEST

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)
        self._gate = SearchQAAcceptanceGate()

    def validate_dependencies(self) -> None:
        """SearchQA's public manifest and evaluator need no extra packages."""

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
            raise ValueError(f"unsupported SearchQA method {method!r}")
        if split not in PUBLIC_SPLITS:
            raise ValueError(f"unsupported SearchQA split {split!r}")
        if method == "baseline" and isinstance(skill, str) and skill.strip():
            raise ValueError("SearchQA baseline cannot include a skill")
        case_split = case.metadata.get("split")
        if case_split is not None and case_split != split:
            raise ValueError(
                f"SearchQA case belongs to {case_split!r}, not {split!r} split"
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
                "output_protocol": "searchqa-single-turn-answer",
            },
        )

    def parse_result(
        self,
        case: BenchmarkCase,
        raw_result: Mapping[str, Any],
    ) -> RolloutResult:
        answer, raw_response = extract_answer(raw_result)
        usage = (
            (normalize_usage(raw_result["usage"]),)
            if raw_result.get("usage") is not None
            else ()
        )
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
            },
        )

    def evaluate(self, result: RolloutResult) -> MetricResult:
        prediction = result.output
        if not isinstance(prediction, str):
            raise ValueError("SearchQA prediction must be text")
        gold_answers = _gold_answers(result.metadata.get("gold_answers"))
        em = exact_match(prediction, gold_answers)
        f1 = best_token_f1(prediction, gold_answers)
        metrics = {
            "exact_match": em,
            "token_f1": f1,
        }
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
            "SearchQA reference answers must be a non-empty string sequence"
        )
    return tuple(value)


def _trajectory(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value in (None, ()):
        return ()
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise ValueError("SearchQA messages must be a sequence of objects")
    return tuple(dict(item) for item in value)
