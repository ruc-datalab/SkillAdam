"""Read-only access to packaged SkillAdam reproducibility skills."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Mapping


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_ROLES = frozenset({"selected", "alternate"})


class SkillArtifactError(ValueError):
    """Base error for invalid artifact selection or integrity."""


class SkillArtifactNotFoundError(SkillArtifactError):
    """Raised when a benchmark, scope, or artifact is unavailable."""


class SkillArtifactIntegrityError(SkillArtifactError):
    """Raised when packaged metadata or bytes fail validation."""


@dataclass(frozen=True, slots=True)
class SkillArtifactSource:
    """Portable source identity for one packaged skill."""

    logical_path: str
    experiment_id: str


@dataclass(frozen=True, slots=True)
class SkillArtifactEvaluation:
    """Historical evidence associated with one skill."""

    evidence_level: str
    split: str
    cases: int
    metric: str
    score: float | None
    solved: int | None
    secondary_metrics: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class SkillArtifact:
    """Immutable metadata for one packaged Markdown artifact."""

    artifact_id: str
    method: str
    benchmark: str
    scope: str
    role: str
    relative_path: str
    source_sha256: str
    public_sha256: str
    source: SkillArtifactSource
    generation: str
    model: str
    evaluation: SkillArtifactEvaluation
    provisional: bool
    contains_optimizer_learned_answer_examples: bool


@dataclass(frozen=True, slots=True)
class BenchmarkSkillArtifacts:
    """Artifact selection rules for one benchmark."""

    default_artifact_id: str | None
    default_by_scope: Mapping[str, str]
    artifacts: tuple[SkillArtifact, ...]


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """Versioned immutable collection of public skill artifacts."""

    schema_version: int
    method: str
    benchmarks: Mapping[str, BenchmarkSkillArtifacts]


def load_skill_manifest() -> SkillManifest:
    """Read and validate a fresh immutable manifest from package resources."""

    manifest_resource = _skill_root() / "manifest.json"
    try:
        payload = json.loads(manifest_resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SkillArtifactIntegrityError(
            "packaged skill manifest is missing or invalid"
        ) from exc
    return _parse_manifest(payload)


def list_skill_scopes(benchmark: str) -> tuple[str, ...]:
    """Return sorted selected scopes for a public benchmark."""

    entry = _get_benchmark(load_skill_manifest(), benchmark)
    return tuple(
        sorted(
            artifact.scope
            for artifact in entry.artifacts
            if artifact.role == "selected"
        )
    )


def load_best_skill(
    benchmark: str,
    *,
    scope: str | None = None,
) -> str:
    """Load the checksum-verified selected skill for a benchmark or scope."""

    entry = _get_benchmark(load_skill_manifest(), benchmark)
    artifact = _select_default_artifact(entry, scope=scope)
    return _read_verified_artifact(artifact)


def load_skill_artifact(
    benchmark: str,
    *,
    artifact_id: str,
) -> str:
    """Load a checksum-verified artifact by explicit ID."""

    entry = _get_benchmark(load_skill_manifest(), benchmark)
    artifact = next(
        (
            item
            for item in entry.artifacts
            if item.artifact_id == artifact_id
        ),
        None,
    )
    if artifact is None:
        raise SkillArtifactNotFoundError(
            f"unknown artifact {artifact_id!r} for benchmark {benchmark!r}"
        )
    return _read_verified_artifact(artifact)


def _skill_root():
    return resources.files("skilladam.artifacts") / "skills"


def _get_benchmark(
    manifest: SkillManifest,
    benchmark: str,
) -> BenchmarkSkillArtifacts:
    try:
        return manifest.benchmarks[benchmark]
    except KeyError as exc:
        raise SkillArtifactNotFoundError(
            f"unknown benchmark {benchmark!r}"
        ) from exc


def _select_default_artifact(
    entry: BenchmarkSkillArtifacts,
    *,
    scope: str | None,
) -> SkillArtifact:
    if entry.default_by_scope:
        if scope is None:
            available = ", ".join(sorted(entry.default_by_scope))
            raise SkillArtifactNotFoundError(
                "this benchmark requires an explicit scope; "
                f"available scopes: {available}"
            )
        try:
            artifact_id = entry.default_by_scope[scope]
        except KeyError as exc:
            raise SkillArtifactNotFoundError(
                f"unknown scope {scope!r}; available scopes: "
                f"{', '.join(sorted(entry.default_by_scope))}"
            ) from exc
    else:
        if scope is not None:
            raise SkillArtifactNotFoundError(
                "this benchmark does not accept a scope"
            )
        artifact_id = entry.default_artifact_id
        if artifact_id is None:
            raise SkillArtifactIntegrityError(
                "benchmark has no selected default artifact"
            )

    artifact = next(
        (
            item
            for item in entry.artifacts
            if item.artifact_id == artifact_id
        ),
        None,
    )
    if artifact is None:
        raise SkillArtifactIntegrityError(
            f"default artifact {artifact_id!r} is not declared"
        )
    if artifact.role != "selected":
        raise SkillArtifactIntegrityError(
            "default artifact must be selected"
        )
    if scope is not None and artifact.scope != scope:
        raise SkillArtifactIntegrityError(
            "default artifact scope does not match selection scope"
        )
    return artifact


def _read_verified_artifact(artifact: SkillArtifact) -> str:
    path = PurePosixPath(artifact.relative_path)
    resource = _skill_root().joinpath(*path.parts)
    if not resource.is_file():
        raise SkillArtifactIntegrityError(
            f"packaged artifact is missing: {artifact.relative_path}"
        )
    return _decode_verified_artifact(artifact, resource.read_bytes())


def _decode_verified_artifact(
    artifact: SkillArtifact,
    payload: bytes,
) -> str:
    digest = sha256(payload).hexdigest()
    if digest != artifact.public_sha256:
        raise SkillArtifactIntegrityError(
            f"artifact checksum mismatch for {artifact.relative_path}"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillArtifactIntegrityError(
            f"artifact is not UTF-8: {artifact.relative_path}"
        ) from exc
    if not text.strip():
        raise SkillArtifactIntegrityError(
            f"artifact is empty: {artifact.relative_path}"
        )
    return text


def _parse_manifest(payload: Any) -> SkillManifest:
    root = _object(payload, "manifest")
    schema_version = root.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise SkillArtifactIntegrityError(
            f"unsupported skill manifest schema_version {schema_version!r}"
        )
    method = _text(root.get("method"), "manifest.method")
    if method != "skilladam":
        raise SkillArtifactIntegrityError(
            "skill manifest method must be 'skilladam'"
        )
    raw_benchmarks = _object(
        root.get("benchmarks"),
        "manifest.benchmarks",
    )
    if not raw_benchmarks:
        raise SkillArtifactIntegrityError(
            "skill manifest must declare benchmarks"
        )

    benchmarks: dict[str, BenchmarkSkillArtifacts] = {}
    all_paths: set[str] = set()
    for benchmark, raw_entry in raw_benchmarks.items():
        benchmark_name = _text(benchmark, "benchmark name")
        entry = _parse_benchmark(
            benchmark_name,
            raw_entry,
            all_paths=all_paths,
        )
        benchmarks[benchmark_name] = entry
    return SkillManifest(
        schema_version=1,
        method=method,
        benchmarks=MappingProxyType(benchmarks),
    )


def _parse_benchmark(
    benchmark: str,
    payload: Any,
    *,
    all_paths: set[str],
) -> BenchmarkSkillArtifacts:
    root = _object(payload, f"benchmarks.{benchmark}")
    default_id_value = root.get("default_artifact_id")
    default_id = (
        None
        if default_id_value is None
        else _text(
            default_id_value,
            f"benchmarks.{benchmark}.default_artifact_id",
        )
    )
    raw_by_scope = _object(
        root.get("default_by_scope"),
        f"benchmarks.{benchmark}.default_by_scope",
    )
    default_by_scope = {
        _text(scope, f"benchmarks.{benchmark}.scope"): _text(
            artifact_id,
            f"benchmarks.{benchmark}.default_by_scope[{scope!r}]",
        )
        for scope, artifact_id in raw_by_scope.items()
    }
    if default_id is not None and default_by_scope:
        raise SkillArtifactIntegrityError(
            f"benchmark {benchmark!r} cannot declare both global and "
            "scoped defaults"
        )
    raw_artifacts = root.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise SkillArtifactIntegrityError(
            f"benchmarks.{benchmark}.artifacts must be a non-empty array"
        )
    artifacts = tuple(
        _parse_artifact(
            benchmark,
            raw_artifact,
            index=index,
            all_paths=all_paths,
        )
        for index, raw_artifact in enumerate(raw_artifacts)
    )

    by_id: dict[str, SkillArtifact] = {}
    selected_scopes: set[str] = set()
    for artifact in artifacts:
        if artifact.artifact_id in by_id:
            raise SkillArtifactIntegrityError(
                f"duplicate artifact ID {artifact.artifact_id!r} "
                f"for benchmark {benchmark!r}"
            )
        by_id[artifact.artifact_id] = artifact
        if artifact.role == "selected":
            if artifact.scope in selected_scopes:
                raise SkillArtifactIntegrityError(
                    f"duplicate selected scope {artifact.scope!r} "
                    f"for benchmark {benchmark!r}"
                )
            selected_scopes.add(artifact.scope)

    referenced_ids = set(default_by_scope.values())
    if default_id is not None:
        referenced_ids.add(default_id)
    missing = referenced_ids - set(by_id)
    if missing:
        raise SkillArtifactIntegrityError(
            f"benchmark {benchmark!r} references unknown defaults: "
            f"{sorted(missing)!r}"
        )
    for scope, artifact_id in default_by_scope.items():
        artifact = by_id[artifact_id]
        if artifact.role != "selected" or artifact.scope != scope:
            raise SkillArtifactIntegrityError(
                f"benchmark {benchmark!r} has invalid default for "
                f"scope {scope!r}"
            )
    if default_id is not None and by_id[default_id].role != "selected":
        raise SkillArtifactIntegrityError(
            f"benchmark {benchmark!r} default artifact must be selected"
        )
    if default_id is not None and by_id[default_id].scope != "all":
        raise SkillArtifactIntegrityError(
            f"benchmark {benchmark!r} global default must use scope 'all'"
        )
    selected_ids = {
        artifact.artifact_id
        for artifact in artifacts
        if artifact.role == "selected"
    }
    if selected_ids != referenced_ids:
        raise SkillArtifactIntegrityError(
            f"benchmark {benchmark!r} selected artifacts must exactly "
            "match defaults"
        )

    return BenchmarkSkillArtifacts(
        default_artifact_id=default_id,
        default_by_scope=MappingProxyType(default_by_scope),
        artifacts=artifacts,
    )


def _parse_artifact(
    benchmark: str,
    payload: Any,
    *,
    index: int,
    all_paths: set[str],
) -> SkillArtifact:
    label = f"benchmarks.{benchmark}.artifacts[{index}]"
    root = _object(payload, label)
    artifact_benchmark = _text(
        root.get("benchmark"),
        f"{label}.benchmark",
    )
    if artifact_benchmark != benchmark:
        raise SkillArtifactIntegrityError(
            f"{label}.benchmark does not match {benchmark!r}"
        )
    method = _text(root.get("method"), f"{label}.method")
    if method != "skilladam":
        raise SkillArtifactIntegrityError(
            f"{label}.method must be 'skilladam'"
        )
    role = _text(root.get("role"), f"{label}.role")
    if role not in _ROLES:
        raise SkillArtifactIntegrityError(
            f"{label}.role must be one of {sorted(_ROLES)!r}"
        )
    relative_path = _portable_relative_path(
        root.get("relative_path"),
        benchmark=benchmark,
        label=f"{label}.relative_path",
    )
    if relative_path in all_paths:
        raise SkillArtifactIntegrityError(
            f"duplicate artifact path {relative_path!r}"
        )
    all_paths.add(relative_path)

    source_payload = _object(root.get("source"), f"{label}.source")
    source = SkillArtifactSource(
        logical_path=_portable_logical_path(
            source_payload.get("logical_path"),
            f"{label}.source.logical_path",
        ),
        experiment_id=_text(
            source_payload.get("experiment_id"),
            f"{label}.source.experiment_id",
        ),
    )
    evaluation = _parse_evaluation(
        root.get("evaluation"),
        f"{label}.evaluation",
    )
    provisional = root.get("provisional")
    learned_examples = root.get(
        "contains_optimizer_learned_answer_examples"
    )
    if not isinstance(provisional, bool):
        raise SkillArtifactIntegrityError(
            f"{label}.provisional must be a boolean"
        )
    if not isinstance(learned_examples, bool):
        raise SkillArtifactIntegrityError(
            f"{label}.contains_optimizer_learned_answer_examples "
            "must be a boolean"
        )
    return SkillArtifact(
        artifact_id=_text(
            root.get("artifact_id"),
            f"{label}.artifact_id",
        ),
        method=method,
        benchmark=benchmark,
        scope=_text(root.get("scope"), f"{label}.scope"),
        role=role,
        relative_path=relative_path,
        source_sha256=_sha256(
            root.get("source_sha256"),
            f"{label}.source_sha256",
        ),
        public_sha256=_sha256(
            root.get("public_sha256"),
            f"{label}.public_sha256",
        ),
        source=source,
        generation=_text(
            root.get("generation"),
            f"{label}.generation",
        ),
        model=_text(root.get("model"), f"{label}.model"),
        evaluation=evaluation,
        provisional=provisional,
        contains_optimizer_learned_answer_examples=learned_examples,
    )


def _parse_evaluation(
    payload: Any,
    label: str,
) -> SkillArtifactEvaluation:
    root = _object(payload, label)
    cases = _positive_int(root.get("cases"), f"{label}.cases")
    solved_value = root.get("solved")
    solved = (
        None
        if solved_value is None
        else _non_negative_int(solved_value, f"{label}.solved")
    )
    if solved is not None and solved > cases:
        raise SkillArtifactIntegrityError(
            f"{label}.solved cannot exceed cases"
        )
    score_value = root.get("score")
    score = (
        None
        if score_value is None
        else _finite_number(score_value, f"{label}.score")
    )
    raw_secondary = _object(
        root.get("secondary_metrics"),
        f"{label}.secondary_metrics",
    )
    secondary = {
        _text(name, f"{label}.secondary metric name"): _finite_number(
            value,
            f"{label}.secondary_metrics[{name!r}]",
        )
        for name, value in raw_secondary.items()
    }
    return SkillArtifactEvaluation(
        evidence_level=_text(
            root.get("evidence_level"),
            f"{label}.evidence_level",
        ),
        split=_text(root.get("split"), f"{label}.split"),
        cases=cases,
        metric=_text(root.get("metric"), f"{label}.metric"),
        score=score,
        solved=solved,
        secondary_metrics=MappingProxyType(secondary),
    )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SkillArtifactIntegrityError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillArtifactIntegrityError(
            f"{label} must be a non-empty string"
        )
    return value


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label)
    if not _SHA256_PATTERN.fullmatch(text):
        raise SkillArtifactIntegrityError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return text


def _portable_relative_path(
    value: Any,
    *,
    benchmark: str,
    label: str,
) -> str:
    text = _portable_logical_path(value, label)
    path = PurePosixPath(text)
    if path.parts[0] != benchmark or path.suffix != ".md":
        raise SkillArtifactIntegrityError(
            f"{label} must be Markdown below {benchmark!r}"
        )
    return text


def _portable_logical_path(value: Any, label: str) -> str:
    text = _text(value, label)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or "\\" in text
        or ".." in path.parts
        or not path.parts
    ):
        raise SkillArtifactIntegrityError(
            f"{label} must be a portable relative POSIX path"
        )
    return text


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SkillArtifactIntegrityError(
            f"{label} must be a positive integer"
        )
    return value


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SkillArtifactIntegrityError(
            f"{label} must be a non-negative integer"
        )
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SkillArtifactIntegrityError(
            f"{label} must be numeric"
        )
    number = float(value)
    if not math.isfinite(number):
        raise SkillArtifactIntegrityError(
            f"{label} must be finite"
        )
    return number


__all__ = [
    "BenchmarkSkillArtifacts",
    "SkillArtifact",
    "SkillArtifactError",
    "SkillArtifactEvaluation",
    "SkillArtifactIntegrityError",
    "SkillArtifactNotFoundError",
    "SkillArtifactSource",
    "SkillManifest",
    "list_skill_scopes",
    "load_best_skill",
    "load_skill_artifact",
    "load_skill_manifest",
]
