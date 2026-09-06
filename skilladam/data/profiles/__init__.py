"""Validated, payload-free main-result split profiles."""

from __future__ import annotations

from hashlib import sha256
from importlib import resources
import json
from typing import Any


_BENCHMARKS = frozenset(
    {"docvqa", "lmb", "officeqa", "searchqa", "spreadsheetbench"}
)


def load_split_profile(benchmark: str) -> dict[str, Any]:
    """Load and verify one packaged ID-only split profile."""

    if benchmark not in _BENCHMARKS:
        raise ValueError(f"no packaged ID split profile for {benchmark!r}")
    path = resources.files(__name__) / f"{benchmark}_main_results.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{benchmark} split profile must be an object")
    if payload.get("schema_version") != 1:
        raise ValueError(f"{benchmark} split profile schema is unsupported")
    if payload.get("benchmark") != benchmark:
        raise ValueError(f"{benchmark} split profile identity is invalid")
    if payload.get("profile") != "skilladam-main-results-v1":
        raise ValueError(f"{benchmark} split profile name is invalid")
    if payload.get("payloads_included") is not False:
        raise ValueError(f"{benchmark} split profile must be payload-free")

    splits = payload.get("splits")
    if not isinstance(splits, dict) or set(splits) != {
        "train",
        "val",
        "test",
    }:
        raise ValueError(f"{benchmark} split profile keys are invalid")
    all_ids: list[str] = []
    counts: dict[str, int] = {}
    for split in ("train", "val", "test"):
        values = splits[split]
        if (
            not isinstance(values, list)
            or any(not isinstance(item, str) or not item for item in values)
            or len(values) != len(set(values))
        ):
            raise ValueError(
                f"{benchmark} {split} split profile IDs are invalid"
            )
        counts[split] = len(values)
        all_ids.extend(values)
    if len(all_ids) != len(set(all_ids)):
        raise ValueError(f"{benchmark} split profile IDs overlap")
    if payload.get("counts") != counts:
        raise ValueError(f"{benchmark} split profile counts are invalid")

    declared_digest = payload.get("profile_sha256")
    identity = dict(payload)
    identity.pop("profile_sha256", None)
    raw = (
        json.dumps(
            identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    if declared_digest != sha256(raw).hexdigest():
        raise ValueError(f"{benchmark} split profile digest is invalid")
    return payload


__all__ = ["load_split_profile"]
