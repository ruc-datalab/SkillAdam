"""Provider-neutral bridge from public execution to the SkillAdam core."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import random
from typing import Any

from skilladam.benchmarks.base import BenchmarkAdapter
from skilladam.benchmarks.deepplanning.gate import (
    ShoppingAcceptanceGate,
    TravelAcceptanceGate,
)
from skilladam.benchmarks.deepplanning.manifest import SLICE_SPECS
from skilladam.benchmarks.registry import list_benchmarks
from skilladam.core.case_sampler import (
    IterationCases,
    build_disjoint_sampler,
    build_same_batch_sampler,
)
from skilladam.core.feedback_loop import (
    CheckpointStore,
    FeedbackLoop,
    FeedbackLoopConfig,
    FeedbackLoopResult,
    IterationContext,
    IterationRecord,
    PostDecisionContext,
    PostDecisionHook,
    ResumeMismatchError,
)
from skilladam.core.momentum import (
    MomentumTracker,
    build_momentum_tools,
)
from skilladam.core.stage0 import (
    Stage0ExecutionResult,
    Stage0Executor,
    Stage0PromptBuilder,
)
from skilladam.core.trajectory_condenser import condense_trajectory_md
from skilladam.cost.ledger import UsageLedger
from skilladam.execution.contracts import (
    BackendContext,
    ExecutionBackend,
    GenerationRequest,
    GenerationResult,
    ToolCall,
)
from skilladam.execution.evaluate import EvaluationResult, EvaluationRunner
from skilladam.execution.skills import (
    ResolvedSkill,
    SkillResolver,
    SkillSource,
)
from skilladam.execution.store import RunStore
from skilladam.methods.skilladam_prompts import (
    BenchmarkPromptContext,
    CondensedTrajectory,
    IterationGeneration,
    IterationPromptBuilder,
    IterationPromptContext,
    load_benchmark_prompt_context,
)
from skilladam.types import (
    BenchmarkCase,
    GateDecision,
    MetricResult,
    UsageRecord,
)


MOMENTUM_TEMPLATE = (
    Path(__file__).parents[1]
    / "core"
    / "prompts"
    / "momentum_update_system.md"
).read_text(encoding="utf-8")
_MOMENTUM_TOOL_NAMES = {
    "report_problem",
    "resolve_problem",
    "reopen_problem",
    "record_attempt",
    "report_disobedience",
    "set_examples_state",
}


@dataclass(frozen=True)
class SkillAdamRunConfig:
    """Portable optimization settings for one scoped SkillAdam run."""

    benchmark: str
    split: str
    scope: str | None
    train_size: int
    validation_size: int
    stage0_size: int
    feedback_loop: FeedbackLoopConfig
    validation_split: str = "train"
    sampling_strategy: str = "random"
    seed: int = 42
    stage0_attempts: int = 2
    validation_mode: str = "disjoint"
    include_partial_final_batch: bool = False
    shuffle_optimization_pool: bool = False
    optimization_case_ids: tuple[str, ...] = ()
    stage0_case_ids: tuple[str, ...] = ()
    gate_profile: str = "default"
    prompt_profile: str = "v2"
    trajectory_compression: str = "deterministic"
    optimizer_history_turns: int | None = None
    optimizer_context_limit: int | None = None

    def __post_init__(self) -> None:
        if self.benchmark not in list_benchmarks():
            raise ValueError(f"unknown benchmark {self.benchmark!r}")
        if self.split != "train":
            raise ValueError("SkillAdam optimization requires the train split")
        if self.validation_split not in {"train", "validation"}:
            raise ValueError(
                "SkillAdam validation_split must be train or validation"
            )
        if self.benchmark == "deepplanning":
            if self.scope not in SLICE_SPECS:
                raise ValueError(
                    "DeepPlanning optimization requires an explicit scope"
                )
        elif self.scope is not None:
            raise ValueError("scope is only supported for DeepPlanning")
        for field_name in (
            "train_size",
            "validation_size",
            "stage0_size",
            "stage0_attempts",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise ValueError(f"{field_name} must be a positive integer")
        if self.sampling_strategy not in {"random", "sequential"}:
            raise ValueError(
                "sampling_strategy must be random or sequential"
            )
        if self.validation_mode not in {"disjoint", "same_batch"}:
            raise ValueError(
                "validation_mode must be disjoint or same_batch"
            )
        if self.validation_mode == "same_batch":
            if self.validation_split != self.split:
                raise ValueError(
                    "same_batch validation requires one optimization pool"
                )
            if self.validation_size != self.train_size:
                raise ValueError(
                    "same_batch validation_size must equal train_size"
                )
        for field_name in (
            "include_partial_final_batch",
            "shuffle_optimization_pool",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be boolean")
        stage0_ids = tuple(self.stage0_case_ids)
        optimization_ids = tuple(self.optimization_case_ids)
        if any(
            not isinstance(case_id, str) or not case_id.strip()
            for case_id in optimization_ids
        ):
            raise ValueError(
                "optimization_case_ids must contain non-empty strings"
            )
        if len(set(optimization_ids)) != len(optimization_ids):
            raise ValueError("optimization_case_ids must be unique")
        object.__setattr__(
            self,
            "optimization_case_ids",
            optimization_ids,
        )
        if any(
            not isinstance(case_id, str) or not case_id.strip()
            for case_id in stage0_ids
        ):
            raise ValueError("stage0_case_ids must contain non-empty strings")
        if len(set(stage0_ids)) != len(stage0_ids):
            raise ValueError("stage0_case_ids must be unique")
        if stage0_ids and len(stage0_ids) != self.stage0_size:
            raise ValueError(
                "stage0_case_ids length must equal stage0_size"
            )
        object.__setattr__(self, "stage0_case_ids", stage0_ids)
        if (
            not isinstance(self.gate_profile, str)
            or not self.gate_profile.strip()
        ):
            raise ValueError("gate_profile must be non-empty text")
        object.__setattr__(
            self,
            "gate_profile",
            self.gate_profile.strip(),
        )
        if (
            not isinstance(self.prompt_profile, str)
            or not self.prompt_profile.strip()
        ):
            raise ValueError("prompt_profile must be non-empty text")
        object.__setattr__(
            self,
            "prompt_profile",
            self.prompt_profile.strip(),
        )
        if self.trajectory_compression not in {"deterministic", "llm"}:
            raise ValueError(
                "trajectory_compression must be deterministic or llm"
            )
        if self.optimizer_history_turns is not None and (
            isinstance(self.optimizer_history_turns, bool)
            or not isinstance(self.optimizer_history_turns, int)
            or self.optimizer_history_turns < 1
        ):
            raise ValueError(
                "optimizer_history_turns must be a positive integer or null"
            )
        if self.optimizer_context_limit is not None and (
            isinstance(self.optimizer_context_limit, bool)
            or not isinstance(self.optimizer_context_limit, int)
            or self.optimizer_context_limit < 1
        ):
            raise ValueError(
                "optimizer_context_limit must be a positive integer "
                "or null"
            )
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        if not isinstance(self.feedback_loop, FeedbackLoopConfig):
            raise ValueError("feedback_loop configuration is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": self.benchmark,
            "split": self.split,
            "scope": self.scope,
            "train_size": self.train_size,
            "validation_size": self.validation_size,
            "stage0_size": self.stage0_size,
            "validation_split": self.validation_split,
            "sampling_strategy": self.sampling_strategy,
            "seed": self.seed,
            "stage0_attempts": self.stage0_attempts,
            "validation_mode": self.validation_mode,
            "include_partial_final_batch": (
                self.include_partial_final_batch
            ),
            "shuffle_optimization_pool": self.shuffle_optimization_pool,
            "optimization_case_ids": list(self.optimization_case_ids),
            "stage0_case_ids": list(self.stage0_case_ids),
            "gate_profile": self.gate_profile,
            "prompt_profile": self.prompt_profile,
            "trajectory_compression": self.trajectory_compression,
            "optimizer_history_turns": self.optimizer_history_turns,
            "optimizer_context_limit": self.optimizer_context_limit,
            "feedback_loop": asdict(self.feedback_loop),
        }


@dataclass(frozen=True)
class SkillAdamRunResult:
    """Completed SkillAdam run and its in-memory method state."""

    final_skill: str
    feedback_loop: FeedbackLoopResult
    momentum: MomentumTracker
    stage0: Stage0ExecutionResult | None


class SkillAdamRunner:
    """Connect Stage0, rollouts, prompts, gate, Momentum, and checkpoints."""

    def __init__(
        self,
        *,
        config: SkillAdamRunConfig,
        adapter: BenchmarkAdapter,
        backend: ExecutionBackend,
        context: BackendContext,
        output_dir: Path,
        baseline_backend: ExecutionBackend | None = None,
        baseline_context: BackendContext | None = None,
        evaluation_runner: EvaluationRunner | None = None,
    ) -> None:
        if not isinstance(config, SkillAdamRunConfig):
            raise ValueError("SkillAdam run configuration is invalid")
        if adapter.manifest.name != config.benchmark:
            raise ValueError("adapter does not match SkillAdam benchmark")
        if (
            not isinstance(context, BackendContext)
            or context.benchmark != config.benchmark
            or context.method != "skilladam"
            or context.seed != config.seed
        ):
            raise ValueError(
                "SkillAdam backend context does not match run configuration"
            )
        if baseline_context is not None and (
            baseline_context.benchmark != config.benchmark
            or baseline_context.method != "baseline"
            or baseline_context.seed != config.seed
        ):
            raise ValueError("baseline context does not match SkillAdam run")
        self.config = config
        self.adapter = adapter
        self.backend = backend
        self.context = context
        self.output_dir = Path(output_dir)
        self.baseline_backend = baseline_backend
        self.baseline_context = baseline_context
        self.evaluation_runner = evaluation_runner or EvaluationRunner()
        self.momentum = MomentumTracker()
        self._run_store: RunStore | None = None
        self._usage_ledger: UsageLedger | None = None
        self._cases_by_id: dict[str, BenchmarkCase] = {}
        self._trajectory_cache: dict[int, tuple[CondensedTrajectory, ...]] = {}
        self._prompt_context: BenchmarkPromptContext | None = None
        self._optimizer_history: list[dict[str, str]] = []
        self._active_iteration: int | None = None
        self._validation_rollout_counts: dict[int, int] = {}

    def run(
        self,
        *,
        cases: Sequence[BenchmarkCase],
        initial_skill: str | None = None,
        resume: bool = False,
    ) -> SkillAdamRunResult:
        selected = self._validate_cases(cases)
        if (
            not resume
            and initial_skill is None
            and (
                self.baseline_backend is None
                or self.baseline_context is None
            )
        ):
            raise ValueError(
                "Stage0 requires an explicit baseline backend and context"
            )
        sampler = self._build_sampler(selected)
        self._cases_by_id = {case.case_id: case for case in selected}
        self._run_store = RunStore(self.output_dir, resume=resume)
        self._usage_ledger = UsageLedger(self.output_dir / "usage.jsonl")
        checkpoint_store = CheckpointStore(self.output_dir)

        stage0_result: Stage0ExecutionResult | None = None
        if resume:
            checkpoint = checkpoint_store.load_payload()
            self._restore_optimizer_history(checkpoint)
            expected_initial_hash = _checkpoint_initial_hash(checkpoint)
            if initial_skill is not None and (
                _skill_hash(_skill_text(initial_skill))
                != expected_initial_hash
            ):
                raise ResumeMismatchError(
                    "resume initial skill does not match the checkpoint"
                )
            initial = str(checkpoint.get("current_skill", ""))
            initial_hash = expected_initial_hash
            self._prompt_context = load_benchmark_prompt_context(
                self.config.benchmark,
                trajectory_count=self.config.train_size,
                benchmark_domain_info_md=(
                    "Training trajectories are supplied per iteration."
                ),
                scope=self.config.scope,
                prompt_profile=self.config.prompt_profile,
            )
        elif initial_skill is None:
            initial, stage0_result = self._run_stage0(selected)
            initial_hash = _skill_hash(initial)
        else:
            initial = _skill_text(initial_skill)
            initial_hash = _skill_hash(initial)
            self._prompt_context = load_benchmark_prompt_context(
                self.config.benchmark,
                trajectory_count=self.config.train_size,
                benchmark_domain_info_md=(
                    "Training trajectories are supplied per iteration."
                ),
                scope=self.config.scope,
                prompt_profile=self.config.prompt_profile,
            )

        gate = _SerializableAdapterGate(
            adapter=self.adapter,
            benchmark=self.config.benchmark,
            scope=self.config.scope,
            profile=self.config.gate_profile,
        )
        loop = FeedbackLoop(
            self.config.feedback_loop,
            gate=gate,
            checkpoint_store=checkpoint_store,
            momentum=self.momentum,
        )
        hook = self._post_decision_hook(initial_hash)
        feedback_result = loop.run(
            initial_skill=initial,
            batches=sampler,
            propose_patch=self._propose_patch,
            evaluate=self._evaluate_validation,
            post_decision_hook=hook,
            resume=resume,
        )
        self._store.write_text(
            "current_skill.md",
            feedback_result.final_skill,
        )
        self._store.write_text(
            "final_skill.md",
            feedback_result.final_skill,
        )
        return SkillAdamRunResult(
            final_skill=feedback_result.final_skill,
            feedback_loop=feedback_result,
            momentum=self.momentum,
            stage0=stage0_result,
        )

    def run_stage0_only(
        self,
        *,
        cases: Sequence[BenchmarkCase],
    ) -> Stage0ExecutionResult:
        """Generate one reusable initial skill without starting iterations."""

        if self.baseline_backend is None or self.baseline_context is None:
            raise ValueError(
                "Stage0 requires an explicit baseline backend and context"
            )
        selected = self._validate_stage0_cases(cases)
        self._cases_by_id = {case.case_id: case for case in selected}
        self._run_store = RunStore(self.output_dir, resume=False)
        self._usage_ledger = UsageLedger(self.output_dir / "usage.jsonl")
        initial, result = self._run_stage0(selected)
        self._store.write_json(
            "stage0_manifest.json",
            {
                "schema_version": 1,
                "benchmark": self.config.benchmark,
                "scope": self.config.scope,
                "seed": self.config.seed,
                "case_ids": list(
                    self.config.stage0_case_ids
                    or tuple(
                        case.case_id
                        for case in selected[: self.config.stage0_size]
                    )
                ),
                "initial_skill_sha256": _skill_hash(initial),
                "shared_by_methods": [
                    "skilladam",
                    "skillopt",
                ],
            },
        )
        return result

    @property
    def _store(self) -> RunStore:
        if self._run_store is None:
            raise RuntimeError("SkillAdam run store is not initialized")
        return self._run_store

    @property
    def _ledger(self) -> UsageLedger:
        if self._usage_ledger is None:
            raise RuntimeError("SkillAdam usage ledger is not initialized")
        return self._usage_ledger

    @property
    def _prompts(self) -> BenchmarkPromptContext:
        if self._prompt_context is None:
            raise RuntimeError("SkillAdam prompt context is not initialized")
        return self._prompt_context

    def _validate_cases(
        self,
        cases: Sequence[BenchmarkCase],
    ) -> tuple[BenchmarkCase, ...]:
        selected = tuple(cases)
        if not selected or any(
            not isinstance(case, BenchmarkCase) for case in selected
        ):
            raise ValueError("SkillAdam requires BenchmarkCase values")
        ids = tuple(case.case_id for case in selected)
        if len(set(ids)) != len(ids):
            raise ValueError("SkillAdam case IDs must be unique")
        if self.config.validation_split == self.config.split:
            required = (
                self.config.train_size
                if self.config.validation_mode == "same_batch"
                else self.config.train_size + self.config.validation_size
            )
            if len(selected) < required:
                raise ValueError(
                    f"SkillAdam requires at least {required} unique cases"
                )
            if self.config.stage0_size > len(selected):
                raise ValueError("stage0_size exceeds selected case count")
        else:
            train_cases = self._cases_for_split(
                selected,
                self.config.split,
            )
            validation_cases = self._cases_for_split(
                selected,
                self.config.validation_split,
            )
            if len(train_cases) < self.config.train_size:
                raise ValueError(
                    "SkillAdam train split has too few selected cases"
                )
            if len(validation_cases) < self.config.validation_size:
                raise ValueError(
                    "SkillAdam validation split has too few selected cases"
                )
            if self.config.stage0_size > len(train_cases):
                raise ValueError("stage0_size exceeds train case count")
        for case in selected:
            allowed_splits = {
                self.config.split,
                self.config.validation_split,
            }
            if case.metadata.get("split") not in {None, *allowed_splits}:
                raise ValueError(
                    "SkillAdam cases must belong to configured optimization "
                    "splits"
                )
            if self.config.benchmark == "deepplanning":
                case_scope = case.metadata.get(
                    "slice",
                    case.payload.get("slice"),
                )
                if case_scope != self.config.scope:
                    raise ValueError(
                        "DeepPlanning optimization cases must match scope"
                    )
        missing_stage0 = set(self.config.stage0_case_ids) - set(ids)
        if missing_stage0:
            raise ValueError(
                "configured Stage0 cases are absent from the optimization "
                f"pool: {sorted(missing_stage0)!r}"
            )
        if self.config.optimization_case_ids:
            configured = self.config.optimization_case_ids
            if len(configured) != len(ids) or set(configured) != set(ids):
                raise ValueError(
                    "configured optimization cases must exactly identify "
                    "the selected optimization pool"
                )
        return selected

    def _validate_stage0_cases(
        self,
        cases: Sequence[BenchmarkCase],
    ) -> tuple[BenchmarkCase, ...]:
        selected = tuple(cases)
        if not selected or any(
            not isinstance(case, BenchmarkCase) for case in selected
        ):
            raise ValueError("Stage0 requires BenchmarkCase values")
        ids = tuple(case.case_id for case in selected)
        if len(set(ids)) != len(ids):
            raise ValueError("Stage0 case IDs must be unique")
        required_ids = set(self.config.stage0_case_ids)
        if required_ids:
            missing = required_ids - set(ids)
            if missing:
                raise ValueError(
                    "configured Stage0 cases are absent from the pool: "
                    f"{sorted(missing)!r}"
                )
        elif len(selected) < self.config.stage0_size:
            raise ValueError("Stage0 case pool is too small")
        return selected

    def _build_sampler(
        self,
        cases: tuple[BenchmarkCase, ...],
    ):
        case_ids = list(
            self.config.optimization_case_ids
            or tuple(case.case_id for case in cases)
        )
        if self.config.shuffle_optimization_pool:
            random.Random(self.config.seed).shuffle(case_ids)
        if self.config.validation_mode == "same_batch":
            return build_same_batch_sampler(
                case_ids,
                batch_size=self.config.train_size,
                max_iterations=self.config.feedback_loop.max_iterations,
                strategy=self.config.sampling_strategy,
                seed=self.config.seed,
                include_partial_final_batch=(
                    self.config.include_partial_final_batch
                ),
            )
        if self.config.validation_split == self.config.split:
            return build_disjoint_sampler(
                tuple(case_ids),
                train_size=self.config.train_size,
                validation_size=self.config.validation_size,
                max_iterations=self.config.feedback_loop.max_iterations,
                strategy=self.config.sampling_strategy,
                seed=self.config.seed,
            )

        train_ids = tuple(
            case.case_id
            for case in self._cases_for_split(cases, self.config.split)
        )
        validation_ids = tuple(
            case.case_id
            for case in self._cases_for_split(
                cases,
                self.config.validation_split,
            )
        )
        if self.config.sampling_strategy == "random":
            rng = random.Random(self.config.seed)
            return tuple(
                IterationCases(
                    training_case_ids=tuple(
                        rng.sample(train_ids, self.config.train_size)
                    ),
                    validation_case_ids=tuple(
                        rng.sample(
                            validation_ids,
                            self.config.validation_size,
                        )
                    ),
                )
                for _ in range(
                    self.config.feedback_loop.max_iterations
                )
            )

        train_batches = _sequential_batches(
            train_ids,
            self.config.train_size,
        )
        validation_batches = _sequential_batches(
            validation_ids,
            self.config.validation_size,
        )
        return tuple(
            IterationCases(
                training_case_ids=train_batch,
                validation_case_ids=validation_batch,
            )
            for train_batch, validation_batch in zip(
                train_batches,
                validation_batches,
            )
        )[: self.config.feedback_loop.max_iterations]

    @staticmethod
    def _cases_for_split(
        cases: tuple[BenchmarkCase, ...],
        split: str,
    ) -> tuple[BenchmarkCase, ...]:
        return tuple(
            case
            for case in cases
            if case.metadata.get("split") == split
        )

    def _run_stage0(
        self,
        cases: tuple[BenchmarkCase, ...],
    ) -> tuple[str, Stage0ExecutionResult]:
        if self.baseline_backend is None or self.baseline_context is None:
            raise ValueError(
                "Stage0 requires an explicit baseline backend and context"
            )
        training_cases = (
            cases
            if self.config.validation_split == self.config.split
            else self._cases_for_split(cases, self.config.split)
        )
        if self.config.stage0_case_ids:
            by_id = {case.case_id: case for case in training_cases}
            stage0_cases = tuple(
                by_id[case_id]
                for case_id in self.config.stage0_case_ids
            )
        else:
            stage0_cases = training_cases[: self.config.stage0_size]
        resolver = SkillResolver.build(
            benchmark=self.config.benchmark,
            method="baseline",
            cases=stage0_cases,
            scope=self.config.scope,
        )
        evaluation = self.evaluation_runner.evaluate(
            adapter=self.adapter,
            cases=stage0_cases,
            method="baseline",
            split="train",
            skill_resolver=resolver,
            backend=self.baseline_backend,
            context=self.baseline_context,
            stage="stage0_baseline_rollout",
        )
        self._store.write_json(
            "stage0_evaluation.json",
            evaluation.to_dict(),
        )
        self._record_usage(
            evaluation.usage,
            stage="stage0_baseline_rollout",
        )
        condensed = tuple(
            self._condense(case, evaluated)
            for case, evaluated in zip(stage0_cases, evaluation.cases)
        )
        domain_info = "\n\n".join(
            f"### Case {item.case_id}\n\n{item.markdown}"
            for item in condensed
        )
        self._prompt_context = load_benchmark_prompt_context(
            self.config.benchmark,
            trajectory_count=len(condensed),
            benchmark_domain_info_md=domain_info,
            scope=self.config.scope,
            prompt_profile=self.config.prompt_profile,
        )

        stage0_attempt = 0

        def _invoke(messages: list[dict[str, str]]) -> str:
            nonlocal stage0_attempt
            stage0_attempt += 1
            result = self._generate(
                stage="stage0",
                messages=tuple(messages),
                metadata={
                    "benchmark": self.config.benchmark,
                    "scope": self.config.scope,
                    "trajectory_case_ids": [
                        item.case_id for item in condensed
                    ],
                },
            )
            self._store.write_json(
                f"stage0_attempt_{stage0_attempt:02d}.json",
                {
                    "schema_version": 1,
                    "attempt": stage0_attempt,
                    "generation": _generation_payload(result),
                },
            )
            self._record_usage(result.usage, stage="stage0")
            return result.text

        result = Stage0Executor(
            invoke_model=_invoke,
            max_attempts=self.config.stage0_attempts,
            prompt_builder=Stage0PromptBuilder(
                system_prompt_path=(
                    self._prompts.stage0_system_prompt_path
                ),
            ),
        ).run(self._prompts.stage0)
        initial = result.draft.skill_body_md
        self._store.write_json("stage0.json", result.draft.to_dict())
        self._store.write_text("initial_skill.md", initial)
        return initial, result

    def _propose_patch(self, context: IterationContext) -> str:
        self._active_iteration = context.iteration
        trajectories = self._training_trajectories(context)
        prompt = IterationPromptBuilder(
            self._prompts.iteration_template_path
        ).build(
            IterationPromptContext(
                benchmark=self._prompts,
                current_skill=context.current_skill,
                trajectories=trajectories,
                momentum_memory=self.momentum.render_memory(),
                previous_patch_error=context.previous_patch_error,
                edit_budget=context.edit_budget,
            )
        )
        self._reset_optimizer_context_if_needed(
            prompt=prompt,
            context=context,
        )
        messages = (
            prompt.messages[:1]
            + tuple(dict(message) for message in self._optimizer_history)
            + prompt.messages[1:]
        )
        result = self._generate(
            stage="iteration_patch",
            messages=messages,
            metadata={
                "iteration": context.iteration,
                "patch_attempt": context.patch_attempt,
                "training_case_ids": list(context.training_case_ids),
                "edit_budget": context.edit_budget,
            },
        )
        self._record_usage(result.usage, stage="iteration_patch")
        parsed = IterationGeneration.from_raw_text(result.text)
        self._store.write_json(
            (
                f"iterations/iter_{context.iteration:03d}/"
                f"patch_attempt_{context.patch_attempt:02d}.json"
            ),
            parsed.to_dict(),
        )
        self._store.write_json(
            (
                f"iterations/iter_{context.iteration:03d}/"
                f"patch_attempt_{context.patch_attempt:02d}_exchange.json"
            ),
            {
                "schema_version": 1,
                "iteration": context.iteration,
                "patch_attempt": context.patch_attempt,
                "user": prompt.user_prompt,
                "assistant": result.text,
            },
        )
        return parsed.patch

    def _reset_optimizer_context_if_needed(
        self,
        *,
        prompt,
        context: IterationContext,
    ) -> None:
        """Mirror the archived char-based optimizer context reset."""

        limit = self.config.optimizer_context_limit
        if limit is None or not self._optimizer_history:
            return
        conversation = (
            prompt.messages[:1]
            + tuple(dict(message) for message in self._optimizer_history)
        )
        estimated = _estimate_conversation_tokens(conversation)
        if estimated <= limit:
            return
        self._store.write_json(
            (
                f"iterations/iter_{context.iteration:03d}/"
                "conversation_pre_reset.json"
            ),
            {
                "schema_version": 1,
                "iteration": context.iteration,
                "estimated_context_units_before": estimated,
                "optimizer_context_limit": limit,
                "messages": [dict(message) for message in conversation],
                "preserved_state": ["current_skill", "momentum"],
            },
        )
        self._optimizer_history = []

    def _training_trajectories(
        self,
        context: IterationContext,
    ) -> tuple[CondensedTrajectory, ...]:
        cached = self._trajectory_cache.get(context.iteration)
        if cached is not None:
            return cached
        cases = self._lookup_cases(context.training_case_ids)
        evaluation = self._evaluate_skill(
            context.current_skill,
            cases,
            stage="training_rollout",
        )
        self._store.write_json(
            (
                f"iterations/iter_{context.iteration:03d}/"
                "training_evaluation.json"
            ),
            evaluation.to_dict(),
        )
        trajectories = tuple(
            self._condense(case, evaluated, allow_llm=True)
            for case, evaluated in zip(cases, evaluation.cases)
        )
        self._trajectory_cache[context.iteration] = trajectories
        self._store.write_json(
            (
                f"iterations/iter_{context.iteration:03d}/"
                "training_trajectories.json"
            ),
            [
                {"case_id": item.case_id, "markdown": item.markdown}
                for item in trajectories
            ],
        )
        return trajectories

    def _evaluate_validation(
        self,
        skill: str,
        case_ids: tuple[int | str, ...],
    ) -> MetricResult:
        iteration = self._active_iteration
        if iteration is None:
            raise RuntimeError(
                "validation rollout has no active iteration"
            )
        cases = self._lookup_cases(case_ids)
        evaluation = self._evaluate_skill(
            skill,
            cases,
            stage="validation_rollout",
        )
        call_number = self._validation_rollout_counts.get(iteration, 0) + 1
        self._validation_rollout_counts[iteration] = call_number
        if call_number == 1:
            label = "baseline"
        elif call_number == 2:
            label = "candidate"
        else:
            raise RuntimeError(
                "iteration produced more than two validation rollouts"
            )
        self._store.write_json(
            (
                f"iterations/iter_{iteration:03d}/"
                f"validation_{label}_evaluation.json"
            ),
            evaluation.to_dict(),
        )
        return evaluation.metrics.overall

    def _evaluate_skill(
        self,
        skill: str,
        cases: tuple[BenchmarkCase, ...],
        *,
        stage: str,
    ) -> EvaluationResult:
        resolver = _resolver_for_skill(
            benchmark=self.config.benchmark,
            scope=self.config.scope,
            cases=cases,
            skill=skill,
        )
        case_splits = {
            case.metadata.get("split")
            for case in cases
            if case.metadata.get("split") is not None
        }
        if len(case_splits) > 1:
            raise ValueError(
                "one SkillAdam rollout cannot mix dataset splits"
            )
        rollout_split = (
            next(iter(case_splits))
            if case_splits
            else self.config.split
        )
        result = self.evaluation_runner.evaluate(
            adapter=self.adapter,
            cases=cases,
            method="skilladam",
            split=rollout_split,
            skill_resolver=resolver,
            backend=self.backend,
            context=self.context,
            stage=stage,
        )
        self._record_usage(result.usage, stage=stage)
        return result

    def _lookup_cases(
        self,
        case_ids: Sequence[int | str],
    ) -> tuple[BenchmarkCase, ...]:
        result: list[BenchmarkCase] = []
        for raw_id in case_ids:
            case_id = str(raw_id)
            try:
                result.append(self._cases_by_id[case_id])
            except KeyError as exc:
                raise ValueError(
                    f"unknown sampled case ID {case_id!r}"
                ) from exc
        return tuple(result)

    def _condense(
        self,
        case,
        evaluated,
        *,
        allow_llm: bool = False,
    ) -> CondensedTrajectory:
        metric = evaluated.metric
        if metric.primary >= 1.0:
            label = "success"
        elif metric.primary > 0.0 or any(
            value > 0.0 for value in metric.metrics.values()
        ):
            label = "partial"
        else:
            label = "failure"
        query = _case_query(case)
        final_answer = _portable_text(evaluated.result.output)
        judgment = json.dumps(
            dict(metric.metrics),
            ensure_ascii=False,
            sort_keys=True,
        )
        markdown = condense_trajectory_md(
            messages=[
                dict(message) for message in evaluated.result.trajectory
            ],
            outcome_label=label,
            query=query,
            final_answer=final_answer,
            judgment_reason=judgment,
        )
        if (
            allow_llm
            and self.config.trajectory_compression == "llm"
        ):
            system = self._prompts.trajectory_compress_system_prompt
            if system is None:
                raise ValueError(
                    f"{self.config.benchmark} has no packaged LLM "
                    "trajectory compressor"
                )
            result = self._generate(
                stage="trajectory_compression",
                messages=(
                    {"role": "system", "content": system},
                    {"role": "user", "content": markdown},
                ),
                metadata={
                    "case_id": case.case_id,
                    "benchmark": self.config.benchmark,
                    "scope": self.config.scope,
                },
            )
            self._record_usage(
                result.usage,
                stage="trajectory_compression",
            )
            if not result.text.strip():
                raise ValueError(
                    "trajectory compression returned empty text"
                )
            markdown = result.text.strip()
        return CondensedTrajectory(case.case_id, markdown)

    def _post_decision_hook(
        self,
        initial_skill_hash: str,
    ) -> PostDecisionHook:
        signature = {
            "kind": "skilladam-momentum",
            "version": 1,
            "initial_skill_sha256": initial_skill_hash,
            "run_config": self.config.to_dict(),
            "backend_context": {
                "benchmark": self.context.benchmark,
                "method": self.context.method,
                "model": self.context.model,
                "reasoning_effort": self.context.reasoning_effort,
                "workers": self.context.workers,
                "seed": self.context.seed,
            },
        }
        return PostDecisionHook(
            run_signature=signature,
            generate=self._generate_momentum,
            apply=self._apply_momentum,
        )

    def _generate_momentum(
        self,
        context: PostDecisionContext,
    ) -> Mapping[str, Any]:
        iteration = context.record.iteration
        trajectories = self._load_trajectories(iteration)
        generation = self._load_patch_generation(
            iteration,
            context.record.patch_attempts,
        )
        system = MOMENTUM_TEMPLATE
        replacements = {
            "{vendor_prompt}": self._prompts.stage0.vendor_prompt,
            "{metric_interpretation}": (
                self._prompts.stage0.metric_interpretation
            ),
            "{type_taxonomy_section}": self._prompts.taxonomy_section,
        }
        for placeholder, value in replacements.items():
            system = system.replace(placeholder, value)
        user = "\n\n".join(
            (
                "# Current Skill",
                context.current_skill,
                "# Current Problem Tracker",
                self.momentum.render_for_agent_input(),
                "# Ordered Condensed Training Trajectories",
                "\n\n".join(
                    f"## Trajectory {index}: {item.case_id}\n\n"
                    f"{item.markdown}"
                    for index, item in enumerate(trajectories, start=1)
                ),
                "# Iteration Generation",
                json.dumps(
                    generation,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "# Gate Decision",
                json.dumps(
                    context.record.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        )
        tools = build_momentum_tools(self._prompts.taxonomy)
        result = self._generate(
            stage="momentum_update",
            messages=(
                {"role": "system", "content": system.strip()},
                {"role": "user", "content": user},
            ),
            metadata={
                "iteration": iteration,
                "iteration_id": context.iteration_id,
                "tools": tools,
            },
        )
        _validate_momentum_tool_calls(result.tool_calls, tools)
        self._record_usage(result.usage, stage="momentum_update")
        return _generation_payload(result)

    def _apply_momentum(
        self,
        context: PostDecisionContext,
        generation: Mapping[str, Any],
    ) -> None:
        result = _generation_from_payload(generation)
        for call in result.tool_calls:
            response = self.momentum.execute_tool_call(
                call.name,
                dict(call.arguments),
            )
            if response.startswith("Unknown tool:") or " call failed:" in response:
                raise ValueError(response)
        self._commit_optimizer_history(context)

    def _commit_optimizer_history(
        self,
        context: PostDecisionContext,
    ) -> None:
        """Persist the compact multi-turn optimizer conversation."""

        iteration = context.record.iteration
        exchange_path = (
            self.output_dir
            / "iterations"
            / f"iter_{iteration:03d}"
            / (
                f"patch_attempt_"
                f"{context.record.patch_attempts:02d}_exchange.json"
            )
        )
        try:
            exchange = json.loads(exchange_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "iteration optimizer exchange is missing or invalid"
            ) from exc
        if (
            not isinstance(exchange, dict)
            or exchange.get("iteration") != iteration
            or exchange.get("patch_attempt")
            != context.record.patch_attempts
            or not isinstance(exchange.get("assistant"), str)
        ):
            raise ValueError("iteration optimizer exchange is invalid")

        trajectories = self._load_trajectories(iteration)
        history_user = "\n\n".join(
            (
                f"# Iteration {iteration + 1} History",
                "# Ordered Condensed Training Trajectories",
                "\n\n".join(
                    f"## Trajectory {index}: {item.case_id}\n\n"
                    f"{item.markdown}"
                    for index, item in enumerate(
                        trajectories,
                        start=1,
                    )
                ),
            )
        )
        self._optimizer_history.extend(
            (
                {"role": "user", "content": history_user},
                {
                    "role": "assistant",
                    "content": str(exchange["assistant"]),
                },
                {
                    "role": "user",
                    "content": _gate_history_message(context.record),
                },
            )
        )
        turns = self.config.optimizer_history_turns
        if turns is not None:
            self._optimizer_history = self._optimizer_history[-3 * turns :]
        self._store.write_json(
            (
                f"iterations/iter_{iteration:03d}/"
                "optimizer_conversation.json"
            ),
            {
                "schema_version": 1,
                "through_iteration": iteration,
                "messages": self._optimizer_history,
            },
        )

    def _restore_optimizer_history(
        self,
        checkpoint: Mapping[str, Any],
    ) -> None:
        """Restore only history whose post-decision action is complete."""

        try:
            next_iteration = int(checkpoint["next_iteration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResumeMismatchError(
                "checkpoint next_iteration is invalid"
            ) from exc
        pending = checkpoint.get("pending_post_decision")
        expected_iteration = next_iteration - (2 if pending is not None else 1)
        if expected_iteration < 0:
            self._optimizer_history = []
            return
        path = (
            self.output_dir
            / "iterations"
            / f"iter_{expected_iteration:03d}"
            / "optimizer_conversation.json"
        )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ResumeMismatchError(
                "optimizer conversation snapshot is missing or invalid"
            ) from exc
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("through_iteration") != expected_iteration
            or not isinstance(messages, list)
            or any(
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message["role"] not in {"user", "assistant"}
                or not isinstance(message["content"], str)
                for message in messages
            )
        ):
            raise ResumeMismatchError(
                "optimizer conversation snapshot is invalid"
            )
        self._optimizer_history = [
            {"role": item["role"], "content": item["content"]}
            for item in messages
        ]

    def _load_trajectories(
        self,
        iteration: int,
    ) -> tuple[CondensedTrajectory, ...]:
        cached = self._trajectory_cache.get(iteration)
        if cached is not None:
            return cached
        path = (
            self.output_dir
            / "iterations"
            / f"iter_{iteration:03d}"
            / "training_trajectories.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("cached training trajectories are invalid")
        result = tuple(
            CondensedTrajectory(
                case_id=item["case_id"],
                markdown=item["markdown"],
            )
            for item in payload
            if isinstance(item, dict)
        )
        if len(result) != len(payload):
            raise ValueError("cached training trajectories are invalid")
        self._trajectory_cache[iteration] = result
        return result

    def _load_patch_generation(
        self,
        iteration: int,
        attempt: int,
    ) -> dict[str, str]:
        path = (
            self.output_dir
            / "iterations"
            / f"iter_{iteration:03d}"
            / f"patch_attempt_{attempt:02d}.json"
        )
        return IterationGeneration.from_raw_text(
            path.read_text(encoding="utf-8")
        ).to_dict()

    def _generate(
        self,
        *,
        stage: str,
        messages: tuple[Mapping[str, Any], ...],
        metadata: Mapping[str, Any],
    ) -> GenerationResult:
        result = self.backend.generate(
            GenerationRequest(
                stage=stage,
                messages=messages,
                context=self.context,
                metadata=metadata,
            )
        )
        if not isinstance(result, GenerationResult):
            raise ValueError(
                "execution backend generate must return GenerationResult"
            )
        return result

    def _record_usage(
        self,
        records: Sequence[UsageRecord],
        *,
        stage: str,
    ) -> None:
        for usage in records:
            self._ledger.append(
                method="skilladam",
                benchmark=self.config.benchmark,
                stage=stage,
                model=self.context.model,
                usage=usage,
            )


class _SerializableAdapterGate:
    """Delegate to the adapter while freezing the selected public config."""

    def __init__(
        self,
        *,
        adapter: BenchmarkAdapter,
        benchmark: str,
        scope: str | None,
        profile: str,
    ) -> None:
        self.adapter = adapter
        self.benchmark = benchmark
        self.scope = scope
        self.profile = profile
        self._gate = _profile_gate(
            adapter,
            benchmark,
            scope,
            profile,
        )
        self._signature = _gate_signature(
            self._gate,
            benchmark,
            scope,
            profile,
        )

    def judge(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        if self._gate is None:
            return self.adapter.gate(baseline, candidate)
        return self._gate.judge(baseline, candidate)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._signature)


def _gate_history_message(record: IterationRecord) -> str:
    decision = "accepted" if record.accepted else "rejected"
    baseline = {
        "primary": record.baseline.primary,
        "metrics": dict(record.baseline.metrics),
    }
    candidate = {
        "primary": record.candidate.primary,
        "metrics": dict(record.candidate.metrics),
    }
    message = "\n".join(
        (
            f"**Validation:** {decision}",
            "baseline: "
            + json.dumps(
                baseline,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "candidate: "
            + json.dumps(
                candidate,
                ensure_ascii=False,
                sort_keys=True,
            ),
            f"gate: {record.reason} -> {decision}",
        )
    )
    if not record.accepted:
        message += (
            "\n\nThe patch was rejected. The skill remains unchanged; "
            "the next patch must apply to that unchanged skill."
        )
    return message


def _sequential_batches(
    values: tuple[str, ...],
    size: int,
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(values[start : start + size])
        for start in range(0, len(values) - size + 1, size)
    )


def _profile_gate(
    adapter: BenchmarkAdapter,
    benchmark: str,
    scope: str | None,
    profile: str,
):
    if profile == "default":
        return getattr(adapter, "_gate", None)
    if profile == "main-result":
        if benchmark == "searchqa":
            from skilladam.benchmarks.searchqa.gate import (
                SearchQAAcceptanceGate,
            )

            return SearchQAAcceptanceGate(
                min_em_gain=0.04,
                min_f1_gain=0.025,
                max_em_drop=0.04,
                max_f1_drop=0.05,
            )
        if benchmark == "docvqa":
            from skilladam.benchmarks.docvqa.gate import (
                DocVQAAcceptanceGate,
            )

            return DocVQAAcceptanceGate.for_v2()
        if benchmark in {
            "alfworld",
            "lmb",
            "officeqa",
            "spreadsheetbench",
        }:
            return getattr(adapter, "_gate", None)
        if benchmark == "deepplanning":
            assert scope is not None
            domain = SLICE_SPECS[scope]["domain"]
            return (
                ShoppingAcceptanceGate()
                if domain == "shopping"
                else TravelAcceptanceGate()
            )
    raise ValueError(
        f"unsupported gate profile {profile!r} for {benchmark!r}"
    )


def _gate_signature(
    gate,
    benchmark: str,
    scope: str | None,
    profile: str,
) -> dict[str, Any]:
    if benchmark == "deepplanning":
        if gate is None:
            assert scope is not None
            domain = SLICE_SPECS[scope]["domain"]
            gate = (
                ShoppingAcceptanceGate()
                if domain == "shopping"
                else TravelAcceptanceGate()
            )
    if gate is None or not callable(getattr(gate, "to_dict", None)):
        raise ValueError(
            f"{benchmark} public gate has no serializable configuration"
        )
    return {
        "benchmark": benchmark,
        "scope": scope,
        "profile": profile,
        "gate_class": type(gate).__name__,
        "configuration": gate.to_dict(),
    }


def _resolver_for_skill(
    *,
    benchmark: str,
    scope: str | None,
    cases: tuple[BenchmarkCase, ...],
    skill: str,
) -> SkillResolver:
    text = _skill_text(skill)
    digest = _skill_hash(text)
    source = SkillSource(
        source="explicit_file",
        sha256=digest,
        artifact_id=None,
        scope=scope or "all",
    )
    resolved = ResolvedSkill(text=text, sha256=digest, source=source)
    return SkillResolver(
        {case.case_id: resolved for case in cases},
        (source,),
    )


def _estimate_conversation_tokens(
    messages: Sequence[Mapping[str, Any]],
) -> int:
    """Conservative archived estimate: text characters divided by 3.5."""

    total_chars = 0
    for message in messages:
        content = message.get("content") or ""
        if isinstance(content, list):
            for part in content:
                if isinstance(part, Mapping):
                    total_chars += len(str(part.get("text") or ""))
        else:
            total_chars += len(str(content))
    return int(total_chars / 3.5)


def _generation_payload(result: GenerationResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "usage": [asdict(item) for item in result.usage],
        "tool_calls": [
            {"name": item.name, "arguments": dict(item.arguments)}
            for item in result.tool_calls
        ],
    }


def _generation_from_payload(
    payload: Mapping[str, Any],
) -> GenerationResult:
    if set(payload) != {"text", "usage", "tool_calls"}:
        raise ValueError("cached Momentum generation has invalid keys")
    raw_usage = payload["usage"]
    raw_calls = payload["tool_calls"]
    if (
        isinstance(raw_usage, (str, bytes))
        or not isinstance(raw_usage, Sequence)
        or isinstance(raw_calls, (str, bytes))
        or not isinstance(raw_calls, Sequence)
    ):
        raise ValueError("cached Momentum generation is malformed")
    usage = tuple(
        UsageRecord(**dict(item))
        for item in raw_usage
        if isinstance(item, Mapping)
    )
    calls = tuple(
        _tool_call_from_payload(item)
        for item in raw_calls
        if isinstance(item, Mapping)
    )
    if len(usage) != len(raw_usage) or len(calls) != len(raw_calls):
        raise ValueError("cached Momentum generation is malformed")
    return GenerationResult(
        text=payload["text"],
        usage=usage,
        tool_calls=calls,
    )


def _tool_call_from_payload(value: Mapping[str, Any]) -> ToolCall:
    return ToolCall(
        name=value.get("name"),
        arguments=value.get("arguments", {}),
    )


def _validate_momentum_tool_calls(
    calls: Sequence[ToolCall],
    schemas: Sequence[Mapping[str, Any]],
) -> None:
    definitions = {
        schema["function"]["name"]: schema["function"]["parameters"]
        for schema in schemas
    }
    if set(definitions) != _MOMENTUM_TOOL_NAMES:
        raise ValueError("Momentum tool schema set is incomplete")
    for call in calls:
        if call.name not in definitions:
            raise ValueError(
                f"unsupported Momentum tool call {call.name!r}"
            )
        parameters = definitions[call.name]
        properties = parameters["properties"]
        required = set(parameters["required"])
        arguments = dict(call.arguments)
        unknown = sorted(set(arguments) - set(properties))
        missing = sorted(required - set(arguments))
        if unknown:
            raise ValueError(
                f"Momentum tool {call.name!r} has unknown arguments: "
                f"{unknown!r}"
            )
        if missing:
            raise ValueError(
                f"Momentum tool {call.name!r} is missing required "
                f"argument(s): {missing!r}"
            )
        for name, value in arguments.items():
            _validate_tool_argument(
                call.name,
                name,
                value,
                properties[name],
            )


def _validate_tool_argument(
    tool_name: str,
    argument_name: str,
    value: Any,
    schema: Mapping[str, Any],
) -> None:
    expected = schema.get("type")
    valid = (
        expected == "string"
        and isinstance(value, str)
        or expected == "boolean"
        and isinstance(value, bool)
        or expected == "array"
        and isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and all(isinstance(item, str) for item in value)
    )
    if not valid:
        raise ValueError(
            f"Momentum tool {tool_name!r} argument {argument_name!r} "
            f"must have type {expected!r}"
        )
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        raise ValueError(
            f"Momentum tool {tool_name!r} argument {argument_name!r} "
            "is outside the allowed enum"
        )


def _checkpoint_initial_hash(payload: Mapping[str, Any]) -> str:
    try:
        value = payload["run_signature"]["post_decision"][
            "initial_skill_sha256"
        ]
    except (KeyError, TypeError) as exc:
        raise ResumeMismatchError(
            "checkpoint has no SkillAdam initial skill signature"
        ) from exc
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ResumeMismatchError(
            "checkpoint SkillAdam initial skill signature is invalid"
        )
    return value


def _skill_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("initial skill must be non-empty text")
    return value


def _skill_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _case_query(case: BenchmarkCase) -> str:
    for field_name in ("question", "task", "instruction", "query"):
        value = case.payload.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"Benchmark case {case.case_id}"


def _portable_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = [
    "SkillAdamRunConfig",
    "SkillAdamRunResult",
    "SkillAdamRunner",
]
