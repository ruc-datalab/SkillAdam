"""DeepPlanning synthetic-fixture and parity-manifest loading."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path, PurePosixPath
import random
from typing import Any

from skilladam.benchmarks.base import (
    BenchmarkManifest,
    DatasetUnavailableError,
)
from skilladam.types import BenchmarkCase, PUBLIC_SPLITS, Split


MANIFEST = BenchmarkManifest(
    name="deepplanning",
    display_name="DeepPlanning",
    description="Long-horizon travel and shopping planning tasks.",
    splits=frozenset(PUBLIC_SPLITS),
)

SLICE_SPECS: dict[str, dict[str, Any]] = {
    "shopping_level1": {
        "domain": "shopping",
        "level": 1,
        "language": None,
        "first_case_id": 1,
        "case_count": 50,
    },
    "shopping_level2": {
        "domain": "shopping",
        "level": 2,
        "language": None,
        "first_case_id": 1,
        "case_count": 50,
    },
    "shopping_level3": {
        "domain": "shopping",
        "level": 3,
        "language": None,
        "first_case_id": 1,
        "case_count": 20,
    },
    "travel_en": {
        "domain": "travel",
        "level": None,
        "language": "en",
        "first_case_id": 0,
        "case_count": 120,
    },
}

_PUBLIC_FIXTURE_NAME = MANIFEST.fixture_filename
_PARITY_FILENAMES = (
    "deepplanning_manifest.json",
    "deepplanning_shopping_travel_en_full_manifest.json",
)
_RUNTIME_MANIFEST_FILENAME = "deepplanning_runtime_manifest.json"
_PATH_FIELDS = (
    "baseline_trace_path",
    "baseline_eval_path",
    "baseline_result_path",
)
_TRAIN_RATIO = 2 / 3
_SPLIT_SEED = 42


def load_cases(data_root: Path, split: Split) -> tuple[BenchmarkCase, ...]:
    """Load public fixtures or the frozen four-slice identifier manifest."""

    if split not in PUBLIC_SPLITS:
        raise ValueError(
            f"unsupported DeepPlanning split {split!r}; "
            f"expected one of {PUBLIC_SPLITS!r}"
        )
    root = Path(data_root)
    if root.is_file():
        payload = _read_json_object(root)
        if _is_public_fixture(payload):
            return _load_public_fixture(payload, split)
        return _load_parity_manifest(payload, split)

    runtime_manifest_path = root / _RUNTIME_MANIFEST_FILENAME
    if runtime_manifest_path.is_file():
        from skilladam.benchmarks.deepplanning.runtime import (
            preflight_runtime,
        )

        preflight_runtime(root)
        return canonical_profile_cases(split)

    fixture_path = root / _PUBLIC_FIXTURE_NAME
    if fixture_path.is_file():
        payload = _read_json_object(fixture_path)
        if _is_public_fixture(payload):
            return _load_public_fixture(payload, split)
    for filename in _PARITY_FILENAMES:
        path = root / filename
        if path.is_file():
            return _load_parity_manifest(_read_json_object(path), split)
    raise DatasetUnavailableError(MANIFEST.name, root)


def canonical_profile_cases(
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    """Build the frozen paper-profile split without copying raw data."""

    rows: list[dict[str, Any]] = []
    for slice_id, spec in SLICE_SPECS.items():
        first = int(spec["first_case_id"])
        for case_id in range(first, first + int(spec["case_count"])):
            rows.append(
                {
                    "case_id": case_id,
                    "slice": slice_id,
                    "domain": spec["domain"],
                    "level": spec["level"],
                    "language": spec["language"],
                    "split": "train" if case_id % 2 else "test",
                }
            )
    return _load_parity_manifest(
        {"cases": rows},
        split,
        source_layout="deepplanning-pinned-runtime",
    )


def _is_public_fixture(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("schema_version") == 1
        and payload.get("benchmark") == MANIFEST.name
    )


def _load_public_fixture(
    payload: Mapping[str, Any],
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError(
            "DeepPlanning public fixture cases must be a JSON array"
        )

    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(
                f"DeepPlanning case at index {index} must be an object"
            )
        case_id = _non_empty_text(
            raw_case.get("case_id"),
            f"cases[{index}].case_id",
        )
        if case_id in seen:
            raise ValueError(
                f"duplicate DeepPlanning fixture case_id {case_id!r}"
            )
        seen.add(case_id)
        case_split = raw_case.get("split")
        if case_split not in PUBLIC_SPLITS:
            raise ValueError(
                f"DeepPlanning case {case_id!r} has invalid split"
            )
        raw_payload = raw_case.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise ValueError(
                f"DeepPlanning case {case_id!r} payload must be an object"
            )
        domain = _fixture_domain(raw_payload, case_id)
        slice_id = _fixture_slice(raw_payload, domain)
        if case_split != split:
            continue
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                payload=dict(raw_payload),
                reference=raw_case.get("reference"),
                metadata={
                    "split": split,
                    "source_layout": "public-fixture",
                    "slice": slice_id,
                    "domain": domain,
                },
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _fixture_domain(
    payload: Mapping[str, Any],
    case_id: str,
) -> str:
    domain = payload.get("domain")
    if domain not in {"shopping", "travel"}:
        raise ValueError(
            f"DeepPlanning fixture case {case_id!r} has invalid domain"
        )
    return str(domain)


def _fixture_slice(
    payload: Mapping[str, Any],
    domain: str,
) -> str:
    value = payload.get("slice")
    if value is not None:
        if not isinstance(value, str) or value not in SLICE_SPECS:
            raise ValueError("DeepPlanning fixture has invalid slice")
        slice_id = value
    elif domain == "travel":
        slice_id = "travel_en"
    else:
        level = payload.get("level", 1)
        if level not in {1, 2, 3}:
            raise ValueError(
                "DeepPlanning shopping fixture has invalid level"
            )
        slice_id = f"shopping_level{level}"

    spec = SLICE_SPECS[slice_id]
    if domain != spec["domain"]:
        raise ValueError(
            "DeepPlanning fixture slice has invalid domain"
        )
    if "level" in payload and payload["level"] != spec["level"]:
        raise ValueError(
            "DeepPlanning fixture slice has invalid level"
        )
    if "language" in payload and payload["language"] != spec["language"]:
        raise ValueError(
            "DeepPlanning fixture slice has invalid language"
        )
    return slice_id


def _load_parity_manifest(
    payload: Mapping[str, Any],
    split: Split,
    *,
    source_layout: str = "deepplanning-parity-manifest",
) -> tuple[BenchmarkCase, ...]:
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError(
            "DeepPlanning parity manifest cases must be a JSON array"
        )
    normalized = _normalize_parity_rows(raw_cases)
    _validate_complete_parity_manifest(normalized)

    selected: list[BenchmarkCase] = []
    for slice_id in sorted(SLICE_SPECS):
        rows = sorted(
            (
                row
                for row in normalized
                if row["slice"] == slice_id
            ),
            key=lambda row: row["case_id"],
        )
        original_train = [
            row for row in rows if row["case_id"] % 2 == 1
        ]
        test_rows = [
            row for row in rows if row["case_id"] % 2 == 0
        ]
        train_count = int(len(original_train) * _TRAIN_RATIO)
        train_ids = {
            row["case_id"]
            for row in random.Random(_SPLIT_SEED).sample(
                original_train,
                train_count,
            )
        }
        if split == "train":
            split_rows = [
                row
                for row in original_train
                if row["case_id"] in train_ids
            ]
        elif split == "validation":
            split_rows = [
                row
                for row in original_train
                if row["case_id"] not in train_ids
            ]
        else:
            split_rows = test_rows
        selected.extend(
            _benchmark_case(
                row,
                split,
                source_layout=source_layout,
            )
            for row in split_rows
        )
    return tuple(sorted(selected, key=lambda case: case.case_id))


def _normalize_parity_rows(
    raw_cases: list[Any],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(
                f"DeepPlanning parity case at index {index} must be an object"
            )
        slice_id = _non_empty_text(
            raw_case.get("slice"),
            f"cases[{index}].slice",
        )
        if slice_id not in SLICE_SPECS:
            raise ValueError(
                f"unsupported DeepPlanning slice {slice_id!r}"
            )
        case_id = raw_case.get("case_id")
        if (
            isinstance(case_id, bool)
            or not isinstance(case_id, int)
            or case_id < int(SLICE_SPECS[slice_id]["first_case_id"])
        ):
            raise ValueError(
                f"DeepPlanning {slice_id} case_id is outside its valid range"
            )
        key = (slice_id, case_id)
        if key in seen:
            raise ValueError(
                f"duplicate DeepPlanning case {slice_id}/{case_id}"
            )
        seen.add(key)

        spec = SLICE_SPECS[slice_id]
        if raw_case.get("domain") != spec["domain"]:
            raise ValueError(
                f"DeepPlanning {slice_id}/{case_id} has invalid domain"
            )
        if raw_case.get("level") != spec["level"]:
            raise ValueError(
                f"DeepPlanning {slice_id}/{case_id} has invalid level"
            )
        if raw_case.get("language") != spec["language"]:
            raise ValueError(
                f"DeepPlanning {slice_id}/{case_id} has invalid language"
            )
        expected_split = "train" if case_id % 2 else "test"
        if raw_case.get("split") != expected_split:
            raise ValueError(
                f"DeepPlanning {slice_id}/{case_id} has invalid split"
            )
        for field_name in _PATH_FIELDS:
            if field_name in raw_case:
                _validate_portable_path(
                    raw_case[field_name],
                    f"cases[{index}].{field_name}",
                )
        normalized.append(
            {
                "case_id": case_id,
                "slice": slice_id,
                "domain": spec["domain"],
                "level": spec["level"],
                "language": spec["language"],
            }
        )
    return normalized


def _validate_complete_parity_manifest(
    rows: list[dict[str, Any]],
) -> None:
    present = {row["slice"] for row in rows}
    missing = sorted(set(SLICE_SPECS) - present)
    if missing:
        raise ValueError(
            "DeepPlanning parity manifest is missing required slices: "
            + ", ".join(missing)
        )
    for slice_id, spec in SLICE_SPECS.items():
        ids = {
            row["case_id"]
            for row in rows
            if row["slice"] == slice_id
        }
        first_case_id = int(spec["first_case_id"])
        expected = set(
            range(
                first_case_id,
                first_case_id + int(spec["case_count"]),
            )
        )
        if ids != expected:
            raise ValueError(
                f"DeepPlanning {slice_id} case IDs/count do not match "
                "the frozen parity manifest"
            )


def _benchmark_case(
    row: Mapping[str, Any],
    split: Split,
    *,
    source_layout: str = "deepplanning-parity-manifest",
) -> BenchmarkCase:
    slice_id = str(row["slice"])
    case_number = int(row["case_id"])
    case_id = f"{slice_id}__case_{case_number:03d}"
    return BenchmarkCase(
        case_id=case_id,
        payload={
            "slice": slice_id,
            "domain": row["domain"],
            "case_number": case_number,
            "level": row["level"],
            "language": row["language"],
        },
        metadata={
            "split": split,
            "slice": slice_id,
            "domain": row["domain"],
            "source_layout": source_layout,
        },
    )


def _validate_portable_path(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"DeepPlanning {field_name} must be a non-empty relative path"
        )
    if "\\" in value:
        raise ValueError(
            f"DeepPlanning {field_name} must use a portable relative path"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(
            f"DeepPlanning {field_name} must not escape its source root"
        )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"invalid DeepPlanning JSON file {path.name}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("DeepPlanning JSON root must be an object")
    return payload


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"DeepPlanning {field_name} must be non-empty text"
        )
    return value.strip()
