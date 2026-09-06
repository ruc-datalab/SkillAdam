"""Offline ALFWorld fixture and path-manifest loading."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path, PurePosixPath
from typing import Any

from skilladam.benchmarks.base import (
    BenchmarkManifest,
    DatasetUnavailableError,
)
from skilladam.types import BenchmarkCase, PUBLIC_SPLITS, Split


MANIFEST = BenchmarkManifest(
    name="alfworld",
    display_name="ALFWorld",
    description="Text-based embodied household tasks.",
    splits=frozenset(PUBLIC_SPLITS),
    requirements=("alfworld==0.4.2",),
)

_PUBLIC_FIXTURE_NAME = MANIFEST.fixture_filename
_HISTORICAL_SPLITS = {
    "train": "train",
    "validation": "val",
    "test": "test",
}


def load_cases(data_root: Path, split: Split) -> tuple[BenchmarkCase, ...]:
    """Load a public fixture or local ALFWorld path split."""

    if split not in PUBLIC_SPLITS:
        raise ValueError(
            f"unsupported ALFWorld split {split!r}; expected one of "
            f"{PUBLIC_SPLITS!r}"
        )
    root = Path(data_root)
    fixture_path = root if root.is_file() else root / _PUBLIC_FIXTURE_NAME
    if fixture_path.is_file():
        return _load_public_fixture(fixture_path, split)
    if _has_path_split(root):
        return _load_path_split(root, split)
    raise DatasetUnavailableError(MANIFEST.name, root)


def _load_public_fixture(
    path: Path,
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    fixture = _read_json_object(path, "public fixture")
    if fixture.get("schema_version") != 1:
        raise ValueError(
            "ALFWorld public fixture schema_version must equal 1"
        )
    if fixture.get("benchmark") != MANIFEST.name:
        raise ValueError(
            "ALFWorld public fixture benchmark must be alfworld"
        )
    raw_cases = fixture.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError(
            "ALFWorld public fixture cases must be a JSON array"
        )

    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(
                f"ALFWorld case at index {index} must be a JSON object"
            )
        case_split = raw_case.get("split")
        if case_split not in PUBLIC_SPLITS:
            raise ValueError(
                f"ALFWorld case has unsupported split {case_split!r}"
            )
        if case_split != split:
            continue
        case_id = _non_empty_text(raw_case.get("case_id"), "case_id")
        if case_id in seen:
            raise ValueError(f"duplicate ALFWorld case_id {case_id!r}")
        seen.add(case_id)
        raw_payload = raw_case.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise ValueError(
                f"ALFWorld case {case_id!r} payload must be an object"
            )
        payload = dict(raw_payload)
        _validate_interactive_payload(case_id, payload)
        reference = _non_empty_text(
            raw_case.get("reference"),
            f"{case_id}.reference",
        )
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                payload=payload,
                reference=reference,
                metadata={
                    "split": split,
                    "source_layout": "public-fixture",
                },
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _has_path_split(root: Path) -> bool:
    return (root / "split_manifest.json").is_file() or any(
        (root / split / "items.json").is_file()
        for split in ("train", "val", "test")
    )


def _load_path_split(
    root: Path,
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    paths = {
        name: root / name / "items.json"
        for name in ("train", "val", "test")
    }
    present = {name for name, path in paths.items() if path.is_file()}
    if len(present) != len(paths):
        missing = sorted(set(paths) - present)
        raise ValueError(
            "ALFWorld split files are incomplete; missing "
            + ", ".join(f"{name}/items.json" for name in missing)
        )

    items_by_split = {
        name: _read_split_items(path, name)
        for name, path in paths.items()
    }
    _reject_cross_split_duplicates(items_by_split)
    split_manifest = _load_split_manifest(root)
    _validate_declared_counts(split_manifest, items_by_split)
    historical_split = _HISTORICAL_SPLITS[split]

    cases: list[BenchmarkCase] = []
    for item in items_by_split[historical_split]:
        metadata: dict[str, Any] = {
            "split": split,
            "historical_split": historical_split,
            "source_layout": "alfworld-path-split",
        }
        for key in ("source_repo", "source_method"):
            value = split_manifest.get(key)
            if isinstance(value, str) and value.strip():
                metadata[key] = value.strip()
        cases.append(
            BenchmarkCase(
                case_id=item["id"],
                payload={
                    "gamefile": item["gamefile"],
                    "task_type": item["task_type"],
                    "environment_split": _environment_split(
                        historical_split,
                        item["gamefile"],
                    ),
                },
                reference=None,
                metadata=metadata,
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _read_split_items(
    path: Path,
    split: str,
) -> tuple[dict[str, str], ...]:
    raw_items = _read_json(path, f"{split}/items.json")
    if not isinstance(raw_items, list):
        raise ValueError(
            f"ALFWorld {split}/items.json must contain a JSON array"
        )
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            raise ValueError(
                f"ALFWorld {split}[{index}] must be a JSON object"
            )
        case_id = _non_empty_text(
            raw_item.get("id"),
            f"{split}[{index}].id",
        )
        if case_id in seen:
            raise ValueError(
                f"duplicate ALFWorld {split} split id {case_id!r}"
            )
        seen.add(case_id)
        gamefile = _safe_gamefile(
            raw_item.get("gamefile"),
            f"{split}[{index}].gamefile",
        )
        task_type = _non_empty_text(
            raw_item.get("task_type"),
            f"{split}[{index}].task_type",
        )
        items.append(
            {
                "id": case_id,
                "gamefile": gamefile,
                "task_type": task_type,
            }
        )
    return tuple(sorted(items, key=lambda item: item["id"]))


def _reject_cross_split_duplicates(
    items_by_split: Mapping[str, Sequence[Mapping[str, str]]],
) -> None:
    owner_by_id: dict[str, str] = {}
    for split, items in items_by_split.items():
        for item in items:
            case_id = item["id"]
            previous = owner_by_id.setdefault(case_id, split)
            if previous != split:
                raise ValueError(
                    f"ALFWorld split overlap: id {case_id!r} appears in "
                    f"multiple splits ({previous!r}, {split!r})"
                )


def _load_split_manifest(root: Path) -> dict[str, Any]:
    path = root / "split_manifest.json"
    if not path.exists():
        return {}
    manifest = _read_json_object(path, "split manifest")
    benchmark = manifest.get("benchmark")
    if (
        benchmark is not None
        and (
            not isinstance(benchmark, str)
            or benchmark.casefold() != "alfworld"
        )
    ):
        raise ValueError(
            "ALFWorld split manifest benchmark must be ALFWorld"
        )
    manifest_type = manifest.get("manifest_type")
    if manifest_type not in (None, "path_split"):
        raise ValueError(
            "ALFWorld split manifest type must be path_split"
        )
    return manifest


def _validate_declared_counts(
    manifest: Mapping[str, Any],
    items_by_split: Mapping[str, Sequence[Mapping[str, str]]],
) -> None:
    counts = manifest.get("counts")
    if counts is None:
        return
    if not isinstance(counts, Mapping):
        raise ValueError("ALFWorld split manifest counts must be an object")
    for split in ("train", "val", "test"):
        declared = counts.get(split)
        if (
            isinstance(declared, bool)
            or not isinstance(declared, int)
            or declared < 0
        ):
            raise ValueError(
                f"ALFWorld split manifest count {split!r} "
                "must be a non-negative integer"
            )
        actual = len(items_by_split[split])
        if declared != actual:
            raise ValueError(
                f"ALFWorld split manifest count mismatch for {split}: "
                f"declared {declared}, loaded {actual}"
            )


def _environment_split(split: str, gamefile: str) -> str:
    if "/valid_seen/" in gamefile:
        return "eval_in_distribution"
    if "/valid_unseen/" in gamefile:
        return "eval_out_of_distribution"
    if split == "train":
        return "train"
    return (
        "eval_in_distribution"
        if split == "val"
        else "eval_out_of_distribution"
    )


def _safe_gamefile(value: Any, field_name: str) -> str:
    text = _non_empty_text(value, field_name)
    if "\\" in text:
        raise ValueError(
            f"ALFWorld {field_name} must be a relative POSIX gamefile path"
        )
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(
            f"ALFWorld {field_name} must be relative and cannot escape "
            "ALFWORLD_DATA"
        )
    return path.as_posix()


def _validate_interactive_payload(
    case_id: str,
    payload: Mapping[str, Any],
) -> None:
    _non_empty_text(payload.get("task"), f"{case_id}.task")
    _non_empty_text(
        payload.get("observation"),
        f"{case_id}.observation",
    )
    _text_sequence(
        payload.get("admissible_actions"),
        f"{case_id}.admissible_actions",
    )


def _text_sequence(value: Any, field_name: str) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(
            f"ALFWorld {field_name} must be a non-empty string sequence"
        )
    return tuple(item.strip() for item in value)


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise DatasetUnavailableError(MANIFEST.name, path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"invalid ALFWorld {label} at {path}: {exc}"
        ) from exc


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = _read_json(path, label)
    if not isinstance(payload, dict):
        raise ValueError(
            f"ALFWorld {label} must contain a JSON object"
        )
    return payload


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"ALFWorld {field_name} must be non-empty text"
        )
    return value.strip()
