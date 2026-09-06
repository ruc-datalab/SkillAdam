"""Validated access to the frozen paper main-result configuration."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import json
from types import MappingProxyType
from typing import Any, Mapping

from skilladam.benchmarks.registry import list_benchmarks


_PROFILE_RESOURCE = "main_results.json"
_METHODS = frozenset({"baseline", "skillopt", "skilladam"})
_PROVIDERS = frozenset({"openrouter", "venus"})


@dataclass(frozen=True, slots=True)
class BenchmarkMainResultProfile:
    """Immutable configuration and paper references for one benchmark."""

    name: str
    provider: str
    model: str
    dataset: Mapping[str, Any]
    stage0: Mapping[str, Any]
    skilladam: Mapping[str, Any]
    skillopt: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    selected_artifacts: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class MainResultProfile:
    """Top-level frozen profile for the paper's main-result experiments."""

    schema_version: int
    profile_id: str
    seed: int
    authority: Mapping[str, Any]
    common: Mapping[str, Any]
    benchmarks: Mapping[str, BenchmarkMainResultProfile]

    def benchmark(self, name: str) -> BenchmarkMainResultProfile:
        try:
            return self.benchmarks[name]
        except KeyError as exc:
            raise KeyError(f"unknown main-result benchmark {name!r}") from exc


def load_main_result_profile() -> MainResultProfile:
    """Load and validate a fresh immutable main-result profile."""

    resource = resources.files("skilladam.experiments") / _PROFILE_RESOURCE
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("main-result profile is missing or invalid") from exc
    root = _object(payload, "profile")
    if root.get("schema_version") != 2:
        raise ValueError("unsupported main-result profile schema")
    profile_id = _text(root.get("profile_id"), "profile_id")
    seed = _positive_int(root.get("seed"), "seed", allow_zero=True)
    if seed != 42:
        raise ValueError("paper main-result profile must use seed 42")
    authority = _freeze_object(root.get("authority"), "authority")
    _sha256(authority.get("pdf_sha256"), "authority.pdf_sha256")
    common = _freeze_object(root.get("common"), "common")

    raw_benchmarks = _object(root.get("benchmarks"), "benchmarks")
    expected = set(list_benchmarks())
    if set(raw_benchmarks) != expected:
        raise ValueError(
            "main-result profile benchmark set mismatch: "
            f"expected={sorted(expected)!r}"
        )
    benchmarks: dict[str, BenchmarkMainResultProfile] = {}
    for name in sorted(raw_benchmarks):
        benchmark = _parse_benchmark(name, raw_benchmarks[name], seed=seed)
        benchmarks[name] = benchmark
    return MainResultProfile(
        schema_version=2,
        profile_id=profile_id,
        seed=seed,
        authority=authority,
        common=common,
        benchmarks=MappingProxyType(benchmarks),
    )


def _parse_benchmark(
    name: str,
    payload: Any,
    *,
    seed: int,
) -> BenchmarkMainResultProfile:
    root = _object(payload, f"benchmarks.{name}")
    provider = _text(root.get("provider"), f"{name}.provider")
    if provider not in _PROVIDERS:
        raise ValueError(f"{name}.provider is unsupported")
    model = _text(root.get("model"), f"{name}.model")
    expected_provider = "venus" if name == "deepplanning" else "openrouter"
    expected_model = (
        "claude-4-5-sonnet-20250929"
        if name == "deepplanning"
        else "openai/gpt-5.5"
    )
    if provider != expected_provider:
        raise ValueError(
            f"{name}.provider must equal {expected_provider!r}"
        )
    if model != expected_model:
        raise ValueError(f"{name}.model must equal {expected_model!r}")
    dataset = _freeze_object(root.get("dataset"), f"{name}.dataset")
    stage0 = _freeze_object(root.get("stage0"), f"{name}.stage0")
    skilladam = _freeze_object(
        root.get("skilladam"),
        f"{name}.skilladam",
    )
    skillopt = _freeze_object(root.get("skillopt"), f"{name}.skillopt")
    evaluation = _freeze_object(
        root.get("evaluation"),
        f"{name}.evaluation",
    )
    artifacts = _freeze_object(
        root.get("selected_artifacts"),
        f"{name}.selected_artifacts",
    )

    if stage0.get("shared_by_methods") is not True:
        raise ValueError(f"{name} must share one Stage0 skill")
    if stage0.get("seed") != seed:
        raise ValueError(f"{name}.stage0 must use seed {seed}")
    if skillopt.get("reasoning_effort") != "medium":
        raise ValueError(
            f"{name}.skillopt must use medium reasoning effort"
        )
    _positive_int(stage0.get("workers"), f"{name}.stage0.workers")
    for method_name, method in (
        ("skilladam", skilladam),
        ("skillopt", skillopt),
    ):
        if method.get("seed") != seed:
            raise ValueError(f"{name}.{method_name} must use seed {seed}")
    if skillopt.get("shuffle_each_epoch") is not True:
        raise ValueError(
            f"{name}.skillopt must deterministically shuffle each epoch"
        )
    if skilladam.get("validation_cases") != "same_as_training_batch":
        raise ValueError(
            f"{name}.skilladam must gate on the training batch"
        )
    if skilladam.get("trajectory_compression") not in {
        "deterministic",
        "llm",
    }:
        raise ValueError(
            f"{name}.skilladam trajectory compression is invalid"
        )
    context_limit = skilladam.get("optimizer_context_limit")
    if context_limit is not None:
        _positive_int(
            context_limit,
            f"{name}.skilladam.optimizer_context_limit",
        )
    _positive_int(
        evaluation.get("workers"),
        f"{name}.evaluation.workers",
    )
    _positive_int(
        evaluation.get("request_timeout_seconds"),
        f"{name}.evaluation.request_timeout_seconds",
    )
    if name == "deepplanning":
        if skilladam.get("framework") != (
            "e2_momentum_adaptive_edit_budget"
        ):
            raise ValueError(
                "deepplanning SkillAdam must use the E2 framework"
            )
        if (
            skilladam.get("momentum") is not True
            or skilladam.get("optimization_memory") is not True
            or not isinstance(
                skilladam.get("adaptive_edit_budget"),
                Mapping,
            )
        ):
            raise ValueError(
                "deepplanning E2 must enable memory and edit budget"
            )
        if skilladam.get("cache_session_prefix") != (
            "deepplanning-e2:momentum-edit-budget"
        ):
            raise ValueError(
                "deepplanning E2 cache session prefix must match the paper profile"
            )
        if skillopt.get("conversion_model") != "gpt-4.1":
            raise ValueError(
                "deepplanning travel conversion must use gpt-4.1"
            )
    paper_results = _object(
        evaluation.get("paper_results_percent"),
        f"{name}.evaluation.paper_results_percent",
    )
    if set(paper_results) != _METHODS:
        raise ValueError(
            f"{name} paper results must cover baseline/skillopt/skilladam"
        )
    for method, value in paper_results.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= float(value) <= 100.0
        ):
            raise ValueError(f"{name}.{method} paper result is invalid")
    if not artifacts:
        raise ValueError(f"{name} must declare selected artifacts")
    for scope, digest in artifacts.items():
        _text(scope, f"{name}.artifact scope")
        _sha256(digest, f"{name}.artifact digest")
    return BenchmarkMainResultProfile(
        name=name,
        provider=provider,
        model=model,
        dataset=dataset,
        stage0=stage0,
        skilladam=skilladam,
        skillopt=skillopt,
        evaluation=evaluation,
        selected_artifacts=artifacts,
    )


def _freeze_object(value: Any, field_name: str) -> Mapping[str, Any]:
    return _freeze(_object(value, field_name))


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()


def _positive_int(
    value: Any,
    field_name: str,
    *,
    allow_zero: bool = False,
) -> int:
    minimum = 0 if allow_zero else 1
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise ValueError(f"{field_name} must be an integer >= {minimum}")
    return value


def _sha256(value: Any, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return value


__all__ = [
    "BenchmarkMainResultProfile",
    "MainResultProfile",
    "load_main_result_profile",
]
