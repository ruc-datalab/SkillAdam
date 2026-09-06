"""Product project snapshots persisted across CLI/MCP processes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from skilladam.product.models import (
    EvaluationPlan,
    EvaluationSpec,
    OptimizationTask,
    PublicModel,
    SkillPackageSnapshot,
    TaskDraft,
    TaskPool,
    TaskSuite,
    UsageIntent,
    _freeze,
)
from skilladam.product.session_store import SessionStore


@dataclass(frozen=True, slots=True)
class ProductProject(PublicModel):
    package: SkillPackageSnapshot
    intent: UsageIntent
    pool: TaskPool
    suite: TaskSuite
    tasks: tuple[OptimizationTask, ...]
    plan: EvaluationPlan
    workflow_config: Mapping[str, Any]
    runtime: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workflow_config",
            _freeze(self.workflow_config),
        )
        object.__setattr__(self, "runtime", _freeze(self.runtime))


class ProductProjectStore:
    def __init__(self, output_dir: Path) -> None:
        self._store = SessionStore(
            output_dir,
            filename="product_project.json",
        )

    @property
    def exists(self) -> bool:
        return self._store.exists

    def save(self, project: ProductProject) -> None:
        self._store.save(project.to_dict())

    def load(self) -> ProductProject:
        payload = self._store.load()
        package = _package(payload["package"])
        intent = _intent(payload["intent"])
        pool = _pool(payload["pool"])
        suite = _suite(payload["suite"])
        tasks = tuple(_task(item) for item in payload["tasks"])
        plan_payload = payload["plan"]
        plan = EvaluationPlan(
            plan_id=str(plan_payload["plan_id"]),
            suite_digest=str(plan_payload["suite_digest"]),
            task_specs={
                str(task_id): _spec(spec)
                for task_id, spec in plan_payload["task_specs"].items()
            },
            aggregation=dict(plan_payload["aggregation"]),
            gate=dict(plan_payload["gate"]),
            version=str(plan_payload.get("version", "1")),
            frozen=bool(plan_payload.get("frozen", True)),
            schema_version=str(plan_payload.get("schema_version", "1")),
        )
        return ProductProject(
            package=package,
            intent=intent,
            pool=pool,
            suite=suite,
            tasks=tasks,
            plan=plan,
            workflow_config=dict(payload["workflow_config"]),
            runtime=dict(payload.get("runtime", {})),
        )


def _package(payload: Mapping[str, Any]) -> SkillPackageSnapshot:
    return SkillPackageSnapshot(
        root=str(payload["root"]),
        entrypoint=str(payload["entrypoint"]),
        files=dict(payload["files"]),
        mutable_paths=tuple(payload["mutable_paths"]),
        metadata=dict(payload.get("metadata", {})),
        schema_version=str(payload.get("schema_version", "1")),
    )


def _intent(payload: Mapping[str, Any]) -> UsageIntent:
    return UsageIntent(
        purpose=str(payload["purpose"]),
        target_users=str(payload.get("target_users", "")),
        desired_behaviors=tuple(payload.get("desired_behaviors", ())),
        constraints=tuple(payload.get("constraints", ())),
        environment=dict(payload.get("environment", {})),
        success_definition=str(payload.get("success_definition", "")),
        schema_version=str(payload.get("schema_version", "1")),
    )


def _draft(payload: Mapping[str, Any]) -> TaskDraft:
    return TaskDraft(
        task_id=str(payload["task_id"]),
        prompt=str(payload["prompt"]),
        capability=str(payload["capability"]),
        source=payload["source"],
        difficulty=str(payload["difficulty"]),
        expected_output=payload.get("expected_output"),
        evaluation_hint=dict(payload.get("evaluation_hint", {})),
        metadata=dict(payload.get("metadata", {})),
        schema_version=str(payload.get("schema_version", "1")),
    )


def _pool(payload: Mapping[str, Any]) -> TaskPool:
    return TaskPool(
        pool_id=str(payload["pool_id"]),
        version=str(payload["version"]),
        tasks=tuple(_draft(item) for item in payload["tasks"]),
        metadata=dict(payload.get("metadata", {})),
        schema_version=str(payload.get("schema_version", "1")),
    )


def _suite(payload: Mapping[str, Any]) -> TaskSuite:
    return TaskSuite(
        suite_id=str(payload["suite_id"]),
        version=str(payload["version"]),
        pool_digest=str(payload["pool_digest"]),
        train_task_ids=tuple(payload["train_task_ids"]),
        validation_task_ids=tuple(payload["validation_task_ids"]),
        test_task_ids=tuple(payload.get("test_task_ids", ())),
        split_policy=dict(payload.get("split_policy", {})),
        provenance_summary=dict(payload.get("provenance_summary", {})),
        schema_version=str(payload.get("schema_version", "1")),
    )


def _task(payload: Mapping[str, Any]) -> OptimizationTask:
    return OptimizationTask(
        task_id=str(payload["task_id"]),
        prompt=str(payload["prompt"]),
        capability=str(payload["capability"]),
        source=payload["source"],
        difficulty=str(payload["difficulty"]),
        split=payload["split"],
        evaluation=_spec(payload["evaluation"]),
        metadata=dict(payload.get("metadata", {})),
        schema_version=str(payload.get("schema_version", "1")),
    )


def _spec(payload: Mapping[str, Any]) -> EvaluationSpec:
    return EvaluationSpec(
        kind=payload["kind"],
        criteria=dict(payload["criteria"]),
        reference=payload.get("reference"),
        rubric=dict(payload.get("rubric", {})),
        evaluator_version=str(payload.get("evaluator_version", "1")),
        evaluator_config=dict(payload.get("evaluator_config", {})),
        weights={
            str(key): float(value)
            for key, value in payload.get("weights", {}).items()
        },
        aggregation=str(payload.get("aggregation", "mean")),
        missing_value_policy=str(
            payload.get("missing_value_policy", "fail")
        ),
        gate_metadata=dict(payload.get("gate_metadata", {})),
        components=tuple(
            _spec(item) for item in payload.get("components", ())
        ),
        version=str(payload.get("version", "1")),
        schema_version=str(payload.get("schema_version", "1")),
    )


__all__ = ["ProductProject", "ProductProjectStore"]
