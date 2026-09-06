"""Auditable dataset preparation plans for the seven public benchmarks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal


DatasetAccess = Literal["public", "gated", "license-review"]


class DatasetPreparationRequiredError(RuntimeError):
    """Raised when a dataset needs an explicit upstream preparation step."""


@dataclass(frozen=True, slots=True)
class DatasetDownloadPlan:
    """One immutable benchmark data acquisition recipe."""

    benchmark: str
    destination: Path
    source_url: str
    revision: str | None
    license_summary: str
    access: DatasetAccess
    redistribution: str
    required_layout: tuple[str, ...]
    preparation_steps: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def plan_sha256(self) -> str:
        """Hash the source recipe independently of a local destination."""

        payload = self._identity_payload()
        raw = (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        return sha256(raw).hexdigest()

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-safe dry-run record."""

        return {
            **self._identity_payload(),
            "destination": str(self.destination),
            "automatic": True,
            "network_requires_execute": True,
            "plan_sha256": self.plan_sha256,
        }

    def _identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "benchmark": self.benchmark,
            "source_url": self.source_url,
            "revision": self.revision,
            "license_summary": self.license_summary,
            "access": self.access,
            "redistribution": self.redistribution,
            "required_layout": list(self.required_layout),
            "preparation_steps": list(self.preparation_steps),
            "notes": list(self.notes),
        }


_PLACEHOLDER_DESTINATION = Path("<destination>")


def _recipe(
    benchmark: str,
    *,
    source_url: str,
    revision: str | None,
    license_summary: str,
    access: DatasetAccess,
    required_layout: tuple[str, ...],
    preparation_steps: tuple[str, ...],
    notes: tuple[str, ...] = (),
) -> DatasetDownloadPlan:
    return DatasetDownloadPlan(
        benchmark=benchmark,
        destination=_PLACEHOLDER_DESTINATION,
        source_url=source_url,
        revision=revision,
        license_summary=license_summary,
        access=access,
        redistribution="external-only",
        required_layout=required_layout,
        preparation_steps=preparation_steps,
        notes=notes,
    )


_RECIPES = {
    "alfworld": _recipe(
        "alfworld",
        source_url="https://github.com/alfworld/alfworld",
        revision="alfworld==0.4.2",
        license_summary=(
            "MIT for the upstream package; downloaded game payload "
            "redistribution was not established by the local audit"
        ),
        access="public",
        required_layout=(
            "split_manifest.json",
            "train/items.json",
            "val/items.json",
            "test/items.json",
            "selected game files below the materialized data root",
        ),
        preparation_steps=(
            "Install the optional dependency with skilladam[alfworld].",
            "Run the pinned upstream ALFWorld data installer in staging.",
            "Copy only the audited 39/18/134 game files and split files.",
            "Validate all gamefile paths relative to the external data root.",
        ),
    ),
    "deepplanning": _recipe(
        "deepplanning",
        source_url="https://huggingface.co/datasets/Qwen/DeepPlanning",
        revision="213876cce679f993a476d01042e13d111c0e3648",
        license_summary=(
            "Apache-2.0 as declared by the pinned official dataset; "
            "historical local snapshot provenance remains unverified"
        ),
        access="public",
        required_layout=(
            "deepplanning_runtime_manifest.json",
            "qwen-agent checkout at revision "
            "31a4d36d123688581a9e9744427272b33ce940e0",
            "official shopping levels 1/2/3 and travel English data",
        ),
        preparation_steps=(
            "Obtain Qwen-Agent at the pinned source revision.",
            "Obtain Qwen/DeepPlanning at the pinned dataset revision.",
            "Extract only the official shopping levels and English travel data.",
            "Write the caller-managed runtime manifest from the public example.",
            "Run the read-only SkillAdam runtime preflight before any API call.",
        ),
        notes=(
            "The four slices are shopping_level1, shopping_level2, "
            "shopping_level3, and travel_en.",
            "The public split must remain 78 train, 42 validation, 120 test.",
            "The current upstream full profile also includes Chinese travel; "
            "it is not silently treated as the paper profile.",
            "Official code, data, tools, and evaluator remain external-only.",
        ),
    ),
    "docvqa": _recipe(
        "docvqa",
        source_url="https://huggingface.co/datasets/lmms-lab/DocVQA",
        revision="539088ef8a8ada01ac8e2e6d4e372586748a265e",
        license_summary=(
            "Apache-2.0 as declared by the audited upstream dataset card"
        ),
        access="public",
        required_layout=(
            "split_manifest.json",
            "train/items.json",
            "val/items.json",
            "test/items.json",
            "images/<question-id>.png",
        ),
        preparation_steps=(
            "Fetch config DocVQA at the pinned revision.",
            "Select the upstream validation split only.",
            "Reproduce the audited ten-percent sample and 107/53/374 split.",
            "Write relative image paths and verify every referenced image.",
        ),
        notes=(
            "The 374-case split is a historical held-out split, not the "
            "official DocVQA test set.",
        ),
    ),
    "lmb": _recipe(
        "lmb",
        source_url=(
            "https://huggingface.co/datasets/LiveMathematicianBench/"
            "LiveMathematicianBench"
        ),
        revision="b72450f6ce96c26158d64d945a5d31ef7727be41",
        license_summary=(
            "Dataset redistribution license was not established by the "
            "local audit"
        ),
        access="license-review",
        required_layout=(
            "raw/data/<month>/qa_<month>_final.json",
            "optional train/items.json",
            "optional val/items.json",
            "optional test/items.json",
        ),
        preparation_steps=(
            "Review and accept the upstream dataset terms.",
            "Obtain the four audited monthly JSON files from upstream.",
            "Place them under raw/data/<month>/.",
            "Optionally materialize the deterministic 35/18/124 split.",
        ),
    ),
    "officeqa": _recipe(
        "officeqa",
        source_url="https://huggingface.co/datasets/databricks/officeqa",
        revision="8ecbf18d3833daf4750a903d14963e4c4c1d4cd8",
        license_summary=(
            "Question CSV CC-BY-SA-4.0; conversion code Apache-2.0; "
            "U.S. Treasury Bulletin corpus public domain"
        ),
        access="gated",
        required_layout=(
            "raw/officeqa_full.csv",
            "split_manifest.json",
            "train/items.json",
            "val/items.json",
            "test/items.json",
            "raw/treasury_bulletins_parsed/transformed with exactly the "
            "285 referenced Treasury Bulletin text files",
            "raw/treasury_bulletins_parsed/jsons with the corresponding "
            "285 parsed JSON files",
        ),
        preparation_steps=(
            "Request and receive access from the upstream gated dataset.",
            "Accept the upstream data terms and record the pinned revision.",
            "Materialize the audited 50/24/172 split.",
            "Download only the 285 public-domain Treasury documents "
            "referenced by the audited profile and their parsed JSON.",
            "Validate every text/JSON pair before installing the external "
            "data root.",
        ),
    ),
    "searchqa": _recipe(
        "searchqa",
        source_url="https://huggingface.co/datasets/lucadiliello/searchqa",
        revision="c1a979068ba118d85467179b704031d113d689cc",
        license_summary=(
            "The audited dataset page did not declare redistribution terms; "
            "the BSD-3-Clause code license does not cover Jeopardy! content"
        ),
        access="license-review",
        required_layout=("searchqa_manifest.json",),
        preparation_steps=(
            "Review the upstream dataset terms before obtaining data.",
            "Use the audited immutable upstream revision.",
            "Materialize train, validation, and test cases into one manifest.",
            "Confirm no test answer leaks into training or skill prompts.",
        ),
        notes=(
            "SearchQA artifacts are not described as zero-shot because the "
            "historical optimization process saw training examples.",
        ),
    ),
    "spreadsheetbench": _recipe(
        "spreadsheetbench",
        source_url=(
            "https://huggingface.co/datasets/KAKA22/SpreadsheetBench"
        ),
        revision="ab0b742b0fc95b946f212d80ac7771b5531272e4",
        license_summary="CC-BY-SA-4.0 for the audited dataset revision",
        access="public",
        required_layout=(
            "data/dataset.json",
            "data/spreadsheet/<id>/<input workbook>",
            "data/spreadsheet/<id>/<golden workbook>",
            "train/items.json",
            "val/items.json",
            "test/items.json",
        ),
        preparation_steps=(
            "Fetch the pinned Verified-400 dataset revision.",
            "Preserve the upstream attribution and license.",
            "Materialize the audited 80/40/280 split.",
            "Validate every input/golden workbook pair before execution.",
        ),
    ),
}


def list_dataset_plans() -> tuple[DatasetDownloadPlan, ...]:
    """Return destination-free recipes in public benchmark order."""

    return tuple(_RECIPES[name] for name in sorted(_RECIPES))


def create_download_plan(
    benchmark: str,
    destination: Path,
) -> DatasetDownloadPlan:
    """Bind one immutable recipe to an explicit local destination."""

    try:
        recipe = _RECIPES[benchmark]
    except KeyError as exc:
        raise ValueError(f"unknown benchmark {benchmark!r}") from exc
    destination = Path(destination)
    if destination.exists() and not destination.is_dir():
        raise ValueError(
            f"dataset destination is an existing file: {destination}"
        )
    return replace(recipe, destination=destination)


def prepare_dataset(
    plan: DatasetDownloadPlan,
    *,
    dry_run: bool,
) -> DatasetDownloadPlan:
    """Validate a plan without silently downloading or modifying data."""

    if dry_run:
        return plan
    raise DatasetPreparationRequiredError(
        f"{plan.benchmark} requires manual preparation; follow "
        "docs/datasets.md. No files were created, replaced, or deleted."
    )


__all__ = [
    "DatasetDownloadPlan",
    "DatasetPreparationRequiredError",
    "create_download_plan",
    "list_dataset_plans",
    "prepare_dataset",
]
