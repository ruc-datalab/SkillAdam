"""SearchQA manifest loading without dataset or network dependencies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from skilladam.benchmarks.base import (
    BenchmarkManifest,
    DatasetUnavailableError,
)
from skilladam.types import BenchmarkCase, PUBLIC_SPLITS, Split


MANIFEST = BenchmarkManifest(
    name="searchqa",
    display_name="SearchQA",
    description="Tool-assisted open-domain search questions.",
    splits=frozenset(PUBLIC_SPLITS),
)

_PUBLIC_FIXTURE_NAME = MANIFEST.fixture_filename
_HISTORICAL_MANIFEST_NAME = "searchqa_manifest.json"


def load_cases(data_root: Path, split: Split) -> tuple[BenchmarkCase, ...]:
    """Load one deterministic split from a public or historical manifest."""

    if split not in PUBLIC_SPLITS:
        raise ValueError(
            f"unsupported SearchQA split {split!r}; "
            f"expected one of {PUBLIC_SPLITS!r}"
        )
    path = _resolve_manifest_path(Path(data_root))
    payload = _load_payload(path)
    run_metadata = payload.get("run_metadata", {})
    if not isinstance(run_metadata, Mapping):
        raise ValueError("SearchQA run_metadata must be a JSON object")
    _validate_manifest_header(payload)
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("SearchQA manifest cases must be a JSON array")

    cases: list[BenchmarkCase] = []
    seen_case_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(
                f"SearchQA case at index {index} must be a JSON object"
            )
        case_split = _public_split(raw_case.get("split"))
        if case_split != split:
            continue
        case = _normalize_case(
            raw_case,
            run_metadata,
            split=case_split,
        )
        if case.case_id in seen_case_ids:
            raise ValueError(
                f"duplicate SearchQA case_id {case.case_id!r}"
            )
        seen_case_ids.add(case.case_id)
        cases.append(case)
    # Preserve the split-file order: the archived SkillOpt dataloader
    # shuffles this exact sequence with a frozen epoch seed.
    return tuple(cases)


def _resolve_manifest_path(data_root: Path) -> Path:
    if data_root.is_file():
        return data_root
    for filename in (
        _HISTORICAL_MANIFEST_NAME,
        _PUBLIC_FIXTURE_NAME,
    ):
        candidate = data_root / filename
        if candidate.is_file():
            return candidate
    raise DatasetUnavailableError(MANIFEST.name, data_root)


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid SearchQA manifest at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("SearchQA manifest must contain a JSON object")
    return payload


def _validate_manifest_header(payload: Mapping[str, Any]) -> None:
    if "benchmark" not in payload and "schema_version" not in payload:
        return
    if payload.get("schema_version") != 1:
        raise ValueError("SearchQA public fixture schema_version must equal 1")
    if payload.get("benchmark") != MANIFEST.name:
        raise ValueError("SearchQA public fixture benchmark must be searchqa")


def _normalize_case(
    raw_case: Mapping[str, Any],
    run_metadata: Mapping[str, Any],
    *,
    split: Split,
) -> BenchmarkCase:
    case_id = _non_empty_text(raw_case.get("case_id"), "case_id")

    if "payload" in raw_case:
        raw_payload = raw_case.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise ValueError(
                f"SearchQA case {case_id!r} payload must be an object"
            )
        payload = dict(raw_payload)
        reference = raw_case.get("reference")
    else:
        payload = {
            "question": raw_case.get("question"),
            "context": raw_case.get("context"),
        }
        reference = raw_case.get("answers")

    _validate_payload(case_id, payload)
    answers = _answer_list(reference, case_id)
    metadata = dict(run_metadata)
    metadata["split"] = split
    if "slice" in raw_case:
        metadata["slice"] = raw_case["slice"]
    return BenchmarkCase(
        case_id=case_id,
        payload=payload,
        reference=answers,
        metadata=metadata,
    )


def _public_split(value: Any) -> Split:
    normalized = "validation" if value == "val" else value
    if normalized not in PUBLIC_SPLITS:
        raise ValueError(
            f"SearchQA case has unsupported split {value!r}"
        )
    return normalized


def _validate_payload(case_id: str, payload: Mapping[str, Any]) -> None:
    _non_empty_text(payload.get("question"), f"{case_id}.question")
    context = payload.get("context")
    documents = payload.get("documents")
    if context is not None:
        _non_empty_text(context, f"{case_id}.context")
        return
    if (
        isinstance(documents, (str, bytes))
        or not isinstance(documents, Sequence)
        or not documents
        or any(not isinstance(item, str) or not item.strip() for item in documents)
    ):
        raise ValueError(
            f"SearchQA case {case_id!r} must provide context or documents"
        )


def _answer_list(value: Any, case_id: str) -> list[str]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(
            f"SearchQA case {case_id!r} reference answers must be "
            "a non-empty string array"
        )
    return list(value)


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"SearchQA {field_name} must be non-empty text")
    return value
