"""Independent execution-backend bridge for the SkillOpt baseline."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

from skilladam.baselines.skillopt.contracts import (
    OptimizerCall,
    OptimizerResult,
    RolloutBatch,
    RolloutCall,
    SkillOptCallbacks,
    SkillOptConfig,
    SkillOptRunResult,
)
from skilladam.baselines.skillopt.runner import (
    ResumeMismatchError,
    SkillOptRunner,
)
from skilladam.benchmarks.base import BenchmarkAdapter
from skilladam.execution.contracts import (
    BackendContext,
    ExecutionBackend,
    GenerationRequest,
)
from skilladam.execution.evaluate import EvaluationRunner
from skilladam.execution.skills import (
    ResolvedSkill,
    SkillResolver,
    SkillSource,
)
from skilladam.methods.skillopt_prompts import (
    SkillOptPromptBuilder,
    parse_skillopt_generation,
)
from skilladam.types import BenchmarkCase, MetricResult


_METRIC_KEYS = {
    "alfworld": ("hard", "soft"),
    "deepplanning": ("hard", "soft"),
    "docvqa": ("hard", "soft"),
    "lmb": ("exact_match", "token_f1"),
    "officeqa": ("em", "f1"),
    "searchqa": ("exact_match", "token_f1"),
    "spreadsheetbench": ("hard", "per_cell_pass_rate"),
}


class SkillOptExecutionBridge:
    """Map the common backend onto independent SkillOpt callbacks."""

    def __init__(
        self,
        *,
        config: SkillOptConfig,
        adapter: BenchmarkAdapter,
        backend: ExecutionBackend,
        context: BackendContext,
        output_dir: Path,
        evaluation_runner: EvaluationRunner | None = None,
        prompt_builder: SkillOptPromptBuilder | None = None,
    ) -> None:
        if not isinstance(config, SkillOptConfig):
            raise ValueError("SkillOpt bridge configuration is invalid")
        if adapter.manifest.name != config.benchmark:
            raise ValueError("SkillOpt adapter does not match benchmark")
        if (
            not isinstance(context, BackendContext)
            or context.benchmark != config.benchmark
            or context.method != "skillopt"
            or context.model != config.model
            or context.seed != config.seed
        ):
            raise ValueError(
                "SkillOpt backend context does not match configuration"
            )
        self.config = config
        self.adapter = adapter
        self.backend = backend
        self.context = context
        self.output_dir = Path(output_dir)
        self.evaluation_runner = evaluation_runner or EvaluationRunner()
        self.prompt_builder = prompt_builder or SkillOptPromptBuilder(
            benchmark=config.benchmark,
        )
        self._cases_by_id: dict[str, BenchmarkCase] = {}

    def run(
        self,
        *,
        cases: Sequence[BenchmarkCase],
        initial_skill: str,
        resume: bool = False,
    ) -> SkillOptRunResult:
        skill = _initial_skill(initial_skill)
        digest = sha256(skill.encode("utf-8")).hexdigest()
        configured_digest = self.config.initial_skill_sha256
        if configured_digest is not None and configured_digest != digest:
            raise ResumeMismatchError(
                "SkillOpt initial skill does not match the configured "
                "initial-skill hash"
            )
        if resume:
            checkpoint_digest = _checkpoint_initial_hash(
                self.output_dir / SkillOptRunner.CHECKPOINT_NAME
            )
            if (
                checkpoint_digest is not None
                and checkpoint_digest != digest
            ):
                raise ResumeMismatchError(
                    "SkillOpt initial skill does not match the checkpoint"
                )
        effective_config = (
            self.config
            if configured_digest is not None
            else replace(
                self.config,
                initial_skill_sha256=digest,
            )
        )
        selected = self._validate_cases(cases, effective_config)
        self._cases_by_id = {
            case.case_id: case for case in selected
        }
        runner = SkillOptRunner(
            effective_config,
            callbacks=self._callbacks(effective_config),
            output_dir=self.output_dir,
        )
        return runner.run(initial_skill=skill, resume=resume)

    def _callbacks(
        self,
        config: SkillOptConfig,
    ) -> SkillOptCallbacks:
        def _rollout(call: RolloutCall) -> RolloutBatch:
            return self._rollout(call, config)

        def _optimizer(call: OptimizerCall) -> OptimizerResult:
            prompt = self.prompt_builder.build(call)
            result = self.backend.generate(
                GenerationRequest(
                    stage=call.stage,
                    messages=prompt.messages,
                    context=self.context,
                    metadata={
                        "epoch": call.epoch,
                        "case_ids": list(call.case_ids),
                        "edit_budget": call.edit_budget,
                    },
                )
            )
            return parse_skillopt_generation(call.stage, result)

        return SkillOptCallbacks(
            rollout=_rollout,
            reflect=_optimizer,
            merge=_optimizer,
            select=_optimizer,
            slow_update=_optimizer,
            update_memory=_optimizer,
        )

    def _rollout(
        self,
        call: RolloutCall,
        config: SkillOptConfig,
    ) -> RolloutBatch:
        split = _split_for_case_ids(call.case_ids, config)
        cases = tuple(
            self._cases_by_id[case_id]
            for case_id in call.case_ids
        )
        resolver = _resolver_for_skill(
            cases=cases,
            scope=config.slice_id,
            skill=call.skill,
        )
        result = self.evaluation_runner.evaluate(
            adapter=self.adapter,
            cases=cases,
            method="skillopt",
            split=split,
            skill_resolver=resolver,
            backend=self.backend,
            context=self.context,
            stage=call.stage,
        )
        return RolloutBatch(
            metric=_skillopt_metric(
                config.benchmark,
                result.metrics.overall,
            ),
            traces=tuple(item.to_dict() for item in result.cases),
            usage=result.usage,
        )

    def _validate_cases(
        self,
        cases: Sequence[BenchmarkCase],
        config: SkillOptConfig,
    ) -> tuple[BenchmarkCase, ...]:
        selected = tuple(cases)
        if not selected or any(
            not isinstance(case, BenchmarkCase) for case in selected
        ):
            raise ValueError("SkillOpt bridge requires BenchmarkCase values")
        ids = tuple(case.case_id for case in selected)
        if len(set(ids)) != len(ids):
            raise ValueError("SkillOpt bridge case IDs must be unique")
        expected = (
            *config.train_case_ids,
            *config.validation_case_ids,
            *config.test_case_ids,
        )
        if set(ids) != set(expected) or len(ids) != len(expected):
            raise ValueError(
                "SkillOpt bridge cases must exactly cover configured IDs"
            )
        expected_splits = {
            case_id: split
            for split, values in (
                ("train", config.train_case_ids),
                ("validation", config.validation_case_ids),
                ("test", config.test_case_ids),
            )
            for case_id in values
        }
        for case in selected:
            split = case.metadata.get("split")
            if split is not None and split != expected_splits[case.case_id]:
                raise ValueError(
                    "SkillOpt bridge case split does not match configuration"
                )
            if config.benchmark == "deepplanning":
                scope = case.metadata.get(
                    "slice",
                    case.payload.get("slice"),
                )
                if scope != config.slice_id:
                    raise ValueError(
                        "SkillOpt DeepPlanning cases must match slice_id"
                    )
        return selected


def _split_for_case_ids(
    case_ids: tuple[str, ...],
    config: SkillOptConfig,
) -> str:
    values = set(case_ids)
    for split, configured in (
        ("train", config.train_case_ids),
        ("validation", config.validation_case_ids),
        ("test", config.test_case_ids),
    ):
        if values <= set(configured):
            return split
    raise ValueError(
        "SkillOpt rollout case IDs cross configured split boundaries"
    )


def _resolver_for_skill(
    *,
    cases: tuple[BenchmarkCase, ...],
    scope: str,
    skill: str,
) -> SkillResolver:
    text = _initial_skill(skill)
    digest = sha256(text.encode("utf-8")).hexdigest()
    source = SkillSource(
        source="explicit_file",
        sha256=digest,
        artifact_id=None,
        scope=scope,
    )
    resolved = ResolvedSkill(
        text=text,
        sha256=digest,
        source=source,
    )
    return SkillResolver(
        {case.case_id: resolved for case in cases},
        (source,),
    )


def _initial_skill(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("SkillOpt requires an explicit initial skill")
    return value


def _checkpoint_initial_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload["signature"].get("initial_skill_sha256")
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ):
        return None
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        return None
    return value


def _skillopt_metric(
    benchmark: str,
    metric: MetricResult,
) -> MetricResult:
    try:
        hard_key, soft_key = _METRIC_KEYS[benchmark]
    except KeyError as exc:
        raise ValueError(
            f"SkillOpt has no metric projection for {benchmark!r}"
        ) from exc
    try:
        hard = float(metric.metrics[hard_key])
        soft = float(metric.metrics[soft_key])
        case_metrics = {
            case_id: {
                "hard": float(values[hard_key]),
                "soft": float(values[soft_key]),
            }
            for case_id, values in metric.case_metrics.items()
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"SkillOpt metric projection for {benchmark!r} is invalid"
        ) from exc
    return MetricResult(
        primary=hard,
        metrics={"hard": hard, "soft": soft},
        case_metrics=case_metrics,
        sample_count=metric.sample_count,
        metadata=metric.metadata,
    )


__all__ = ["SkillOptExecutionBridge"]
