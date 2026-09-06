"""Integrity checks for the frozen main-result prompt resources."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from importlib import resources
import json
from typing import Any

from skilladam.benchmarks.registry import list_benchmarks


_RESOURCE = "prompt_resources.json"
_PREFIX = "skilladam/"


def verify_prompt_resources() -> dict[str, Any]:
    """Verify every packaged benchmark prompt and active shared prompt."""

    manifest_resource = resources.files("skilladam.experiments") / _RESOURCE
    try:
        manifest_bytes = manifest_resource.read_bytes()
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "prompt resource manifest is missing or invalid"
        ) from exc
    root = _object(payload, "prompt resource manifest")
    if root.get("schema_version") != 1:
        raise ValueError("unsupported prompt resource manifest schema")
    if root.get("profile_id") != "paper-main-results-v1":
        raise ValueError("prompt resource profile does not match main results")
    if root.get("provenance") != "archived_formal_runs":
        raise ValueError("prompt resource provenance is invalid")

    shared = _hash_mapping(root.get("shared_files"), "shared_files")
    skillopt = _hash_mapping(
        root.get("skillopt_benchmark_files"),
        "skillopt_benchmark_files",
    )
    raw_benchmarks = _object(root.get("benchmarks"), "benchmarks")
    expected_benchmarks = set(list_benchmarks())
    if set(raw_benchmarks) != expected_benchmarks:
        raise ValueError("prompt resource benchmark set mismatch")

    _verify_hashes(shared)
    _verify_exact_skillopt_directory(skillopt)
    _verify_hashes(skillopt)
    rows: list[dict[str, Any]] = []
    total_packaged = 0
    total_main_result = 0
    for benchmark in sorted(raw_benchmarks):
        entry = _object(raw_benchmarks[benchmark], benchmark)
        if set(entry) != {"packaged_files", "main_result_files"}:
            raise ValueError(
                f"{benchmark} prompt resource keys are invalid"
            )
        packaged = _hash_mapping(
            entry.get("packaged_files"),
            f"{benchmark}.packaged_files",
        )
        main_result = _path_list(
            entry.get("main_result_files"),
            f"{benchmark}.main_result_files",
        )
        if not set(main_result).issubset(packaged):
            raise ValueError(
                f"{benchmark} main-result prompts are not all packaged"
            )
        _verify_exact_benchmark_directory(benchmark, packaged)
        _verify_hashes(packaged)
        total_packaged += len(packaged)
        total_main_result += len(main_result)
        rows.append(
            {
                "benchmark": benchmark,
                "packaged_files": len(packaged),
                "main_result_files": len(main_result),
            }
        )
    return {
        "schema_version": 1,
        "profile_id": root["profile_id"],
        "provenance": root["provenance"],
        "manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "shared_files": len(shared),
        "skillopt_benchmark_files": len(skillopt),
        "packaged_benchmark_files": total_packaged,
        "main_result_benchmark_files": total_main_result,
        "benchmarks": rows,
    }


def _verify_exact_skillopt_directory(
    expected: Mapping[str, str],
) -> None:
    prompt_root = (
        resources.files("skilladam.baselines.skillopt")
        / "prompts"
        / "benchmarks"
    )
    actual: set[str] = set()
    for benchmark_dir in prompt_root.iterdir():
        if not benchmark_dir.is_dir():
            continue
        for item in benchmark_dir.iterdir():
            if item.is_file() and item.name.endswith(".md"):
                actual.add(
                    "skilladam/baselines/skillopt/prompts/benchmarks/"
                    f"{benchmark_dir.name}/{item.name}"
                )
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        unhashed = sorted(actual - set(expected))
        raise ValueError(
            "SkillOpt benchmark prompt directory differs from manifest: "
            f"missing={missing!r}, unhashed={unhashed!r}"
        )


def _verify_exact_benchmark_directory(
    benchmark: str,
    expected: Mapping[str, str],
) -> None:
    prompt_root = (
        resources.files(f"skilladam.benchmarks.{benchmark}") / "prompts"
    )
    actual = {
        f"skilladam/benchmarks/{benchmark}/prompts/{item.name}"
        for item in prompt_root.iterdir()
        if item.is_file() and item.name.endswith(".md")
    }
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        unhashed = sorted(actual - set(expected))
        raise ValueError(
            f"{benchmark} prompt directory differs from manifest: "
            f"missing={missing!r}, unhashed={unhashed!r}"
        )


def _verify_hashes(expected: Mapping[str, str]) -> None:
    package = resources.files("skilladam")
    for path, digest in expected.items():
        parts = _path_parts(path)
        resource = package.joinpath(*parts)
        try:
            actual = sha256(resource.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError(
                f"packaged prompt resource is missing: {path}"
            ) from exc
        if actual != digest:
            raise ValueError(
                f"packaged prompt resource hash mismatch: {path}"
            )


def _hash_mapping(value: Any, field_name: str) -> dict[str, str]:
    raw = _object(value, field_name)
    if not raw:
        raise ValueError(f"{field_name} must not be empty")
    result: dict[str, str] = {}
    for path, digest in raw.items():
        normalized = _path(path, field_name)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError(f"{field_name} contains an invalid SHA-256")
        result[normalized] = digest
    return result


def _path_list(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty array")
    result = tuple(_path(item, field_name) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} contains duplicate paths")
    return result


def _path(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        raise ValueError(f"{field_name} contains an invalid package path")
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError(f"{field_name} contains an unsafe package path")
    return value


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(path[len(_PREFIX) :].split("/"))


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


__all__ = ["verify_prompt_resources"]
