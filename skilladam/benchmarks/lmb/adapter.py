"""LMB implementation of the public benchmark adapter contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from skilladam.benchmarks.lmb.choices import (
    normalize_choices,
    resolve_correct_choice,
    shuffle_choices,
)
from skilladam.benchmarks.lmb.evaluation import (
    extract_answer,
    parse_choice_label,
)
from skilladam.benchmarks.lmb.gate import LMBAcceptanceGate
from skilladam.benchmarks.lmb.manifest import MANIFEST, load_cases
from skilladam.benchmarks.lmb.rollout import (
    build_messages,
    prompt_profile_for_method,
    question_from_payload,
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


class LMBAdapter:
    """Dependency-free LMB loader, prompt, evaluator, and gate."""

    manifest = MANIFEST

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)
        self._gate = LMBAcceptanceGate()

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
            raise ValueError(f"unsupported LMB method {method!r}")
        if split not in PUBLIC_SPLITS:
            raise ValueError(f"unsupported LMB split {split!r}")
        if method == "baseline" and isinstance(skill, str) and skill.strip():
            raise ValueError("LMB baseline cannot include a skill")
        case_split = case.metadata.get("split")
        if case_split is not None and case_split != split:
            raise ValueError(
                f"LMB case belongs to {case_split!r}, not {split!r} split"
            )

        choices = normalize_choices(case.payload.get("choices"))
        correct = resolve_correct_choice(case.reference, choices)
        shuffled, correct_label = shuffle_choices(
            choices,
            correct["label"],
            seed=seed,
            item_id=case.case_id,
        )
        evaluation_choices = tuple(dict(choice) for choice in shuffled)
        return RolloutRequest(
            case=case,
            method=method,
            split=split,
            skill=skill,
            seed=seed,
            metadata={
                "messages": build_messages(
                    question=question_from_payload(case.payload),
                    choices=evaluation_choices,
                    method=method,
                    skill=skill,
                ),
                "prompt_profile": prompt_profile_for_method(method),
                "output_protocol": "answer-tag-choice-label",
                "choice_shuffle_seed": seed,
                "evaluation_choices": evaluation_choices,
                "correct_label": correct_label,
            },
        )

    def parse_result(
        self,
        case: BenchmarkCase,
        raw_result: Mapping[str, Any],
    ) -> RolloutResult:
        choices, correct = _evaluation_context(case, raw_result)
        answer, raw_response = extract_answer(raw_result)
        predicted_label, predicted_text = parse_choice_label(answer, choices)
        usage = (
            (normalize_usage(raw_result["usage"]),)
            if raw_result.get("usage") is not None
            else ()
        )
        return RolloutResult(
            case_id=case.case_id,
            output=predicted_label,
            trajectory=_trajectory(raw_result.get("messages", ())),
            usage=usage,
            metadata={
                "correct_label": correct["label"],
                "correct_text": correct["text"],
                "predicted_text": predicted_text,
                "raw_answer": answer,
                "raw_response": raw_response,
                "n_turns": _non_negative_integer(
                    raw_result.get("n_turns", 0),
                    "n_turns",
                ),
            },
        )

    def evaluate(self, result: RolloutResult) -> MetricResult:
        prediction = result.output
        correct_label = result.metadata.get("correct_label")
        if not isinstance(prediction, str):
            raise ValueError("LMB prediction must be text")
        if not isinstance(correct_label, str) or not correct_label:
            raise ValueError("LMB result must include a correct label")
        score = float(prediction == correct_label)
        metrics = {"exact_match": score, "token_f1": score}
        return MetricResult(
            primary=score,
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


def _evaluation_context(
    case: BenchmarkCase,
    raw_result: Mapping[str, Any],
) -> tuple[tuple[dict[str, str], ...], dict[str, str]]:
    request_metadata = raw_result.get("request_metadata")
    if request_metadata is not None:
        if not isinstance(request_metadata, Mapping):
            raise ValueError("LMB request_metadata must be an object")
        choices = normalize_choices(
            request_metadata.get("evaluation_choices")
        )
        correct_label = request_metadata.get("correct_label")
        correct = resolve_correct_choice(correct_label, choices)
        return choices, correct

    choices = normalize_choices(case.payload.get("choices"))
    correct = resolve_correct_choice(case.reference, choices)
    return choices, correct


def _trajectory(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value in (None, ()):
        return ()
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise ValueError("LMB messages must be a sequence of objects")
    return tuple(dict(item) for item in value)


def _non_negative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"LMB {field_name} must be a non-negative integer")
    return value
