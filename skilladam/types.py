"""Shared, dependency-free contracts for SkillAdam benchmark adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


Method = Literal["skilladam", "skillopt", "baseline"]
Split = Literal["train", "validation", "test"]
ReasoningEffort = Literal["none", "low", "medium", "high"]

PUBLIC_METHODS: tuple[Method, ...] = ("skilladam", "skillopt", "baseline")
PUBLIC_SPLITS: tuple[Split, ...] = ("train", "validation", "test")
PUBLIC_REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "none",
    "low",
    "medium",
    "high",
)


@dataclass(frozen=True)
class BenchmarkCase:
    """One normalized benchmark example."""

    case_id: str
    payload: Mapping[str, Any]
    reference: Any | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UsageRecord:
    """Provider-neutral usage for one interaction or aggregate scope."""

    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    requests: int = 1
    cost_usd: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "cached_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "requests",
        ):
            value = getattr(self, field_name)
            if value is None and field_name != "requests":
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} must be an integer or null")
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        known_cache_tokens = (
            self.cached_input_tokens,
            self.cache_creation_input_tokens,
        )
        if self.input_tokens is not None and all(
            value is not None for value in known_cache_tokens
        ):
            cache_total = sum(
                value for value in known_cache_tokens if value is not None
            )
            if cache_total > self.input_tokens:
                raise ValueError(
                    "cache-read and cache-creation input tokens cannot "
                    "exceed input_tokens"
                )
        elif (
            self.input_tokens is not None
            and any(
                value is not None and value > self.input_tokens
                for value in known_cache_tokens
            )
        ):
            raise ValueError(
                "a cache input counter cannot exceed input_tokens"
            )
        if (
            self.output_tokens is not None
            and self.reasoning_tokens is not None
            and self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError(
                "reasoning_tokens cannot exceed output_tokens"
            )
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens is not None
            and self.total_tokens
            != self.input_tokens + self.output_tokens
        ):
            raise ValueError(
                "total_tokens must equal input_tokens + output_tokens"
            )

    @property
    def uncached_input_tokens(self) -> int | None:
        """Return base input after cache reads and cache writes."""

        if (
            self.input_tokens is None
            or self.cached_input_tokens is None
            or self.cache_creation_input_tokens is None
        ):
            return None
        return (
            self.input_tokens
            - self.cached_input_tokens
            - self.cache_creation_input_tokens
        )


@dataclass(frozen=True)
class RolloutRequest:
    """Normalized input passed from an adapter to its rollout implementation."""

    case: BenchmarkCase
    method: Method
    split: Split
    skill: str | None = None
    seed: int = 42
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RolloutResult:
    """Normalized result returned by a benchmark rollout."""

    case_id: str
    output: Any
    trajectory: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    usage: Sequence[UsageRecord] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricResult:
    """Evaluation metrics for one case or an aggregate batch."""

    primary: float
    metrics: Mapping[str, float]
    case_metrics: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    sample_count: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateDecision:
    """Acceptance-gate decision comparing baseline and candidate metrics."""

    accepted: bool
    reason: str
    baseline: MetricResult
    candidate: MetricResult
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunConfig:
    """Common configuration shared by run and evaluation commands."""

    benchmark: str
    method: Method
    split: Split
    model: str
    output_dir: Path
    reasoning_effort: ReasoningEffort = "medium"
    seed: int = 42
    workers: int = 1
