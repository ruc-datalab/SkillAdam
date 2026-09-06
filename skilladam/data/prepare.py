"""Explicit, atomic benchmark data acquisition and preparation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any
import zipfile

from skilladam.benchmarks.deepplanning.runtime import (
    DeepPlanningRuntimePreflight,
    OFFICIAL_DATASET_REVISION,
    OFFICIAL_QWEN_AGENT_REVISION,
    preflight_runtime,
)
from skilladam.data.download import create_download_plan
from skilladam.data.materialize import (
    MaterializationResult,
    materialize_dataset,
)
from skilladam.data.profiles import load_split_profile


_SEARCHQA_REVISION = "c1a979068ba118d85467179b704031d113d689cc"
_OFFICEQA_REVISION = "8ecbf18d3833daf4750a903d14963e4c4c1d4cd8"
_SPREADSHEET_ARCHIVE = "spreadsheetbench_verified_400.tar.gz"
_SPREADSHEET_ARCHIVE_SHA256 = (
    "10ef893dd29cb13ab97143ea787e68cdc9574a13873ab9a54e50b31dc03fc949"
)
_SPREADSHEET_TOP_LEVEL = "spreadsheetbench_verified_400"
_DEEPPLANNING_ARCHIVES = (
    "shoppingplanning/database_zip/database_level1.tar.gz",
    "shoppingplanning/database_zip/database_level2.tar.gz",
    "shoppingplanning/database_zip/database_level3.tar.gz",
    "travelplanning/database/database_en.zip",
)


class DataPreparationError(RuntimeError):
    """Raised when acquisition cannot prove the requested data identity."""


@dataclass(frozen=True, slots=True)
class DataPreparationResult:
    """Secret-free identity for one completed preparation."""

    benchmark: str
    destination: Path
    mode: str
    plan_sha256: str
    materialization: MaterializationResult | None = None
    deepplanning: DeepPlanningRuntimePreflight | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "benchmark": self.benchmark,
            "destination": str(self.destination),
            "mode": self.mode,
            "network": self.mode == "pinned-download",
            "plan_sha256": self.plan_sha256,
        }
        if self.materialization is not None:
            payload["materialization"] = self.materialization.to_dict()
        if self.deepplanning is not None:
            payload["deepplanning_runtime"] = (
                self.deepplanning.to_public_dict()
            )
        return payload


def prepare_benchmark_data(
    benchmark: str,
    destination: Path,
    *,
    source_root: Path | None = None,
    accept_terms: bool = False,
    cache_dir: Path | None = None,
    token: str | None = None,
    snapshot_download: Callable[..., str] | None = None,
    hub_download: Callable[..., str] | None = None,
    command_runner: Callable[[Sequence[str]], None] | None = None,
) -> DataPreparationResult:
    """Prepare one exact benchmark profile after an explicit execute choice.

    A local source never causes network access. Without ``source_root``, the
    function uses only the immutable upstream recipe associated with the
    benchmark. Restricted sources require an explicit terms acknowledgement.
    """

    plan = create_download_plan(benchmark, Path(destination))
    if source_root is not None:
        return _prepare_local_source(plan, Path(source_root))
    if plan.access in {"gated", "license-review"} and not accept_terms:
        raise DataPreparationError(
            f"{benchmark} requires --accept-terms before its upstream "
            "payload may be downloaded"
        )
    if benchmark == "officeqa" and not token:
        raise DataPreparationError(
            "officeqa requires an authorized Hugging Face token in HF_TOKEN"
        )

    target = _safe_external_destination(plan.destination)
    selected_cache = _safe_cache_dir(cache_dir)
    if benchmark == "alfworld":
        selected_snapshot = snapshot_download or _unused_downloader
        selected_hub = hub_download or _unused_downloader
    else:
        selected_snapshot, selected_hub = _huggingface_functions(
            snapshot_download,
            hub_download,
        )
    selected_runner = command_runner or _run_command
    request_token: str | bool = token if benchmark == "officeqa" else False

    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}.acquiring-",
        dir=target.parent,
    ) as temporary_text:
        temporary = Path(temporary_text)
        if benchmark == "deepplanning":
            runtime = _acquire_deepplanning(
                temporary,
                target,
                cache_dir=selected_cache,
                token=request_token,
                hub_download=selected_hub,
                command_runner=selected_runner,
                plan_sha256=plan.plan_sha256,
            )
            return DataPreparationResult(
                benchmark=benchmark,
                destination=target,
                mode="pinned-download",
                plan_sha256=plan.plan_sha256,
                deepplanning=runtime,
            )

        source = temporary / "source"
        source.mkdir()
        _acquire_materializer_source(
            benchmark,
            source,
            cache_dir=selected_cache,
            token=request_token,
            snapshot_download=selected_snapshot,
            hub_download=selected_hub,
            command_runner=selected_runner,
        )
        result = materialize_dataset(benchmark, source, target)
        _write_preparation_manifest(
            target,
            benchmark=benchmark,
            plan_sha256=plan.plan_sha256,
            mode="pinned-download",
        )
        return DataPreparationResult(
            benchmark=benchmark,
            destination=target,
            mode="pinned-download",
            plan_sha256=plan.plan_sha256,
            materialization=result,
        )


def _prepare_local_source(
    plan: Any,
    source_root: Path,
) -> DataPreparationResult:
    if plan.benchmark == "deepplanning":
        source = source_root.resolve()
        if plan.destination.resolve() != source:
            raise DataPreparationError(
                "DeepPlanning local mode is validation-only; destination "
                "must name the existing source runtime"
            )
        preflight = preflight_runtime(source)
        return DataPreparationResult(
            benchmark=plan.benchmark,
            destination=source,
            mode="validated-local-source",
            plan_sha256=plan.plan_sha256,
            deepplanning=preflight,
        )
    result = materialize_dataset(
        plan.benchmark,
        source_root,
        plan.destination,
    )
    _write_preparation_manifest(
        plan.destination.resolve(),
        benchmark=plan.benchmark,
        plan_sha256=plan.plan_sha256,
        mode="local-source",
    )
    return DataPreparationResult(
        benchmark=plan.benchmark,
        destination=plan.destination.resolve(),
        mode="local-source",
        plan_sha256=plan.plan_sha256,
        materialization=result,
    )


def _acquire_materializer_source(
    benchmark: str,
    source: Path,
    *,
    cache_dir: Path | None,
    token: str | bool,
    snapshot_download: Callable[..., str],
    hub_download: Callable[..., str],
    command_runner: Callable[[Sequence[str]], None],
) -> None:
    if benchmark == "alfworld":
        command_runner((
            "alfworld-download",
            "--data-dir",
            str(source),
        ))
        return
    if benchmark == "docvqa":
        snapshot = Path(snapshot_download(
            repo_id="lmms-lab/DocVQA",
            repo_type="dataset",
            revision=(
                "539088ef8a8ada01ac8e2e6d4e372586748a265e"
            ),
            allow_patterns=["DocVQA/validation-*.parquet", "README.md"],
            local_dir=str(source / "upstream"),
            cache_dir=_optional_text(cache_dir),
            token=token,
        ))
        _build_docvqa_source(snapshot, source)
        return
    if benchmark == "searchqa":
        snapshot = Path(snapshot_download(
            repo_id="lucadiliello/searchqa",
            repo_type="dataset",
            revision=_SEARCHQA_REVISION,
            allow_patterns=["data/train-*.parquet", "data/validation-*.parquet"],
            local_dir=str(source / "upstream"),
            cache_dir=_optional_text(cache_dir),
            token=token,
        ))
        _build_searchqa_source(snapshot, source)
        return
    if benchmark == "spreadsheetbench":
        archive = Path(hub_download(
            repo_id="KAKA22/SpreadsheetBench",
            repo_type="dataset",
            revision="ab0b742b0fc95b946f212d80ac7771b5531272e4",
            filename=_SPREADSHEET_ARCHIVE,
            local_dir=str(source / "upstream"),
            cache_dir=_optional_text(cache_dir),
            token=token,
        ))
        _build_spreadsheet_source(archive, source)
        return
    if benchmark == "lmb":
        snapshot = Path(snapshot_download(
            repo_id="LiveMathematicianBench/LiveMathematicianBench",
            repo_type="dataset",
            revision="b72450f6ce96c26158d64d945a5d31ef7727be41",
            allow_patterns=list(
                load_split_profile("lmb")["source_files"]
            ),
            local_dir=str(source / "upstream"),
            cache_dir=_optional_text(cache_dir),
            token=token,
        ))
        _build_lmb_source(snapshot, source)
        return
    if benchmark == "officeqa":
        csv_path = Path(hub_download(
            repo_id="databricks/officeqa",
            repo_type="dataset",
            revision=_OFFICEQA_REVISION,
            filename="officeqa_full.csv",
            local_dir=str(source / "upstream"),
            cache_dir=_optional_text(cache_dir),
            token=token,
        ))
        source_files = _officeqa_referenced_source_files(csv_path)
        allow_patterns = [
            *[
                f"treasury_bulletins_parsed/transformed/{name}"
                for name in source_files
            ],
            *[
                "treasury_bulletins_parsed/jsons/"
                f"{PurePosixPath(name).stem}.json"
                for name in source_files
            ],
            "LICENSE-APACHE",
            "LICENSE-CC-BY-SA",
            "NOTICE",
            "README.md",
        ]
        snapshot = Path(snapshot_download(
            repo_id="databricks/officeqa",
            repo_type="dataset",
            revision=_OFFICEQA_REVISION,
            allow_patterns=allow_patterns,
            local_dir=str(source / "upstream"),
            cache_dir=_optional_text(cache_dir),
            token=token,
        ))
        _build_officeqa_source(
            csv_path,
            snapshot,
            source,
            source_files=source_files,
        )
        return
    raise DataPreparationError(f"unsupported benchmark {benchmark!r}")


def _build_docvqa_source(snapshot: Path, source: Path) -> None:
    profile = load_split_profile("docvqa")
    wanted = _profile_owner(profile)
    found: dict[str, dict[str, Any]] = {}
    images = source / "images"
    images.mkdir()
    paths = sorted((snapshot / "DocVQA").glob("validation-*.parquet"))
    if not paths:
        raise DataPreparationError("DocVQA validation parquet shards missing")
    for row in _iter_parquet_rows(paths):
        case_id = str(row.get("questionId", "")).strip()
        if case_id not in wanted or case_id in found:
            continue
        image = row.get("image")
        if not isinstance(image, Mapping) or not image.get("bytes"):
            raise DataPreparationError(
                f"DocVQA image bytes missing for {case_id!r}"
            )
        _save_png(image["bytes"], images / f"{case_id}.png")
        found[case_id] = {
            "id": case_id,
            "questionId": case_id,
            "docId": str(row.get("docId", "")),
            "question": str(row.get("question", "")).strip(),
            "answers": [str(item).strip() for item in row.get("answers") or []],
            "image_path": f"images/{case_id}.png",
            "ucsf_document_id": str(row.get("ucsf_document_id", "")),
            "ucsf_document_page_no": str(
                row.get("ucsf_document_page_no", "")
            ),
            "topic": ", ".join(row.get("question_types") or []),
            "source_dataset": profile["source_repo"],
            "source_split": "validation",
        }
    _require_all_ids("docvqa", wanted, found)
    _write_profile_split_manifest(source, profile)
    for split, ids in profile["splits"].items():
        _write_json(
            source / split / "items.json",
            {"ids": ids, "by_id": {case_id: found[case_id] for case_id in ids}},
        )


def _build_searchqa_source(snapshot: Path, source: Path) -> None:
    profile = load_split_profile("searchqa")
    wanted = _profile_owner(profile)
    found: dict[str, Mapping[str, Any]] = {}
    paths = sorted((snapshot / "data").glob("*.parquet"))
    if not paths:
        raise DataPreparationError("SearchQA parquet files missing")
    for row in _iter_parquet_rows(paths):
        case_id = str(row.get("key", "")).strip()
        if case_id in wanted and case_id not in found:
            found[case_id] = row
    _require_all_ids("searchqa", wanted, found)
    cases: list[dict[str, Any]] = []
    for split in ("train", "val", "test"):
        for case_id in profile["splits"][split]:
            row = found[case_id]
            cases.append({
                "slice": "searchqa",
                "case_id": case_id,
                "split": split,
                "question": str(row.get("question", "")).strip(),
                "context": str(row.get("context", "")).strip(),
                "answers": [str(item).strip() for item in row.get("answers") or []],
            })
    _write_json(
        source / "searchqa_manifest.json",
        {
            "run_metadata": {
                "benchmark": "searchqa",
                "source_repo": profile["source_repo"],
                "source_revision": _SEARCHQA_REVISION,
                "split_profile_sha256": profile["profile_sha256"],
            },
            "cases": cases,
        },
    )


def _build_spreadsheet_source(archive: Path, source: Path) -> None:
    if _file_sha256(archive) != _SPREADSHEET_ARCHIVE_SHA256:
        raise DataPreparationError(
            "SpreadsheetBench archive SHA-256 does not match the audited file"
        )
    extracted = source / "extracted"
    extracted.mkdir()
    _safe_extract_tar(archive, extracted)
    upstream = extracted / _SPREADSHEET_TOP_LEVEL
    if not upstream.is_dir():
        raise DataPreparationError(
            "SpreadsheetBench archive top-level directory is missing"
        )
    data = source / "data"
    data.mkdir()
    for name in ("dataset.json", "spreadsheet"):
        candidate = upstream / name
        if not candidate.exists() or candidate.is_symlink():
            raise DataPreparationError(
                f"SpreadsheetBench archive is missing {name!r}"
            )
        shutil.move(str(candidate), data / name)
    shutil.rmtree(extracted)
    _write_profile_files(source, load_split_profile("spreadsheetbench"))


def _build_lmb_source(snapshot: Path, source: Path) -> None:
    profile = load_split_profile("lmb")
    for relative in profile["source_files"]:
        source_path = _safe_child(snapshot, relative, require_file=True)
        target = source / "raw" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
    _write_profile_files(source, profile)


def _officeqa_referenced_source_files(csv_path: Path) -> tuple[str, ...]:
    from skilladam.benchmarks.officeqa.manifest import _read_csv

    profile = load_split_profile("officeqa")
    wanted = _profile_owner(profile)
    items = _read_csv(csv_path)
    _require_all_ids("officeqa", wanted, items)
    names: set[str] = set()
    for case_id in sorted(wanted):
        source_files = items[case_id]["source_files"]
        if not source_files:
            raise DataPreparationError(
                f"OfficeQA case {case_id!r} has no source_files"
            )
        for value in source_files:
            relative = PurePosixPath(value)
            if (
                relative.is_absolute()
                or len(relative.parts) != 1
                or relative.name != value
                or relative.suffix.lower() != ".txt"
            ):
                raise DataPreparationError(
                    f"OfficeQA source filename is unsafe: {value!r}"
                )
            names.add(value)
    if len(names) != 285:
        raise DataPreparationError(
            "OfficeQA pinned profile must reference exactly 285 documents"
        )
    return tuple(sorted(names))


def _build_officeqa_source(
    csv_path: Path,
    snapshot: Path,
    source: Path,
    *,
    source_files: tuple[str, ...],
) -> None:
    if not csv_path.is_file() or csv_path.is_symlink():
        raise DataPreparationError("OfficeQA gated CSV is missing or unsafe")
    raw = source / "raw"
    raw.mkdir()
    shutil.copy2(csv_path, raw / "officeqa_full.csv")
    corpus = raw / "treasury_bulletins_parsed"
    for name in source_files:
        text_relative = f"treasury_bulletins_parsed/transformed/{name}"
        json_relative = (
            "treasury_bulletins_parsed/jsons/"
            f"{PurePosixPath(name).stem}.json"
        )
        for relative in (text_relative, json_relative):
            upstream = _safe_child(snapshot, relative, require_file=True)
            target = corpus.joinpath(
                *PurePosixPath(relative).relative_to(
                    "treasury_bulletins_parsed"
                ).parts
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(upstream, target)
    for name in (
        "LICENSE-APACHE",
        "LICENSE-CC-BY-SA",
        "NOTICE",
        "README.md",
    ):
        candidate = snapshot / name
        if candidate.is_file() and not candidate.is_symlink():
            shutil.copy2(candidate, raw / name)
    _validate_officeqa_corpus_files(raw, source_files)
    _write_profile_files(source, load_split_profile("officeqa"))


def _validate_officeqa_corpus_files(
    raw: Path,
    source_files: tuple[str, ...],
) -> None:
    corpus = raw / "treasury_bulletins_parsed"
    transformed = corpus / "transformed"
    parsed = corpus / "jsons"
    for name in source_files:
        text_path = transformed / name
        json_path = parsed / f"{PurePosixPath(name).stem}.json"
        if (
            not text_path.is_file()
            or text_path.is_symlink()
            or text_path.stat().st_size == 0
        ):
            raise DataPreparationError(
                f"OfficeQA referenced document is missing or unsafe: {name}"
            )
        if not json_path.is_file() or json_path.is_symlink():
            raise DataPreparationError(
                f"OfficeQA parsed document is missing or unsafe: {name}"
            )
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DataPreparationError(
                f"OfficeQA parsed document is unreadable: {name}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise DataPreparationError(
                f"OfficeQA parsed document is invalid: {name}"
            )


def _acquire_deepplanning(
    temporary: Path,
    target: Path,
    *,
    cache_dir: Path | None,
    token: str | bool,
    hub_download: Callable[..., str],
    command_runner: Callable[[Sequence[str]], None],
    plan_sha256: str,
) -> DeepPlanningRuntimePreflight:
    runtime = temporary / "runtime"
    checkout = runtime / "qwen-agent"
    runtime.mkdir()
    command_runner((
        "git",
        "clone",
        "--no-checkout",
        "https://github.com/QwenLM/Qwen-Agent.git",
        str(checkout),
    ))
    command_runner((
        "git",
        "-C",
        str(checkout),
        "checkout",
        "--detach",
        OFFICIAL_QWEN_AGENT_REVISION,
    ))
    benchmark_root = checkout / "benchmark" / "deepplanning"
    for filename in _DEEPPLANNING_ARCHIVES:
        downloaded = Path(hub_download(
            repo_id="Qwen/DeepPlanning",
            repo_type="dataset",
            revision=OFFICIAL_DATASET_REVISION,
            filename=filename,
            local_dir=str(temporary / "upstream"),
            cache_dir=_optional_text(cache_dir),
            token=token,
        ))
        if filename.endswith(".tar.gz"):
            _safe_extract_tar(downloaded, benchmark_root / "shoppingplanning")
        else:
            _safe_extract_zip(
                downloaded,
                benchmark_root / "travelplanning" / "database",
            )
    example = {
        "schema_version": 1,
        "profile": "skilladam-paper-240-v1",
        "qwen_agent_revision": OFFICIAL_QWEN_AGENT_REVISION,
        "dataset_revision": OFFICIAL_DATASET_REVISION,
        "qwen_agent_root": "qwen-agent",
        "benchmark_root": "qwen-agent/benchmark/deepplanning",
        "bridge_module": "skilladam.backends.deepplanning_bridge",
        "bridge_protocol_version": 1,
        "slices": {
            "shopping_level1": {
                "query_file": "shoppingplanning/data/level_1_query_meta.json",
                "database_root": "shoppingplanning/database_level1",
            },
            "shopping_level2": {
                "query_file": "shoppingplanning/data/level_2_query_meta.json",
                "database_root": "shoppingplanning/database_level2",
            },
            "shopping_level3": {
                "query_file": "shoppingplanning/data/level_3_query_meta.json",
                "database_root": "shoppingplanning/database_level3",
            },
            "travel_en": {
                "query_file": "travelplanning/data/travelplanning_query_en.json",
                "database_root": "travelplanning/database/database_en",
            },
        },
    }
    _write_json(runtime / "deepplanning_runtime_manifest.json", example)
    preflight = preflight_runtime(runtime)
    if not preflight.git_revision_verified:
        raise DataPreparationError(
            "DeepPlanning Qwen-Agent Git revision could not be verified"
        )
    _write_preparation_manifest(
        runtime,
        benchmark="deepplanning",
        plan_sha256=plan_sha256,
        mode="pinned-download",
    )
    if target.exists():
        target.rmdir()
    os.replace(runtime, target)
    return preflight


def _write_profile_files(source: Path, profile: Mapping[str, Any]) -> None:
    _write_profile_split_manifest(source, profile)
    for split, ids in profile["splits"].items():
        _write_json(
            source / split / "items.json",
            [{"id": case_id} for case_id in ids],
        )


def _write_profile_split_manifest(
    source: Path,
    profile: Mapping[str, Any],
) -> None:
    payload = {
        "benchmark": profile["benchmark"],
        "manifest_type": "id_split",
        "profile": profile["profile"],
        "profile_sha256": profile["profile_sha256"],
        "source_repo": profile["source_repo"],
        "source_revision": profile["source_revision"],
        "counts": profile["counts"],
    }
    _write_json(source / "split_manifest.json", payload)


def _profile_owner(profile: Mapping[str, Any]) -> dict[str, str]:
    return {
        case_id: split
        for split, ids in profile["splits"].items()
        for case_id in ids
    }


def _require_all_ids(
    benchmark: str,
    wanted: Mapping[str, str],
    found: Mapping[str, Any],
) -> None:
    missing = sorted(set(wanted) - set(found))
    if missing:
        raise DataPreparationError(
            f"{benchmark} pinned source is missing {len(missing)} audited "
            f"case IDs; first missing ID: {missing[0]!r}"
        )


def _iter_parquet_rows(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise DataPreparationError(
            "parquet preparation requires pip install 'skilladam[data]'"
        ) from exc
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise DataPreparationError(f"parquet shard is missing: {path.name}")
        for batch in parquet.ParquetFile(path).iter_batches(batch_size=32):
            yield from batch.to_pylist()


def _save_png(raw: Any, path: Path) -> None:
    if not isinstance(raw, bytes):
        raise DataPreparationError("DocVQA image payload must be bytes")
    try:
        from PIL import Image
    except ImportError as exc:
        raise DataPreparationError(
            "DocVQA preparation requires pip install 'skilladam[data]'"
        ) from exc
    try:
        with Image.open(BytesIO(raw)) as image:
            image.save(path, format="PNG")
    except Exception as exc:
        raise DataPreparationError("DocVQA image payload is invalid") from exc


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        handle = tarfile.open(archive, "r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise DataPreparationError(f"invalid tar archive: {archive.name}") from exc
    with handle:
        for member in handle.getmembers():
            target = _archive_target(root, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise DataPreparationError(
                    f"archive contains unsupported entry: {member.name!r}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise DataPreparationError(
                    f"archive contains a duplicate entry: {member.name!r}"
                )
            source = handle.extractfile(member)
            if source is None:
                raise DataPreparationError(
                    f"archive entry cannot be read: {member.name!r}"
                )
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        handle = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DataPreparationError(f"invalid zip archive: {archive.name}") from exc
    with handle:
        for member in handle.infolist():
            target = _archive_target(root, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            mode = member.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise DataPreparationError(
                    f"archive contains a symlink: {member.filename!r}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise DataPreparationError(
                    f"archive contains a duplicate entry: {member.filename!r}"
                )
            with handle.open(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def _archive_target(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise DataPreparationError(f"unsafe archive path: {name!r}")
    target = root.joinpath(*relative.parts).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise DataPreparationError(f"archive path escapes root: {name!r}") from None
    return target


def _safe_child(root: Path, relative: str, *, require_file: bool) -> Path:
    root = root.resolve()
    parts = PurePosixPath(relative).parts
    candidate = root.joinpath(*parts)
    current = candidate
    while current != root:
        if current.is_symlink():
            raise DataPreparationError(
                f"upstream file is missing or unsafe: {relative}"
            )
        current = current.parent
    path = _archive_target(root, relative)
    if require_file and not path.is_file():
        raise DataPreparationError(f"upstream file is missing or unsafe: {relative}")
    return path


def _safe_external_destination(path: Path) -> Path:
    target = path.resolve()
    repository = _git_root(Path.cwd())
    if repository is not None and _within(target, repository):
        raise DataPreparationError(
            "prepared data destination must be outside the Git repository"
        )
    if not target.parent.is_dir():
        raise DataPreparationError(
            "prepared data destination parent must already exist"
        )
    if target.is_symlink():
        raise DataPreparationError("destination must not be a symlink")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise DataPreparationError(
            "destination must be absent or an empty directory"
        )
    return target


def _safe_cache_dir(path: Path | None) -> Path | None:
    if path is None:
        return None
    selected = path.resolve()
    repository = _git_root(Path.cwd())
    if repository is not None and _within(selected, repository):
        raise DataPreparationError("download cache must be outside the repository")
    try:
        selected.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DataPreparationError(
            "download cache could not be created"
        ) from exc
    if not selected.is_dir() or selected.is_symlink():
        raise DataPreparationError("download cache must be a real directory")
    return selected


def _huggingface_functions(
    snapshot_download: Callable[..., str] | None,
    hub_download: Callable[..., str] | None,
) -> tuple[Callable[..., str], Callable[..., str]]:
    if snapshot_download is not None and hub_download is not None:
        return snapshot_download, hub_download
    try:
        from huggingface_hub import hf_hub_download
        from huggingface_hub import snapshot_download as hf_snapshot_download
    except ImportError as exc:
        raise DataPreparationError(
            "automatic data preparation requires pip install 'skilladam[data]'"
        ) from exc
    return (
        snapshot_download or hf_snapshot_download,
        hub_download or hf_hub_download,
    )


def _run_command(argv: Sequence[str]) -> None:
    try:
        subprocess.run(
            list(argv),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DataPreparationError(
            f"external data command failed: {argv[0]}"
        ) from exc


def _unused_downloader(**_: object) -> str:
    raise AssertionError("unexpected Hugging Face downloader call")


def _write_preparation_manifest(
    root: Path,
    *,
    benchmark: str,
    plan_sha256: str,
    mode: str,
) -> None:
    _write_json(
        root / "data_preparation_manifest.json",
        {
            "schema_version": 1,
            "benchmark": benchmark,
            "mode": mode,
            "network": mode == "pinned-download",
            "plan_sha256": plan_sha256,
            "source_paths_recorded": False,
            "secrets_recorded": False,
        },
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _optional_text(path: Path | None) -> str | None:
    return str(path) if path is not None else None


def _git_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = [
    "DataPreparationError",
    "DataPreparationResult",
    "prepare_benchmark_data",
]
