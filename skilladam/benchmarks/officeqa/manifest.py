"""Offline OfficeQA fixture and historical CSV manifest loading."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
import json
from pathlib import Path
import random
from typing import Any

from skilladam.benchmarks.base import (
    BenchmarkManifest,
    DatasetUnavailableError,
)
from skilladam.types import BenchmarkCase, PUBLIC_SPLITS, Split


MANIFEST = BenchmarkManifest(
    name="officeqa",
    display_name="OfficeQA",
    description="Question answering over U.S. Treasury Bulletin documents.",
    splits=frozenset(PUBLIC_SPLITS),
)

_PUBLIC_FIXTURE_NAME = MANIFEST.fixture_filename
_HISTORICAL_SPLITS = {
    "train": "train",
    "validation": "val",
    "test": "test",
}


def load_cases(data_root: Path, split: Split) -> tuple[BenchmarkCase, ...]:
    """Load a public synthetic fixture or a materialized OfficeQA CSV."""

    if split not in PUBLIC_SPLITS:
        raise ValueError(
            f"unsupported OfficeQA split {split!r}; "
            f"expected one of {PUBLIC_SPLITS!r}"
        )
    root = Path(data_root)
    fixture_path = root if root.is_file() else root / _PUBLIC_FIXTURE_NAME
    if fixture_path.is_file():
        return _load_public_fixture(fixture_path, split)
    csv_path = _resolve_csv(root)
    if csv_path is not None:
        return _load_historical(root, csv_path, split)
    raise DatasetUnavailableError(MANIFEST.name, root)


def _load_public_fixture(
    path: Path,
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    fixture = _read_json_object(path, "public fixture")
    if fixture.get("schema_version") != 1:
        raise ValueError("OfficeQA public fixture schema_version must equal 1")
    if fixture.get("benchmark") != MANIFEST.name:
        raise ValueError(
            "OfficeQA public fixture benchmark must be officeqa"
        )
    raw_cases = fixture.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("OfficeQA public fixture cases must be a JSON array")

    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(
                f"OfficeQA case at index {index} must be a JSON object"
            )
        case_split = raw_case.get("split")
        if case_split not in PUBLIC_SPLITS:
            raise ValueError(
                f"OfficeQA case has unsupported split {case_split!r}"
            )
        if case_split != split:
            continue
        case_id = _non_empty_text(raw_case.get("case_id"), "case_id")
        if case_id in seen:
            raise ValueError(f"duplicate OfficeQA case_id {case_id!r}")
        seen.add(case_id)
        raw_payload = raw_case.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise ValueError(
                f"OfficeQA case {case_id!r} payload must be an object"
            )
        payload = dict(raw_payload)
        _validate_payload(case_id, payload)
        reference = _reference_answers(
            raw_case.get("reference"),
            f"case {case_id!r} reference",
        )
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                payload=payload,
                reference=list(reference),
                metadata={
                    "split": split,
                    "source_layout": "public-fixture",
                },
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _load_historical(
    root: Path,
    csv_path: Path,
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    items = _read_csv(csv_path)
    manifest = _load_split_manifest(root)
    splits = _load_or_generate_splits(root, tuple(items), manifest)
    historical_split = _HISTORICAL_SPLITS[split]

    cases: list[BenchmarkCase] = []
    for case_id in splits[historical_split]:
        try:
            item = items[case_id]
        except KeyError as exc:
            raise ValueError(
                f"OfficeQA split id {case_id!r} is absent from source CSV"
            ) from exc
        metadata: dict[str, Any] = {
            "split": split,
            "historical_split": historical_split,
            "source_layout": "officeqa-full-csv",
        }
        for key in ("source_repo", "source_revision"):
            value = manifest.get(key)
            if isinstance(value, str) and value.strip():
                metadata[key] = value.strip()
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                payload={
                    "question": item["question"],
                    "source_files": list(item["source_files"]),
                    "source_docs": list(item["source_docs"]),
                    "category": item["category"],
                    "task_type": "officeqa",
                },
                reference=[item["answer"]],
                metadata=metadata,
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _resolve_csv(root: Path) -> Path | None:
    for candidate in (
        root / "raw/officeqa_full.csv",
        root / "officeqa_full.csv",
    ):
        if candidate.is_file():
            return candidate
    return None


def _read_csv(path: Path) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    try:
        handle = path.open(encoding="utf-8", newline="")
    except OSError as exc:
        raise ValueError(f"could not read OfficeQA CSV at {path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"OfficeQA CSV at {path} has no header")
        for row_index, row in enumerate(reader, start=2):
            case_id = _first_non_empty(
                row.get("uid"),
                row.get("id"),
                row.get("question_id"),
            )
            if case_id is None:
                raise ValueError(
                    f"OfficeQA CSV row {row_index} has no id"
                )
            if case_id in items:
                raise ValueError(
                    f"duplicate OfficeQA CSV id {case_id!r}"
                )
            question = _non_empty_text(
                row.get("question"),
                f"CSV row {row_index} question",
            )
            answer = _first_non_empty(
                row.get("answer"),
                row.get("ground_truth"),
            )
            if answer is None:
                raise ValueError(
                    f"OfficeQA CSV row {row_index} answer must be non-empty"
                )
            items[case_id] = {
                "question": question,
                "answer": answer,
                "source_files": _parse_list_field(
                    row.get("source_files", "")
                ),
                "source_docs": _parse_list_field(
                    row.get("source_docs", "")
                ),
                "category": (
                    row.get("difficulty")
                    or row.get("category")
                    or ""
                ).strip(),
            }
    return items


def _parse_list_field(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, str):
        raise ValueError("OfficeQA list-valued CSV field must be text")
    text = value.strip()
    if not text:
        return ()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return tuple(
            str(item).strip()
            for item in parsed
            if str(item).strip()
        )
    if "\n" in text:
        return tuple(
            line.strip()
            for line in text.splitlines()
            if line.strip()
        )
    return (text,)


def _load_split_manifest(root: Path) -> dict[str, Any]:
    path = root / "split_manifest.json"
    if not path.exists():
        return {}
    return _read_json_object(path, "split manifest")


def _load_or_generate_splits(
    root: Path,
    item_ids: Sequence[str],
    manifest: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    paths = {
        split: root / split / "items.json"
        for split in ("train", "val", "test")
    }
    present = {split for split, path in paths.items() if path.is_file()}
    if present and len(present) != len(paths):
        missing = sorted(set(paths) - present)
        raise ValueError(
            "OfficeQA split files are incomplete; missing "
            + ", ".join(f"{split}/items.json" for split in missing)
        )
    if present:
        splits = {
            split: _load_split_ids(path, split)
            for split, path in paths.items()
        }
        _reject_split_overlap(splits)
        return splits

    ratio = _parse_ratio(manifest.get("split_ratio", "5:1:4"))
    seed = manifest.get("split_seed", 42)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("OfficeQA split_seed must be an integer")
    return _generate_splits(item_ids, ratio=ratio, seed=seed)


def _load_split_ids(path: Path, split: str) -> tuple[str, ...]:
    raw_items = _read_json(path, f"{split}/items.json")
    if not isinstance(raw_items, list):
        raise ValueError(
            f"OfficeQA {split}/items.json must contain a JSON array"
        )
    ids: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_items):
        raw_id = item.get("id") if isinstance(item, Mapping) else item
        case_id = _non_empty_text(raw_id, f"{split}[{index}].id")
        if case_id in seen:
            raise ValueError(
                f"duplicate OfficeQA {split} split id {case_id!r}"
            )
        seen.add(case_id)
        ids.append(case_id)
    return tuple(sorted(ids))


def _reject_split_overlap(
    splits: Mapping[str, Sequence[str]],
) -> None:
    owner_by_id: dict[str, str] = {}
    for split, case_ids in splits.items():
        for case_id in case_ids:
            previous = owner_by_id.setdefault(case_id, split)
            if previous != split:
                raise ValueError(
                    f"OfficeQA split overlap: id {case_id!r} appears in "
                    f"multiple splits ({previous!r}, {split!r})"
                )


def _parse_ratio(value: Any) -> tuple[int, int, int]:
    if isinstance(value, str):
        raw_parts = value.split(":")
    elif (
        not isinstance(value, (str, bytes))
        and isinstance(value, Sequence)
    ):
        raw_parts = list(value)
    else:
        raise ValueError("OfficeQA split_ratio must use train:val:test")
    if len(raw_parts) != 3:
        raise ValueError("OfficeQA split_ratio must have three parts")
    try:
        ratio = tuple(int(part) for part in raw_parts)
    except (TypeError, ValueError) as exc:
        raise ValueError("OfficeQA split_ratio must contain integers") from exc
    if any(part < 0 for part in ratio) or sum(ratio) <= 0:
        raise ValueError("OfficeQA split_ratio parts must be non-negative")
    return ratio  # type: ignore[return-value]


def _generate_splits(
    item_ids: Sequence[str],
    *,
    ratio: tuple[int, int, int],
    seed: int,
) -> dict[str, tuple[str, ...]]:
    shuffled = sorted(item_ids)
    random.Random(seed).shuffle(shuffled)
    total_parts = sum(ratio)
    train_count = round(len(shuffled) * ratio[0] / total_parts)
    val_count = round(len(shuffled) * ratio[1] / total_parts)
    return {
        "train": tuple(sorted(shuffled[:train_count])),
        "val": tuple(
            sorted(shuffled[train_count : train_count + val_count])
        ),
        "test": tuple(sorted(shuffled[train_count + val_count :])),
    }


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise DatasetUnavailableError(MANIFEST.name, path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid OfficeQA {label} at {path}: {exc}") from exc


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = _read_json(path, label)
    if not isinstance(payload, dict):
        raise ValueError(f"OfficeQA {label} must contain a JSON object")
    return payload


def _validate_payload(case_id: str, payload: Mapping[str, Any]) -> None:
    _non_empty_text(payload.get("question"), f"{case_id}.question")
    document_text = payload.get("document_text")
    source_files = payload.get("source_files")
    if document_text is None and source_files is None:
        raise ValueError(
            f"OfficeQA case {case_id!r} must contain document_text "
            "or source_files"
        )
    if document_text is not None:
        _non_empty_text(document_text, f"{case_id}.document_text")
    if source_files is not None:
        _text_sequence(source_files, f"{case_id}.source_files")
    if payload.get("source_docs") is not None:
        _text_sequence(payload["source_docs"], f"{case_id}.source_docs")


def _reference_answers(value: Any, field_name: str) -> tuple[str, ...]:
    return _text_sequence(value, field_name)


def _text_sequence(value: Any, field_name: str) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(
            f"OfficeQA {field_name} must be a non-empty string sequence"
        )
    return tuple(item.strip() for item in value)


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"OfficeQA {field_name} must be non-empty text")
    return value.strip()
