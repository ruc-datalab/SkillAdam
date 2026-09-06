"""Historical OfficeQA exact-match and counter-based token-F1 metrics."""

from __future__ import annotations

from collections import Counter
import re
import string


_NUMERIC_CHARS = set("0123456789.-")
_UNIT_WORDS = re.compile(
    r"\b(million|millions|billion|billions|dollars|dollar|nominal)\b"
)


def normalize_answer(text: str) -> str:
    """Apply the normalization used by the historical OfficeQA evaluator."""

    if not isinstance(text, str):
        raise ValueError("OfficeQA answer must be text")
    normalized = text.lower().strip().replace(",", "")
    normalized = "".join(
        character
        for character in normalized
        if (
            character not in string.punctuation
            or character in _NUMERIC_CHARS
            or character == "%"
        )
    )
    normalized = _UNIT_WORDS.sub(" ", normalized)
    return " ".join(normalized.split())


def exact_match(prediction: str, gold: str) -> float:
    """Return one when normalized prediction and reference are identical."""

    return float(normalize_answer(prediction) == normalize_answer(gold))


def token_f1(prediction: str, gold: str) -> float:
    """Return multiset token F1 after OfficeQA normalization."""

    predicted_tokens = normalize_answer(prediction).split()
    gold_tokens = normalize_answer(gold).split()
    if not predicted_tokens or not gold_tokens:
        return float(predicted_tokens == gold_tokens)
    shared = sum((Counter(predicted_tokens) & Counter(gold_tokens)).values())
    if shared == 0:
        return 0.0
    precision = shared / len(predicted_tokens)
    recall = shared / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)
