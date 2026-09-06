"""SearchQA exact-match and counter-based token-F1 evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import re


def normalize_answer(text: str) -> str:
    """Apply the historical SQuAD-style SearchQA normalization."""

    normalized = text.lower()
    normalized = re.sub(r"\b(a|an|the)\b", " ", normalized)
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def exact_match(prediction: str, gold_answers: Sequence[str]) -> float:
    normalized = normalize_answer(prediction)
    return float(
        any(normalize_answer(gold) == normalized for gold in gold_answers)
    )


def token_f1(prediction: str, gold: str) -> float:
    predicted_tokens = normalize_answer(prediction).split()
    gold_tokens = normalize_answer(gold).split()
    if not predicted_tokens or not gold_tokens:
        return float(predicted_tokens == gold_tokens)
    common = Counter(predicted_tokens) & Counter(gold_tokens)
    shared = sum(common.values())
    if shared == 0:
        return 0.0
    precision = shared / len(predicted_tokens)
    recall = shared / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def best_token_f1(
    prediction: str,
    gold_answers: Sequence[str],
) -> float:
    if not gold_answers:
        return 0.0
    return max(token_f1(prediction, gold) for gold in gold_answers)
