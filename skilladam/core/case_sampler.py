"""Deterministic case sampling for disjoint and same-batch validation."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterator, Sequence


CaseId = int | str


@dataclass(frozen=True)
class IterationCases:
    """Training and validation case IDs for one feedback-loop iteration."""

    training_case_ids: tuple[CaseId, ...]
    validation_case_ids: tuple[CaseId, ...]
    validation_mode: str = "disjoint"

    def __post_init__(self) -> None:
        if self.validation_mode not in {"disjoint", "same_batch"}:
            raise ValueError(
                "validation_mode must be disjoint or same_batch"
            )
        overlap = set(self.training_case_ids) & set(self.validation_case_ids)
        if self.validation_mode == "disjoint" and overlap:
            raise ValueError(
                "training and validation cases must be disjoint; "
                f"overlap={sorted(map(str, overlap))}"
            )
        if (
            self.validation_mode == "same_batch"
            and self.training_case_ids != self.validation_case_ids
        ):
            raise ValueError(
                "same_batch validation must exactly reuse the training cases"
            )


class DisjointCaseSampler:
    """Finite, replayable sequence of disjoint iteration batches."""

    def __init__(self, batches: Sequence[IterationCases]) -> None:
        self._batches = tuple(batches)

    def __iter__(self) -> Iterator[IterationCases]:
        return iter(self._batches)

    def __len__(self) -> int:
        return len(self._batches)


def build_disjoint_sampler(
    case_ids: Sequence[CaseId],
    *,
    train_size: int,
    validation_size: int,
    max_iterations: int,
    strategy: str = "random",
    seed: int = 42,
) -> DisjointCaseSampler:
    """Build deterministic batches whose train and validation sets differ."""

    if train_size <= 0:
        raise ValueError("train_size must be positive")
    if validation_size <= 0:
        raise ValueError("validation_size must be positive")
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")

    pool = list(case_ids)
    unique = list(dict.fromkeys(pool))
    required = train_size + validation_size
    if len(unique) != len(pool) or len(unique) < required:
        raise ValueError(
            f"at least {required} unique cases are required for disjoint sampling"
        )

    if strategy == "random":
        batches = _random_batches(
            unique,
            train_size=train_size,
            validation_size=validation_size,
            max_iterations=max_iterations,
            seed=seed,
        )
    elif strategy == "sequential":
        batches = _sequential_batches(
            unique,
            train_size=train_size,
            validation_size=validation_size,
            max_iterations=max_iterations,
        )
    else:
        raise ValueError(f"unknown sampling strategy: {strategy!r}")
    return DisjointCaseSampler(batches)


def build_same_batch_sampler(
    case_ids: Sequence[CaseId],
    *,
    batch_size: int,
    max_iterations: int,
    strategy: str = "random",
    seed: int = 42,
    include_partial_final_batch: bool = False,
) -> DisjointCaseSampler:
    """Build deterministic batches whose validation reuses training cases."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    pool = list(case_ids)
    unique = list(dict.fromkeys(pool))
    if len(unique) != len(pool) or len(unique) < batch_size:
        raise ValueError(
            f"at least {batch_size} unique cases are required for sampling"
        )
    if strategy == "random":
        rng = random.Random(seed)
        batches = [
            _same_batch(rng.sample(unique, batch_size))
            for _ in range(max_iterations)
        ]
    elif strategy == "sequential":
        batches = []
        for start in range(0, len(unique), batch_size):
            selected = unique[start : start + batch_size]
            if len(selected) < batch_size and not include_partial_final_batch:
                break
            batches.append(_same_batch(selected))
            if len(batches) >= max_iterations:
                break
    else:
        raise ValueError(f"unknown sampling strategy: {strategy!r}")
    return DisjointCaseSampler(batches)


def _same_batch(case_ids: Sequence[CaseId]) -> IterationCases:
    selected = tuple(case_ids)
    return IterationCases(
        training_case_ids=selected,
        validation_case_ids=selected,
        validation_mode="same_batch",
    )


def _random_batches(
    pool: list[CaseId],
    *,
    train_size: int,
    validation_size: int,
    max_iterations: int,
    seed: int,
) -> list[IterationCases]:
    rng = random.Random(seed)
    width = train_size + validation_size
    batches: list[IterationCases] = []
    for _ in range(max_iterations):
        selected = rng.sample(pool, width)
        batches.append(
            IterationCases(
                training_case_ids=tuple(selected[:train_size]),
                validation_case_ids=tuple(selected[train_size:]),
            )
        )
    return batches


def _sequential_batches(
    pool: list[CaseId],
    *,
    train_size: int,
    validation_size: int,
    max_iterations: int,
) -> list[IterationCases]:
    width = train_size + validation_size
    batches: list[IterationCases] = []
    for start in range(0, len(pool) - width + 1, width):
        selected = pool[start : start + width]
        batches.append(
            IterationCases(
                training_case_ids=tuple(selected[:train_size]),
                validation_case_ids=tuple(selected[train_size:]),
            )
        )
        if len(batches) >= max_iterations:
            break
    return batches
