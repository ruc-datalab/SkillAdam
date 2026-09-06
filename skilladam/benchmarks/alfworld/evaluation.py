"""ALFWorld action extraction and synthetic fixture evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from skilladam.benchmarks.alfworld.rollout import (
    extract_action,
    extract_reasoning,
    normalize_action,
)


def extract_synthetic_action(
    raw_result: Mapping[str, Any],
) -> tuple[str, str, str]:
    """Return normalized action, reasoning, and raw response."""

    for key in ("final_answer", "action", "predicted_action"):
        if key in raw_result:
            value = raw_result[key]
            if not isinstance(value, str):
                raise ValueError("ALFWorld answer must be text")
            response = raw_result.get("response", value)
            if not isinstance(response, str):
                raise ValueError("ALFWorld response must be text")
            reasoning = (
                extract_reasoning(response)
                if "<think>" in response.casefold()
                else ""
            )
            return normalize_action(value), reasoning, response

    response = raw_result.get("response")
    if not isinstance(response, str):
        raise ValueError(
            "ALFWorld raw result must contain a text answer or response"
        )
    return (
        extract_action(response),
        extract_reasoning(response),
        response,
    )


def action_exact_match(prediction: str, reference: str) -> float:
    """Return binary normalized action equality."""

    return float(
        normalize_action(prediction) == normalize_action(reference)
    )
