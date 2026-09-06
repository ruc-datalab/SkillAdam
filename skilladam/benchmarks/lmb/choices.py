"""Choice normalization and deterministic LMB option shuffling."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import random
from typing import Any


_LABELS = tuple("ABCDEFG")


def normalize_choices(value: Any) -> tuple[dict[str, str], ...]:
    """Return validated ``label``/``text`` records without mutating input."""

    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or len(value) > len(_LABELS)
    ):
        raise ValueError(
            "LMB choices must be a non-empty sequence with at most 7 items"
        )

    choices: list[dict[str, str]] = []
    seen_labels: set[str] = set()
    for index, raw_choice in enumerate(value):
        if isinstance(raw_choice, str):
            label = _LABELS[index]
            text = raw_choice.strip()
        elif isinstance(raw_choice, Mapping):
            label = normalize_label(raw_choice.get("label"))
            raw_text = raw_choice.get("text")
            text = raw_text.strip() if isinstance(raw_text, str) else ""
        else:
            raise ValueError(
                f"LMB choices[{index}] must be text or an object"
            )
        if not label or label not in _LABELS:
            raise ValueError(
                f"LMB choices[{index}] has an unsupported label"
            )
        if label in seen_labels:
            raise ValueError(f"duplicate LMB choice label {label!r}")
        if not text:
            raise ValueError(
                f"LMB choices[{index}] text must be non-empty"
            )
        seen_labels.add(label)
        choices.append({"label": label, "text": text})
    return tuple(choices)


def normalize_label(value: Any) -> str:
    """Normalize one possible MCQ label."""

    if not isinstance(value, str):
        return ""
    return value.strip().upper().rstrip(".):")


def resolve_correct_choice(
    reference: Any,
    choices: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    """Resolve a label or exact choice text to one normalized choice."""

    normalized = normalize_choices(choices)
    by_label = {choice["label"]: choice for choice in normalized}

    if isinstance(reference, Mapping):
        label = normalize_label(reference.get("label"))
        raw_text = reference.get("text")
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        if label not in by_label:
            raise ValueError(
                "LMB reference correct label is absent from choices"
            )
        correct = by_label[label]
        if text and text.casefold() != correct["text"].casefold():
            raise ValueError(
                "LMB reference correct text does not match its choice label"
            )
        return dict(correct)

    if not isinstance(reference, str) or not reference.strip():
        raise ValueError(
            "LMB reference must identify the correct label or choice text"
        )
    label = normalize_label(reference)
    if label in by_label:
        return dict(by_label[label])
    matches = [
        choice
        for choice in normalized
        if choice["text"].casefold() == reference.strip().casefold()
    ]
    if len(matches) == 1:
        return dict(matches[0])
    raise ValueError(
        "LMB reference does not match any correct choice label or text"
    )


def shuffle_choices(
    choices: Sequence[Mapping[str, str]],
    correct_label: str,
    *,
    seed: int,
    item_id: str,
) -> tuple[list[dict[str, str]], str]:
    """Reproduce the historical per-item deterministic LMB shuffle."""

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("LMB choice shuffle seed must be an integer")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("LMB choice shuffle item_id must be non-empty text")

    normalized = normalize_choices(choices)
    correct = normalize_label(correct_label)
    if correct not in {choice["label"] for choice in normalized}:
        raise ValueError("LMB correct label is absent from choices")

    item_seed = int(
        hashlib.sha256(f"{seed}:{item_id}".encode()).hexdigest()[:16],
        16,
    )
    shuffled = [dict(choice) for choice in normalized]
    random.Random(item_seed).shuffle(shuffled)

    relabeled: list[dict[str, str]] = []
    shuffled_correct = ""
    for index, choice in enumerate(shuffled):
        new_label = _LABELS[index]
        relabeled.append({"label": new_label, "text": choice["text"]})
        if choice["label"] == correct:
            shuffled_correct = new_label
    return relabeled, shuffled_correct
