"""Immutable contracts for provider-neutral benchmark execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from skilladam.types import (
    PUBLIC_METHODS,
    PUBLIC_REASONING_EFFORTS,
    Method,
    ReasoningEffort,
    RolloutRequest,
    UsageRecord,
)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("runtime mapping keys must be strings")
            result[key] = _freeze(item)
        return MappingProxyType(result)
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return tuple(_freeze(item) for item in value)
    return value


def _frozen_mapping(
    value: object,
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return _freeze(value)


@dataclass(frozen=True, slots=True)
class BackendContext:
    """Portable, non-secret execution settings passed to one backend."""

    benchmark: str
    method: Method
    model: str
    reasoning_effort: ReasoningEffort
    workers: int
    seed: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "benchmark",
            _text(self.benchmark, "benchmark"),
        )
        object.__setattr__(self, "model", _text(self.model, "model"))
        if self.method not in PUBLIC_METHODS:
            raise ValueError(f"unsupported method {self.method!r}")
        if self.reasoning_effort not in PUBLIC_REASONING_EFFORTS:
            raise ValueError(
                "unsupported reasoning_effort "
                f"{self.reasoning_effort!r}"
            )
        if (
            isinstance(self.workers, bool)
            or not isinstance(self.workers, int)
            or self.workers < 1
        ):
            raise ValueError("workers must be a positive integer")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")


@dataclass(frozen=True, slots=True)
class BackendInit:
    """Runtime-only values supplied to one explicit backend factory."""

    data_root: Path
    output_dir: Path
    config: Mapping[str, Any]
    context: BackendContext
    config_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_root", Path(self.data_root))
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        object.__setattr__(
            self,
            "config",
            _frozen_mapping(self.config, "config"),
        )
        if not isinstance(self.context, BackendContext):
            raise ValueError("context must be a BackendContext")
        if self.config_sha256 is not None and (
            not isinstance(self.config_sha256, str)
            or len(self.config_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.config_sha256
            )
        ):
            raise ValueError(
                "config_sha256 must be a lowercase SHA-256 or null"
            )


@dataclass(frozen=True, slots=True)
class BatchExecution:
    """One exact, backend-managed rollout batch."""

    requests: tuple[RolloutRequest, ...]
    context: BackendContext
    stage: str

    def __post_init__(self) -> None:
        requests = tuple(self.requests)
        if not requests:
            raise ValueError("batch execution requires at least one request")
        if any(not isinstance(item, RolloutRequest) for item in requests):
            raise ValueError(
                "batch execution requests must be RolloutRequest values"
            )
        if not isinstance(self.context, BackendContext):
            raise ValueError("context must be a BackendContext")
        if any(item.method != self.context.method for item in requests):
            raise ValueError(
                "batch request method must match backend context method"
            )
        object.__setattr__(self, "stage", _text(self.stage, "stage"))
        object.__setattr__(self, "requests", requests)


@dataclass(frozen=True, slots=True)
class RawRollout:
    """One backend result before benchmark-specific parsing."""

    case_id: str
    result: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "case_id",
            _text(self.case_id, "case_id"),
        )
        object.__setattr__(
            self,
            "result",
            _frozen_mapping(self.result, "result"),
        )


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One optimizer tool call returned by a generation backend."""

    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "tool name"))
        object.__setattr__(
            self,
            "arguments",
            _frozen_mapping(self.arguments, "tool arguments"),
        )


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """One public optimizer-model generation request."""

    stage: str
    messages: tuple[Mapping[str, Any], ...]
    context: BackendContext
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _text(self.stage, "stage"))
        messages = tuple(self.messages)
        if not messages:
            raise ValueError("generation messages must not be empty")
        if any(not isinstance(item, Mapping) for item in messages):
            raise ValueError("generation messages must be objects")
        object.__setattr__(
            self,
            "messages",
            tuple(_freeze(item) for item in messages),
        )
        if not isinstance(self.context, BackendContext):
            raise ValueError("context must be a BackendContext")
        object.__setattr__(
            self,
            "metadata",
            _frozen_mapping(self.metadata, "generation metadata"),
        )


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Validated text, usage, and tool calls from one model generation."""

    text: str
    usage: tuple[UsageRecord, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("generation text must be text")
        usage = tuple(self.usage)
        tool_calls = tuple(self.tool_calls)
        if any(not isinstance(item, UsageRecord) for item in usage):
            raise ValueError("generation usage must contain UsageRecord values")
        if any(not isinstance(item, ToolCall) for item in tool_calls):
            raise ValueError("generation tool_calls must contain ToolCall values")
        if not self.text.strip() and not tool_calls:
            raise ValueError(
                "generation result requires text or tool calls"
            )
        object.__setattr__(self, "usage", usage)
        object.__setattr__(self, "tool_calls", tool_calls)


@runtime_checkable
class ExecutionBackend(Protocol):
    """External model/environment boundary used by public runners."""

    def execute_batch(
        self,
        execution: BatchExecution,
    ) -> tuple[RawRollout, ...]:
        """Execute one exact rollout batch."""

    def generate(
        self,
        request: GenerationRequest,
    ) -> GenerationResult:
        """Execute one optimizer-model generation."""

    def public_metadata(self) -> Mapping[str, Any]:
        """Return a portable, non-secret backend description."""


__all__ = [
    "BackendContext",
    "BackendInit",
    "BatchExecution",
    "ExecutionBackend",
    "GenerationRequest",
    "GenerationResult",
    "RawRollout",
    "ToolCall",
]
