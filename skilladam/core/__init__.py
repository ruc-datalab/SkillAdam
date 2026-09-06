"""Lazy dependency-free core exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_MODULE_EXPORTS = {
    "skilladam.core.acceptance_gate": (
        "AcceptanceGate",
        "ImprovementRule",
        "NonRegressionRule",
    ),
    "skilladam.core.case_sampler": (
        "IterationCases",
        "build_disjoint_sampler",
    ),
    "skilladam.core.edit_budget": (
        "build_budget_section",
        "compute_edit_budget",
        "compute_per_case_deltas",
        "compute_sigma_sq",
        "update_v_ema",
    ),
    "skilladam.core.feedback_loop": (
        "CheckpointStore",
        "EditBudgetConfig",
        "FeedbackLoop",
        "FeedbackLoopConfig",
        "FeedbackLoopResult",
        "IterationContext",
        "IterationRecord",
        "PostDecisionContext",
        "PostDecisionHook",
    ),
}
_EXPORTS = {
    name: module_name
    for module_name, names in _MODULE_EXPORTS.items()
    for name in names
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
