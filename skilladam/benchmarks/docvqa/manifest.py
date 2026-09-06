"""Offline DocVQA fixture and materialized split loading."""

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
    name="docvqa",
    display_name="DocVQA",
    description="Visual question answering over document images.",
    splits=frozenset(PUBLIC_SPLITS),
)

_PUBLIC_FIXTURE_NAME = MANIFEST.fixture_filename
_HISTORICAL_SPLITS = {
    "train": "train",
    "validation": "val",
    "test": "test",
}


def load_cases(data_root: Path, split: Split) -> tuple[BenchmarkCase, ...]:
    """Load a public text fixture or a validated local image split."""

    if split not in PUBLIC_SPLITS:
        raise ValueError(
            f"unsupported DocVQA split {split!r}; "
            f"expected one of {PUBLIC_SPLITS!r}"
        )
    root = Path(data_root)
    fixture_path = (
        root if root.is_file() else root / _PUBLIC_FIXTURE_NAME
    )
    if fixture_path.is_file():
        return _load_public_fixture(fixture_path, split)
    if _has_materialized_split(root):
        return _load_materialized_split(root, split)
    raise DatasetUnavailableError(MANIFEST.name, root)


def _load_public_fixture(
    path: Path,
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    fixture = _read_json_object(path, "public fixture")
    if fixture.get("schema_version") != 1:
        raise ValueError(
            "DocVQA public fixture schema_version must equal 1"
        )
    if fixture.get("benchmark") != MANIFEST.name:
        raise ValueError(
            "DocVQA public fixture benchmark must be docvqa"
        )
    raw_cases = fixture.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("DocVQA public fixture cases must be a JSON array")

    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(
                f"DocVQA case at index {index} must be a JSON object"
            )
        case_split = raw_case.get("split")
        if case_split not in PUBLIC_SPLITS:
            raise ValueError(
                f"DocVQA case has unsupported split {case_split!r}"
            )
        if case_split != split:
            continue
        case_id = _non_empty_text(raw_case.get("case_id"), "case_id")
        if case_id in seen:
            raise ValueError(f"duplicate DocVQA case_id {case_id!r}")
        seen.add(case_id)
        payload = raw_case.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"DocVQA case {case_id!r} payload must be an object"
            )
        normalized_payload = dict(payload)
        _validate_fixture_payload(case_id, normalized_payload)
        references = _answer_sequence(
            raw_case.get("reference"),
            f"{case_id}.reference",
        )
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                payload=normalized_payload,
                reference=list(references),
                metadata={
                    "split": split,
                    "source_layout": "public-fixture",
                },
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _has_materialized_split(root: Path) -> bool:
    return (root / "split_manifest.json").is_file() or any(
        (root / name / "items.json").is_file()
        for name in ("train", "val", "test")
    )


def _load_materialized_split(
    root: Path,
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    split_paths = {
        name: root / name / "items.json"
        for name in ("train", "val", "test")
    }
    present = {name for name, path in split_paths.items() if path.is_file()}
    if len(present) != len(split_paths):
        missing = sorted(set(split_paths) - present)
        raise ValueError(
            "DocVQA split files are incomplete; missing "
            + ", ".join(f"{name}/items.json" for name in missing)
        )

    items_by_split = {
        name: _read_materialized_items(root, path, name)
        for name, path in split_paths.items()
    }
    _reject_cross_split_duplicates(items_by_split)
    manifest = _read_split_manifest(root)
    _validate_declared_counts(manifest, items_by_split)
    historical_split = _HISTORICAL_SPLITS[split]

    metadata_common: dict[str, Any] = {
        "split": split,
        "historical_split": historical_split,
        "source_layout": "docvqa-materialized",
    }
    for key in (
        "source_repo",
        "source_revision",
        "source_split",
    ):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            metadata_common[key] = value.strip()

    cases = [
        BenchmarkCase(
            case_id=item["id"],
            payload={
                "question": item["question"],
                "document": {
                    "text": None,
                    "image_path": item["image_path"],
                },
                "topic": item["topic"],
                "doc_id": item["doc_id"],
            },
            reference=list(item["answers"]),
            metadata=dict(metadata_common),
        )
        for item in items_by_split[historical_split]
    ]
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _read_materialized_items(
    root: Path,
    path: Path,
    split: str,
) -> tuple[dict[str, Any], ...]:
    payload = _read_json_object(path, f"{split}/items.json")
    ids = payload.get("ids")
    by_id = payload.get("by_id")
    if (
        isinstance(ids, (str, bytes))
        or not isinstance(ids, Sequence)
        or any(not isinstance(case_id, (str, int)) for case_id in ids)
    ):
        raise ValueError(
            f"DocVQA {split}/items.json ids must be an array"
        )
    normalized_ids = [str(case_id) for case_id in ids]
    if len(set(normalized_ids)) != len(normalized_ids):
        raise ValueError(f"duplicate DocVQA {split} split id in ids")
    if not isinstance(by_id, Mapping):
        raise ValueError(
            f"DocVQA {split}/items.json by_id must be an object"
        )
    normalized_keys = {str(case_id) for case_id in by_id}
    if set(normalized_ids) != normalized_keys:
        raise ValueError(
            f"DocVQA {split}/items.json ids and by_id must match"
        )

    items: list[dict[str, Any]] = []
    for index, case_id in enumerate(normalized_ids):
        raw_item = by_id.get(case_id)
        if raw_item is None:
            for raw_key, candidate in by_id.items():
                if str(raw_key) == case_id:
                    raw_item = candidate
                    break
        if not isinstance(raw_item, Mapping):
            raise ValueError(
                f"DocVQA {split} item {case_id!r} must be an object"
            )
        item_id = _non_empty_text(
            raw_item.get("id"),
            f"{split}[{index}].id",
        )
        question_id = _non_empty_text(
            raw_item.get("questionId"),
            f"{split}[{index}].questionId",
        )
        if item_id != case_id or question_id != case_id:
            raise ValueError(
                f"DocVQA {split} id/questionId mismatch for {case_id!r}"
            )
        image_path = _safe_image_path(
            root,
            raw_item.get("image_path"),
            f"{split}[{index}].image_path",
        )
        items.append(
            {
                "id": case_id,
                "doc_id": _non_empty_text(
                    raw_item.get("docId"),
                    f"{split}[{index}].docId",
                ),
                "question": _non_empty_text(
                    raw_item.get("question"),
                    f"{split}[{index}].question",
                ),
                "answers": _answer_sequence(
                    raw_item.get("answers"),
                    f"{split}[{index}].answers",
                ),
                "image_path": image_path,
                "topic": _optional_text(
                    raw_item.get("topic"),
                    f"{split}[{index}].topic",
                ),
            }
        )
    return tuple(sorted(items, key=lambda item: item["id"]))


def _safe_image_path(root: Path, value: Any, field_name: str) -> str:
    raw = _non_empty_text(value, field_name)
    if "\\" in raw:
        raise ValueError(
            f"DocVQA {field_name} must use a safe relative image path"
        )
    relative = PurePosixPath(raw)
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or relative.parts[0] != "images"
    ):
        raise ValueError(
            f"DocVQA {field_name} must be a relative images/ path "
            "without escape components"
        )
    root_resolved = root.resolve()
    image = (root / Path(*relative.parts)).resolve()
    try:
        image.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"DocVQA image path escapes or follows a symlink outside "
            f"the dataset root: {raw!r}"
        ) from exc
    if not image.is_file():
        raise ValueError(f"DocVQA image not found: {image}")
    return relative.as_posix()


def _reject_cross_split_duplicates(
    items_by_split: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    owner_by_id: dict[str, str] = {}
    for split, items in items_by_split.items():
        for item in items:
            case_id = str(item["id"])
            previous = owner_by_id.setdefault(case_id, split)
            if previous != split:
                raise ValueError(
                    f"DocVQA split overlap: id {case_id!r} appears in "
                    f"multiple splits ({previous!r}, {split!r})"
                )


def _read_split_manifest(root: Path) -> dict[str, Any]:
    path = root / "split_manifest.json"
    if not path.is_file():
        raise ValueError(
            "DocVQA materialized dataset is incomplete; missing "
            "split_manifest.json"
        )
    manifest = _read_json_object(path, "split manifest")
    benchmark = manifest.get("benchmark")
    if (
        not isinstance(benchmark, str)
        or benchmark.casefold() != "docvqa"
    ):
        raise ValueError(
            "DocVQA split manifest benchmark must be DocVQA"
        )
    if manifest.get("manifest_type") != "id_split":
        raise ValueError(
            "DocVQA split manifest type must be id_split"
        )
    return manifest


def _validate_declared_counts(
    manifest: Mapping[str, Any],
    items_by_split: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    counts = manifest.get("counts")
    if not isinstance(counts, Mapping):
        raise ValueError(
            "DocVQA split manifest counts must be an object"
        )
    for split, items in items_by_split.items():
        declared = counts.get(split)
        if (
            isinstance(declared, bool)
            or not isinstance(declared, int)
            or declared < 0
        ):
            raise ValueError(
                f"DocVQA declared count for {split} must be non-negative"
            )
        if declared != len(items):
            raise ValueError(
                f"DocVQA count mismatch for {split}: "
                f"declared {declared}, loaded {len(items)}"
            )


def _validate_fixture_payload(
    case_id: str,
    payload: Mapping[str, Any],
) -> None:
    _non_empty_text(payload.get("question"), f"{case_id}.question")
    document = payload.get("document")
    if not isinstance(document, Mapping):
        raise ValueError(
            f"DocVQA {case_id}.document must be an object"
        )
    text = document.get("text")
    image_path = document.get("image_path")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            f"DocVQA fixture {case_id}.document.text must be non-empty"
        )
    if image_path is not None:
        raise ValueError(
            f"DocVQA fixture {case_id} must not include benchmark images"
        )


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise DatasetUnavailableError(MANIFEST.name, path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid DocVQA {label} at {path}: {exc}") from exc


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = _read_json(path, label)
    if not isinstance(payload, dict):
        raise ValueError(
            f"DocVQA {label} must contain a JSON object"
        )
    return payload


def _answer_sequence(value: Any, field_name: str) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or any(
            not isinstance(answer, str) or not answer.strip()
            for answer in value
        )
    ):
        raise ValueError(
            f"DocVQA {field_name} must be a non-empty answers sequence"
        )
    return tuple(answer.strip() for answer in value)


def _optional_text(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"DocVQA {field_name} must be text")
    return value.strip()


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"DocVQA {field_name} must be non-empty text")
    return value.strip()
