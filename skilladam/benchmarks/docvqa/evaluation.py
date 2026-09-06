"""DocVQA answer extraction and ANLS evaluation."""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any


DEFAULT_ANLS_THRESHOLD = 0.5
HARD_MATCH_THRESHOLD = 0.999
_ANSWER_PATTERN = re.compile(
    r"<answer>(.*?)</answer>",
    flags=re.IGNORECASE | re.DOTALL,
)


def extract_answer(text: str) -> str:
    """Return the last complete answer tag, or the last non-empty line."""

    if not isinstance(text, str):
        raise ValueError("DocVQA response must be text")
    matches = _ANSWER_PATTERN.findall(text)
    if matches:
        return matches[-1].strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else text.strip()


def anls_score(
    prediction: str,
    gold_answers: Sequence[str],
    *,
    threshold: float = DEFAULT_ANLS_THRESHOLD,
) -> float:
    """Return maximum Average Normalized Levenshtein Similarity."""

    if not isinstance(prediction, str):
        raise ValueError("DocVQA prediction must be text")
    answers = _gold_answers(gold_answers)
    return max(
        _score_pair(prediction, answer, threshold=threshold)
        for answer in answers
    )


def _score_pair(
    prediction: Any,
    target: Any,
    *,
    threshold: float,
) -> float:
    predicted_norm = _normalize_text(prediction)
    target_norm = _normalize_text(target)
    if not predicted_norm and not target_norm:
        return 1.0
    if not predicted_norm or not target_norm:
        return 0.0
    distance = _levenshtein_distance(predicted_norm, target_norm)
    normalized_distance = distance / max(
        len(predicted_norm),
        len(target_norm),
    )
    if normalized_distance >= threshold:
        return 0.0
    return 1.0 - normalized_distance


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def _levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    if len(left) > len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_character in enumerate(left, start=1):
        current = [row]
        for column, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[column - 1] + 1,
                    previous[column] + 1,
                    previous[column - 1]
                    + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _gold_answers(value: Any) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or any(
            not isinstance(answer, str) or not answer.strip()
            for answer in value
        )
    ):
        raise ValueError(
            "DocVQA gold answers must be a non-empty string sequence"
        )
    return tuple(answer.strip() for answer in value)
