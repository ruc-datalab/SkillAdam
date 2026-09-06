"""Product services shared by CLI, MCP, and platform adapters."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from skilladam.core.acceptance_gate import (
    AcceptanceGate,
    ImprovementRule,
    NonRegressionRule,
)
from skilladam.core.case_sampler import IterationCases, build_same_batch_sampler
from skilladam.core.feedback_loop import (
    EditBudgetConfig,
    FeedbackLoopConfig,
    IterationContext,
)
from skilladam.product.evaluation import EvaluationRunner
from skilladam.product.evaluation_planning import EvaluationPlanner
from skilladam.product.feedback import (
    IterationFeedback,
    PatchGeneration,
    build_iteration_feedback,
)
from skilladam.product.model import ProductModel
from skilladam.product.models import (
    EvaluationPlan,
    OptimizationTask,
    SkillPackageSnapshot,
    UsageIntent,
    canonical_json,
    digest_value,
)
from skilladam.product.project import ProductProject, ProductProjectStore
from skilladam.product.session_store import SessionStore
from skilladam.product.tasking import (
    freeze_task_pool,
    freeze_task_suite,
)
from skilladam.product.task_manifest import ingest_task_manifest
from skilladam.product.workflow import (
    OptimizationWorkflow,
    ProductSessionView,
)


class ProductService:
    """One output directory holds one persisted optimization project and session."""

    def __init__(
        self,
        *,
        output_dir: Path,
        project: ProductProject,
        model: ProductModel,
        resume: bool,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.project = project
        self.model = model
        self.recorder = _RolloutRecorder(model)
        self.runner = EvaluationRunner(
            project.tasks,
            project.plan,
            self.recorder,
            judge=model.judge,
        )
        config = _feedback_config(project.workflow_config)
        optimization = dict(project.runtime.get("optimization", {}))
        if optimization.get("validation_mode") == "same_batch":
            batches = tuple(
                build_same_batch_sampler(
                    project.suite.train_task_ids,
                    batch_size=int(optimization["batch_size"]),
                    max_iterations=config.max_iterations,
                    seed=int(optimization.get("seed", 42)),
                )
            )
        else:
            # Keep the original signature for projects without sampling config to support resume.
            batches = tuple(
                IterationCases(
                    training_case_ids=project.suite.train_task_ids,
                    validation_case_ids=project.suite.validation_task_ids,
                )
                for _ in range(config.max_iterations)
            )
        holder: dict[str, OptimizationWorkflow] = {}
        feedback_cache: dict[tuple[int, str], IterationFeedback] = {}

        def _propose(context: IterationContext) -> PatchGeneration:
            cache_key = (context.iteration, context.current_skill)
            feedback = feedback_cache.get(cache_key)
            if feedback is None:
                self.recorder.clear()
                training_result = self.runner.evaluate(
                    context.current_skill,
                    candidate_digest=digest_value(
                        {project.package.entrypoint: context.current_skill}
                    ),
                    task_ids=tuple(
                        str(item) for item in context.training_case_ids
                    ),
                )
                feedback = build_iteration_feedback(
                    tasks={task.task_id: task for task in project.tasks},
                    validation=training_result,
                    outputs=self.recorder.outputs,
                    ordered_task_ids=tuple(
                        str(item) for item in context.training_case_ids
                    ),
                )
                feedback_cache[cache_key] = feedback
            raw_patch = model.propose_patch(
                context,
                feedback,
                project.intent,
                holder["workflow"].momentum.render_memory(),
            )
            return PatchGeneration(
                raw_patch=raw_patch,
                baseline_validation=feedback.validation,
                feedback=feedback,
            )

        def _validate(
            files: Mapping[str, str],
            task_ids,
        ):
            self.recorder.clear()
            return self.runner.evaluate(
                files[project.package.entrypoint],
                candidate_digest=digest_value(files),
                task_ids=tuple(str(item) for item in task_ids),
            )

        workflow = OptimizationWorkflow(
            store=SessionStore(self.output_dir),
            session_id=f"session_{project.digest}",
            package=project.package,
            plan=project.plan,
            batches=batches,
            loop_config=config,
            propose_patch=_propose,
            validate=_validate,
            gate=gate_from_plan(project.plan),
            update_momentum=model.update_momentum,
            resume=resume,
        )
        holder["workflow"] = workflow
        self.workflow = workflow

    @classmethod
    def bootstrap(
        cls,
        *,
        skill_path: Path,
        intent_text: str,
        output_dir: Path,
        model: ProductModel,
        runtime: Mapping[str, Any],
        task_count: int = 8,
        validation_size: int | None = None,
        batch_size: int | None = None,
        task_manifest: Mapping[str, Any] | None = None,
        loop_config: FeedbackLoopConfig | None = None,
    ) -> "ProductService":
        skill_path = Path(skill_path).resolve()
        output_dir = Path(output_dir).resolve()
        project_store = ProductProjectStore(output_dir)
        if project_store.exists or SessionStore(output_dir).exists:
            raise ValueError("output directory already contains a product project")
        skill_text = skill_path.read_text(encoding="utf-8")
        package = SkillPackageSnapshot(
            root=str(skill_path.parent),
            entrypoint=skill_path.name,
            files={skill_path.name: skill_text},
            mutable_paths=(skill_path.name,),
            metadata={"source_path": str(skill_path)},
        )
        intent = UsageIntent(purpose=intent_text)
        if task_manifest is None:
            raise ValueError(
                "initial prepare requires a host-generated task_manifest"
            )
        supplied = ingest_task_manifest(
            task_manifest,
            expected_count=task_count,
        )
        pool = freeze_task_pool(
            supplied,
            pool_id=(
                "pool_"
                + digest_value(
                    (
                        package.package_digest,
                        intent.digest,
                        tuple(task.digest for task in supplied),
                    )
                )
            ),
            metadata={"input": "host_task_manifest"},
        )
        suite = freeze_task_suite(
            pool,
            suite_id=f"suite_{pool.digest}",
            validation_size=validation_size,
        )
        tasks, plan = EvaluationPlanner().freeze_plan(
            pool,
            suite,
            gate={"metric": "primary", "minimum_gain": 0.0},
        )
        config = loop_config or FeedbackLoopConfig(
            max_iterations=3,
            edit_budget=EditBudgetConfig(
                metric_key="score",
                v_max=0.1,
                base=4,
                minimum=1,
                beta=0.9,
            ),
        )
        selected_batch_size = (
            min(3, len(suite.train_task_ids))
            if batch_size is None
            else batch_size
        )
        if (
            isinstance(selected_batch_size, bool)
            or not isinstance(selected_batch_size, int)
            or selected_batch_size < 1
            or selected_batch_size > len(
                suite.train_task_ids
            )
        ):
            raise ValueError(
                "batch_size must be between 1 and the frozen training size"
            )
        runtime_config = dict(runtime)
        runtime_config["optimization"] = {
            "validation_mode": "same_batch",
            "batch_size": selected_batch_size,
            "seed": 42,
        }
        project = ProductProject(
            package=package,
            intent=intent,
            pool=pool,
            suite=suite,
            tasks=tasks,
            plan=plan,
            workflow_config=asdict(config),
            runtime=runtime_config,
        )
        project_store.save(project)
        return cls(
            output_dir=output_dir,
            project=project,
            model=model,
            resume=False,
        )

    @classmethod
    def load(
        cls,
        output_dir: Path,
        model: ProductModel,
    ) -> "ProductService":
        output_dir = Path(output_dir).resolve()
        return cls(
            output_dir=output_dir,
            project=ProductProjectStore(output_dir).load(),
            model=model,
            resume=True,
        )

    def prepare(self) -> ProductSessionView:
        return self._finalize_if_completed(self.workflow.prepare())

    def submit(
        self,
        accepted_hunk_ids: Sequence[str],
        *,
        idempotency_key: str,
        actor: str = "user",
        reason: str = "",
    ) -> ProductSessionView:
        view = self.workflow.view
        if view.proposal is None:
            raise ValueError("session has no patch proposal")
        from skilladam.product.patch_review import select_patch_hunks

        selection = select_patch_hunks(
            view.proposal,
            accepted_hunk_ids,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        # Validate source files only when applying patches; outside awaiting_review,
        # submit_selection replays idempotently, so the file can already equal the candidate.
        # Do not mistake an idempotent retry for a source conflict.
        if accepted_hunk_ids and view.session.state == "awaiting_review":
            self._assert_source_unchanged(view.proposal.base_digest)
        result = self.workflow.submit_selection(selection)
        self._sync_accepted_skill(result)
        return self._finalize_if_completed(result)

    def stage_selection(
        self,
        accepted_hunk_ids: Sequence[str],
        *,
        idempotency_key: str,
        actor: str = "user",
        reason: str = "",
    ) -> ProductSessionView:
        """Record review choices and return immediately; continue_pending resumes validation."""

        view = self.workflow.view
        if view.proposal is None:
            raise ValueError("session has no patch proposal")
        from skilladam.product.patch_review import select_patch_hunks

        selection = select_patch_hunks(
            view.proposal,
            accepted_hunk_ids,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        if accepted_hunk_ids and view.session.state == "awaiting_review":
            self._assert_source_unchanged(view.proposal.base_digest)
        result = self.workflow.stage_selection(selection)
        # App interactions must return promptly; even if reject-all ends the last iteration,
        # defer held-out final validation to the subsequent continue call.
        self._persist_iteration_artifacts(result)
        return result

    def continue_pending(self) -> ProductSessionView:
        view = self.workflow.view
        if view.session.state == "validating" and view.proposal is not None:
            self._assert_source_unchanged(view.proposal.base_digest)
        result = self.workflow.continue_pending()
        self._sync_accepted_skill(result)
        return self._finalize_if_completed(result)

    @property
    def status(self) -> ProductSessionView:
        return self.workflow.view

    @property
    def _entrypoint_path(self) -> Path:
        return Path(self.project.package.root) / self.project.package.entrypoint

    def _source_digest(self) -> str:
        return digest_value(
            {
                self.project.package.entrypoint: self._entrypoint_path.read_text(
                    encoding="utf-8"
                )
            }
        )

    def _assert_source_unchanged(self, base_digest: str) -> None:
        """Reject externally modified source skills before advancing the checkpoint.

        Call before submit_selection/continue_pending; otherwise the session records
        acceptance and advances while the file remains unchanged, causing divergence.
        """

        if self._source_digest() != base_digest:
            raise ProductSourceConflict(
                "source Skill changed after patch preparation; refusing to overwrite"
            )

    def _sync_accepted_skill(self, view: ProductSessionView) -> None:
        """Write an accepted candidate only when the file can still be safely replaced.

        Check all three states:
        - source == target: already synchronized; return idempotently.
        - source == base: safe to write, including recovery after checkpoint acceptance
          but before the file was written.
        - Otherwise: the file was changed externally, possibly during validation;
          report a conflict instead of silently overwriting it.
        """

        if not view.records or not view.records[-1]["accepted"]:
            return
        path = self._entrypoint_path
        text = self.workflow.current_files[self.project.package.entrypoint]
        target_digest = digest_value(
            {self.project.package.entrypoint: text}
        )
        source_digest = self._source_digest()
        if source_digest == target_digest:
            return
        base_digest = (
            view.proposal.base_digest if view.proposal is not None else ""
        )
        if source_digest != base_digest:
            raise ProductSourceConflict(
                "source Skill changed after patch preparation; refusing to overwrite"
            )
        temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            # newline="" keeps written bytes identical to the text covered by the digest; default
            # newline conversion on Windows would change LF to CRLF and invalidate the digest.
            with temporary.open("x", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            # Recheck immediately before os.replace to narrow the concurrent-edit window.
            if self._source_digest() != base_digest:
                raise ProductSourceConflict(
                    "source Skill changed during validation; refusing to overwrite"
                )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _finalize_if_completed(
        self,
        view: ProductSessionView,
    ) -> ProductSessionView:
        """Run held-out validation once after termination without feeding it to the optimizer."""

        self._persist_iteration_artifacts(view)
        if view.session.state != "completed":
            return view
        store = SessionStore(
            self.output_dir,
            filename="final_validation.json",
        )
        if store.exists:
            return view
        task_ids = tuple(self.project.suite.validation_task_ids)
        self.recorder.clear()
        result = self.runner.evaluate(
            self.workflow.current_files[self.project.package.entrypoint],
            candidate_digest=digest_value(self.workflow.current_files),
            task_ids=task_ids,
        )
        store.save(
            {
                "schema_version": 1,
                "kind": "heldout_final_validation",
                "task_ids": list(task_ids),
                "validation": result.to_dict(),
            }
        )
        return view

    def _persist_iteration_artifacts(self, view: ProductSessionView) -> None:
        """Export ordered checkpoint evidence into stable per-iteration files."""

        for record in view.records:
            iteration = int(record["iteration"])
            iteration_dir = (
                self.output_dir / "iterations" / f"iter_{iteration:03d}"
            )
            iteration_store = SessionStore(
                iteration_dir,
                filename="iteration.json",
            )
            if not iteration_store.exists:
                iteration_store.save(json.loads(canonical_json(record)))
            feedback = record.get("iteration_feedback")
            if feedback is not None:
                feedback_store = SessionStore(
                    iteration_dir,
                    filename="training_feedback.json",
                )
                if not feedback_store.exists:
                    feedback_store.save(json.loads(canonical_json(feedback)))


class _RolloutRecorder:
    def __init__(self, model: ProductModel) -> None:
        self.model = model
        self.outputs: dict[str, Any] = {}

    def __call__(self, skill: str, task: OptimizationTask) -> Any:
        output = self.model.rollout(skill, task)
        self.outputs[task.task_id] = output
        return output

    def clear(self) -> None:
        self.outputs.clear()


def gate_from_plan(plan: EvaluationPlan) -> AcceptanceGate:
    """Build AcceptanceGate from the frozen EvaluationPlan rules.

    plan.gate contributes to the plan digest and run signature; hardcoding would ignore
    non-default gates and make validation inconsistent with the decision criteria.
    """

    gate = dict(plan.gate)
    improvements = (
        ImprovementRule(
            metric=str(gate.get("metric", "primary")),
            min_gain=float(gate.get("minimum_gain", 0.0)),
        ),
    )
    non_regressions = tuple(
        NonRegressionRule(
            metric=str(rule["metric"]),
            max_drop=float(rule.get("maximum_drop", 0.0)),
        )
        for rule in gate.get("non_regressions", ())
    )
    return AcceptanceGate(
        improvements=improvements,
        non_regressions=non_regressions,
    )


def _feedback_config(payload: Mapping[str, Any]) -> FeedbackLoopConfig:
    values = dict(payload)
    edit_budget = values.get("edit_budget")
    values["edit_budget"] = (
        EditBudgetConfig(**edit_budget) if edit_budget is not None else None
    )
    return FeedbackLoopConfig(**values)


class ProductSourceConflict(RuntimeError):
    """The source skill was modified externally during review."""


__all__ = ["ProductService", "ProductSourceConflict", "gate_from_plan"]
