"""Historical LMB answer-tag parsing and exact-match evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from skilladam.benchmarks.lmb.choices import (
    normalize_choices,
    normalize_label,
)


_ANSWER_RE = re.compile(
    r"<answer>(.*?)</answer>",
    flags=re.IGNORECASE | re.DOTALL,
)


def extract_answer(raw_result: Mapping[str, Any]) -> tuple[str, str]:
    """Extract explicit output or the final historical answer tag."""

    for key in ("answer", "predicted_answer"):
        if key in raw_result:
            answer = raw_result[key]
            if not isinstance(answer, str):
                raise ValueError("LMB answer must be text")
            return answer.strip(), str(raw_result.get("response", answer))

    response = raw_result.get("response")
    if not isinstance(response, str):
        raise ValueError(
            "LMB raw result must contain a text answer or response"
        )
    matches = _ANSWER_RE.findall(response)
    if matches:
        return matches[-1].strip(), response
    lines = [line.strip() for line in response.splitlines() if line.strip()]
    if lines:
        return lines[-1], response
    return response.strip(), response


def parse_choice_label(
    answer: str,
    choices: Sequence[Mapping[str, str]],
) -> tuple[str, str]:
    """Map a label, exact option text, or leading label to an MCQ choice."""

    if not isinstance(answer, str):
        raise ValueError("LMB answer must be text")
    normalized_choices = normalize_choices(choices)
    by_label = {choice["label"]: choice for choice in normalized_choices}

    normalized = normalize_label(answer)
    if normalized in by_label:
        return normalized, by_label[normalized]["text"]

    clean = answer.strip()
    for choice in normalized_choices:
        if clean.casefold() == choice["text"].casefold():
            return choice["label"], choice["text"]

    first_token = clean.split(maxsplit=1)[0] if clean else ""
    token_label = normalize_label(first_token)
    if token_label in by_label:
        return token_label, by_label[token_label]["text"]
    return normalized or clean.upper(), ""
