"""Lazy provider-neutral execution exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_MODULE_EXPORTS = {
    "skilladam.execution.aggregation": (
        "AggregationResult",
        "aggregate_benchmark_metrics",
        "aggregate_metric_results",
    ),
    "skilladam.execution.backend": (
        "BackendConfig",
        "BackendConfigurationError",
        "BackendError",
        "BackendLoadError",
        "BackendProtocolError",
        "LoadedBackend",
        "load_backend_config",
        "load_execution_backend",
        "validate_public_metadata",
    ),
    "skilladam.execution.contracts": (
        "BackendContext",
        "BackendInit",
        "BatchExecution",
        "ExecutionBackend",
        "GenerationRequest",
        "GenerationResult",
        "RawRollout",
        "ToolCall",
    ),
    "skilladam.execution.evaluate": (
        "CaseEvaluation",
        "EvaluationResult",
        "EvaluationRunner",
    ),
    "skilladam.execution.planning": ("ExecutionPlan",),
    "skilladam.execution.selection": ("select_cases",),
    "skilladam.execution.skills": (
        "ResolvedSkill",
        "SkillResolver",
        "SkillSource",
    ),
    "skilladam.execution.store": ("RunStore",),
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
