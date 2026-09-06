"""Offline LMB fixture and historical monthly-JSON manifest loading."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import random
from typing import Any

from skilladam.benchmarks.base import (
    BenchmarkManifest,
    DatasetUnavailableError,
)
from skilladam.benchmarks.lmb.choices import (
    normalize_choices,
    resolve_correct_choice,
)
from skilladam.types import BenchmarkCase, PUBLIC_SPLITS, Split


MANIFEST = BenchmarkManifest(
    name="lmb",
    display_name="LMB",
    description="LiveMathematicianBench mathematical reasoning tasks.",
    splits=frozenset(PUBLIC_SPLITS),
)

_PUBLIC_FIXTURE_NAME = MANIFEST.fixture_filename
_HISTORICAL_SPLITS = {
    "train": "train",
    "validation": "val",
    "test": "test",
}


def load_cases(data_root: Path, split: Split) -> tuple[BenchmarkCase, ...]:
    """Load a synthetic public fixture or historical monthly JSON files."""

    if split not in PUBLIC_SPLITS:
        raise ValueError(
            f"unsupported LMB split {split!r}; expected one of "
            f"{PUBLIC_SPLITS!r}"
        )
    root = Path(data_root)
    fixture_path = root if root.is_file() else root / _PUBLIC_FIXTURE_NAME
    if fixture_path.is_file():
        return _load_public_fixture(fixture_path, split)

    monthly_files, source_base = _resolve_monthly_files(root)
    if monthly_files:
        return _load_historical(
            root,
            monthly_files,
            source_base,
            split,
        )
    raise DatasetUnavailableError(MANIFEST.name, root)


def _load_public_fixture(
    path: Path,
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    fixture = _read_json_object(path, "public fixture")
    if fixture.get("schema_version") != 1:
        raise ValueError("LMB public fixture schema_version must equal 1")
    if fixture.get("benchmark") != MANIFEST.name:
        raise ValueError("LMB public fixture benchmark must be lmb")
    raw_cases = fixture.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("LMB public fixture cases must be a JSON array")

    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(
                f"LMB case at index {index} must be a JSON object"
            )
        case_split = raw_case.get("split")
        if case_split not in PUBLIC_SPLITS:
            raise ValueError(
                f"LMB case has unsupported split {case_split!r}"
            )
        if case_split != split:
            continue
        case_id = _non_empty_text(raw_case.get("case_id"), "case_id")
        if case_id in seen:
            raise ValueError(f"duplicate LMB case_id {case_id!r}")
        seen.add(case_id)
        raw_payload = raw_case.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise ValueError(
                f"LMB case {case_id!r} payload must be an object"
            )
        payload = dict(raw_payload)
        _question(payload, case_id)
        choices = normalize_choices(payload.get("choices"))
        resolve_correct_choice(raw_case.get("reference"), choices)
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                payload=payload,
                reference=raw_case.get("reference"),
                metadata={
                    "split": split,
                    "source_layout": "public-fixture",
                },
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _load_historical(
    root: Path,
    monthly_files: Sequence[Path],
    source_base: Path,
    split: Split,
) -> tuple[BenchmarkCase, ...]:
    items = _read_monthly_items(monthly_files, source_base)
    manifest = _load_split_manifest(root)
    splits = _load_or_generate_splits(root, tuple(items), manifest)
    historical_split = _HISTORICAL_SPLITS[split]

    cases: list[BenchmarkCase] = []
    for case_id in splits[historical_split]:
        try:
            item = items[case_id]
        except KeyError as exc:
            raise ValueError(
                f"LMB split id {case_id!r} is absent from raw monthly data"
            ) from exc
        metadata: dict[str, Any] = {
            "split": split,
            "historical_split": historical_split,
            "source_layout": "monthly-json",
            "source_file": item["source_file"],
            "hidden_reference": {
                "theorem": item["theorem"],
                "sketch": item["sketch"],
            },
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
                    "choices": [dict(choice) for choice in item["choices"]],
                    "theorem_type": list(item["theorem_type"]),
                    "paper_link": item["paper_link"],
                    "task_type": "math_mcq",
                },
                reference=dict(item["reference"]),
                metadata=metadata,
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _resolve_monthly_files(root: Path) -> tuple[tuple[Path, ...], Path]:
    for source_base in (root / "raw", root):
        files = tuple(
            sorted((source_base / "data").glob("*/qa_*_final.json"))
        )
        if files:
            return files, source_base
    return (), root


def _read_monthly_items(
    paths: Sequence[Path],
    source_base: Path,
) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for path in paths:
        raw_items = _read_json(path, "monthly source")
        if not isinstance(raw_items, list):
            raise ValueError(
                f"LMB monthly source at {path} must be a JSON array"
            )
        for index, raw_item in enumerate(raw_items):
            item = _normalize_raw_item(
                raw_item,
                label=f"{path.name}[{index}]",
                source_file=path.relative_to(source_base).as_posix(),
            )
            case_id = item["case_id"]
            if case_id in items:
                raise ValueError(f"duplicate LMB raw id {case_id!r}")
            items[case_id] = item
    return items


def _normalize_raw_item(
    value: Any,
    *,
    label: str,
    source_file: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"LMB {label} must be a JSON object")
    month = _non_empty_text(value.get("month"), f"{label}.month")
    number = value.get("no")
    if isinstance(number, bool) or not isinstance(number, (int, str)):
        raise ValueError(f"LMB {label}.no must be an integer or text")
    number_text = str(number).strip()
    if not number_text:
        raise ValueError(f"LMB {label}.no must be non-empty")
    case_id = f"{month}:{number_text}"

    mcq = value.get("mcq")
    if not isinstance(mcq, Mapping):
        raise ValueError(f"LMB {label}.mcq must be an object")
    correct_raw = mcq.get("correct_choice")
    distractors = mcq.get("choices")
    if not isinstance(correct_raw, Mapping):
        raise ValueError(
            f"LMB {label}.mcq.correct_choice must be an object"
        )
    if (
        isinstance(distractors, (str, bytes))
        or not isinstance(distractors, Sequence)
    ):
        raise ValueError(f"LMB {label}.mcq.choices must be a sequence")
    choices = tuple(
        sorted(
            normalize_choices([correct_raw, *distractors]),
            key=lambda choice: choice["label"],
        )
    )
    correct = resolve_correct_choice(correct_raw, choices)
    theorem_type = _text_sequence(
        value.get("theorem_type", ()),
        f"{label}.theorem_type",
        allow_empty=True,
    )
    return {
        "case_id": case_id,
        "question": _non_empty_text(
            mcq.get("question"),
            f"{label}.mcq.question",
        ),
        "choices": choices,
        "reference": correct,
        "theorem_type": theorem_type,
        "paper_link": _optional_text(value.get("paper_link")),
        "theorem": _optional_text(value.get("theorem")),
        "sketch": _optional_text(value.get("sketch")),
        "source_file": source_file,
    }


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
            "LMB split files are incomplete; missing "
            + ", ".join(f"{split}/items.json" for split in missing)
        )
    if present:
        splits = {
            split: _load_split_ids(path, split)
            for split, path in paths.items()
        }
        _reject_split_overlap(splits)
        known_ids = set(item_ids)
        unknown = sorted(
            set().union(*(set(ids) for ids in splits.values())) - known_ids
        )
        if unknown:
            raise ValueError(
                f"LMB split id {unknown[0]!r} is absent from raw data"
            )
        return splits

    ratio = _parse_ratio(manifest.get("split_ratio", "2:1:7"))
    seed = manifest.get("split_seed", 42)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("LMB split_seed must be an integer")
    return _generate_splits(item_ids, ratio=ratio, seed=seed)


def _load_split_ids(path: Path, split: str) -> tuple[str, ...]:
    raw_items = _read_json(path, f"{split}/items.json")
    if not isinstance(raw_items, list):
        raise ValueError(
            f"LMB {split}/items.json must contain a JSON array"
        )
    ids: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_items):
        raw_id = item.get("id") if isinstance(item, Mapping) else item
        case_id = _non_empty_text(raw_id, f"{split}[{index}].id")
        if case_id in seen:
            raise ValueError(
                f"duplicate LMB {split} split id {case_id!r}"
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
                    f"LMB split overlap: id {case_id!r} appears in "
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
        raise ValueError("LMB split_ratio must use train:val:test")
    if len(raw_parts) != 3:
        raise ValueError("LMB split_ratio must have three parts")
    try:
        ratio = tuple(int(part) for part in raw_parts)
    except (TypeError, ValueError) as exc:
        raise ValueError("LMB split_ratio must contain integers") from exc
    if any(part < 0 for part in ratio) or sum(ratio) <= 0:
        raise ValueError("LMB split_ratio parts must be non-negative")
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


def _question(payload: Mapping[str, Any], case_id: str) -> str:
    value = payload.get("question", payload.get("problem"))
    return _non_empty_text(value, f"{case_id}.question")


def _text_sequence(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(value, str):
        values: Sequence[Any] = (value,)
    elif not isinstance(value, (str, bytes)) and isinstance(value, Sequence):
        values = value
    else:
        raise ValueError(f"LMB {field_name} must be a string sequence")
    result = tuple(
        _non_empty_text(item, f"{field_name}[{index}]")
        for index, item in enumerate(values)
    )
    if not result and not allow_empty:
        raise ValueError(f"LMB {field_name} must not be empty")
    return result


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("LMB optional reference fields must be text")
    return value.strip()


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise DatasetUnavailableError(MANIFEST.name, path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid LMB {label} at {path}: {exc}") from exc


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = _read_json(path, label)
    if not isinstance(payload, dict):
        raise ValueError(f"LMB {label} must contain a JSON object")
    return payload


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"LMB {field_name} must be non-empty text")
    return value.strip()
