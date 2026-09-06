"""Portable, path-free execution planning records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from skilladam.execution.backend import validate_public_metadata
from skilladam.execution.skills import SkillSource
from skilladam.types import (
    Method,
    PUBLIC_METHODS,
    PUBLIC_REASONING_EFFORTS,
    PUBLIC_SPLITS,
    ReasoningEffort,
    Split,
)


Command = Literal["evaluate", "run"]


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Complete public execution identity without local runtime paths."""

    command: Command
    benchmark: str
    method: Method
    split: Split
    scope: str | None
    model: str
    reasoning_effort: ReasoningEffort
    workers: int
    seed: int
    case_ids: tuple[str, ...]
    skill_sources: tuple[SkillSource, ...]
    backend_spec: str
    backend_config_sha256: str
    backend_metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.command not in {"evaluate", "run"}:
            raise ValueError(f"unsupported command {self.command!r}")
        object.__setattr__(
            self,
            "benchmark",
            _text(self.benchmark, "benchmark"),
        )
        if self.method not in PUBLIC_METHODS:
            raise ValueError(f"unsupported method {self.method!r}")
        if self.split not in PUBLIC_SPLITS:
            raise ValueError(f"unsupported split {self.split!r}")
        if self.scope is not None:
            object.__setattr__(
                self,
                "scope",
                _text(self.scope, "scope"),
            )
        object.__setattr__(self, "model", _text(self.model, "model"))
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

        case_ids = tuple(self.case_ids)
        if not case_ids or any(
            not isinstance(case_id, str) or not case_id.strip()
            for case_id in case_ids
        ):
            raise ValueError(
                "execution plan requires non-empty case IDs"
            )
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("execution plan has duplicate case IDs")
        object.__setattr__(self, "case_ids", case_ids)

        sources = tuple(self.skill_sources)
        if any(not isinstance(source, SkillSource) for source in sources):
            raise ValueError(
                "execution plan skills must be SkillSource values"
            )
        if self.method == "baseline" and sources:
            raise ValueError("baseline execution cannot include skills")
        stage0_run = (
            self.command == "run"
            and self.method == "skilladam"
            and not sources
        )
        if self.method != "baseline" and not sources and not stage0_run:
            raise ValueError(
                f"{self.method} execution requires skill sources"
            )
        object.__setattr__(self, "skill_sources", sources)

        object.__setattr__(
            self,
            "backend_spec",
            _text(self.backend_spec, "backend spec"),
        )
        _sha256(self.backend_config_sha256)
        metadata = validate_public_metadata(self.backend_metadata)
        object.__setattr__(self, "backend_metadata", metadata)
        validate_public_metadata(_portable_payload(self))

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-safe data for stdout or a manifest."""

        payload = _thaw(_portable_payload(self))
        assert isinstance(payload, dict)
        return payload


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()


def _sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(
            "backend config hash must be a lowercase SHA-256"
        )
    return value


def _portable_payload(plan: ExecutionPlan) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "command": plan.command,
        "benchmark": plan.benchmark,
        "method": plan.method,
        "split": plan.split,
        "scope": plan.scope,
        "model": plan.model,
        "reasoning_effort": plan.reasoning_effort,
        "workers": plan.workers,
        "seed": plan.seed,
        "case_ids": list(plan.case_ids),
        "skills": [
            source.to_dict() for source in plan.skill_sources
        ],
        "backend": {
            "spec": plan.backend_spec,
            "config_sha256": plan.backend_config_sha256,
            "metadata": plan.backend_metadata,
        },
    }


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


__all__ = ["Command", "ExecutionPlan"]
