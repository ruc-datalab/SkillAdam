"""Minimal provider-neutral SkillOpt state machine."""

from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import random
import uuid
from typing import Any, Callable

from skilladam.baselines.skillopt.contracts import (
    EpochRecord,
    OptimizerCall,
    OptimizerResult,
    RolloutBatch,
    RolloutCall,
    SkillOptCallbacks,
    SkillOptConfig,
    SkillOptRunResult,
    SkillOptState,
    SkillVersion,
    metric_from_dict,
    metric_to_dict,
)
from skilladam.baselines.skillopt.gate import (
    GateConfig,
    evaluate_gate,
    select_gate_score,
)
from skilladam.baselines.skillopt.report import build_report, write_report
from skilladam.baselines.skillopt.reflection_trace import (
    prepare_reflection_traces,
)
from skilladam.baselines.skillopt.scheduler import build_scheduler
from skilladam.baselines.skillopt.slow_update import (
    SLOW_UPDATE_START,
    apply_force_slow_update,
    apply_gated_slow_update,
    build_longitudinal_pairs,
    extract_slow_update_block,
    inject_slow_update_block,
    replace_slow_update_block,
)
from skilladam.cost.ledger import UsageLedger
from skilladam.types import MetricResult, UsageRecord


class CheckpointExistsError(RuntimeError):
    """Raised when a new run would overwrite SkillOpt state."""


class ResumeMismatchError(RuntimeError):
    """Raised when resume configuration differs from saved state."""


class SkillOptRunner:
    """Run independent current/best optimization with injected callbacks."""

    SCHEMA_VERSION = 2
    CHECKPOINT_NAME = "skillopt_checkpoint.json"

    def __init__(
        self,
        config: SkillOptConfig,
        *,
        callbacks: SkillOptCallbacks,
        output_dir: Path,
        usage_ledger: UsageLedger | None = None,
    ) -> None:
        self.config = config
        self.callbacks = callbacks
        self.output_dir = Path(output_dir)
        self.checkpoint_path = self.output_dir / self.CHECKPOINT_NAME
        self.usage_ledger = usage_ledger or UsageLedger(
            self.output_dir / "usage.jsonl"
        )
        self.gate_config = GateConfig(
            metric=config.gate_metric,
            mixed_weight=config.mixed_weight,
        )

    def run(
        self,
        *,
        initial_skill: str,
        resume: bool = False,
    ) -> SkillOptRunResult:
        if not isinstance(initial_skill, str) or not initial_skill.strip():
            raise ValueError("SkillOpt initial skill must be non-empty")
        initial_digest = hashlib.sha256(
            initial_skill.encode("utf-8")
        ).hexdigest()
        if (
            self.config.initial_skill_sha256 is not None
            and initial_digest != self.config.initial_skill_sha256
        ):
            error = (
                "SkillOpt initial skill does not match the configured "
                "initial-skill hash"
            )
            if resume:
                raise ResumeMismatchError(error)
            raise ValueError(error)
        scheduler = build_scheduler(
            self.config.scheduler_mode,
            max_budget=self.config.max_edit_budget,
            min_budget=self.config.min_edit_budget,
            total_steps=self.config.total_steps,
        )
        if self.checkpoint_path.exists():
            if not resume:
                raise CheckpointExistsError(
                    f"SkillOpt checkpoint already exists: "
                    f"{self.checkpoint_path}"
                )
            (
                state,
                records,
                previous_epoch_skill,
                test_metric,
                scheduler_state,
            ) = self._load_checkpoint()
            scheduler.load_state_dict(scheduler_state)
        else:
            if resume:
                raise FileNotFoundError(
                    f"SkillOpt checkpoint does not exist: "
                    f"{self.checkpoint_path}"
                )
            self._refuse_orphaned_artifacts()
            initial = SkillVersion(
                text=initial_skill,
                score=0.0,
                base_origin="initial_skill",
            )
            state = SkillOptState(
                current=initial,
                best=initial,
                next_epoch=1,
                next_batch=1,
            )
            records: list[EpochRecord] = []
            previous_epoch_skill = initial_skill
            test_metric = None
            self._save_checkpoint(
                state=state,
                records=records,
                previous_epoch_skill=previous_epoch_skill,
                scheduler_state=scheduler.state_dict(),
                test_metric=None,
            )

        resumed_from_epoch = state.next_epoch
        completed_batches = 0
        smoke_limit_reached = False
        for epoch in range(state.next_epoch, self.config.epochs + 1):
            epoch_batches = self._epoch_batches(epoch)
            first_batch = (
                state.next_batch
                if epoch == state.next_epoch
                else 1
            )
            if first_batch > len(epoch_batches):
                raise ResumeMismatchError(
                    "SkillOpt checkpoint next batch exceeds the epoch plan"
                )
            for batch_index, case_ids in enumerate(
                epoch_batches[first_batch - 1 :],
                start=first_batch,
            ):
                edit_budget = scheduler.step()
                is_last_batch = batch_index == len(epoch_batches)
                state, record = self._run_candidate_step(
                    epoch=epoch,
                    batch_index=batch_index,
                    total_batches=len(epoch_batches),
                    case_ids=case_ids,
                    edit_budget=edit_budget,
                    state=state,
                    previous_epoch_skill=previous_epoch_skill,
                    is_last_batch=is_last_batch,
                )
                records.append(record)
                if is_last_batch:
                    previous_epoch_skill = state.current.text
                    state = replace(
                        state,
                        next_epoch=epoch + 1,
                        next_batch=1,
                    )
                else:
                    state = replace(
                        state,
                        next_epoch=epoch,
                        next_batch=batch_index + 1,
                    )
                self._save_checkpoint(
                    state=state,
                    records=records,
                    previous_epoch_skill=previous_epoch_skill,
                    scheduler_state=scheduler.state_dict(),
                    test_metric=None,
                )
                completed_batches += 1
                if (
                    self.config.max_batches is not None
                    and completed_batches >= self.config.max_batches
                ):
                    smoke_limit_reached = True
                    break
            if smoke_limit_reached:
                break

        if (
            not smoke_limit_reached
            and test_metric is None
            and self.config.test_case_ids
        ):
            test_metric = self._rollout(
                stage="test_rollout",
                epoch=self.config.epochs,
                skill=state.best.text,
                case_ids=self.config.test_case_ids,
                artifact_path=(
                    self.output_dir
                    / "rollouts"
                    / "test"
                    / "best_evaluation.json"
                ),
            ).metric
            self._save_checkpoint(
                state=state,
                records=records,
                previous_epoch_skill=previous_epoch_skill,
                scheduler_state=scheduler.state_dict(),
                test_metric=test_metric,
            )

        result = SkillOptRunResult(
            state=state,
            records=tuple(records),
            test_metric=test_metric,
            resumed_from_epoch=resumed_from_epoch,
        )
        self._write_artifacts(result)
        return result

    def _epoch_batches(self, epoch: int) -> tuple[tuple[str, ...], ...]:
        case_ids = list(self.config.train_case_ids)
        if self.config.shuffle_each_epoch:
            random.Random(
                self.config.seed + epoch * 1000
            ).shuffle(case_ids)
        size = self.config.effective_batch_size
        return tuple(
            tuple(case_ids[start : start + size])
            for start in range(0, len(case_ids), size)
        )

    def _run_candidate_step(
        self,
        *,
        epoch: int,
        batch_index: int,
        total_batches: int,
        case_ids: tuple[str, ...],
        edit_budget: int,
        state: SkillOptState,
        previous_epoch_skill: str,
        is_last_batch: bool,
    ) -> tuple[SkillOptState, EpochRecord]:
        target = self._rollout(
            stage="target_train_rollout",
            epoch=epoch,
            skill=state.current.text,
            case_ids=case_ids,
            artifact_path=self._batch_rollout_path(
                epoch,
                batch_index,
                "training_evaluation.json",
            ),
        )
        reflections, merged = self._reflect_and_merge(
            epoch=epoch,
            batch_index=batch_index,
            state=state,
            edit_budget=edit_budget,
            case_ids=case_ids,
            target=target,
        )
        selected = self._optimizer(
            self.callbacks.select,
            stage="optimizer_select",
            epoch=epoch,
            state=state,
            edit_budget=edit_budget,
            case_ids=case_ids,
            inputs={
                "reflection": (
                    reflections[0]
                    if len(reflections) == 1
                    else reflections
                ),
                "merged": merged,
            },
        )
        candidate_text = _preserve_slow_update(
            state.current.text,
            selected.text,
        )

        baseline_batch = self._rollout(
            stage="selection_rollout",
            epoch=epoch,
            skill=state.current.text,
            case_ids=self.config.validation_case_ids,
            artifact_path=self._batch_rollout_path(
                epoch,
                batch_index,
                "validation_baseline_evaluation.json",
            ),
        )
        candidate_batch = self._rollout(
            stage="selection_rollout",
            epoch=epoch,
            skill=candidate_text,
            case_ids=self.config.validation_case_ids,
            artifact_path=self._batch_rollout_path(
                epoch,
                batch_index,
                "validation_candidate_evaluation.json",
            ),
        )
        baseline_score = select_gate_score(
            baseline_batch.metric,
            self.gate_config,
        )
        candidate_score = select_gate_score(
            candidate_batch.metric,
            self.gate_config,
        )
        current = replace(state.current, score=baseline_score)
        best = state.best
        if (
            best.text == state.current.text
            and best.base_origin == state.current.base_origin
            and best.slow_update_origin == state.current.slow_update_origin
        ):
            best = replace(best, score=baseline_score)
        origin = (
            f"epoch-{epoch:03d}"
            if total_batches == 1
            else f"epoch-{epoch:03d}-batch-{batch_index:03d}"
        )
        candidate = SkillVersion(
            text=candidate_text,
            score=candidate_score,
            base_origin=origin,
            slow_update_origin=current.slow_update_origin,
        )
        transition = evaluate_gate(
            current=current,
            best=best,
            candidate=candidate,
        )
        state = replace(
            state,
            current=transition.current,
            best=transition.best,
        )

        slow_action: str | None = None
        if is_last_batch and self.config.use_slow_update:
            if epoch == 1:
                state = replace(
                    state,
                    current=_inject_placeholder(state.current),
                    best=_inject_placeholder(state.best),
                )
                slow_action = "inject_placeholder"
            elif epoch >= self.config.slow_update_start_epoch:
                state, slow_action = self._run_slow_update(
                    epoch=epoch,
                    state=state,
                    previous_epoch_skill=previous_epoch_skill,
                    edit_budget=edit_budget,
                )

        memory = self._optimizer(
            self.callbacks.update_memory,
            stage="optimizer_memory",
            epoch=epoch,
            state=state,
            edit_budget=edit_budget,
            case_ids=case_ids,
            inputs={
                "candidate_action": transition.action,
                "slow_update_action": slow_action,
                "previous_memory": state.optimizer_memory,
            },
        )
        state = replace(
            state,
            optimizer_memory=memory.text,
        )
        record = EpochRecord(
            epoch=epoch,
            edit_budget=edit_budget,
            action=transition.action,
            candidate_origin=candidate.base_origin,
            candidate_sha256=hashlib.sha256(
                candidate.text.encode("utf-8")
            ).hexdigest(),
            candidate_score=candidate_score,
            current_score=state.current.score,
            best_score=state.best.score,
            slow_update_action=slow_action,
            batch_index=batch_index,
            case_ids=case_ids,
        )
        return state, record

    def _reflect_and_merge(
        self,
        *,
        epoch: int,
        batch_index: int,
        state: SkillOptState,
        edit_budget: int,
        case_ids: tuple[str, ...],
        target: RolloutBatch,
    ) -> tuple[tuple[str, ...], str]:
        minibatch_size = (
            self.config.reflection_minibatch_size or len(case_ids)
        )
        failures, successes = _reflection_groups(
            benchmark=self.config.benchmark,
            case_ids=case_ids,
            metric=target.metric,
        )
        reflection_seed = (
            self.config.seed
            + (epoch - 1) * self.config.steps_per_epoch
            + batch_index
            - 1
        )
        random.Random(reflection_seed).shuffle(failures)
        random.Random(reflection_seed + 1).shuffle(successes)
        ordered_groups = (failures, successes)
        if target.traces and len(target.traces) != len(case_ids):
            raise ValueError(
                "SkillOpt rollout traces do not match reflection cases"
            )
        trace_by_case = (
            dict(zip(case_ids, target.traces, strict=True))
            if target.traces
            else {}
        )
        reflections: list[str] = []
        for group in ordered_groups:
            for start in range(0, len(group), minibatch_size):
                minibatch_ids = tuple(
                    group[start : start + minibatch_size]
                )
                traces = (
                    tuple(
                        trace_by_case[case_id]
                        for case_id in minibatch_ids
                    )
                    if trace_by_case
                    else ()
                )
                traces = prepare_reflection_traces(
                    self.config.benchmark,
                    traces,
                )
                result = self._optimizer(
                    self.callbacks.reflect,
                    stage="optimizer_reflection",
                    epoch=epoch,
                    state=state,
                    edit_budget=edit_budget,
                    case_ids=minibatch_ids,
                    inputs={
                        "metric": _subset_metric_summary(
                            target.metric,
                            minibatch_ids,
                        ),
                        "traces": traces,
                    },
                )
                reflections.append(result.text)

        merged_values = list(reflections)
        rounds = 0
        while rounds < self.config.max_analyst_rounds:
            rounds += 1
            next_values: list[str] = []
            for start in range(
                0,
                len(merged_values),
                self.config.merge_batch_size,
            ):
                group = merged_values[
                    start : start + self.config.merge_batch_size
                ]
                result = self._optimizer(
                    self.callbacks.merge,
                    stage="optimizer_merge",
                    epoch=epoch,
                    state=state,
                    edit_budget=edit_budget,
                    case_ids=case_ids,
                    inputs={
                        "reflection": (
                            group[0] if len(group) == 1 else tuple(group)
                        )
                    },
                )
                next_values.append(result.text)
            merged_values = next_values
            if len(merged_values) == 1:
                break
        if len(merged_values) != 1:
            raise ValueError(
                "SkillOpt analyst merge did not converge within "
                "max_analyst_rounds"
            )
        return tuple(reflections), merged_values[0]

    def _refuse_orphaned_artifacts(self) -> None:
        managed = (
            self.output_dir / "current_skill.md",
            self.output_dir / "best_skill.md",
            self.output_dir / "skillopt_report.json",
            self.output_dir / "rollouts",
        )
        if self.usage_ledger.path == self.output_dir / "usage.jsonl":
            managed = (*managed, self.usage_ledger.path)
        existing = [path.name for path in managed if path.exists()]
        if existing:
            raise CheckpointExistsError(
                "SkillOpt managed artifact already exists without a "
                f"checkpoint: {', '.join(sorted(existing))}"
            )

    def _run_slow_update(
        self,
        *,
        epoch: int,
        state: SkillOptState,
        previous_epoch_skill: str,
        edit_budget: int,
    ) -> tuple[SkillOptState, str]:
        case_ids = self.config.train_case_ids[
            : self.config.slow_update_samples
        ]
        previous_batch = self._rollout(
            stage="slow_update_rollout",
            epoch=epoch,
            skill=previous_epoch_skill,
            case_ids=case_ids,
            artifact_path=(
                self.output_dir
                / "rollouts"
                / f"epoch_{epoch:03d}"
                / "slow_update_previous_evaluation.json"
            ),
        )
        current_batch = self._rollout(
            stage="slow_update_rollout",
            epoch=epoch,
            skill=state.current.text,
            case_ids=case_ids,
            artifact_path=(
                self.output_dir
                / "rollouts"
                / f"epoch_{epoch:03d}"
                / "slow_update_current_evaluation.json"
            ),
        )
        pairs = build_longitudinal_pairs(
            previous_batch.metric,
            current_batch.metric,
        )
        guidance = self._optimizer(
            self.callbacks.slow_update,
            stage="slow_update_generation",
            epoch=epoch,
            state=state,
            edit_budget=edit_budget,
            case_ids=case_ids,
            inputs={
                "pairs": tuple(asdict(pair) for pair in pairs),
                "existing_guidance": extract_slow_update_block(
                    state.current.text
                ),
            },
        )
        origin = f"epoch-{epoch:03d}"
        if self.config.slow_update_mode == "force":
            transition = apply_force_slow_update(
                current=state.current,
                best=state.best,
                guidance=guidance.text,
                origin=origin,
            )
        else:
            candidate_text = replace_slow_update_block(
                state.current.text,
                guidance.text,
            )
            candidate_metric = self._rollout(
                stage="selection_rollout",
                epoch=epoch,
                skill=candidate_text,
                case_ids=self.config.validation_case_ids,
                artifact_path=(
                    self.output_dir
                    / "rollouts"
                    / f"epoch_{epoch:03d}"
                    / "slow_update_candidate_evaluation.json"
                ),
            ).metric
            transition = apply_gated_slow_update(
                current=state.current,
                best=state.best,
                guidance=guidance.text,
                candidate_score=select_gate_score(
                    candidate_metric,
                    self.gate_config,
                ),
                origin=origin,
            )
        return (
            replace(
                state,
                current=transition.current,
                best=transition.best,
            ),
            transition.action,
        )

    def _rollout(
        self,
        *,
        stage: str,
        epoch: int,
        skill: str,
        case_ids: tuple[str, ...],
        artifact_path: Path,
    ) -> RolloutBatch:
        result = self.callbacks.rollout(
            RolloutCall(
                stage=stage,
                epoch=epoch,
                skill=skill,
                case_ids=case_ids,
            )
        )
        if tuple(result.metric.case_metrics) != case_ids:
            raise ValueError(
                "SkillOpt rollout must preserve ordered case IDs"
            )
        self._append_usage(stage, result.usage)
        self._write_rollout_artifact(
            artifact_path,
            stage=stage,
            epoch=epoch,
            skill=skill,
            case_ids=case_ids,
            result=result,
        )
        return result

    def _batch_rollout_path(
        self,
        epoch: int,
        batch_index: int,
        name: str,
    ) -> Path:
        return (
            self.output_dir
            / "rollouts"
            / f"epoch_{epoch:03d}"
            / f"batch_{batch_index:03d}"
            / name
        )

    def _write_rollout_artifact(
        self,
        path: Path,
        *,
        stage: str,
        epoch: int,
        skill: str,
        case_ids: tuple[str, ...],
        result: RolloutBatch,
    ) -> None:
        _atomic_write_preserving_json(
            path,
            {
                "schema_version": 1,
                "stage": stage,
                "epoch": epoch,
                "case_ids": list(case_ids),
                "skill_sha256": hashlib.sha256(
                    skill.encode("utf-8")
                ).hexdigest(),
                "metric": metric_to_dict(result.metric),
                "traces": [
                    dict(trace) for trace in result.traces
                ],
                "usage": [
                    asdict(usage) for usage in result.usage
                ],
            },
        )

    def _optimizer(
        self,
        callback: Callable[[OptimizerCall], OptimizerResult],
        *,
        stage: str,
        epoch: int,
        state: SkillOptState,
        edit_budget: int,
        case_ids: tuple[str, ...],
        inputs: dict[str, Any],
    ) -> OptimizerResult:
        result = callback(
            OptimizerCall(
                stage=stage,
                epoch=epoch,
                current_skill=state.current.text,
                case_ids=case_ids,
                edit_budget=edit_budget,
                memory=state.optimizer_memory,
                inputs=inputs,
            )
        )
        self._append_usage(stage, result.usage)
        return result

    def _append_usage(
        self,
        stage: str,
        usages: tuple[UsageRecord, ...],
    ) -> None:
        for usage in usages:
            self.usage_ledger.append(
                method="skillopt",
                benchmark=self.config.benchmark,
                stage=stage,
                model=self.config.model,
                usage=usage,
            )

    def _save_checkpoint(
        self,
        *,
        state: SkillOptState,
        records: list[EpochRecord],
        previous_epoch_skill: str,
        scheduler_state: dict[str, int],
        test_metric: MetricResult | None,
    ) -> None:
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "signature": self.config.signature(),
            "state": {
                "current": state.current.to_dict(),
                "best": state.best.to_dict(),
                "next_epoch": state.next_epoch,
                "next_batch": state.next_batch,
                "optimizer_memory": state.optimizer_memory,
            },
            "records": [record.to_dict() for record in records],
            "previous_epoch_skill": previous_epoch_skill,
            "scheduler_state": scheduler_state,
            "test_metric": metric_to_dict(test_metric),
        }
        _atomic_write_json(self.checkpoint_path, payload)

    def _load_checkpoint(
        self,
    ) -> tuple[
        SkillOptState,
        list[EpochRecord],
        str,
        MetricResult | None,
        dict[str, int],
    ]:
        try:
            payload = json.loads(
                self.checkpoint_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid SkillOpt checkpoint") from exc
        if not isinstance(payload, dict):
            raise ValueError("SkillOpt checkpoint must be an object")
        schema_version = payload.get("schema_version")
        if schema_version not in {1, self.SCHEMA_VERSION}:
            raise ValueError("unsupported SkillOpt checkpoint schema")
        if not _resume_signature_matches(
            payload.get("signature"),
            self.config.signature(),
        ):
            raise ResumeMismatchError(
                "SkillOpt resume signature does not match checkpoint"
            )
        raw_state = payload.get("state")
        if not isinstance(raw_state, dict):
            raise ValueError("SkillOpt checkpoint state must be an object")
        state = SkillOptState(
            current=SkillVersion.from_dict(raw_state.get("current")),
            best=SkillVersion.from_dict(raw_state.get("best")),
            next_epoch=int(raw_state.get("next_epoch")),
            next_batch=int(raw_state.get("next_batch", 1)),
            optimizer_memory=str(
                raw_state.get("optimizer_memory", "")
            ),
        )
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            raise ValueError("SkillOpt checkpoint records must be an array")
        previous_epoch_skill = payload.get("previous_epoch_skill")
        if (
            not isinstance(previous_epoch_skill, str)
            or not previous_epoch_skill.strip()
        ):
            raise ValueError(
                "SkillOpt checkpoint previous skill must be non-empty"
            )
        scheduler_state = payload.get("scheduler_state")
        if not isinstance(scheduler_state, dict):
            raise ValueError(
                "SkillOpt checkpoint scheduler state must be an object"
            )
        records = [
            EpochRecord.from_dict(record)
            for record in raw_records
        ]
        self._validate_checkpoint_progress(
            state=state,
            records=records,
            scheduler_state=scheduler_state,
            schema_version=int(schema_version),
        )
        return (
            state,
            records,
            previous_epoch_skill,
            metric_from_dict(payload.get("test_metric")),
            scheduler_state,
        )

    def _validate_checkpoint_progress(
        self,
        *,
        state: SkillOptState,
        records: list[EpochRecord],
        scheduler_state: dict[str, int],
        schema_version: int,
    ) -> None:
        if not 1 <= state.next_epoch <= self.config.epochs + 1:
            raise ValueError(
                "SkillOpt checkpoint next_epoch is outside the run plan"
            )
        if state.next_epoch == self.config.epochs + 1:
            if state.next_batch != 1:
                raise ValueError(
                    "completed SkillOpt checkpoint must reset next_batch"
                )
        elif not 1 <= state.next_batch <= self.config.steps_per_epoch:
            raise ValueError(
                "SkillOpt checkpoint next_batch is outside the epoch plan"
            )
        current_step = scheduler_state.get("current_step")
        if (
            isinstance(current_step, bool)
            or not isinstance(current_step, int)
            or current_step != len(records)
        ):
            raise ValueError(
                "SkillOpt checkpoint scheduler progress does not match "
                "completed optimizer records"
            )
        if schema_version == 1:
            return
        expected_progress = (
            (state.next_epoch - 1) * self.config.steps_per_epoch
            + state.next_batch
            - 1
        )
        if len(records) != expected_progress:
            raise ValueError(
                "SkillOpt checkpoint batch progress does not match records"
            )
        for index, record in enumerate(records):
            expected_epoch = index // self.config.steps_per_epoch + 1
            expected_batch = index % self.config.steps_per_epoch + 1
            expected_ids = self._epoch_batches(expected_epoch)[
                expected_batch - 1
            ]
            if (
                record.epoch != expected_epoch
                or record.batch_index != expected_batch
                or record.case_ids != expected_ids
            ):
                raise ValueError(
                    "SkillOpt checkpoint records do not match the frozen "
                    "batch plan"
                )

    def _write_artifacts(self, result: SkillOptRunResult) -> None:
        _atomic_write_text(
            self.output_dir / "current_skill.md",
            result.state.current.text.rstrip() + "\n",
        )
        _atomic_write_text(
            self.output_dir / "best_skill.md",
            result.state.best.text.rstrip() + "\n",
        )
        write_report(
            self.output_dir / "skillopt_report.json",
            build_report(self.config, result),
        )


def _resume_signature_matches(
    checkpoint: Any,
    configured: dict[str, Any],
) -> bool:
    if checkpoint == configured:
        return True
    if not isinstance(checkpoint, dict):
        return False
    if (
        "initial_skill_sha256" in configured
        and "initial_skill_sha256" not in checkpoint
    ):
        legacy = dict(configured)
        legacy.pop("initial_skill_sha256")
        return checkpoint == legacy
    return False


def _inject_placeholder(version: SkillVersion) -> SkillVersion:
    return replace(
        version,
        text=inject_slow_update_block(version.text),
    )


def _preserve_slow_update(current: str, candidate: str) -> str:
    if SLOW_UPDATE_START not in current:
        return candidate
    return replace_slow_update_block(
        candidate,
        extract_slow_update_block(current),
    )


def _metric_summary(metric: MetricResult) -> dict[str, Any]:
    return {
        "primary": metric.primary,
        "metrics": dict(metric.metrics),
        "sample_count": metric.sample_count,
    }


def _subset_metric_summary(
    metric: MetricResult,
    case_ids: tuple[str, ...],
) -> dict[str, Any]:
    if not case_ids:
        raise ValueError("SkillOpt reflection minibatch cannot be empty")
    try:
        rows = [metric.case_metrics[case_id] for case_id in case_ids]
    except KeyError as exc:
        raise ValueError(
            "SkillOpt reflection minibatch is absent from rollout metrics"
        ) from exc
    metric_names = tuple(metric.metrics)
    averages = {
        name: sum(float(row[name]) for row in rows) / len(rows)
        for name in metric_names
    }
    primary_name = (
        "soft"
        if "soft" in averages
        else next(iter(averages), None)
    )
    return {
        "primary": (
            averages[primary_name]
            if primary_name is not None
            else metric.primary
        ),
        "metrics": averages,
        "sample_count": len(case_ids),
    }


_REFLECTION_HARD_METRICS = {
    "alfworld": "hard",
    "deepplanning": "hard",
    "docvqa": "hard",
    "lmb": "exact_match",
    "officeqa": "em",
    "searchqa": "exact_match",
    "spreadsheetbench": "hard",
}


def _reflection_groups(
    *,
    benchmark: str,
    case_ids: tuple[str, ...],
    metric: MetricResult,
) -> tuple[list[str], list[str]]:
    try:
        hard_name = _REFLECTION_HARD_METRICS[benchmark]
    except KeyError as exc:
        raise ValueError(
            f"unsupported SkillOpt benchmark {benchmark!r}"
        ) from exc
    failures: list[str] = []
    successes: list[str] = []
    for case_id in case_ids:
        try:
            row = metric.case_metrics[case_id]
        except KeyError as exc:
            raise ValueError(
                "SkillOpt reflection case is missing its hard metric"
            ) from exc
        value = row.get(hard_name, row.get("hard"))
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            raise ValueError(
                "SkillOpt reflection case is missing its hard metric"
            )
        target = successes if float(value) > 1e-9 else failures
        target.append(case_id)
    return failures, successes


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    _atomic_write_text(path, text)


def _atomic_write_preserving_json(
    path: Path,
    payload: dict[str, Any],
) -> Path:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    target = path
    if target.exists():
        if target.read_text(encoding="utf-8") == text:
            return target
        for attempt in range(2, 10_000):
            candidate = target.with_name(
                f"{target.stem}_attempt_{attempt:02d}{target.suffix}"
            )
            if not candidate.exists():
                target = candidate
                break
        else:
            raise RuntimeError(
                "SkillOpt rollout artifact attempts are exhausted"
            )
    _atomic_write_text(target, text)
    return target


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    )
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
