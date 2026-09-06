#!/usr/bin/env python3
"""Export payload-free main-result split profiles from a dataset source.

This maintainer tool intentionally keeps only case IDs and source provenance.
It never copies questions, answers, images, workbooks, or document contents.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


_SPECS = {
    "docvqa": (
        "docvqa_id_split",
        "questionId",
    ),
    "lmb": (
        "livemathematicianbench_id_split",
        "id",
    ),
    "officeqa": (
        "officeqa_id_split",
        "id",
    ),
    "searchqa": (
        "searchqa_id_split",
        "id",
    ),
    "spreadsheetbench": (
        "spreadsheetbench_id_split",
        "id",
    ),
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _text(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{label} must be text or a non-boolean integer")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} must be non-empty")
    return text


def export_profile(
    benchmark: str,
    source_root: Path,
    destination: Path,
) -> Path:
    """Export one deterministic ID-only profile."""

    directory_name, id_field = _SPECS[benchmark]
    source = source_root / directory_name
    manifest = _read_json(source / "split_manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError(f"{benchmark} split manifest must be an object")

    splits: dict[str, list[str]] = {}
    for split in ("train", "val", "test"):
        items = _read_json(source / split / "items.json")
        if not isinstance(items, list):
            raise ValueError(f"{benchmark} {split} items must be an array")
        ids = [
            _text(item.get(id_field), f"{benchmark}.{split}[{index}]")
            for index, item in enumerate(items)
            if isinstance(item, dict)
        ]
        if len(ids) != len(items):
            raise ValueError(f"{benchmark} {split} item must be an object")
        if len(ids) != len(set(ids)):
            raise ValueError(f"{benchmark} {split} contains duplicate IDs")
        splits[split] = ids

    all_ids = [item for values in splits.values() for item in values]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError(f"{benchmark} split IDs overlap")
    declared = manifest.get("counts")
    actual = {split: len(ids) for split, ids in splits.items()}
    if declared != actual:
        raise ValueError(
            f"{benchmark} split counts {actual!r} != {declared!r}"
        )

    source_files = manifest.get("source_files")
    profile = {
        "schema_version": 1,
        "benchmark": benchmark,
        "profile": "skilladam-main-results-v1",
        "source_repo": manifest.get("source_repo"),
        "source_revision": manifest.get("source_revision"),
        "source_file": manifest.get("source_file"),
        "source_files": source_files if isinstance(source_files, list) else [],
        "counts": actual,
        "splits": splits,
        "payloads_included": False,
    }
    identity = dict(profile)
    raw = (
        json.dumps(
            identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    profile["profile_sha256"] = sha256(raw).hexdigest()
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"{benchmark}_main_results.json"
    output.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    for benchmark in sorted(_SPECS):
        print(export_profile(
            benchmark,
            args.source_root,
            args.destination,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
