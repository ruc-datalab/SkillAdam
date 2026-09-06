"""Deterministic, local-only benchmark data materialization."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
import json
import os
from pathlib import Path
import shutil
import uuid
from typing import Any

from skilladam.benchmarks.registry import create_adapter
from skilladam.experiments.main_results import load_main_result_profile


_SUPPORTED = frozenset(
    {
        "alfworld",
        "docvqa",
        "lmb",
        "officeqa",
        "searchqa",
        "spreadsheetbench",
    }
)
_SPLIT_NAMES = ("train", "val", "test")
_IGNORED_NAMES = frozenset({".cache", ".git", "__pycache__"})


class MaterializationError(ValueError):
    """Raised when a local source cannot be safely materialized."""


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    """Path-free identity plus the caller-selected destination."""

    benchmark: str
    destination: Path
    split_counts: dict[str, int]
    file_count: int
    content_sha256: str
    dry_run: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "benchmark": self.benchmark,
            "destination": str(self.destination),
            "split_counts": dict(self.split_counts),
            "file_count": self.file_count,
            "content_sha256": self.content_sha256,
            "dry_run": self.dry_run,
            "network": False,
        }


def materialize_dataset(
    benchmark: str,
    source_root: Path,
    destination: Path,
    *,
    dry_run: bool = False,
    enforce_main_result_counts: bool = True,
) -> MaterializationResult:
    """Copy one audited local source into the strict public layout."""

    if benchmark == "deepplanning":
        raise MaterializationError(
            "DeepPlanning requires the pinned external runtime workflow"
        )
    if benchmark not in _SUPPORTED:
        raise MaterializationError(
            f"unsupported materialization benchmark {benchmark!r}"
        )
    source = _existing_source(Path(source_root))
    target = _safe_destination(Path(destination), source=source)
    temporary = target.with_name(
        f".{target.name}.materializing-{uuid.uuid4().hex}"
    )
    if temporary.exists():
        raise MaterializationError("temporary destination already exists")
    try:
        temporary.mkdir(parents=False)
        _materialize(benchmark, source, temporary)
        counts = _validate_adapter(
            benchmark,
            temporary,
            enforce_main_result_counts=enforce_main_result_counts,
        )
        file_count, digest = _tree_identity(temporary)
        if dry_run:
            shutil.rmtree(temporary)
            return MaterializationResult(
                benchmark=benchmark,
                destination=target,
                split_counts=counts,
                file_count=file_count,
                content_sha256=digest,
                dry_run=True,
            )
        _write_json(
            temporary / "materialization_manifest.json",
            {
                "schema_version": 1,
                "benchmark": benchmark,
                "split_counts": counts,
                "file_count": file_count,
                "content_sha256": digest,
                "network": False,
                "source_paths_recorded": False,
            },
        )
        if target.exists():
            target.rmdir()
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return MaterializationResult(
        benchmark=benchmark,
        destination=target,
        split_counts=counts,
        file_count=file_count,
        content_sha256=digest,
        dry_run=False,
    )


def _materialize(benchmark: str, source: Path, target: Path) -> None:
    if benchmark == "alfworld":
        _materialize_alfworld(source, target)
        return
    if benchmark == "docvqa":
        _materialize_docvqa(source, target)
        return
    if benchmark == "searchqa":
        _materialize_searchqa(source, target)
        return
    entries = {
        "spreadsheetbench": (
            "split_manifest.json",
            "data",
            "train",
            "val",
            "test",
            "README.md",
        ),
        "officeqa": (
            "split_manifest.json",
            "raw",
            "train",
            "val",
            "test",
        ),
        "lmb": (
            "split_manifest.json",
            "raw",
            "train",
            "val",
            "test",
        ),
    }[benchmark]
    copied = 0
    for name in entries:
        path = source / name
        if path.exists():
            _copy_entry(path, target / name)
            copied += 1
    if copied == 0:
        raise MaterializationError(
            f"{benchmark} source has none of the required layout entries"
        )


def _materialize_alfworld(source: Path, target: Path) -> None:
    profile_path = (
        resources.files("skilladam.data.profiles")
        / "alfworld_main_results.json"
    )
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if (
        not isinstance(profile, dict)
        or profile.get("schema_version") != 1
        or profile.get("benchmark") != "alfworld"
    ):
        raise MaterializationError("packaged ALFWorld profile is invalid")
    splits = profile.get("splits")
    if not isinstance(splits, dict) or set(splits) != set(_SPLIT_NAMES):
        raise MaterializationError(
            "packaged ALFWorld profile split set is invalid"
        )
    game_root = source.resolve()
    for split in _SPLIT_NAMES:
        items = splits[split]
        if not isinstance(items, list):
            raise MaterializationError(
                f"packaged ALFWorld {split} split is invalid"
            )
        for item in items:
            if not isinstance(item, dict):
                raise MaterializationError(
                    f"packaged ALFWorld {split} item is invalid"
                )
            raw = item.get("gamefile")
            if not isinstance(raw, str) or not raw.strip():
                raise MaterializationError(
                    f"packaged ALFWorld {split} gamefile is invalid"
                )
            gamefile = (game_root / raw).resolve()
            if not _is_within(gamefile, game_root):
                raise MaterializationError(
                    "packaged ALFWorld gamefile escapes the game root"
                )
            if not gamefile.is_file() or gamefile.is_symlink():
                raise MaterializationError(
                    f"ALFWorld gamefile is missing or unsafe: {raw!r}"
                )
            target_gamefile = target.joinpath(*Path(raw).parts)
            target_gamefile.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(gamefile, target_gamefile)
        _write_json(target / split / "items.json", items)
    _write_json(
        target / "split_manifest.json",
        {
            "benchmark": "ALFWorld",
            "manifest_type": "path_split",
            "profile": profile["profile"],
            "counts": profile["counts"],
            "source_method": "packaged-relative-path-profile",
        },
    )


def _materialize_searchqa(source: Path, target: Path) -> None:
    path = (
        source
        if source.is_file()
        else source / "searchqa_manifest.json"
    )
    payload = _read_json_object(path, "SearchQA manifest")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise MaterializationError(
            "SearchQA manifest cases must be a JSON array"
        )
    normalized: list[dict[str, Any]] = []
    for index, value in enumerate(raw_cases):
        if not isinstance(value, dict):
            raise MaterializationError(
                f"SearchQA case {index} must be a JSON object"
            )
        item = dict(value)
        if item.get("split") == "val":
            item["split"] = "validation"
        normalized.append(item)
    result = dict(payload)
    result["cases"] = normalized
    _write_json(target / "searchqa_manifest.json", result)
    if source.is_dir() and (source / "split_manifest.json").is_file():
        _copy_entry(
            source / "split_manifest.json",
            target / "split_manifest.json",
        )


def _materialize_docvqa(source: Path, target: Path) -> None:
    manifest = source / "split_manifest.json"
    if not manifest.is_file():
        raise MaterializationError(
            "DocVQA source is missing split_manifest.json"
        )
    _copy_entry(manifest, target / manifest.name)
    images_target = target / "images"
    images_target.mkdir()
    seen_images: dict[str, Path] = {}
    for split in _SPLIT_NAMES:
        source_items = source / split / "items.json"
        payload = _read_json_object(
            source_items,
            f"DocVQA {split}/items.json",
        )
        by_id = payload.get("by_id")
        if not isinstance(by_id, dict):
            raise MaterializationError(
                f"DocVQA {split}/items.json by_id must be an object"
            )
        normalized_by_id: dict[str, Any] = {}
        for case_id, raw_item in by_id.items():
            if not isinstance(raw_item, dict):
                raise MaterializationError(
                    f"DocVQA {split} item {case_id!r} must be an object"
                )
            item = dict(raw_item)
            raw_path = item.get("image_path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise MaterializationError(
                    f"DocVQA {split} item {case_id!r} has no image path"
                )
            filename = Path(raw_path).name
            if filename in {"", ".", ".."}:
                raise MaterializationError("DocVQA image filename is unsafe")
            image_source = source / "images" / filename
            if not image_source.is_file() or image_source.is_symlink():
                raise MaterializationError(
                    f"DocVQA image is missing or unsafe: {filename!r}"
                )
            previous = seen_images.setdefault(filename, image_source)
            if previous != image_source:
                raise MaterializationError(
                    f"DocVQA image basename collision: {filename!r}"
                )
            image_target = images_target / filename
            if not image_target.exists():
                shutil.copy2(image_source, image_target)
            item["image_path"] = f"images/{filename}"
            normalized_by_id[str(case_id)] = item
        normalized = dict(payload)
        normalized["by_id"] = normalized_by_id
        split_target = target / split
        split_target.mkdir()
        _write_json(split_target / "items.json", normalized)


def _validate_adapter(
    benchmark: str,
    root: Path,
    *,
    enforce_main_result_counts: bool,
) -> dict[str, int]:
    adapter = create_adapter(benchmark, root)
    counts = {
        split: len(tuple(adapter.load_cases(split)))
        for split in ("train", "validation", "test")
    }
    all_ids = [
        case.case_id
        for split in ("train", "validation", "test")
        for case in adapter.load_cases(split)
    ]
    if len(all_ids) != len(set(all_ids)):
        raise MaterializationError(
            f"{benchmark} materialized splits overlap"
        )
    if enforce_main_result_counts:
        expected = _expected_counts(benchmark)
        if counts != expected:
            raise MaterializationError(
                f"{benchmark} split counts {counts!r} != {expected!r}"
            )
    return counts


def _expected_counts(benchmark: str) -> dict[str, int]:
    dataset = load_main_result_profile().benchmark(benchmark).dataset
    return {
        "train": int(dataset["train_cases"]),
        "validation": int(dataset["selection_cases"]),
        "test": int(dataset["test_cases"]),
    }


def _existing_source(path: Path) -> Path:
    if path.is_symlink():
        raise MaterializationError("source_root must not be a symlink")
    if not path.exists():
        raise MaterializationError("source_root does not exist")
    return path.resolve()


def _safe_destination(path: Path, *, source: Path) -> Path:
    target = path.resolve()
    if target == source or _is_within(target, source) or _is_within(
        source,
        target,
    ):
        raise MaterializationError(
            "source_root and destination must not contain each other"
        )
    repository = _git_root(Path.cwd())
    if repository is not None and (
        target == repository or _is_within(target, repository)
    ):
        raise MaterializationError(
            "materialized data destination must be outside the Git repository"
        )
    if not target.parent.is_dir():
        raise MaterializationError(
            "materialized data destination parent must already exist"
        )
    if target.is_symlink():
        raise MaterializationError("destination must not be a symlink")
    if target.exists() and (
        not target.is_dir() or any(target.iterdir())
    ):
        raise MaterializationError(
            "destination must be absent or an empty directory"
        )
    return target


def _copy_entry(source: Path, target: Path) -> None:
    if source.is_symlink():
        raise MaterializationError(
            f"materialization source contains a symlink: {source.name!r}"
        )
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return
    if not source.is_dir():
        raise MaterializationError(
            f"materialization source entry is unsupported: {source.name!r}"
        )
    target.mkdir(parents=True, exist_ok=False)
    for child in sorted(source.iterdir(), key=lambda item: item.name):
        if (
            child.name in _IGNORED_NAMES
            or child.suffix == ".pyc"
        ):
            continue
        _copy_entry(child, target / child.name)


def _tree_identity(root: Path) -> tuple[int, str]:
    rows: list[bytes] = []
    files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name != "materialization_manifest.json"
    ]
    for path in sorted(files):
        if path.is_symlink():
            raise MaterializationError(
                "materialized tree must not contain symlinks"
            )
        relative = path.relative_to(root).as_posix()
        digest = sha256(path.read_bytes()).hexdigest()
        rows.append(f"{relative}\0{digest}\n".encode("utf-8"))
    return len(files), sha256(b"".join(rows)).hexdigest()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise MaterializationError(f"{label} is missing or unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise MaterializationError(f"{label} must be a JSON object")
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _git_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = [
    "MaterializationError",
    "MaterializationResult",
    "materialize_dataset",
]
