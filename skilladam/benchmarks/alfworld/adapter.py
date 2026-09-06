"""ALFWorld implementation of the public benchmark adapter contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from numbers import Real
from pathlib import Path
from typing import Any

from skilladam.benchmarks.alfworld.evaluation import (
    action_exact_match,
    extract_synthetic_action,
)
from skilladam.benchmarks.alfworld.gate import (
    ALFWorldAcceptanceGate,
)
from skilladam.benchmarks.alfworld.manifest import MANIFEST, load_cases
from skilladam.benchmarks.alfworld.rollout import (
    build_step_messages,
    normalize_action,
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


class ALFWorldAdapter:
    """Offline loader, step request, evaluator, and historical gate."""

    manifest = MANIFEST

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)
        self._gate = ALFWorldAcceptanceGate()

    def validate_dependencies(self) -> None:
        """Metadata, fixtures, and saved results need no optional import.

        The execution runner will import the upstream ``alfworld`` package
        only when it starts an environment.
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
            raise ValueError(f"unsupported ALFWorld method {method!r}")
        if split not in PUBLIC_SPLITS:
            raise ValueError(f"unsupported ALFWorld split {split!r}")
        if method == "baseline" and isinstance(skill, str) and skill.strip():
            raise ValueError("ALFWorld baseline cannot include a skill")
        case_split = case.metadata.get("split")
        if case_split is not None and case_split != split:
            raise ValueError(
                f"ALFWorld case belongs to {case_split!r}, "
                f"not {split!r} split"
            )

        common_metadata: dict[str, Any] = {
            "prompt_profile": prompt_profile_for_method(method),
            "output_protocol": "think-action-tags",
            "max_steps": 50,
            "history_length": 2,
        }
        if "gamefile" in case.payload and "observation" not in case.payload:
            common_metadata.update(
                {
                    "messages": (),
                    "requires_environment_reset": True,
                    "gamefile": case.payload["gamefile"],
                    "environment_split": case.payload.get(
                        "environment_split"
                    ),
                    "task_type": case.payload.get("task_type"),
                }
            )
        else:
            actions = _text_sequence(
                case.payload.get("admissible_actions"),
                "admissible_actions",
            )
            history = _trajectory(case.payload.get("history", ()), "history")
            messages = build_step_messages(
                task_description=_text(
                    case.payload.get("task"),
                    "task",
                ),
                current_observation=_text(
                    case.payload.get("observation"),
                    "observation",
                ),
                admissible_actions=actions,
                history=history,
                method=method,
                skill=skill,
                history_length=2,
            )
            common_metadata.update(
                {
                    "messages": messages,
                    "requires_environment_reset": False,
                    "admissible_actions": actions,
                    "task_type": case.payload.get("task_type", ""),
                }
            )
        return RolloutRequest(
            case=case,
            method=method,
            split=split,
            skill=skill,
            seed=seed,
            metadata=common_metadata,
        )

    def parse_result(
        self,
        case: BenchmarkCase,
        raw_result: Mapping[str, Any],
    ) -> RolloutResult:
        usage = _usage_records(raw_result)
        if "hard" in raw_result:
            return _parse_episode_result(case, raw_result, usage)

        action, reasoning, raw_response = extract_synthetic_action(raw_result)
        trajectory = _trajectory(
            raw_result.get("messages", ()),
            "messages",
        )
        reference = _text(case.reference, "reference")
        return RolloutResult(
            case_id=case.case_id,
            output=action,
            trajectory=trajectory,
            usage=usage,
            metadata={
                "result_shape": "synthetic-action",
                "reference": normalize_action(reference),
                "reasoning": reasoning,
                "raw_response": raw_response,
            },
        )

    def evaluate(self, result: RolloutResult) -> MetricResult:
        if result.metadata.get("result_shape") == "episode-result":
            hard = _binary_metric(result.metadata.get("hard"), "hard")
            soft = _unit_metric(result.metadata.get("soft"), "soft")
            turns = float(
                _non_negative_integer(
                    result.metadata.get("n_turns"),
                    "n_turns",
                )
            )
        else:
            if not isinstance(result.output, str):
                raise ValueError("ALFWorld prediction must be text")
            reference = _text(
                result.metadata.get("reference"),
                "reference",
            )
            hard = action_exact_match(result.output, reference)
            soft = hard
            turns = 1.0
        metrics = {
            "hard": hard,
            "soft": soft,
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


def _parse_episode_result(
    case: BenchmarkCase,
    raw_result: Mapping[str, Any],
    usage: tuple[Any, ...],
) -> RolloutResult:
    hard = _binary_metric(raw_result.get("hard"), "hard")
    soft = _unit_metric(raw_result.get("soft"), "soft")
    turns = _non_negative_integer(raw_result.get("n_turns"), "n_turns")
    conversation = _trajectory(
        raw_result.get("conversation", ()),
        "conversation",
    )
    output = ""
    if conversation:
        final_action = conversation[-1].get("action", "")
        if final_action:
            output = normalize_action(_text(final_action, "final action"))
    fail_reason = raw_result.get("fail_reason", "")
    if not isinstance(fail_reason, str):
        raise ValueError("ALFWorld fail_reason must be text")
    return RolloutResult(
        case_id=case.case_id,
        output=output,
        trajectory=conversation,
        usage=usage,
        metadata={
            "result_shape": "episode-result",
            "hard": hard,
            "soft": soft,
            "n_turns": turns,
            "fail_reason": fail_reason,
            "task_type": raw_result.get("task_type", ""),
            "task_description": raw_result.get("task_description", ""),
            "gamefile": raw_result.get("gamefile", ""),
        },
    )


def _trajectory(
    value: Any,
    field_name: str,
) -> tuple[Mapping[str, Any], ...]:
    if value in (None, ()):
        return ()
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise ValueError(
            f"ALFWorld {field_name} must be a sequence of objects"
        )
    return tuple(dict(item) for item in value)


def _usage_records(
    raw_result: Mapping[str, Any],
) -> tuple[Any, ...]:
    single = raw_result.get("usage")
    multiple = raw_result.get("usage_records")
    if single is not None and multiple is not None:
        raise ValueError(
            "ALFWorld result cannot contain both usage and usage_records"
        )
    if multiple is not None:
        if (
            isinstance(multiple, (str, bytes))
            or not isinstance(multiple, Sequence)
            or not multiple
        ):
            raise ValueError(
                "ALFWorld usage_records must be a non-empty sequence"
            )
        try:
            return tuple(normalize_usage(item) for item in multiple)
        except ValueError:
            raise ValueError(
                "ALFWorld usage_records contain invalid usage"
            ) from None
    if single is None:
        return ()
    try:
        return (normalize_usage(single),)
    except ValueError:
        raise ValueError("ALFWorld usage is invalid") from None


def _text_sequence(
    value: Any,
    field_name: str,
) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(
            f"ALFWorld {field_name} must be a non-empty string sequence"
        )
    return tuple(item.strip() for item in value)


def _binary_metric(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        or float(value) not in {0.0, 1.0}
    ):
        raise ValueError(f"ALFWorld {name} must be 0 or 1")
    return float(value)


def _unit_metric(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(
            f"ALFWorld {name} must be finite and within [0, 1]"
        )
    return float(value)


def _non_negative_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"ALFWorld {name} must be a non-negative integer"
        )
    return value


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"ALFWorld {field_name} must be non-empty text"
        )
    return value.strip()
