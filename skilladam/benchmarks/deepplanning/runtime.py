"""Read-only validation for a pinned external DeepPlanning runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

from skilladam.benchmarks.deepplanning.manifest import SLICE_SPECS


OFFICIAL_QWEN_AGENT_REVISION = (
    "31a4d36d123688581a9e9744427272b33ce940e0"
)
OFFICIAL_DATASET_REVISION = (
    "213876cce679f993a476d01042e13d111c0e3648"
)
PAPER_PROFILE = "skilladam-paper-240-v1"
RUNTIME_MANIFEST_FILENAME = "deepplanning_runtime_manifest.json"
BRIDGE_PROTOCOL_VERSION = 1

_MODULE_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "profile",
        "qwen_agent_revision",
        "dataset_revision",
        "qwen_agent_root",
        "benchmark_root",
        "bridge_module",
        "bridge_protocol_version",
        "slices",
    }
)
_SLICE_KEYS = frozenset({"query_file", "database_root"})
_REQUIRED_CODE_PATHS = (
    "README.md",
    "requirements.txt",
    "shoppingplanning/run.py",
    "shoppingplanning/agent/call_llm.py",
    "shoppingplanning/agent/prompts.py",
    "shoppingplanning/agent/shopping_agent.py",
    "shoppingplanning/evaluation/evaluation_pipeline.py",
    "shoppingplanning/tools/shopping_tool_schema.json",
    "travelplanning/run.py",
    "travelplanning/agent/call_llm.py",
    "travelplanning/agent/prompts.py",
    "travelplanning/agent/tools_fn_agent.py",
    "travelplanning/evaluation/convert_report.py",
    "travelplanning/evaluation/eval_converted.py",
    "travelplanning/tools/tool_schema_en.json",
)


class DeepPlanningRuntimeError(ValueError):
    """Raised when an external runtime cannot prove the pinned profile."""


@dataclass(frozen=True, slots=True)
class SliceRuntime:
    """Portable paths for one canonical DeepPlanning slice."""

    query_file: PurePosixPath
    database_root: PurePosixPath


@dataclass(frozen=True, slots=True)
class DeepPlanningRuntimeManifest:
    """Validated, path-portable runtime identity."""

    root: Path
    qwen_agent_root: PurePosixPath
    benchmark_root: PurePosixPath
    bridge_module: str
    slices: Mapping[str, SliceRuntime]


@dataclass(frozen=True, slots=True)
class DeepPlanningRuntimePreflight:
    """Secret-free result of one read-only runtime validation."""

    profile: str
    qwen_agent_revision: str
    dataset_revision: str
    bridge_module: str
    bridge_protocol_version: int
    case_count: int
    slice_case_counts: Mapping[str, int]
    git_revision_verified: bool
    identity_sha256: str

    def to_public_dict(self) -> dict[str, object]:
        """Return path-free metadata safe for run manifests."""

        return {
            "profile": self.profile,
            "qwen_agent_revision": self.qwen_agent_revision,
            "dataset_revision": self.dataset_revision,
            "bridge_module": self.bridge_module,
            "bridge_protocol_version": self.bridge_protocol_version,
            "case_count": self.case_count,
            "slice_case_counts": dict(self.slice_case_counts),
            "git_revision_verified": self.git_revision_verified,
            "identity_sha256": self.identity_sha256,
        }


def load_runtime_manifest(
    root: Path,
) -> DeepPlanningRuntimeManifest:
    """Load the strict caller-managed runtime manifest without mutation."""

    runtime_root = _existing_directory(Path(root), "runtime root")
    manifest_path = _contained_path(
        runtime_root,
        PurePosixPath(RUNTIME_MANIFEST_FILENAME),
        expected="file",
    )
    payload = _read_json_object(manifest_path)
    _exact_keys(payload, _MANIFEST_KEYS, "runtime manifest")

    if payload["schema_version"] != 1:
        raise DeepPlanningRuntimeError(
            "DeepPlanning runtime schema_version must be 1"
        )
    if payload["profile"] != PAPER_PROFILE:
        raise DeepPlanningRuntimeError(
            "DeepPlanning runtime profile must be "
            f"{PAPER_PROFILE!r}"
        )
    if payload["qwen_agent_revision"] != OFFICIAL_QWEN_AGENT_REVISION:
        raise DeepPlanningRuntimeError(
            "DeepPlanning Qwen-Agent revision does not match the "
            "pinned public runtime"
        )
    if payload["dataset_revision"] != OFFICIAL_DATASET_REVISION:
        raise DeepPlanningRuntimeError(
            "DeepPlanning dataset revision does not match the "
            "pinned public runtime"
        )
    if payload["bridge_protocol_version"] != BRIDGE_PROTOCOL_VERSION:
        raise DeepPlanningRuntimeError(
            "DeepPlanning bridge protocol version is unsupported"
        )

    bridge_module = payload["bridge_module"]
    if (
        not isinstance(bridge_module, str)
        or not _MODULE_PATTERN.fullmatch(bridge_module)
    ):
        raise DeepPlanningRuntimeError(
            "DeepPlanning bridge_module must be an absolute dotted "
            "Python module"
        )

    raw_slices = payload["slices"]
    if not isinstance(raw_slices, Mapping):
        raise DeepPlanningRuntimeError(
            "DeepPlanning runtime slices must be an object"
        )
    if set(raw_slices) != set(SLICE_SPECS):
        raise DeepPlanningRuntimeError(
            "DeepPlanning runtime slices must exactly cover the "
            "four paper-profile slices"
        )
    slices: dict[str, SliceRuntime] = {}
    for slice_id in sorted(SLICE_SPECS):
        raw_slice = raw_slices[slice_id]
        if not isinstance(raw_slice, Mapping):
            raise DeepPlanningRuntimeError(
                f"DeepPlanning runtime slice {slice_id!r} must be an object"
            )
        _exact_keys(
            raw_slice,
            _SLICE_KEYS,
            f"runtime slice {slice_id}",
        )
        slices[slice_id] = SliceRuntime(
            query_file=_portable_path(
                raw_slice["query_file"],
                f"slices.{slice_id}.query_file",
            ),
            database_root=_portable_path(
                raw_slice["database_root"],
                f"slices.{slice_id}.database_root",
            ),
        )

    return DeepPlanningRuntimeManifest(
        root=runtime_root,
        qwen_agent_root=_portable_path(
            payload["qwen_agent_root"],
            "qwen_agent_root",
        ),
        benchmark_root=_portable_path(
            payload["benchmark_root"],
            "benchmark_root",
        ),
        bridge_module=bridge_module,
        slices=slices,
    )


def preflight_runtime(
    root: Path,
    *,
    revision_reader: Callable[[Path], str | None] | None = None,
) -> DeepPlanningRuntimePreflight:
    """Validate code/data identity without importing or executing it."""

    manifest = load_runtime_manifest(root)
    qwen_agent_root = _contained_path(
        manifest.root,
        manifest.qwen_agent_root,
        expected="directory",
    )
    benchmark_root = _contained_path(
        manifest.root,
        manifest.benchmark_root,
        expected="directory",
    )
    try:
        benchmark_root.relative_to(qwen_agent_root)
    except ValueError:
        raise DeepPlanningRuntimeError(
            "DeepPlanning benchmark_root must be inside qwen_agent_root"
        ) from None

    selected_reader = revision_reader or _read_git_revision
    revision = selected_reader(qwen_agent_root)
    if (
        revision is not None
        and revision != OFFICIAL_QWEN_AGENT_REVISION
    ):
        raise DeepPlanningRuntimeError(
            "DeepPlanning checkout HEAD does not match the pinned "
            "Qwen-Agent revision"
        )

    for relative in _REQUIRED_CODE_PATHS:
        _contained_path(
            benchmark_root,
            PurePosixPath(relative),
            expected="file",
        )

    slice_counts: dict[str, int] = {}
    identity_rows: list[dict[str, object]] = []
    for slice_id in sorted(SLICE_SPECS):
        slice_runtime = manifest.slices[slice_id]
        query_path = _contained_path(
            benchmark_root,
            slice_runtime.query_file,
            expected="file",
        )
        database_root = _contained_path(
            benchmark_root,
            slice_runtime.database_root,
            expected="directory",
        )
        expected_ids = _expected_ids(slice_id)
        actual_ids = _query_ids(query_path, slice_id)
        if actual_ids != expected_ids:
            raise DeepPlanningRuntimeError(
                f"DeepPlanning {slice_id} query IDs do not match "
                "the paper profile"
            )
        _validate_case_directories(
            database_root,
            slice_id=slice_id,
            case_ids=expected_ids,
            runtime_root=manifest.root,
        )
        slice_counts[slice_id] = len(expected_ids)
        spec = SLICE_SPECS[slice_id]
        identity_rows.extend(
            {
                "slice": slice_id,
                "case_id": case_id,
                "domain": spec["domain"],
                "level": spec["level"],
                "language": spec["language"],
            }
            for case_id in expected_ids
        )

    identity_raw = (
        json.dumps(
            {
                "profile": PAPER_PROFILE,
                "qwen_agent_revision": OFFICIAL_QWEN_AGENT_REVISION,
                "dataset_revision": OFFICIAL_DATASET_REVISION,
                "cases": identity_rows,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return DeepPlanningRuntimePreflight(
        profile=PAPER_PROFILE,
        qwen_agent_revision=OFFICIAL_QWEN_AGENT_REVISION,
        dataset_revision=OFFICIAL_DATASET_REVISION,
        bridge_module=manifest.bridge_module,
        bridge_protocol_version=BRIDGE_PROTOCOL_VERSION,
        case_count=sum(slice_counts.values()),
        slice_case_counts=slice_counts,
        git_revision_verified=revision is not None,
        identity_sha256=sha256(identity_raw).hexdigest(),
    )


def _read_git_revision(root: Path) -> str | None:
    git_marker = root / ".git"
    if not git_marker.exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeepPlanningRuntimeError(
            "could not verify the DeepPlanning Qwen-Agent checkout"
        ) from exc
    revision = completed.stdout.strip()
    if (
        completed.returncode != 0
        or len(revision) != 40
        or any(character not in "0123456789abcdef" for character in revision)
    ):
        raise DeepPlanningRuntimeError(
            "could not verify the DeepPlanning Qwen-Agent checkout"
        )
    return revision


def _query_ids(path: Path, slice_id: str) -> tuple[int, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeepPlanningRuntimeError(
            f"invalid DeepPlanning {slice_id} query file"
        ) from exc
    if not isinstance(payload, list):
        raise DeepPlanningRuntimeError(
            f"DeepPlanning {slice_id} query file must be an array"
        )
    case_ids: list[int] = []
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise DeepPlanningRuntimeError(
                f"DeepPlanning {slice_id} query row {index} "
                "must be an object"
            )
        raw_case_id = row.get("id")
        if (
            isinstance(raw_case_id, str)
            and raw_case_id.isdecimal()
            and str(int(raw_case_id)) == raw_case_id
        ):
            case_id = int(raw_case_id)
        elif (
            not isinstance(raw_case_id, bool)
            and isinstance(raw_case_id, int)
        ):
            case_id = raw_case_id
        else:
            raise DeepPlanningRuntimeError(
                f"DeepPlanning {slice_id} query row {index} "
                "has an invalid id"
            )
        case_ids.append(case_id)
    if len(set(case_ids)) != len(case_ids):
        raise DeepPlanningRuntimeError(
            f"DeepPlanning {slice_id} query IDs must be unique"
        )
    return tuple(sorted(case_ids))


def _expected_ids(slice_id: str) -> tuple[int, ...]:
    spec = SLICE_SPECS[slice_id]
    first = int(spec["first_case_id"])
    return tuple(range(first, first + int(spec["case_count"])))


def _validate_case_directories(
    database_root: Path,
    *,
    slice_id: str,
    case_ids: Sequence[int],
    runtime_root: Path,
) -> None:
    prefix = "id_" if slice_id == "travel_en" else "case_"
    for case_id in case_ids:
        candidate = database_root / f"{prefix}{case_id}"
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise DeepPlanningRuntimeError(
                f"DeepPlanning {slice_id} is missing database case "
                f"{case_id}"
            ) from exc
        if not resolved.is_dir():
            raise DeepPlanningRuntimeError(
                f"DeepPlanning {slice_id} database case {case_id} "
                "must be a directory"
            )
        try:
            resolved.relative_to(runtime_root)
        except ValueError:
            raise DeepPlanningRuntimeError(
                f"DeepPlanning {slice_id} database case {case_id} "
                "escapes the runtime root"
            ) from None


def _contained_path(
    root: Path,
    relative: PurePosixPath,
    *,
    expected: str,
) -> Path:
    candidate = root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DeepPlanningRuntimeError(
            f"DeepPlanning required {expected} is missing: {relative}"
        ) from exc
    try:
        resolved.relative_to(root)
    except ValueError:
        raise DeepPlanningRuntimeError(
            f"DeepPlanning path escapes the runtime root: {relative}"
        ) from None
    if expected == "file" and not resolved.is_file():
        raise DeepPlanningRuntimeError(
            f"DeepPlanning required path is not a file: {relative}"
        )
    if expected == "directory" and not resolved.is_dir():
        raise DeepPlanningRuntimeError(
            f"DeepPlanning required path is not a directory: {relative}"
        )
    return resolved


def _existing_directory(path: Path, field_name: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise DeepPlanningRuntimeError(
            f"DeepPlanning {field_name} does not exist"
        ) from exc
    if not resolved.is_dir():
        raise DeepPlanningRuntimeError(
            f"DeepPlanning {field_name} must be a directory"
        )
    return resolved


def _portable_path(value: Any, field_name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        raise DeepPlanningRuntimeError(
            f"DeepPlanning {field_name} must be a portable relative path"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise DeepPlanningRuntimeError(
            f"DeepPlanning {field_name} must be a portable relative path"
        )
    return path


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeepPlanningRuntimeError(
            "invalid DeepPlanning runtime manifest"
        ) from exc
    if not isinstance(payload, dict):
        raise DeepPlanningRuntimeError(
            "DeepPlanning runtime manifest must be an object"
        )
    return payload


def _exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    label: str,
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        raise DeepPlanningRuntimeError(
            f"DeepPlanning {label} keys are invalid; "
            f"missing={missing!r}, unknown={unknown!r}"
        )


__all__ = [
    "BRIDGE_PROTOCOL_VERSION",
    "DeepPlanningRuntimeError",
    "DeepPlanningRuntimeManifest",
    "DeepPlanningRuntimePreflight",
    "OFFICIAL_DATASET_REVISION",
    "OFFICIAL_QWEN_AGENT_REVISION",
    "PAPER_PROFILE",
    "RUNTIME_MANIFEST_FILENAME",
    "SliceRuntime",
    "load_runtime_manifest",
    "preflight_runtime",
]
