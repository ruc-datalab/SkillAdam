"""Compatibility facade for the canonical benchmark registry."""

from skilladam.benchmarks.registry import (
    AdapterFactory,
    BenchmarkRegistration,
    BenchmarkSpec,
    UnknownBenchmarkError,
    create_adapter,
    get_benchmark,
    get_registration,
    list_benchmarks,
    list_registrations,
)

__all__ = [
    "AdapterFactory",
    "BenchmarkRegistration",
    "BenchmarkSpec",
    "UnknownBenchmarkError",
    "create_adapter",
    "get_benchmark",
    "get_registration",
    "list_benchmarks",
    "list_registrations",
]
