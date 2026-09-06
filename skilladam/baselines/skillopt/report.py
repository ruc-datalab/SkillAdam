"""Deterministic sanitized SkillOpt run reports."""

from __future__ import annotations

import json
import os
from pathlib import Path
import uuid
from typing import Any

from skilladam.baselines.skillopt.contracts import (
    SkillOptConfig,
    SkillOptRunResult,
)


def build_report(
    config: SkillOptConfig,
    result: SkillOptRunResult,
) -> dict[str, Any]:
    """Build a report without skills, prompts, endpoints, or local paths."""

    best = result.state.best
    return {
        "schema_version": 1,
        "method": "skillopt",
        "benchmark": config.benchmark,
        "slice": config.slice_id,
        "incomplete_smoke": (
            config.max_batches is not None
            and len(result.records) < config.total_steps
        ),
        "config": {
            "epochs": config.epochs,
            "seed": config.seed,
            "model": config.model,
            "train_case_ids": list(config.train_case_ids),
            "validation_case_ids": list(
                config.validation_case_ids
            ),
            "test_case_ids": list(config.test_case_ids),
            "scheduler_mode": config.scheduler_mode,
            "max_edit_budget": config.max_edit_budget,
            "min_edit_budget": config.min_edit_budget,
            "gate_metric": config.gate_metric,
            "mixed_weight": config.mixed_weight,
            "use_slow_update": config.use_slow_update,
            "slow_update_mode": config.slow_update_mode,
            "slow_update_start_epoch": (
                config.slow_update_start_epoch
            ),
            "slow_update_samples": config.slow_update_samples,
            "max_batches": config.max_batches,
        },
        "state": {
            "current_score": result.state.current.score,
            "best_score": best.score,
            "base_origin": best.base_origin,
            "slow_update_origin": best.slow_update_origin,
            "current_base_origin": (
                result.state.current.base_origin
            ),
            "best_base_origin": best.base_origin,
            "current_slow_update_origin": (
                result.state.current.slow_update_origin
            ),
            "best_slow_update_origin": best.slow_update_origin,
            "next_epoch": result.state.next_epoch,
            "next_batch": result.state.next_batch,
            "optimizer_memory_present": bool(
                result.state.optimizer_memory
            ),
        },
        "epochs": [record.to_dict() for record in result.records],
        "test_metric": _metric_summary(result.test_metric),
        "resumed_from_epoch": result.resumed_from_epoch,
        "artifacts": {
            "best_skill": "best_skill.md",
            "checkpoint": "skillopt_checkpoint.json",
            "current_skill": "current_skill.md",
            "rollouts": "rollouts",
            "usage_ledger": "usage.jsonl",
        },
    }


def write_report(path: Path, payload: dict[str, Any]) -> Path:
    """Atomically write a stable JSON report."""

    target = Path(path)
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    )
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _metric_summary(metric: Any) -> dict[str, Any] | None:
    if metric is None:
        return None
    return {
        "primary": metric.primary,
        "metrics": dict(metric.metrics),
        "sample_count": metric.sample_count,
    }
