"""Single lazy registry for public benchmark metadata and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from skilladam.benchmarks.base import (
    AdapterNotImplementedError,
    BenchmarkAdapter,
    BenchmarkManifest,
)
from skilladam.types import PUBLIC_SPLITS


AdapterFactory = Callable[[Path], BenchmarkAdapter]


class UnknownBenchmarkError(KeyError):
    """Raised when a benchmark name is absent from the public registry."""


def _create_alfworld_adapter(data_root: Path) -> BenchmarkAdapter:
    """Import ALFWorld only when callers instantiate that adapter."""

    from skilladam.benchmarks.alfworld import ALFWorldAdapter

    return ALFWorldAdapter(data_root)


def _create_searchqa_adapter(data_root: Path) -> BenchmarkAdapter:
    """Import SearchQA only when callers instantiate that adapter."""

    from skilladam.benchmarks.searchqa import SearchQAAdapter

    return SearchQAAdapter(data_root)


def _create_spreadsheetbench_adapter(
    data_root: Path,
) -> BenchmarkAdapter:
    """Import SpreadsheetBench only when callers instantiate it."""

    from skilladam.benchmarks.spreadsheetbench import (
        SpreadsheetBenchAdapter,
    )

    return SpreadsheetBenchAdapter(data_root)


def _create_officeqa_adapter(data_root: Path) -> BenchmarkAdapter:
    """Import OfficeQA only when callers instantiate that adapter."""

    from skilladam.benchmarks.officeqa import OfficeQAAdapter

    return OfficeQAAdapter(data_root)


def _create_lmb_adapter(data_root: Path) -> BenchmarkAdapter:
    """Import LMB only when callers instantiate that adapter."""

    from skilladam.benchmarks.lmb import LMBAdapter

    return LMBAdapter(data_root)


def _create_docvqa_adapter(data_root: Path) -> BenchmarkAdapter:
    """Import DocVQA only when callers instantiate that adapter."""

    from skilladam.benchmarks.docvqa import DocVQAAdapter

    return DocVQAAdapter(data_root)


def _create_deepplanning_adapter(data_root: Path) -> BenchmarkAdapter:
    """Import DeepPlanning only when callers instantiate that adapter."""

    from skilladam.benchmarks.deepplanning import DeepPlanningAdapter

    return DeepPlanningAdapter(data_root)


@dataclass(frozen=True)
class BenchmarkRegistration:
    """Public metadata and one lazy adapter factory."""

    manifest: BenchmarkManifest
    implementation_task: int
    factory: AdapterFactory | None = None

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def display_name(self) -> str:
        return self.manifest.display_name

    @property
    def description(self) -> str:
        return self.manifest.description

    @property
    def requirements(self) -> tuple[str, ...]:
        return self.manifest.requirements

    @property
    def adapter_factory(self) -> AdapterFactory | None:
        """Compatibility view used by the dependency-free CLI metadata."""

        return self.factory

    def create(self, data_root: Path) -> BenchmarkAdapter:
        """Instantiate this adapter or report its planned implementation."""

        if self.factory is None:
            raise AdapterNotImplementedError(
                self.manifest.name,
                self.implementation_task,
            )
        return self.factory(Path(data_root))


BenchmarkSpec = BenchmarkRegistration


_BENCHMARK_METADATA = {
    "alfworld": (
        "ALFWorld",
        "Text-based embodied household tasks.",
        7,
    ),
    "deepplanning": (
        "DeepPlanning",
        "Long-horizon travel and shopping planning tasks.",
        8,
    ),
    "docvqa": (
        "DocVQA",
        "Visual question answering over document images.",
        7,
    ),
    "lmb": (
        "LMB",
        "LiveMathematicianBench mathematical reasoning tasks.",
        6,
    ),
    "officeqa": (
        "OfficeQA",
        "Question answering over U.S. Treasury Bulletin documents.",
        6,
    ),
    "searchqa": (
        "SearchQA",
        "Tool-assisted open-domain search questions.",
        6,
    ),
    "spreadsheetbench": (
        "SpreadsheetBench",
        "Spreadsheet manipulation and reasoning tasks.",
        6,
    ),
}

_ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    "alfworld": _create_alfworld_adapter,
    "deepplanning": _create_deepplanning_adapter,
    "docvqa": _create_docvqa_adapter,
    "lmb": _create_lmb_adapter,
    "officeqa": _create_officeqa_adapter,
    "searchqa": _create_searchqa_adapter,
    "spreadsheetbench": _create_spreadsheetbench_adapter,
}

_BENCHMARK_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "alfworld": ("alfworld==0.4.2",),
    "spreadsheetbench": ("openpyxl>=3.1", "pandas"),
}


def _build_registrations() -> dict[str, BenchmarkRegistration]:
    registrations: dict[str, BenchmarkRegistration] = {}
    for name, (
        display_name,
        description,
        implementation_task,
    ) in _BENCHMARK_METADATA.items():
        registrations[name] = BenchmarkRegistration(
            manifest=BenchmarkManifest(
                name=name,
                display_name=display_name,
                description=description,
                splits=frozenset(PUBLIC_SPLITS),
                requirements=_BENCHMARK_REQUIREMENTS.get(name, ()),
            ),
            implementation_task=implementation_task,
            factory=_ADAPTER_FACTORIES.get(name),
        )
    return registrations


_REGISTRATIONS = _build_registrations()


def list_benchmarks() -> tuple[str, ...]:
    """Return public benchmark names in deterministic order."""

    return tuple(sorted(_REGISTRATIONS))


def list_registrations() -> tuple[BenchmarkRegistration, ...]:
    """Return public adapter registrations in deterministic order."""

    return tuple(_REGISTRATIONS[name] for name in list_benchmarks())


def get_registration(name: str) -> BenchmarkRegistration:
    """Return the canonical lazy registration for *name*."""

    try:
        return _REGISTRATIONS[name]
    except KeyError as exc:
        raise UnknownBenchmarkError(name) from exc


def get_benchmark(name: str) -> BenchmarkRegistration:
    """Compatibility name for the canonical registration lookup."""

    return get_registration(name)


def create_adapter(name: str, data_root: Path) -> BenchmarkAdapter:
    """Create an adapter without importing unrelated benchmarks."""

    return get_registration(name).create(data_root)
