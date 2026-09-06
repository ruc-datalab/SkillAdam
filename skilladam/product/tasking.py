"""Task ingestion, synthetic bootstrap, and frozen splits."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import math
from typing import Any

from skilladam.product.models import (
    SCHEMA_VERSION,
    SkillPackageSnapshot,
    TaskDraft,
    TaskPool,
    TaskSource,
    TaskSuite,
    UsageIntent,
    digest_value,
)


SYNTHETIC_COVERAGE = (
    "common",
    "boundary",
    "error_recovery",
    "constraint_following",
    "tool_use",
    "multi_step",
    "adversarial",
)


class TaskIngestionError(ValueError):
    """Task input violates the public ingestion protocol."""


@dataclass(frozen=True, slots=True)
class SyntheticGenerationRequest:
    package: SkillPackageSnapshot
    intent: UsageIntent
    count: int
    coverage: tuple[str, ...] = SYNTHETIC_COVERAGE
    regeneration: int = 0
    schema_version: str = SCHEMA_VERSION


StructuredTaskGenerator = Callable[
    [SyntheticGenerationRequest],
    Sequence[Mapping[str, Any]],
]


def generate_synthetic_tasks(
    package: SkillPackageSnapshot,
    intent: UsageIntent,
    generator: StructuredTaskGenerator,
    *,
    count: int = 12,
    baseline_evaluator: Callable[[tuple[TaskDraft, ...]], float] | None = None,
) -> tuple[TaskDraft, ...]:
    """Generate and freeze a synthetic bootstrap pool with one optional headroom retry.

    The host supplies ``baseline_evaluator`` after freezing tasks and evaluation. It takes
    the current pool drafts and returns the original skill's mean score on the full set; above
    0.9, request at most one more discriminative set. The default generates once.
    """

    if isinstance(count, bool) or not isinstance(count, int) or count < 2:
        raise TaskIngestionError("synthetic task count must be at least 2")
    def _generate(regeneration: int) -> tuple[TaskDraft, ...]:
        records = tuple(
            generator(
                SyntheticGenerationRequest(
                    package=package,
                    intent=intent,
                    count=count,
                    regeneration=regeneration,
                )
            )
        )
        if len(records) != count:
            raise TaskIngestionError(
                f"synthetic generator returned {len(records)} tasks; expected {count}"
            )
        return ingest_task_records(
            records,
            source="synthetic",
            provenance={
                "package_digest": package.package_digest,
                "intent_digest": intent.digest,
                "coverage": SYNTHETIC_COVERAGE,
                "regeneration": regeneration,
            },
        )

    drafts = _generate(0)
    if baseline_evaluator is None:
        return drafts
    try:
        baseline = float(baseline_evaluator(drafts))
    except (TypeError, ValueError) as exc:
        raise TaskIngestionError(
            "baseline_evaluator must return a finite numeric score"
        ) from exc
    if not math.isfinite(baseline):
        raise TaskIngestionError(
            "baseline_evaluator must return a finite numeric score"
        )
    if baseline > 0.9:
        return _generate(1)
    return drafts


def ingest_user_tasks(
    records: Iterable[Mapping[str, Any]],
    *,
    provenance: Mapping[str, Any] | None = None,
) -> tuple[TaskDraft, ...]:
    return ingest_task_records(
        records,
        source="user_provided",
        provenance=provenance,
    )


def ingest_task_records(
    records: Iterable[Mapping[str, Any]],
    *,
    source: TaskSource,
    provenance: Mapping[str, Any] | None = None,
) -> tuple[TaskDraft, ...]:
    """Adapter-neutral ingestion shared by all four source types."""

    provenance_data = dict(provenance or {})
    drafts: list[TaskDraft] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise TaskIngestionError("task records must be objects")
        prompt = str(record.get("prompt", "")).strip()
        if not prompt:
            raise TaskIngestionError("each task record requires prompt")
        capability = str(record.get("capability", "general")).strip() or "general"
        difficulty = str(record.get("difficulty", "common")).strip() or "common"
        metadata = dict(record.get("metadata", {}))
        metadata["provenance"] = {
            "source": source,
            "ordinal": index,
            **provenance_data,
        }
        task_id = str(record.get("task_id", "")).strip()
        if not task_id:
            task_id = _task_id(
                source=source,
                prompt=prompt,
                capability=capability,
                expected_output=record.get("expected_output"),
                ordinal=index,
                provenance=provenance_data,
            )
        drafts.append(
            TaskDraft(
                task_id=task_id,
                prompt=prompt,
                capability=capability,
                source=source,
                difficulty=difficulty,
                expected_output=record.get("expected_output"),
                evaluation_hint=dict(record.get("evaluation_hint", {})),
                metadata=metadata,
            )
        )
    if not drafts:
        raise TaskIngestionError("at least one task record is required")
    if len({draft.task_id for draft in drafts}) != len(drafts):
        raise TaskIngestionError("ingested task IDs must be unique")
    return tuple(drafts)


def freeze_task_pool(
    tasks: Iterable[TaskDraft],
    *,
    pool_id: str,
    version: str = "1",
    metadata: Mapping[str, Any] | None = None,
) -> TaskPool:
    """Freeze the full task set; do not append targeted tasks to the same pool."""

    return TaskPool(
        pool_id=pool_id,
        version=version,
        tasks=tuple(tasks),
        metadata={"frozen": True, **dict(metadata or {})},
    )


def freeze_task_suite(
    pool: TaskPool,
    *,
    suite_id: str,
    validation_size: int | None = None,
    test_size: int = 0,
    seed: int = 42,
    version: str = "1",
) -> TaskSuite:
    """Split deterministically after freezing the full pool, prioritizing real tasks."""

    total = len(pool.tasks)
    if validation_size is None:
        validation_size = max(1, round(total * 0.25))
    if (
        validation_size < 1
        or test_size < 0
        or validation_size + test_size >= total
    ):
        raise TaskIngestionError(
            "split requires validation tasks and at least one training task"
        )
    ranked = sorted(pool.tasks, key=lambda task: _holdout_key(task, seed))
    test = ranked[:test_size]
    validation = ranked[test_size : test_size + validation_size]
    holdout_ids = {task.task_id for task in test + validation}
    train = tuple(task for task in pool.tasks if task.task_id not in holdout_ids)
    provenance: dict[str, int] = {}
    for task in pool.tasks:
        provenance[task.source] = provenance.get(task.source, 0) + 1
    return TaskSuite(
        suite_id=suite_id,
        version=version,
        pool_digest=pool.digest,
        train_task_ids=tuple(task.task_id for task in train),
        validation_task_ids=tuple(task.task_id for task in validation),
        test_task_ids=tuple(task.task_id for task in test),
        split_policy={
            "kind": "deterministic_holdout_priority",
            "seed": seed,
            "validation_size": validation_size,
            "test_size": test_size,
        },
        provenance_summary=provenance,
    )


def derive_task_pool(
    previous: TaskPool,
    added_tasks: Iterable[TaskDraft],
    *,
    version: str,
    pool_id: str | None = None,
) -> TaskPool:
    """Add real or failure-targeted evidence to a new pool version."""

    if version == previous.version:
        raise TaskIngestionError("derived task pool must use a new version")
    additions = tuple(added_tasks)
    if not additions:
        raise TaskIngestionError("derived task pool requires added tasks")
    return freeze_task_pool(
        previous.tasks + additions,
        pool_id=pool_id or previous.pool_id,
        version=version,
        metadata={
            **dict(previous.metadata),
            "parent_pool_digest": previous.digest,
        },
    )


def _task_id(
    *,
    source: TaskSource,
    prompt: str,
    capability: str,
    expected_output: Any,
    ordinal: int,
    provenance: Mapping[str, Any],
) -> str:
    digest = digest_value(
        {
            "source": source,
            "prompt": prompt,
            "capability": capability,
            "expected_output": expected_output,
            "ordinal": ordinal,
            "provenance": provenance,
        }
    )
    return f"task_{digest}"


def _holdout_key(task: TaskDraft, seed: int) -> tuple[int, str]:
    priority = {
        "real_session": 0,
        "user_provided": 1,
        "synthetic": 2,
        "failure_targeted": 3,
    }[task.source]
    tie_break = sha256(f"{seed}:{task.task_id}".encode("utf-8")).hexdigest()
    return priority, tie_break


__all__ = [
    "SYNTHETIC_COVERAGE",
    "StructuredTaskGenerator",
    "SyntheticGenerationRequest",
    "TaskIngestionError",
    "derive_task_pool",
    "freeze_task_pool",
    "freeze_task_suite",
    "generate_synthetic_tasks",
    "ingest_task_records",
    "ingest_user_tasks",
]
