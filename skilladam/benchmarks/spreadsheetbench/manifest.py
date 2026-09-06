"""Offline SpreadsheetBench fixture and Verified-400 manifest loading."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from skilladam.benchmarks.base import (
    BenchmarkManifest,
    DatasetUnavailableError,
)
from skilladam.types import BenchmarkCase, PUBLIC_SPLITS, Split


MANIFEST = BenchmarkManifest(
    name="spreadsheetbench",
    display_name="SpreadsheetBench",
    description="Spreadsheet manipulation and reasoning tasks.",
    splits=frozenset(PUBLIC_SPLITS),
    requirements=("openpyxl>=3.1", "pandas"),
)

_PUBLIC_FIXTURE_NAME = MANIFEST.fixture_filename
_HISTORICAL_SPLITS = {
    "train": "train",
    "validation": "val",
    "test": "test",
}


def load_cases(data_root: Path, split: Split) -> tuple[BenchmarkCase, ...]:
    """Load a deterministic public fixture or materialized Verified-400."""

    if split not in PUBLIC_SPLITS:
        raise ValueError(
            f"unsupported SpreadsheetBench split {split!r}; "
            f"expected one of {PUBLIC_SPLITS!r}"
        )
    root = Path(data_root)
    fixture_path = (
        root if root.is_file() else root / _PUBLIC_FIXTURE_NAME
    )
    if fixture_path.is_file():
        return _load_public_fixture(fixture_path, split)
    if (root / "data/dataset.json").is_file():
        return _load_verified_400(root, split)
    raise DatasetUnavailableError(MANIFEST.name, root)


def resolve_xlsx_pair(
    task_dir: Path,
    case_id: str,
) -> tuple[Path, Path]:
    """Resolve the historical filename variants without opening a workbook."""

    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        raise FileNotFoundError(
            f"could not locate init/golden xlsx pair under {task_dir}"
        )
    canonical = (
        task_dir / f"1_{case_id}_init.xlsx",
        task_dir / f"1_{case_id}_golden.xlsx",
    )
    if all(path.is_file() for path in canonical):
        return canonical
    bare = (
        task_dir / "initial.xlsx",
        task_dir / "golden.xlsx",
    )
    if all(path.is_file() for path in bare):
        return bare
    init_hits = sorted(task_dir.glob("*_init.xlsx"))
    golden_hits = sorted(task_dir.glob("*_golden.xlsx"))
    if init_hits and golden_hits:
        return init_hits[0], golden_hits[0]
    raise FileNotFoundError(
        f"could not locate init/golden xlsx pair under {task_dir}; "
        f"contents: {sorted(path.name for path in task_dir.iterdir())}"
    )


def _load_public_fixture(
    path: Path,
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    payload = _read_json_object(path, "public fixture")
    if payload.get("schema_version") != 1:
        raise ValueError(
            "SpreadsheetBench public fixture schema_version must equal 1"
        )
    if payload.get("benchmark") != MANIFEST.name:
        raise ValueError(
            "SpreadsheetBench public fixture benchmark must be "
            "spreadsheetbench"
        )
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError(
            "SpreadsheetBench public fixture cases must be a JSON array"
        )
    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(
                "SpreadsheetBench case at index "
                f"{index} must be a JSON object"
            )
        case_split = raw_case.get("split")
        if case_split not in PUBLIC_SPLITS:
            raise ValueError(
                f"SpreadsheetBench case has unsupported split {case_split!r}"
            )
        if case_split != split:
            continue
        case_id = _non_empty_text(raw_case.get("case_id"), "case_id")
        if case_id in seen:
            raise ValueError(
                f"duplicate SpreadsheetBench case_id {case_id!r}"
            )
        seen.add(case_id)
        raw_payload = raw_case.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise ValueError(
                f"SpreadsheetBench case {case_id!r} payload must be an object"
            )
        normalized_payload = dict(raw_payload)
        _validate_payload(case_id, normalized_payload)
        reference = raw_case.get("reference")
        if not isinstance(reference, Mapping) or not reference:
            raise ValueError(
                f"SpreadsheetBench case {case_id!r} reference "
                "must be a non-empty object"
            )
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                payload=normalized_payload,
                reference=dict(reference),
                metadata={"split": split, "source_layout": "public-fixture"},
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _load_verified_400(
    root: Path,
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    dataset_path = root / "data/dataset.json"
    raw_dataset = _read_json(dataset_path, "dataset.json")
    if not isinstance(raw_dataset, list):
        raise ValueError(
            "SpreadsheetBench dataset.json must contain a JSON array"
        )
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(raw_dataset):
        if not isinstance(item, Mapping):
            raise ValueError(
                f"SpreadsheetBench dataset item {index} must be an object"
            )
        case_id = _case_id(item.get("id"), f"dataset[{index}].id")
        if case_id in by_id:
            raise ValueError(
                f"duplicate SpreadsheetBench dataset id {case_id!r}"
            )
        by_id[case_id] = item

    historical_split = _HISTORICAL_SPLITS[split]
    split_path = root / historical_split / "items.json"
    raw_split = _read_json(split_path, f"{historical_split}/items.json")
    if not isinstance(raw_split, list):
        raise ValueError(
            f"SpreadsheetBench {historical_split}/items.json "
            "must contain a JSON array"
        )

    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for index, split_item in enumerate(raw_split):
        if not isinstance(split_item, Mapping):
            raise ValueError(
                f"SpreadsheetBench split item {index} must be an object"
            )
        case_id = _case_id(
            split_item.get("id"),
            f"{historical_split}[{index}].id",
        )
        if case_id in seen:
            raise ValueError(
                f"duplicate SpreadsheetBench split id {case_id!r}"
            )
        seen.add(case_id)
        try:
            item = by_id[case_id]
        except KeyError as exc:
            raise ValueError(
                f"SpreadsheetBench split {historical_split!r} references "
                f"id {case_id!r} absent from dataset.json"
            ) from exc
        task_dir = root / "data/spreadsheet" / case_id
        input_path, golden_path = resolve_xlsx_pair(task_dir, case_id)
        payload = {
            "instruction": item.get("instruction"),
            "instruction_type": item.get("instruction_type", ""),
            "answer_position": item.get("answer_position", ""),
            "answer_sheet": item.get("answer_sheet", ""),
            "data_position": item.get("data_position", ""),
            "input_path": str(input_path),
        }
        _validate_payload(case_id, payload)
        reference = {
            "golden_path": str(golden_path),
            "answer_position": payload["answer_position"],
            "answer_sheet": payload["answer_sheet"],
        }
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                payload=payload,
                reference=reference,
                metadata={
                    "split": split,
                    "historical_split": historical_split,
                    "source_layout": "verified-400",
                },
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise DatasetUnavailableError(MANIFEST.name, path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"invalid SpreadsheetBench {label} at {path}: {exc}"
        ) from exc


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = _read_json(path, label)
    if not isinstance(payload, dict):
        raise ValueError(
            f"SpreadsheetBench {label} must contain a JSON object"
        )
    return payload


def _validate_payload(case_id: str, payload: Mapping[str, Any]) -> None:
    _non_empty_text(
        payload.get("instruction"),
        f"{case_id}.instruction",
    )
    workbook = payload.get("workbook")
    input_path = payload.get("input_path")
    if workbook is None and input_path is None:
        raise ValueError(
            f"SpreadsheetBench case {case_id!r} must provide "
            "workbook or input_path"
        )
    if workbook is not None and not isinstance(workbook, Mapping):
        raise ValueError(
            f"SpreadsheetBench case {case_id!r} workbook must be an object"
        )
    if input_path is not None:
        _non_empty_text(input_path, f"{case_id}.input_path")


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"SpreadsheetBench {field_name} must be non-empty text"
        )
    return value


def _case_id(value: Any, field_name: str) -> str:
    if isinstance(value, bool):
        raise ValueError(
            f"SpreadsheetBench {field_name} must be text or an integer"
        )
    if isinstance(value, int):
        return str(value)
    return _non_empty_text(value, field_name)
