"""Platform-neutral skill optimization product protocols."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence


SCHEMA_VERSION = "1"
TaskSource = Literal[
    "synthetic",
    "real_session",
    "user_provided",
    "failure_targeted",
]
TaskSplit = Literal["train", "validation", "test"]
EvaluationKind = Literal[
    "programmatic",
    "reference",
    "rubric_judge",
    "hybrid",
]
SessionState = Literal[
    "ready",
    "preparing",
    "awaiting_review",
    "validating",
    "completed",
]

TASK_SOURCES = {
    "synthetic",
    "real_session",
    "user_provided",
    "failure_targeted",
}
TASK_SPLITS = {"train", "validation", "test"}
EVALUATION_KINDS = {
    "programmatic",
    "reference",
    "rubric_judge",
    "hybrid",
}
SESSION_STATES = {
    "ready",
    "preparing",
    "awaiting_review",
    "validating",
    "completed",
}


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _json_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_json_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Produce canonical JSON for protocol digests."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest_value(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return tuple(_freeze(item) for item in value)
    return value


def _text(value: object, field_name: str, *, optional: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    normalized = value.strip()
    if not normalized and not optional:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


class PublicModel:
    """Shared JSON and digest behavior for public dataclasses."""

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)

    def to_json(self) -> str:
        return canonical_json(self)

    @property
    def digest(self) -> str:
        return digest_value(self)


@dataclass(frozen=True, slots=True)
class SkillPackageSnapshot(PublicModel):
    root: str
    entrypoint: str
    files: Mapping[str, str]
    mutable_paths: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        root = _text(self.root, "root")
        entrypoint = _text(self.entrypoint, "entrypoint").replace("\\", "/")
        normalized_files = {
            _text(path, "file path").replace("\\", "/"): content
            for path, content in self.files.items()
        }
        if not normalized_files:
            raise ValueError("files must not be empty")
        if any(not isinstance(content, str) for content in normalized_files.values()):
            raise ValueError("file content must be text")
        if entrypoint not in normalized_files:
            raise ValueError("entrypoint must exist in files")
        mutable = tuple(path.replace("\\", "/") for path in self.mutable_paths)
        if not mutable or any(path not in normalized_files for path in mutable):
            raise ValueError("mutable_paths must reference existing files")
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "entrypoint", entrypoint)
        object.__setattr__(self, "files", _freeze(normalized_files))
        object.__setattr__(self, "mutable_paths", mutable)
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    @property
    def package_digest(self) -> str:
        return digest_value(self.files)


@dataclass(frozen=True, slots=True)
class UsageIntent(PublicModel):
    purpose: str
    target_users: str = ""
    desired_behaviors: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    environment: Mapping[str, Any] = field(default_factory=dict)
    success_definition: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "purpose", _text(self.purpose, "purpose"))
        object.__setattr__(
            self,
            "target_users",
            _text(self.target_users, "target_users", optional=True),
        )
        object.__setattr__(self, "desired_behaviors", tuple(self.desired_behaviors))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "environment", _freeze(self.environment))
        object.__setattr__(
            self,
            "success_definition",
            _text(
                self.success_definition,
                "success_definition",
                optional=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class EvaluationSpec(PublicModel):
    kind: EvaluationKind
    criteria: Mapping[str, Any]
    reference: Any | None = None
    rubric: Mapping[str, Any] = field(default_factory=dict)
    evaluator_version: str = "1"
    evaluator_config: Mapping[str, Any] = field(default_factory=dict)
    weights: Mapping[str, float] = field(default_factory=dict)
    aggregation: str = "mean"
    missing_value_policy: str = "fail"
    gate_metadata: Mapping[str, Any] = field(default_factory=dict)
    components: tuple["EvaluationSpec", ...] = ()
    version: str = "1"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.kind not in EVALUATION_KINDS:
            raise ValueError(f"unsupported evaluation kind {self.kind!r}")
        object.__setattr__(self, "criteria", _freeze(self.criteria))
        object.__setattr__(self, "reference", _freeze(self.reference))
        object.__setattr__(self, "rubric", _freeze(self.rubric))
        object.__setattr__(self, "evaluator_config", _freeze(self.evaluator_config))
        object.__setattr__(self, "weights", _freeze(self.weights))
        object.__setattr__(self, "gate_metadata", _freeze(self.gate_metadata))
        components = tuple(self.components)
        if any(not isinstance(item, EvaluationSpec) for item in components):
            raise ValueError("components must contain EvaluationSpec values")
        if self.kind == "hybrid" and not components:
            raise ValueError("hybrid evaluation requires components")
        if self.kind != "hybrid" and components:
            raise ValueError("only hybrid evaluation may contain components")
        object.__setattr__(self, "components", components)


@dataclass(frozen=True, slots=True)
class TaskDraft(PublicModel):
    task_id: str
    prompt: str
    capability: str
    source: TaskSource
    difficulty: str
    expected_output: Any | None = None
    evaluation_hint: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _text(self.task_id, "task_id"))
        object.__setattr__(self, "prompt", _text(self.prompt, "prompt"))
        object.__setattr__(self, "capability", _text(self.capability, "capability"))
        if self.source not in TASK_SOURCES:
            raise ValueError(f"unsupported task source {self.source!r}")
        object.__setattr__(self, "expected_output", _freeze(self.expected_output))
        object.__setattr__(self, "evaluation_hint", _freeze(self.evaluation_hint))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class OptimizationTask(PublicModel):
    task_id: str
    prompt: str
    capability: str
    source: TaskSource
    difficulty: str
    split: TaskSplit
    evaluation: EvaluationSpec
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _text(self.task_id, "task_id"))
        object.__setattr__(self, "prompt", _text(self.prompt, "prompt"))
        object.__setattr__(self, "capability", _text(self.capability, "capability"))
        if self.source not in TASK_SOURCES:
            raise ValueError(f"unsupported task source {self.source!r}")
        if self.split not in TASK_SPLITS:
            raise ValueError(f"unsupported task split {self.split!r}")
        if not isinstance(self.evaluation, EvaluationSpec):
            raise ValueError("evaluation must be an EvaluationSpec")
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class TaskPool(PublicModel):
    pool_id: str
    version: str
    tasks: tuple[TaskDraft, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        tasks = tuple(self.tasks)
        if any(not isinstance(task, TaskDraft) for task in tasks):
            raise ValueError("tasks must contain TaskDraft values")
        identifiers = [task.task_id for task in tasks]
        if not tasks or len(identifiers) != len(set(identifiers)):
            raise ValueError("tasks must be non-empty with unique IDs")
        object.__setattr__(self, "pool_id", _text(self.pool_id, "pool_id"))
        object.__setattr__(self, "tasks", tasks)
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class TaskSuite(PublicModel):
    suite_id: str
    version: str
    pool_digest: str
    train_task_ids: tuple[str, ...]
    validation_task_ids: tuple[str, ...]
    test_task_ids: tuple[str, ...] = ()
    split_policy: Mapping[str, Any] = field(default_factory=dict)
    provenance_summary: Mapping[str, int] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        all_ids = self.train_task_ids + self.validation_task_ids + self.test_task_ids
        if not all_ids or len(all_ids) != len(set(all_ids)):
            raise ValueError("suite task IDs must be non-empty and disjoint")
        object.__setattr__(self, "suite_id", _text(self.suite_id, "suite_id"))
        object.__setattr__(self, "split_policy", _freeze(self.split_policy))
        object.__setattr__(self, "provenance_summary", _freeze(self.provenance_summary))


@dataclass(frozen=True, slots=True)
class EvaluationPlan(PublicModel):
    plan_id: str
    suite_digest: str
    task_specs: Mapping[str, EvaluationSpec]
    aggregation: Mapping[str, Any]
    gate: Mapping[str, Any]
    version: str = "1"
    frozen: bool = True
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.frozen:
            raise ValueError("an EvaluationPlan must be frozen")
        if not self.task_specs:
            raise ValueError("task_specs must not be empty")
        if any(not isinstance(spec, EvaluationSpec) for spec in self.task_specs.values()):
            raise ValueError("task_specs must contain EvaluationSpec values")
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id"))
        object.__setattr__(self, "task_specs", MappingProxyType(dict(self.task_specs)))
        object.__setattr__(self, "aggregation", _freeze(self.aggregation))
        object.__setattr__(self, "gate", _freeze(self.gate))


@dataclass(frozen=True, slots=True)
class ValidationResult(PublicModel):
    candidate_digest: str
    evaluation_plan_digest: str
    primary: float
    metrics: Mapping[str, float]
    case_results: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", _freeze(self.metrics))
        object.__setattr__(self, "case_results", _freeze(self.case_results))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class GateResult(PublicModel):
    accepted: bool
    reason: str
    baseline_validation_digest: str
    candidate_validation_digest: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class OptimizationSession(PublicModel):
    session_id: str
    state: SessionState
    iteration: int
    package_digest: str
    task_suite_digest: str = ""
    evaluation_plan_digest: str = ""
    proposal_id: str = ""
    candidate_digest: str = ""
    stop_reason: str = ""
    artifacts: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.state not in SESSION_STATES:
            raise ValueError(f"unsupported session state {self.state!r}")
        if (
            isinstance(self.iteration, bool)
            or not isinstance(self.iteration, int)
            or self.iteration < 0
        ):
            raise ValueError("iteration must be a non-negative integer")
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        object.__setattr__(self, "artifacts", _freeze(self.artifacts))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


__all__ = [
    "EvaluationKind",
    "EvaluationPlan",
    "EvaluationSpec",
    "GateResult",
    "OptimizationSession",
    "OptimizationTask",
    "PublicModel",
    "SCHEMA_VERSION",
    "SessionState",
    "SkillPackageSnapshot",
    "TaskPool",
    "TaskDraft",
    "TaskSource",
    "TaskSplit",
    "TaskSuite",
    "UsageIntent",
    "ValidationResult",
    "canonical_json",
    "digest_value",
]
