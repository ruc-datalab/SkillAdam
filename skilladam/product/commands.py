"""Lifecycle commands shared by CLI, MCP, and platform adapters."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from skilladam.core.feedback_loop import EditBudgetConfig, FeedbackLoopConfig
from skilladam.product.project import ProductProjectStore
from skilladam.product.runtime import model_from_runtime
from skilladam.product.service import ProductService
from skilladam.product.workflow import ProductSessionView


def prepare_product(
    *,
    output_dir: Path,
    skill_path: Path | None = None,
    intent: str | None = None,
    runtime: Mapping[str, Any] | None = None,
    task_manifest: Mapping[str, Any] | None = None,
    task_count: int = 8,
    validation_size: int | None = None,
    batch_size: int | None = None,
    max_iterations: int = 3,
    min_iterations: int = 0,
    max_consecutive_failures: int = 3,
    patch_attempts: int = 3,
    edit_budget_base: int = 4,
    edit_budget_minimum: int = 1,
    edit_budget_beta: float = 0.9,
    edit_budget_v_max: float = 0.1,
) -> ProductSessionView:
    store = ProductProjectStore(output_dir)
    if store.exists:
        return load_product_service(output_dir).prepare()
    if edit_budget_base < 1 or edit_budget_minimum < 1:
        raise ValueError("edit budget base and minimum must be at least 1")
    if edit_budget_minimum > edit_budget_base:
        raise ValueError("edit_budget_minimum cannot exceed edit_budget_base")
    if not math.isfinite(edit_budget_beta) or not 0.0 <= edit_budget_beta <= 1.0:
        raise ValueError("edit_budget_beta must be between 0 and 1")
    if not math.isfinite(edit_budget_v_max) or edit_budget_v_max <= 0.0:
        raise ValueError("edit_budget_v_max must be positive")
    if skill_path is None or not intent:
        raise ValueError("initial prepare requires skill_path and intent")
    if runtime is None:
        raise ValueError("initial prepare requires runtime configuration")
    service = ProductService.bootstrap(
        skill_path=skill_path,
        intent_text=intent,
        output_dir=output_dir,
        model=model_from_runtime(runtime),
        runtime=runtime,
        task_count=task_count,
        validation_size=validation_size,
        batch_size=batch_size,
        task_manifest=task_manifest,
        loop_config=FeedbackLoopConfig(
            max_iterations=max_iterations,
            min_iterations=min_iterations,
            max_consecutive_failures=max_consecutive_failures,
            patch_attempts=patch_attempts,
            edit_budget=EditBudgetConfig(
                metric_key="score",
                v_max=edit_budget_v_max,
                base=edit_budget_base,
                minimum=edit_budget_minimum,
                beta=edit_budget_beta,
            ),
        ),
    )
    return service.prepare()


def apply_default_review_policy(
    *,
    output_dir: Path,
    view: ProductSessionView,
    review_mode: bool,
) -> ProductSessionView:
    """Accept all by default; retain human selection only in explicit review mode."""

    if review_mode or view.session.state != "awaiting_review":
        return view
    if view.proposal is None:
        raise ValueError("awaiting_review session has no patch proposal")
    return submit_product_selection(
        output_dir=output_dir,
        accepted_hunk_ids=None,
        idempotency_key=f"auto-accept-all:{view.proposal.proposal_id}",
        actor="adapter",
        reason="default auto-accept-all; no explicit review request",
    )


def product_status(output_dir: Path) -> ProductSessionView:
    service = load_product_service(output_dir)
    view = service.status
    if view.session.evaluation_plan_digest != service.project.plan.digest:
        raise ValueError("status project and session plan differ")
    return view


def submit_product_selection(
    *,
    output_dir: Path,
    accepted_hunk_ids: Sequence[str] | None,
    idempotency_key: str,
    actor: str = "user",
    reason: str = "",
) -> ProductSessionView:
    """Submit choices; ``accepted_hunk_ids=None`` means auto-accept-all."""

    service = load_product_service(output_dir)
    if accepted_hunk_ids is None:
        proposal = service.status.proposal
        if proposal is None:
            raise ValueError("session has no patch proposal")
        accepted_hunk_ids = tuple(hunk.hunk_id for hunk in proposal.hunks)
    return service.submit(
        accepted_hunk_ids,
        idempotency_key=idempotency_key,
        actor=actor,
        reason=reason,
    )


def stage_product_selection(
    *,
    output_dir: Path,
    accepted_hunk_ids: Sequence[str],
    idempotency_key: str,
    actor: str = "user",
    reason: str = "",
) -> ProductSessionView:
    """Persist human choices without running candidate validation in the interactive request."""

    return load_product_service(output_dir).stage_selection(
        accepted_hunk_ids,
        idempotency_key=idempotency_key,
        actor=actor,
        reason=reason,
    )


def continue_product(output_dir: Path) -> ProductSessionView:
    return load_product_service(output_dir).continue_pending()


def load_product_service(output_dir: Path) -> ProductService:
    project = ProductProjectStore(output_dir).load()
    return ProductService.load(
        output_dir,
        model_from_runtime(project.runtime),
    )


__all__ = [
    "apply_default_review_policy",
    "continue_product",
    "load_product_service",
    "prepare_product",
    "product_status",
    "stage_product_selection",
    "submit_product_selection",
]
