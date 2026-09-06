"""Path-private skill resolution for public benchmark execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Literal

from skilladam.artifacts import (
    load_best_skill,
    load_skill_manifest,
)
from skilladam.benchmarks.deepplanning.manifest import SLICE_SPECS
from skilladam.types import BenchmarkCase, Method, PUBLIC_METHODS


SkillSourceKind = Literal[
    "packaged_selected",
    "explicit_file",
    "explicit_map",
]


@dataclass(frozen=True, slots=True)
class SkillSource:
    """Portable skill identity that never includes a local path."""

    source: SkillSourceKind
    sha256: str
    artifact_id: str | None
    scope: str

    def __post_init__(self) -> None:
        if self.source not in {
            "packaged_selected",
            "explicit_file",
            "explicit_map",
        }:
            raise ValueError(f"unsupported skill source {self.source!r}")
        _sha256(self.sha256)
        if self.artifact_id is not None and (
            not isinstance(self.artifact_id, str)
            or not self.artifact_id.strip()
        ):
            raise ValueError("artifact_id must be non-empty text or null")
        if self.artifact_id is not None:
            _portable_label(self.artifact_id, "artifact_id")
        _portable_label(self.scope, "skill scope")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "source": self.source,
            "sha256": self.sha256,
            "artifact_id": self.artifact_id,
            "scope": self.scope,
        }


@dataclass(frozen=True, slots=True)
class ResolvedSkill:
    """One immutable skill payload plus its portable identity."""

    text: str
    sha256: str
    source: SkillSource

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("resolved skill must contain non-empty text")
        _sha256(self.sha256)
        if sha256(self.text.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("resolved skill checksum does not match text")
        if not isinstance(self.source, SkillSource):
            raise ValueError("resolved skill source is invalid")
        if self.source.sha256 != self.sha256:
            raise ValueError(
                "resolved skill source checksum does not match text"
            )


class SkillResolver:
    """Resolve one validated skill or no skill for every selected case."""

    def __init__(
        self,
        by_case_id: Mapping[str, ResolvedSkill | None],
        sources: Sequence[SkillSource],
    ) -> None:
        self._by_case_id = MappingProxyType(dict(by_case_id))
        self._sources = tuple(sources)

    @classmethod
    def build(
        cls,
        *,
        benchmark: str,
        method: Method,
        cases: Sequence[BenchmarkCase],
        scope: str | None,
        skill_path: Path | None = None,
        skill_map_path: Path | None = None,
    ) -> SkillResolver:
        selected = _selected_cases(cases)
        if method not in PUBLIC_METHODS:
            raise ValueError(f"unsupported method {method!r}")
        if skill_path is not None and skill_map_path is not None:
            raise ValueError(
                "conflicting --skill and --skill-map sources"
            )
        if method == "baseline":
            if skill_path is not None or skill_map_path is not None:
                raise ValueError("baseline cannot receive a skill source")
            return cls(
                {case.case_id: None for case in selected},
                (),
            )
        if method == "skillopt" and (
            skill_path is None and skill_map_path is None
        ):
            raise ValueError("SkillOpt requires an explicit skill source")

        case_scopes = _selected_scopes(
            benchmark,
            selected,
            scope=scope,
        )
        if skill_map_path is not None:
            return cls._from_map(
                benchmark=benchmark,
                cases=selected,
                case_scopes=case_scopes,
                path=Path(skill_map_path),
            )
        if skill_path is not None:
            return cls._from_file(
                benchmark=benchmark,
                cases=selected,
                case_scopes=case_scopes,
                path=Path(skill_path),
            )
        return cls._from_packaged(
            benchmark=benchmark,
            cases=selected,
            case_scopes=case_scopes,
        )

    @classmethod
    def _from_file(
        cls,
        *,
        benchmark: str,
        cases: tuple[BenchmarkCase, ...],
        case_scopes: Mapping[str, str],
        path: Path,
    ) -> SkillResolver:
        selected_scopes = tuple(dict.fromkeys(case_scopes.values()))
        if benchmark == "deepplanning" and len(selected_scopes) != 1:
            raise ValueError(
                "one skill file requires one DeepPlanning scope"
            )
        text, digest = _read_markdown(path)
        skill_scope = (
            selected_scopes[0]
            if benchmark == "deepplanning"
            else "all"
        )
        source = SkillSource(
            source="explicit_file",
            sha256=digest,
            artifact_id=None,
            scope=skill_scope,
        )
        resolved = ResolvedSkill(
            text=text,
            sha256=digest,
            source=source,
        )
        return cls(
            {case.case_id: resolved for case in cases},
            (source,),
        )

    @classmethod
    def _from_map(
        cls,
        *,
        benchmark: str,
        cases: tuple[BenchmarkCase, ...],
        case_scopes: Mapping[str, str],
        path: Path,
    ) -> SkillResolver:
        if benchmark != "deepplanning":
            raise ValueError(
                "--skill-map is only supported for DeepPlanning"
            )
        raw_map = _read_skill_map(path)
        selected_scopes = set(case_scopes.values())
        if set(raw_map) != selected_scopes:
            raise ValueError(
                "skill map must exactly cover selected "
                f"DeepPlanning scopes: {sorted(selected_scopes)!r}"
            )

        resolved_by_scope: dict[str, ResolvedSkill] = {}
        sources: list[SkillSource] = []
        for skill_scope in sorted(selected_scopes):
            candidate = Path(raw_map[skill_scope])
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            text, digest = _read_markdown(candidate)
            source = SkillSource(
                source="explicit_map",
                sha256=digest,
                artifact_id=None,
                scope=skill_scope,
            )
            resolved_by_scope[skill_scope] = ResolvedSkill(
                text=text,
                sha256=digest,
                source=source,
            )
            sources.append(source)
        return cls(
            {
                case.case_id: resolved_by_scope[
                    case_scopes[case.case_id]
                ]
                for case in cases
            },
            sources,
        )

    @classmethod
    def _from_packaged(
        cls,
        *,
        benchmark: str,
        cases: tuple[BenchmarkCase, ...],
        case_scopes: Mapping[str, str],
    ) -> SkillResolver:
        manifest = load_skill_manifest()
        try:
            entry = manifest.benchmarks[benchmark]
        except KeyError as exc:
            raise ValueError(
                f"no packaged skill for benchmark {benchmark!r}"
            ) from exc

        resolved_by_scope: dict[str, ResolvedSkill] = {}
        sources: list[SkillSource] = []
        for skill_scope in tuple(dict.fromkeys(case_scopes.values())):
            artifact_scope = (
                skill_scope if benchmark == "deepplanning" else None
            )
            text = load_best_skill(
                benchmark,
                scope=artifact_scope,
            )
            digest = sha256(text.encode("utf-8")).hexdigest()
            artifact_id = (
                entry.default_by_scope[skill_scope]
                if benchmark == "deepplanning"
                else entry.default_artifact_id
            )
            if artifact_id is None:
                raise ValueError("packaged skill has no artifact ID")
            source = SkillSource(
                source="packaged_selected",
                sha256=digest,
                artifact_id=artifact_id,
                scope=skill_scope,
            )
            resolved_by_scope[skill_scope] = ResolvedSkill(
                text=text,
                sha256=digest,
                source=source,
            )
            sources.append(source)
        return cls(
            {
                case.case_id: resolved_by_scope[
                    case_scopes[case.case_id]
                ]
                for case in cases
            },
            sources,
        )

    def resolve(self, case: BenchmarkCase) -> ResolvedSkill | None:
        """Return the resolved skill for one already-selected case."""

        try:
            return self._by_case_id[case.case_id]
        except KeyError as exc:
            raise ValueError(
                f"case {case.case_id!r} was not selected for this run"
            ) from exc

    def portable_sources(self) -> tuple[dict[str, str | None], ...]:
        """Return deterministic, local-path-free source metadata."""

        return tuple(source.to_dict() for source in self._sources)

    @property
    def sources(self) -> tuple[SkillSource, ...]:
        """Return immutable source records for execution planning."""

        return self._sources


def _selected_cases(
    cases: Sequence[BenchmarkCase],
) -> tuple[BenchmarkCase, ...]:
    selected = tuple(cases)
    if not selected:
        raise ValueError("skill resolution requires selected cases")
    if any(not isinstance(case, BenchmarkCase) for case in selected):
        raise ValueError("skill cases must be BenchmarkCase values")
    case_ids = tuple(case.case_id for case in selected)
    if any(
        not isinstance(case_id, str) or not case_id.strip()
        for case_id in case_ids
    ):
        raise ValueError("skill case IDs must be non-empty text")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("skill cases contain duplicate case IDs")
    return selected


def _selected_scopes(
    benchmark: str,
    cases: tuple[BenchmarkCase, ...],
    *,
    scope: str | None,
) -> Mapping[str, str]:
    if benchmark != "deepplanning":
        if scope is not None:
            raise ValueError("scope is only supported for DeepPlanning")
        return MappingProxyType(
            {case.case_id: "all" for case in cases}
        )

    by_case: dict[str, str] = {}
    for case in cases:
        case_scope = case.metadata.get(
            "slice",
            case.payload.get("slice"),
        )
        if (
            not isinstance(case_scope, str)
            or case_scope not in SLICE_SPECS
        ):
            raise ValueError(
                f"DeepPlanning case {case.case_id!r} has invalid slice"
            )
        if scope is not None and case_scope != scope:
            raise ValueError(
                "selected DeepPlanning cases do not match scope"
            )
        by_case[case.case_id] = case_scope
    return MappingProxyType(by_case)


def _read_markdown(path: Path) -> tuple[str, str]:
    if path.suffix.casefold() != ".md":
        raise ValueError("skill file must be Markdown with a .md suffix")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"could not read skill file: {exc}") from exc
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("skill file must be UTF-8") from exc
    if not text.strip():
        raise ValueError("skill file must not be empty")
    return text, sha256(payload).hexdigest()


def _read_skill_map(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"could not read skill map: {exc}") from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"skill map must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("skill map must contain a JSON object")
    result: dict[str, str] = {}
    for raw_scope, raw_path in payload.items():
        if (
            not isinstance(raw_scope, str)
            or raw_scope not in SLICE_SPECS
        ):
            raise ValueError(
                f"skill map has unknown DeepPlanning scope {raw_scope!r}"
            )
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(
                "skill map values must be non-empty Markdown paths"
            )
        result[raw_scope] = raw_path
    return result


def _unique_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"skill map has duplicate key {key!r}")
        result[key] = value
    return result


def _sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("skill sha256 must be a lowercase SHA-256")
    return value


def _portable_label(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    if (
        PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        raise ValueError(
            f"{field_name} must be a portable logical value"
        )
    return value


__all__ = [
    "ResolvedSkill",
    "SkillResolver",
    "SkillSource",
    "SkillSourceKind",
]
