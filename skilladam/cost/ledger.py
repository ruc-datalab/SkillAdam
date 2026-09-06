"""Append-only JSONL ledger and deterministic usage aggregation."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from skilladam.types import PUBLIC_METHODS, UsageRecord

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


LEDGER_FIELDS = (
    "method",
    "benchmark",
    "stage",
    "model",
    "input_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
    "requests",
)
GROUP_FIELDS = ("method", "benchmark", "stage", "model")
TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
)


@dataclass(frozen=True)
class UsageLedgerEntry:
    """One provider-neutral usage row with experiment dimensions."""

    method: str
    benchmark: str
    stage: str
    model: str
    usage: UsageRecord

    def __post_init__(self) -> None:
        if self.method not in PUBLIC_METHODS:
            raise ValueError(f"unknown public method: {self.method!r}")
        for field_name in ("benchmark", "stage", "model"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")

    @property
    def uncached_input_tokens(self) -> int | None:
        return self.usage.uncached_input_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "benchmark": self.benchmark,
            "stage": self.stage,
            "model": self.model,
            "input_tokens": self.usage.input_tokens,
            "cached_input_tokens": self.usage.cached_input_tokens,
            "cache_creation_input_tokens": (
                self.usage.cache_creation_input_tokens
            ),
            "output_tokens": self.usage.output_tokens,
            "reasoning_tokens": self.usage.reasoning_tokens,
            "total_tokens": self.usage.total_tokens,
            "requests": self.usage.requests,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "UsageLedgerEntry":
        if not isinstance(payload, dict):
            raise ValueError("usage ledger row must be a JSON object")
        unknown = sorted(set(payload) - set(LEDGER_FIELDS))
        missing = sorted(set(LEDGER_FIELDS) - set(payload))
        if unknown or missing:
            details = []
            if unknown:
                details.append(f"unknown={unknown}")
            if missing:
                details.append(f"missing={missing}")
            raise ValueError(
                "invalid usage ledger fields: " + ", ".join(details)
            )
        return cls(
            method=payload["method"],
            benchmark=payload["benchmark"],
            stage=payload["stage"],
            model=payload["model"],
            usage=UsageRecord(
                input_tokens=payload["input_tokens"],
                cached_input_tokens=payload["cached_input_tokens"],
                cache_creation_input_tokens=payload[
                    "cache_creation_input_tokens"
                ],
                output_tokens=payload["output_tokens"],
                reasoning_tokens=payload["reasoning_tokens"],
                total_tokens=payload["total_tokens"],
                requests=payload["requests"],
            ),
        )


class UsageLedger:
    """Append and load shared JSONL rows.

    Cross-process read/write locking is guaranteed on POSIX. Other platforms
    retain per-instance thread locking and append-mode writes.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(
        self,
        *,
        method: str,
        benchmark: str,
        stage: str,
        model: str,
        usage: UsageRecord,
    ) -> UsageLedgerEntry:
        entry = UsageLedgerEntry(
            method=method,
            benchmark=benchmark,
            stage=stage,
            model=model,
            usage=usage,
        )
        text = json.dumps(
            entry.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = text.encode("utf-8")
        with self._lock:
            descriptor = os.open(
                self.path,
                os.O_APPEND | os.O_CREAT | os.O_RDWR,
                0o666,
            )
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                size = os.fstat(descriptor).st_size
                if size:
                    os.lseek(descriptor, -1, os.SEEK_END)
                    if os.read(descriptor, 1) != b"\n":
                        raise ValueError(
                            "refusing to append to a corrupt JSONL ledger: "
                            "the existing file does not end with a newline"
                        )
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    if written == 0:  # pragma: no cover - OS failure guard
                        raise OSError("failed to append usage ledger row")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
        return entry

    def read(self) -> list[UsageLedgerEntry]:
        if not self.path.exists():
            raise FileNotFoundError(f"usage ledger not found: {self.path}")
        with self._lock:
            with self.path.open("r", encoding="utf-8") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                try:
                    content = handle.read()
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        entries: list[UsageLedgerEntry] = []
        for line_number, line in enumerate(
            content.splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                entries.append(UsageLedgerEntry.from_dict(payload))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(
                    f"invalid usage ledger row at line {line_number}: {exc}"
                ) from exc
        return entries


@dataclass(frozen=True)
class AggregatedUsage:
    """One deterministic group key and its aggregated counters."""

    key: tuple[str, ...]
    usage: UsageRecord


def aggregate_entries(
    entries: Iterable[UsageLedgerEntry],
    *,
    group_by: tuple[str, ...],
) -> list[AggregatedUsage]:
    """Aggregate entries; any unknown token makes that aggregate unknown."""

    if not group_by or any(field not in GROUP_FIELDS for field in group_by):
        raise ValueError(
            f"group_by must use one or more of {GROUP_FIELDS!r}"
        )
    grouped: dict[tuple[str, ...], list[UsageLedgerEntry]] = {}
    for entry in entries:
        key = tuple(getattr(entry, field) for field in group_by)
        grouped.setdefault(key, []).append(entry)

    results: list[AggregatedUsage] = []
    for key in sorted(grouped):
        values = grouped[key]
        counters = {
            field: _sum_known(
                [getattr(entry.usage, field) for entry in values]
            )
            for field in TOKEN_FIELDS
        }
        results.append(
            AggregatedUsage(
                key=key,
                usage=UsageRecord(
                    **counters,
                    requests=sum(
                        entry.usage.requests for entry in values
                    ),
                ),
            )
        )
    return results


def _sum_known(values: list[int | None]) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)
