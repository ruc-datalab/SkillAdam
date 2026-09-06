"""Provider-neutral token usage normalization."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from skilladam.types import UsageRecord


_MISSING = object()


def normalize_usage(
    raw_usage: Any,
    *,
    requests: int | None = None,
) -> UsageRecord:
    """Normalize common provider usage shapes without inventing zeros.

    ``requests`` counts attempted model calls. A missing usage object therefore
    retains unknown token fields while still counting the request.
    """

    request_count = _request_count(raw_usage, requests)
    if raw_usage is None:
        return UsageRecord(requests=request_count)
    if isinstance(raw_usage, UsageRecord):
        return UsageRecord(
            input_tokens=raw_usage.input_tokens,
            cached_input_tokens=raw_usage.cached_input_tokens,
            cache_creation_input_tokens=(
                raw_usage.cache_creation_input_tokens
            ),
            output_tokens=raw_usage.output_tokens,
            reasoning_tokens=raw_usage.reasoning_tokens,
            total_tokens=raw_usage.total_tokens,
            requests=request_count,
            cost_usd=raw_usage.cost_usd,
            metadata=raw_usage.metadata,
        )

    for field_name in ("usage", "usage_metadata"):
        nested_usage = _lookup(raw_usage, field_name)
        if nested_usage is not _MISSING and nested_usage is not None:
            raw_usage = nested_usage
            break
    if requests is None:
        nested_requests = _first_count(
            raw_usage,
            "requests",
            "num_calls",
        )
        if nested_requests is not None:
            request_count = nested_requests

    input_tokens = _first_count(
        raw_usage,
        "input_tokens",
        "prompt_tokens",
        "prompt_token_count",
    )
    reasoning_tokens = _first_count(
        raw_usage,
        "reasoning_tokens",
        "thinking_tokens",
        "thoughts_token_count",
    )
    completion_details = _lookup(
        raw_usage,
        "completion_tokens_details",
    )
    if reasoning_tokens is None and completion_details is not _MISSING:
        reasoning_tokens = _first_count(
            completion_details,
            "reasoning_tokens",
        )

    output_tokens = _first_count(
        raw_usage,
        "output_tokens",
        "completion_tokens",
    )
    if output_tokens is None:
        candidate_tokens = _first_count(
            raw_usage,
            "candidates_token_count",
        )
        if candidate_tokens is not None:
            output_tokens = candidate_tokens + (reasoning_tokens or 0)

    total_tokens = _first_count(
        raw_usage,
        "total_tokens",
        "total_token_count",
    )

    prompt_details = _lookup(raw_usage, "prompt_tokens_details")
    cached_input_tokens = _first_count(
        raw_usage,
        "cached_input_tokens",
        "cached_content_token_count",
    )
    cache_creation_input_tokens = _first_count(
        raw_usage,
        "cache_creation_input_tokens",
    )
    if cached_input_tokens is None and prompt_details is not _MISSING:
        cached_input_tokens = _first_count(
            prompt_details,
            "cached_tokens",
            "cache_read_tokens",
            "cache_read_input_tokens",
        )
    if (
        cache_creation_input_tokens is None
        and prompt_details is not _MISSING
    ):
        cache_creation_input_tokens = _first_count(
            prompt_details,
            "cache_creation_tokens",
            "cache_creation_input_tokens",
        )

    native_cache_read = _lookup(raw_usage, "cache_read_input_tokens")
    native_cache_creation = _lookup(
        raw_usage,
        "cache_creation_input_tokens",
    )
    uses_anthropic_cache_shape = native_cache_read is not _MISSING
    if uses_anthropic_cache_shape:
        base_input_tokens = input_tokens
        cached_input_tokens = _first_count(
            raw_usage,
            "cache_read_input_tokens",
        )
        cache_creation_input_tokens = _first_count(
            raw_usage,
            "cache_creation_input_tokens",
        )
        if (
            base_input_tokens is not None
            and cached_input_tokens is not None
            and cache_creation_input_tokens is not None
        ):
            input_tokens = (
                base_input_tokens
                + cached_input_tokens
                + cache_creation_input_tokens
            )
        else:
            input_tokens = None

    metadata: dict[str, Any] = {}
    if (
        total_tokens is not None
        and input_tokens is not None
        and output_tokens is not None
        and total_tokens != input_tokens + output_tokens
    ):
        cache_components = (
            cached_input_tokens,
            cache_creation_input_tokens,
        )
        known_cache_total = sum(
            value for value in cache_components if value is not None
        )
        expanded_input_tokens = input_tokens + known_cache_total
        if (
            known_cache_total > 0
            and total_tokens == expanded_input_tokens + output_tokens
        ):
            metadata = {
                "input_token_accounting": "cache_components_exclusive",
                "provider_reported_input_tokens": input_tokens,
            }
            input_tokens = expanded_input_tokens
        elif (
            known_cache_total > 0
            and total_tokens + known_cache_total
            == input_tokens + output_tokens
        ):
            metadata = {
                "total_token_accounting": (
                    "cache_components_excluded"
                ),
                "provider_reported_total_tokens": total_tokens,
            }
            total_tokens = input_tokens + output_tokens

    if (
        total_tokens is None
        and input_tokens is not None
        and output_tokens is not None
    ):
        total_tokens = input_tokens + output_tokens

    try:
        return UsageRecord(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
            requests=request_count,
            metadata=metadata,
        )
    except ValueError as exc:
        counters = {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cache_creation_input_tokens": (
                cache_creation_input_tokens
            ),
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
        }
        diagnostic = ", ".join(
            f"{field_name}={value!r}"
            for field_name, value in counters.items()
        )
        raise ValueError(
            f"{exc}; normalized usage counters: {diagnostic}"
        ) from None


def _request_count(raw_usage: Any, explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    if isinstance(raw_usage, UsageRecord):
        return raw_usage.requests
    request_count = _first_count(raw_usage, "requests", "num_calls")
    return request_count if request_count is not None else 1


def _first_count(value: Any, *field_names: str) -> int | None:
    for field_name in field_names:
        candidate = _lookup(value, field_name)
        if candidate is _MISSING or candidate is None:
            continue
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            raise ValueError(
                f"usage field {field_name!r} must be an integer or null"
            )
        if candidate < 0:
            raise ValueError(
                f"usage field {field_name!r} must be non-negative"
            )
        return candidate
    return None


def _lookup(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name, _MISSING)
    return getattr(value, field_name, _MISSING)
