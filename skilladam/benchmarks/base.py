"""Dependency-free benchmark adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import (
    Any,
    Callable,
    Mapping,
    Protocol,
    Sequence,
    runtime_checkable,
)

from skilladam.types import (
    BenchmarkCase,
    GateDecision,
    Method,
    MetricResult,
    PUBLIC_SPLITS,
    RolloutRequest,
    RolloutResult,
    Split,
)


class AdapterNotImplementedError(NotImplementedError):
    """Raised when a registered public adapter has not been ported."""

    def __init__(self, benchmark: str, implementation_task: int) -> None:
        self.benchmark = benchmark
        self.implementation_task = implementation_task
        super().__init__(
            f"{benchmark} adapter is not implemented yet; it is scheduled "
            f"for public-release Task {implementation_task}"
        )


class DatasetUnavailableError(FileNotFoundError):
    """Raised when an adapter cannot find its configured dataset."""

    def __init__(self, benchmark: str, path: Path) -> None:
        self.benchmark = benchmark
        self.path = Path(path)
        super().__init__(
            f"{benchmark} dataset not found at {self.path}. Configure the "
            "adapter data root or run `skilladam download-data --benchmark "
            f"{benchmark} --destination <path>`."
        )


class DependencyUnavailableError(ImportError):
    """Raised when an adapter's optional dependency is unavailable."""

    def __init__(
        self,
        benchmark: str,
        dependency: str,
        install_hint: str,
    ) -> None:
        self.benchmark = benchmark
        self.dependency = dependency
        self.install_hint = install_hint
        super().__init__(
            f"{benchmark} requires optional dependency {dependency!r}. "
            f"Install it with: {install_hint}"
        )


def load_optional_dependency(
    benchmark: str,
    dependency: str,
    install_hint: str,
    *,
    importer: Callable[[str], Any] = import_module,
) -> Any:
    """Import one optional module with a consistent actionable failure."""

    try:
        return importer(dependency)
    except ModuleNotFoundError as exc:
        missing_name = exc.name
        dependency_root = dependency.split(".", 1)[0]
        if missing_name not in {dependency, dependency_root}:
            raise
        raise DependencyUnavailableError(
            benchmark,
            dependency,
            install_hint,
        ) from exc


@dataclass(frozen=True)
class BenchmarkManifest:
    """Public metadata describing one adapter contract."""

    name: str
    display_name: str
    description: str
    splits: frozenset[str]
    fixture_filename: str = "cases.json"
    requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("name", "display_name", "description"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        normalized_splits = frozenset(self.splits)
        unsupported = normalized_splits - set(PUBLIC_SPLITS)
        if unsupported:
            raise ValueError(
                f"unsupported split names: {sorted(unsupported)!r}"
            )
        if not normalized_splits:
            raise ValueError("splits must not be empty")
        object.__setattr__(self, "splits", normalized_splits)
        fixture_path = Path(self.fixture_filename)
        if (
            fixture_path.is_absolute()
            or fixture_path.name != self.fixture_filename
        ):
            raise ValueError("fixture_filename must be a plain filename")


@runtime_checkable
class BenchmarkAdapter(Protocol):
    """Structural contract implemented by public benchmark adapters."""

    manifest: BenchmarkManifest
    data_root: Path

    def validate_dependencies(self) -> None:
        """Raise an actionable error for a missing optional dependency."""

    def load_cases(self, split: Split) -> Sequence[BenchmarkCase]:
        """Load deterministic normalized cases for one public split."""

    def build_rollout_request(
        self,
        case: BenchmarkCase,
        *,
        method: Method,
        split: Split,
        skill: str | None,
        seed: int,
    ) -> RolloutRequest:
        """Construct the provider-neutral request used by a rollout."""

    def parse_result(
        self,
        case: BenchmarkCase,
        raw_result: Mapping[str, Any],
    ) -> RolloutResult:
        """Parse a benchmark-specific raw result."""

    def evaluate(self, result: RolloutResult) -> MetricResult:
        """Calculate benchmark metrics for a parsed rollout result."""

    def gate(
        self,
        baseline: MetricResult,
        candidate: MetricResult,
    ) -> GateDecision:
        """Decide whether a candidate skill is accepted."""
