"""DocVQA implementation of the public benchmark adapter contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from numbers import Real
from pathlib import Path
from typing import Any

from skilladam.benchmarks.docvqa.evaluation import (
    HARD_MATCH_THRESHOLD,
    anls_score,
    extract_answer,
)
from skilladam.benchmarks.docvqa.gate import DocVQAAcceptanceGate
from skilladam.benchmarks.docvqa.manifest import MANIFEST, load_cases
from skilladam.benchmarks.docvqa.rollout import (
    build_messages,
    prompt_profile_for_method,
    sanitize_messages,
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


class DocVQAAdapter:
    """Dependency-free loader, multimodal prompt, evaluator, and gate."""

    manifest = MANIFEST

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)
        self._gate = DocVQAAcceptanceGate()

    def validate_dependencies(self) -> None:
        """Offline metadata, prompts, and saved results need no extras."""

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
            raise ValueError(f"unsupported DocVQA method {method!r}")
        if split not in PUBLIC_SPLITS:
            raise ValueError(f"unsupported DocVQA split {split!r}")
        if method == "baseline" and isinstance(skill, str) and skill.strip():
            raise ValueError("DocVQA baseline cannot include a skill")
        case_split = case.metadata.get("split")
        if case_split is not None and case_split != split:
            raise ValueError(
                f"DocVQA case belongs to {case_split!r}, "
                f"not {split!r} split"
            )
        messages = build_messages(
            case,
            method=method,
            skill=skill,
            image_root=self.data_root,
        )
        image_path = _image_path(self.data_root, case)
        return RolloutRequest(
            case=case,
            method=method,
            split=split,
            skill=skill,
            seed=seed,
            metadata={
                "messages": messages,
                "prompt_profile": prompt_profile_for_method(method),
                "output_protocol": "answer-tags",
                "image_path": (
                    str(image_path.resolve())
                    if image_path is not None
                    else None
                ),
                "image_detail": "auto",
                "max_turns": 1,
            },
        )

    def parse_result(
        self,
        case: BenchmarkCase,
        raw_result: Mapping[str, Any],
    ) -> RolloutResult:
        response = raw_result.get("response", "")
        if not isinstance(response, str):
            raise ValueError("DocVQA response must be text")
        raw_answer = raw_result.get(
            "predicted_answer",
            raw_result.get("answer"),
        )
        if raw_answer is not None and not isinstance(raw_answer, str):
            raise ValueError("DocVQA answer must be text")
        answer = (
            raw_answer.strip()
            if isinstance(raw_answer, str)
            else extract_answer(response)
        )
        usage = (
            (normalize_usage(raw_result["usage"]),)
            if raw_result.get("usage") is not None
            else ()
        )
        trajectory = _trajectory(raw_result.get("messages", ()))
        image_value = raw_result.get("image_path")
        image_basename = (
            Path(image_value).name
            if isinstance(image_value, str) and image_value
            else _case_image_basename(case)
        )
        sanitized = sanitize_messages(trajectory, image_basename)

        metadata: dict[str, Any] = {
            "gold_answers": list(_gold_answers(case.reference)),
            "question": case.payload.get("question", ""),
            "raw_response": response,
            "result_shape": "calculated",
        }
        if "hard" in raw_result:
            metadata.update(
                {
                    "hard": _binary_metric(
                        raw_result.get("hard"),
                        "hard",
                    ),
                    "soft": _unit_metric(
                        raw_result.get("soft"),
                        "soft",
                    ),
                    "n_turns": _non_negative_integer(
                        raw_result.get("n_turns"),
                        "n_turns",
                    ),
                    "result_shape": "precomputed",
                }
            )
        return RolloutResult(
            case_id=case.case_id,
            output=answer,
            trajectory=sanitized,
            usage=usage,
            metadata=metadata,
        )

    def evaluate(self, result: RolloutResult) -> MetricResult:
        if result.metadata.get("result_shape") == "precomputed":
            hard = _binary_metric(result.metadata.get("hard"), "hard")
            soft = _unit_metric(result.metadata.get("soft"), "soft")
        else:
            if not isinstance(result.output, str):
                raise ValueError("DocVQA prediction must be text")
            soft = anls_score(
                result.output,
                _gold_answers(result.metadata.get("gold_answers")),
            )
            hard = float(soft >= HARD_MATCH_THRESHOLD)
        metrics = {"hard": hard, "soft": soft}
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


def _image_path(root: Path, case: BenchmarkCase) -> Path | None:
    document = case.payload.get("document")
    if not isinstance(document, Mapping):
        return None
    absolute = document.get("image_path_abs")
    if isinstance(absolute, str) and absolute.strip():
        return Path(absolute)
    relative = document.get("image_path")
    if isinstance(relative, str) and relative.strip():
        return root / relative
    return None


def _case_image_basename(case: BenchmarkCase) -> str:
    image = _image_path(Path(), case)
    return image.name if image is not None else f"{case.case_id}.png"


def _trajectory(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value in (None, ()):
        return ()
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or any(not isinstance(message, Mapping) for message in value)
    ):
        raise ValueError(
            "DocVQA messages must be a sequence of objects"
        )
    return tuple(dict(message) for message in value)


def _gold_answers(value: Any) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or any(
            not isinstance(answer, str) or not answer.strip()
            for answer in value
        )
    ):
        raise ValueError(
            "DocVQA gold answers must be a non-empty string sequence"
        )
    return tuple(answer.strip() for answer in value)


def _unit_metric(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(
            f"DocVQA {name} must be finite and within [0, 1]"
        )
    return float(value)


def _binary_metric(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or float(value) not in (0.0, 1.0)
    ):
        raise ValueError(f"DocVQA {name} must be 0 or 1")
    return float(value)


def _non_negative_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"DocVQA {name} must be a non-negative integer"
        )
    return value
