"""Evaluate a skill against an independently frozen task manifest for before/after comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from skilladam.product.evaluation import EvaluationRunner
from skilladam.product.evaluation_planning import EvaluationPlanner
from skilladam.product.feedback import extract_final_output
from skilladam.product.model import ProductModel
from skilladam.product.models import digest_value
from skilladam.product.runtime import build_runtime_config, model_from_runtime
from skilladam.product.task_manifest import ingest_task_manifest
from skilladam.product.tasking import freeze_task_pool, freeze_task_suite


def evaluate_manifest(
    *,
    skill_path: Path,
    manifest: dict[str, Any],
    model: ProductModel,
) -> dict[str, Any]:
    skill_path = Path(skill_path).resolve()
    skill = skill_path.read_text(encoding="utf-8")
    records = manifest.get("tasks")
    expected_count = len(records) if isinstance(records, list) else 0
    drafts = ingest_task_manifest(manifest, expected_count=expected_count)
    pool = freeze_task_pool(
        drafts,
        pool_id=f"external_pool_{digest_value(manifest)}",
        metadata={"purpose": "independent_effect_evaluation"},
    )
    suite = freeze_task_suite(
        pool,
        suite_id=f"external_suite_{pool.digest}",
        validation_size=1,
    )
    tasks, plan = EvaluationPlanner().freeze_plan(pool, suite)
    outputs: dict[str, Any] = {}

    def _rollout(current_skill, task):
        raw = model.rollout(current_skill, task)
        outputs[task.task_id] = extract_final_output(raw)
        return raw

    ordered_ids = tuple(task.task_id for task in tasks)
    result = EvaluationRunner(
        tasks,
        plan,
        _rollout,
        judge=model.judge,
    ).evaluate(
        skill,
        candidate_digest=digest_value({skill_path.name: skill}),
        task_ids=ordered_ids,
    )
    return {
        "schema_version": 1,
        "kind": "independent_task_manifest_evaluation",
        "skill_path": str(skill_path),
        "manifest_digest": digest_value(manifest),
        "evaluation_plan_digest": plan.digest,
        "task_ids": list(ordered_ids),
        "outputs": outputs,
        "validation": result.to_dict(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evaluate-task-manifest")
    parser.add_argument("--skill", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--platform",
        required=True,
        choices=("claude-code", "codex", "github-copilot"),
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must contain a JSON object")
    runtime = build_runtime_config(
        provider="platform-cli",
        platform=args.platform,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=args.timeout_seconds,
    )
    report = evaluate_manifest(
        skill_path=args.skill,
        manifest=manifest,
        model=model_from_runtime(runtime),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="",
    )
    print(json.dumps(report["validation"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
