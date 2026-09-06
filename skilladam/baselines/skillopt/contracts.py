"""Immutable, provider-neutral contracts for the SkillOpt baseline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
from typing import Any

from skilladam.types import MetricResult, UsageRecord


SCHEDULER_MODES = frozenset(
    {"constant", "linear", "cosine", "autonomous"}
)
GATE_METRICS = frozenset({"hard", "soft", "mixed"})
SLOW_UPDATE_MODES = frozenset({"force", "gated"})


@dataclass(frozen=True)
class SkillVersion:
    """One scored skill with independent base and slow-update origins."""

    text: str
    score: float
    base_origin: str
    slow_update_origin: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("SkillOpt skill text must be non-empty")
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("SkillOpt skill score must be in range [0, 1]")
        if (
            not isinstance(self.base_origin, str)
            or not self.base_origin.strip()
        ):
            raise ValueError("SkillOpt base_origin must be non-empty")
        if self.slow_update_origin is not None and (
            not isinstance(self.slow_update_origin, str)
            or not self.slow_update_origin.strip()
        ):
            raise ValueError(
                "SkillOpt slow_update_origin must be non-empty or null"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "score": self.score,
            "base_origin": self.base_origin,
            "slow_update_origin": self.slow_update_origin,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "SkillVersion":
        if not isinstance(payload, Mapping):
            raise ValueError("SkillOpt skill version must be an object")
        expected = {
            "text",
            "score",
            "base_origin",
            "slow_update_origin",
        }
        if set(payload) != expected:
            raise ValueError("invalid SkillOpt skill version fields")
        return cls(
            text=payload["text"],
            score=float(payload["score"]),
            base_origin=payload["base_origin"],
            slow_update_origin=payload["slow_update_origin"],
        )


@dataclass(frozen=True)
class RolloutCall:
    """Input to an injected benchmark rollout callback."""

    stage: str
    epoch: int
    skill: str
    case_ids: tuple[str, ...]


@dataclass(frozen=True)
class RolloutBatch:
    """Scored portable rollout output."""

    metric: MetricResult
    traces: tuple[Mapping[str, Any], ...] = ()
    usage: tuple[UsageRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.metric.sample_count != len(self.metric.case_metrics):
            raise ValueError(
                "SkillOpt rollout metric case coverage is inconsistent"
            )
        if any(
            not isinstance(item, Mapping) for item in self.traces
        ):
            raise ValueError("SkillOpt rollout traces must be objects")
        if any(
            not isinstance(item, UsageRecord) for item in self.usage
        ):
            raise ValueError("SkillOpt rollout usage must be normalized")


@dataclass(frozen=True)
class OptimizerCall:
    """Input to one injected optimizer callback."""

    stage: str
    epoch: int
    current_skill: str
    case_ids: tuple[str, ...]
    edit_budget: int | None
    memory: str
    inputs: Mapping[str, Any]


@dataclass(frozen=True)
class OptimizerResult:
    """Text plus normalized usage from one optimizer callback."""

    text: str
    usage: tuple[UsageRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("SkillOpt optimizer text must be non-empty")
        if any(
            not isinstance(item, UsageRecord) for item in self.usage
        ):
            raise ValueError("SkillOpt optimizer usage must be normalized")


RolloutCallback = Callable[[RolloutCall], RolloutBatch]
OptimizerCallback = Callable[[OptimizerCall], OptimizerResult]


@dataclass(frozen=True)
class SkillOptCallbacks:
    """All external work required by the minimal state machine."""

    rollout: RolloutCallback
    reflect: OptimizerCallback
    merge: OptimizerCallback
    select: OptimizerCallback
    slow_update: OptimizerCallback
    update_memory: OptimizerCallback


@dataclass(frozen=True)
class SkillOptConfig:
    """Validated signature for one independently checkpointed run."""

    benchmark: str
    slice_id: str
    epochs: int
    train_case_ids: tuple[str, ...]
    validation_case_ids: tuple[str, ...]
    test_case_ids: tuple[str, ...]
    seed: int
    model: str
    initial_skill_sha256: str | None = None
    scheduler_mode: str = "cosine"
    max_edit_budget: int = 4
    min_edit_budget: int = 2
    gate_metric: str = "soft"
    mixed_weight: float = 0.5
    use_slow_update: bool = True
    slow_update_mode: str = "force"
    slow_update_start_epoch: int = 2
    slow_update_samples: int = 20
    batch_size: int = 0
    reflection_minibatch_size: int = 0
    merge_batch_size: int = 8
    max_analyst_rounds: int = 3
    analyst_workers: int = 16
    shuffle_each_epoch: bool = True
    max_batches: int | None = None

    def __post_init__(self) -> None:
        for field_name in ("benchmark", "slice_id", "model"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"SkillOpt {field_name} must be non-empty")
        if isinstance(self.epochs, bool) or self.epochs < 1:
            raise ValueError("SkillOpt epochs must be at least one")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("SkillOpt seed must be an integer")
        if self.initial_skill_sha256 is not None and (
            not isinstance(self.initial_skill_sha256, str)
            or len(self.initial_skill_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.initial_skill_sha256
            )
        ):
            raise ValueError(
                "SkillOpt initial_skill_sha256 must be a lowercase digest "
                "or null"
            )
        normalized_groups: dict[str, tuple[str, ...]] = {}
        for field_name in (
            "train_case_ids",
            "validation_case_ids",
            "test_case_ids",
        ):
            values = tuple(getattr(self, field_name))
            if field_name != "test_case_ids" and not values:
                raise ValueError(f"SkillOpt {field_name} must not be empty")
            if any(
                not isinstance(value, str) or not value.strip()
                for value in values
            ):
                raise ValueError(
                    f"SkillOpt {field_name} must contain non-empty IDs"
                )
            if len(set(values)) != len(values):
                raise ValueError(
                    f"SkillOpt {field_name} contains duplicate IDs"
                )
            normalized_groups[field_name] = values
            object.__setattr__(self, field_name, values)
        group_sets = {
            name: set(values)
            for name, values in normalized_groups.items()
        }
        for left, right in (
            ("train_case_ids", "validation_case_ids"),
            ("train_case_ids", "test_case_ids"),
            ("validation_case_ids", "test_case_ids"),
        ):
            if group_sets[left] & group_sets[right]:
                raise ValueError(
                    "SkillOpt train, validation, and test IDs "
                    "must be disjoint"
                )
        if self.scheduler_mode not in SCHEDULER_MODES:
            raise ValueError("SkillOpt scheduler_mode is unsupported")
        if (
            isinstance(self.max_edit_budget, bool)
            or isinstance(self.min_edit_budget, bool)
            or not isinstance(self.max_edit_budget, int)
            or not isinstance(self.min_edit_budget, int)
            or self.min_edit_budget < 1
            or self.max_edit_budget < self.min_edit_budget
        ):
            raise ValueError("SkillOpt edit-budget bounds are invalid")
        if self.gate_metric not in GATE_METRICS:
            raise ValueError("SkillOpt gate_metric is unsupported")
        if (
            not math.isfinite(self.mixed_weight)
            or not 0.0 <= self.mixed_weight <= 1.0
        ):
            raise ValueError("SkillOpt mixed_weight must be in range [0, 1]")
        if self.slow_update_mode not in SLOW_UPDATE_MODES:
            raise ValueError("SkillOpt slow_update_mode is unsupported")
        if (
            isinstance(self.slow_update_start_epoch, bool)
            or self.slow_update_start_epoch < 2
        ):
            raise ValueError(
                "SkillOpt slow_update_start_epoch must be at least two"
            )
        if (
            isinstance(self.slow_update_samples, bool)
            or self.slow_update_samples < 1
        ):
            raise ValueError(
                "SkillOpt slow_update_samples must be at least one"
            )
        for field_name, minimum in (
            ("batch_size", 0),
            ("reflection_minibatch_size", 0),
            ("merge_batch_size", 1),
            ("max_analyst_rounds", 1),
            ("analyst_workers", 1),
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < minimum
            ):
                raise ValueError(
                    f"SkillOpt {field_name} must be an integer >= {minimum}"
                )
        if not isinstance(self.shuffle_each_epoch, bool):
            raise ValueError("SkillOpt shuffle_each_epoch must be boolean")
        if self.max_batches is not None and (
            isinstance(self.max_batches, bool)
            or not isinstance(self.max_batches, int)
            or self.max_batches < 1
        ):
            raise ValueError(
                "SkillOpt max_batches must be a positive integer or null"
            )

    @property
    def effective_batch_size(self) -> int:
        """Return the configured batch size, capped by the train pool."""

        return min(
            len(self.train_case_ids),
            self.batch_size or len(self.train_case_ids),
        )

    @property
    def steps_per_epoch(self) -> int:
        """Return the number of optimizer steps in one epoch."""

        return math.ceil(
            len(self.train_case_ids) / self.effective_batch_size
        )

    @property
    def total_steps(self) -> int:
        return self.epochs * self.steps_per_epoch

    def signature(self) -> dict[str, Any]:
        """Return the complete portable resume signature."""

        signature = {
            "method": "skillopt",
            "benchmark": self.benchmark,
            "slice_id": self.slice_id,
            "epochs": self.epochs,
            "train_case_ids": list(self.train_case_ids),
            "validation_case_ids": list(self.validation_case_ids),
            "test_case_ids": list(self.test_case_ids),
            "seed": self.seed,
            "model": self.model,
            "scheduler": {
                "mode": self.scheduler_mode,
                "max_edit_budget": self.max_edit_budget,
                "min_edit_budget": self.min_edit_budget,
                "total_steps": self.total_steps,
            },
            "batching": {
                "batch_size": self.effective_batch_size,
                "reflection_minibatch_size": (
                    self.reflection_minibatch_size
                    or self.effective_batch_size
                ),
                "merge_batch_size": self.merge_batch_size,
                "max_analyst_rounds": self.max_analyst_rounds,
                "analyst_workers": self.analyst_workers,
                "shuffle_each_epoch": self.shuffle_each_epoch,
                "steps_per_epoch": self.steps_per_epoch,
            },
            "gate": {
                "metric": self.gate_metric,
                "mixed_weight": self.mixed_weight,
            },
            "slow_update": {
                "enabled": self.use_slow_update,
                "mode": self.slow_update_mode,
                "start_epoch": self.slow_update_start_epoch,
                "samples": self.slow_update_samples,
            },
        }
        if self.initial_skill_sha256 is not None:
            signature["initial_skill_sha256"] = self.initial_skill_sha256
        if self.max_batches is not None:
            signature["max_batches"] = self.max_batches
        return signature


@dataclass(frozen=True)
class SkillOptState:
    """Current/best SkillOpt state, independent of SkillAdam state."""

    current: SkillVersion
    best: SkillVersion
    next_epoch: int
    next_batch: int = 1
    optimizer_memory: str = ""


@dataclass(frozen=True)
class EpochRecord:
    """Sanitized outcome of one normal candidate step."""

    epoch: int
    edit_budget: int
    action: str
    candidate_origin: str
    candidate_sha256: str
    candidate_score: float
    current_score: float
    best_score: float
    slow_update_action: str | None
    batch_index: int = 1
    case_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("epoch", "edit_budget"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise ValueError(
                    f"SkillOpt record {field_name} must be positive"
                )
        if (
            isinstance(self.batch_index, bool)
            or not isinstance(self.batch_index, int)
            or self.batch_index < 1
        ):
            raise ValueError(
                "SkillOpt record batch_index must be positive"
            )
        case_ids = tuple(self.case_ids)
        if (
            len(set(case_ids)) != len(case_ids)
            or any(
                not isinstance(case_id, str) or not case_id.strip()
                for case_id in case_ids
            )
        ):
            raise ValueError(
                "SkillOpt record case_ids must be unique non-empty strings"
            )
        object.__setattr__(self, "case_ids", case_ids)
        if self.action not in {
            "accept",
            "accept_new_best",
            "reject",
        }:
            raise ValueError("SkillOpt record action is invalid")
        if (
            not isinstance(self.candidate_origin, str)
            or not self.candidate_origin.strip()
        ):
            raise ValueError(
                "SkillOpt candidate_origin must be non-empty"
            )
        if (
            not isinstance(self.candidate_sha256, str)
            or len(self.candidate_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.candidate_sha256
            )
        ):
            raise ValueError(
                "SkillOpt candidate_sha256 must be a lowercase digest"
            )
        for field_name in (
            "candidate_score",
            "current_score",
            "best_score",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(
                    f"SkillOpt record {field_name} must be in [0, 1]"
                )
        if self.best_score < self.current_score:
            raise ValueError(
                "SkillOpt record best_score cannot trail current_score"
            )
        if self.slow_update_action not in {
            None,
            "inject_placeholder",
            "force_accept",
            "accept",
            "accept_new_best",
            "reject",
        }:
            raise ValueError(
                "SkillOpt record slow_update_action is invalid"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "epoch": self.epoch,
            "edit_budget": self.edit_budget,
            "action": self.action,
            "candidate_origin": self.candidate_origin,
            "candidate_sha256": self.candidate_sha256,
            "candidate_score": self.candidate_score,
            "current_score": self.current_score,
            "best_score": self.best_score,
            "slow_update_action": self.slow_update_action,
            "batch_index": self.batch_index,
            "case_ids": list(self.case_ids),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "EpochRecord":
        if not isinstance(payload, Mapping):
            raise ValueError("SkillOpt epoch record must be an object")
        return cls(
            epoch=int(payload["epoch"]),
            edit_budget=int(payload["edit_budget"]),
            action=str(payload["action"]),
            candidate_origin=str(payload["candidate_origin"]),
            candidate_sha256=str(payload["candidate_sha256"]),
            candidate_score=float(payload["candidate_score"]),
            current_score=float(payload["current_score"]),
            best_score=float(payload["best_score"]),
            slow_update_action=payload.get("slow_update_action"),
            batch_index=int(payload.get("batch_index", 1)),
            case_ids=tuple(payload.get("case_ids", ())),
        )


@dataclass(frozen=True)
class SkillOptRunResult:
    """Completed or resumed public SkillOpt run."""

    state: SkillOptState
    records: tuple[EpochRecord, ...]
    test_metric: MetricResult | None
    resumed_from_epoch: int


def metric_to_dict(metric: MetricResult | None) -> dict[str, Any] | None:
    if metric is None:
        return None
    return {
        "primary": metric.primary,
        "metrics": dict(metric.metrics),
        "case_metrics": {
            case_id: dict(values)
            for case_id, values in metric.case_metrics.items()
        },
        "sample_count": metric.sample_count,
        "metadata": dict(metric.metadata),
    }


def metric_from_dict(payload: object) -> MetricResult | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("SkillOpt metric checkpoint must be an object")
    case_metrics = payload.get("case_metrics")
    if not isinstance(case_metrics, Mapping):
        raise ValueError("SkillOpt metric case_metrics must be an object")
    return MetricResult(
        primary=float(payload["primary"]),
        metrics={
            str(name): float(value)
            for name, value in dict(payload["metrics"]).items()
        },
        case_metrics={
            str(case_id): {
                str(name): float(value)
                for name, value in dict(values).items()
            }
            for case_id, values in case_metrics.items()
        },
        sample_count=int(payload["sample_count"]),
        metadata=dict(payload.get("metadata", {})),
    )
