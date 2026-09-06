"""Explicit optional pricing for normalized usage records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from skilladam.types import UsageRecord


ONE_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class TokenPricing:
    """Per-million-token rates; reasoning is included in output tokens."""

    input_per_million: Decimal
    cached_input_per_million: Decimal
    cache_creation_input_per_million: Decimal
    output_per_million: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "input_per_million",
            "cached_input_per_million",
            "cache_creation_input_per_million",
            "output_per_million",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                value = Decimal(str(value))
                object.__setattr__(self, field_name, value)
            if not value.is_finite() or value < 0:
                raise ValueError(
                    f"{field_name} must be finite and non-negative"
                )


@dataclass(frozen=True)
class PriceBook:
    """Explicit model-to-pricing mapping loaded only when requested."""

    models: Mapping[str, TokenPricing]

    @classmethod
    def from_dict(cls, payload: object) -> "PriceBook":
        if not isinstance(payload, dict):
            raise ValueError("pricing file must contain a JSON object")
        models: dict[str, TokenPricing] = {}
        for model, raw_pricing in payload.items():
            if not isinstance(model, str) or not model.strip():
                raise ValueError("pricing model names must be non-empty")
            if not isinstance(raw_pricing, dict):
                raise ValueError(
                    f"pricing for model {model!r} must be an object"
                )
            models[model] = TokenPricing(
                input_per_million=Decimal(
                    str(raw_pricing["input_per_million"])
                ),
                cached_input_per_million=Decimal(
                    str(raw_pricing["cached_input_per_million"])
                ),
                cache_creation_input_per_million=Decimal(
                    str(
                        raw_pricing[
                            "cache_creation_input_per_million"
                        ]
                    )
                ),
                output_per_million=Decimal(
                    str(raw_pricing["output_per_million"])
                ),
            )
        return cls(models=models)

    @classmethod
    def load(cls, path: Path) -> "PriceBook":
        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8"))
        )


@dataclass(frozen=True)
class PromptTokenWeights:
    """Relative prompt-token weights for a documented billing policy."""

    base_input: Decimal
    cache_read_input: Decimal
    cache_creation_input: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "base_input",
            "cache_read_input",
            "cache_creation_input",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                value = Decimal(str(value))
                object.__setattr__(self, field_name, value)
            if not value.is_finite() or value < 0:
                raise ValueError(
                    f"{field_name} must be finite and non-negative"
                )


@dataclass(frozen=True)
class PromptWeightBook:
    """Explicit model-to-prompt-weight mapping."""

    models: Mapping[str, PromptTokenWeights]

    @classmethod
    def from_dict(cls, payload: object) -> "PromptWeightBook":
        if not isinstance(payload, dict):
            raise ValueError("prompt weights file must contain a JSON object")
        models: dict[str, PromptTokenWeights] = {}
        for model, raw_weights in payload.items():
            if not isinstance(model, str) or not model.strip():
                raise ValueError("prompt-weight model names must be non-empty")
            if not isinstance(raw_weights, dict):
                raise ValueError(
                    f"prompt weights for model {model!r} must be an object"
                )
            models[model] = PromptTokenWeights(
                base_input=Decimal(str(raw_weights["base_input"])),
                cache_read_input=Decimal(
                    str(raw_weights["cache_read_input"])
                ),
                cache_creation_input=Decimal(
                    str(raw_weights["cache_creation_input"])
                ),
            )
        return cls(models=models)

    @classmethod
    def load(cls, path: Path) -> "PromptWeightBook":
        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8"))
        )


def estimate_cost(
    usage: UsageRecord,
    pricing: TokenPricing | None,
) -> Decimal | None:
    """Estimate cost only when pricing and required counters are known."""

    if pricing is None:
        return None
    if usage.input_tokens is None or usage.output_tokens is None:
        return None
    cached = usage.cached_input_tokens
    if cached is None:
        if pricing.cached_input_per_million != pricing.input_per_million:
            return None
        cached = 0
    cache_creation = usage.cache_creation_input_tokens
    if cache_creation is None:
        if (
            pricing.cache_creation_input_per_million
            != pricing.input_per_million
        ):
            return None
        cache_creation = 0
    uncached = usage.input_tokens - cached - cache_creation
    amount = (
        Decimal(uncached) * pricing.input_per_million
        + Decimal(cached) * pricing.cached_input_per_million
        + Decimal(cache_creation)
        * pricing.cache_creation_input_per_million
        + Decimal(usage.output_tokens) * pricing.output_per_million
    )
    return amount / ONE_MILLION


def prompt_billing_equivalent_tokens(
    usage: UsageRecord,
    weights: PromptTokenWeights | None,
) -> Decimal | None:
    """Apply explicit prompt weights without treating the result as USD."""

    if weights is None or usage.uncached_input_tokens is None:
        return None
    return (
        Decimal(usage.uncached_input_tokens) * weights.base_input
        + Decimal(usage.cached_input_tokens)
        * weights.cache_read_input
        + Decimal(usage.cache_creation_input_tokens)
        * weights.cache_creation_input
    )
