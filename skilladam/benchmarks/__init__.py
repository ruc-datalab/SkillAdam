"""Public benchmark adapter contracts and lazy registrations."""

from skilladam.benchmarks.base import (
    AdapterNotImplementedError,
    BenchmarkAdapter,
    BenchmarkManifest,
    DatasetUnavailableError,
    DependencyUnavailableError,
    load_optional_dependency,
)
from skilladam.benchmarks.registry import (
    BenchmarkRegistration,
    create_adapter,
    get_registration,
    list_registrations,
)

__all__ = [
    "AdapterNotImplementedError",
    "BenchmarkAdapter",
    "BenchmarkManifest",
    "BenchmarkRegistration",
    "DatasetUnavailableError",
    "DependencyUnavailableError",
    "create_adapter",
    "get_registration",
    "list_registrations",
    "load_optional_dependency",
]
