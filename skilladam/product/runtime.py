"""Non-sensitive runtime configuration for product model providers."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from skilladam.product.model import (
    FixtureProductModel,
    OpenAICompatibleProductModel,
    ProductModel,
)
from skilladam.product.cli_model import (
    DEFAULT_PLATFORM_CLI_MODELS,
    DEFAULT_PLATFORM_CLI_TIMEOUT_SECONDS,
    SUPPORTED_PLATFORMS,
    PlatformCliProductModel,
)


def build_runtime_config(
    *,
    provider: str,
    fixture: Path | None = None,
    model: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    base_url_env: str = "OPENAI_BASE_URL",
    wire_api: str = "completions",
    reasoning_effort: str | None = None,
    platform: str | None = None,
    executable: str | None = None,
    timeout_seconds: int = DEFAULT_PLATFORM_CLI_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if provider == "fixture":
        if fixture is None:
            raise ValueError("fixture provider requires a fixture path")
        return {
            "provider": provider,
            "fixture": str(Path(fixture).resolve()),
        }
    if provider == "openai-compatible":
        if not model:
            raise ValueError("openai-compatible provider requires a model")
        runtime = {
            "provider": provider,
            "model": model,
            "api_key_env": api_key_env,
            "base_url_env": base_url_env,
        }
        if wire_api != "completions":
            runtime["wire_api"] = wire_api
        if reasoning_effort:
            runtime["reasoning_effort"] = reasoning_effort
        return runtime
    if provider == "platform-cli":
        selected_platform = (
            platform or os.environ.get("SKILLADAM_PLATFORM", "")
        ).strip()
        if selected_platform not in SUPPORTED_PLATFORMS:
            raise ValueError(
                "platform-cli provider requires platform to be one of "
                + ", ".join(sorted(SUPPORTED_PLATFORMS))
            )
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, int
        ):
            raise ValueError("timeout_seconds must be an integer")
        if not 10 <= timeout_seconds <= 1800:
            raise ValueError("timeout_seconds must be between 10 and 1800")
        selected_model = model or None
        if selected_model is None:
            selected_model = DEFAULT_PLATFORM_CLI_MODELS.get(
                selected_platform
            )
        runtime = {
            "provider": provider,
            "platform": selected_platform,
            "model": selected_model,
            "executable": executable or None,
            "timeout_seconds": timeout_seconds,
        }
        if reasoning_effort:
            runtime["reasoning_effort"] = reasoning_effort
        return runtime
    raise ValueError(f"unsupported product provider {provider!r}")


def default_runtime_config() -> dict[str, Any] | None:
    """Read installer-supplied platform identity without guessing for non-plugin calls."""

    provider = os.environ.get("SKILLADAM_PROVIDER", "").strip()
    platform = os.environ.get("SKILLADAM_PLATFORM", "").strip()
    if not provider:
        if not platform:
            return None
        provider = "platform-cli"
    common = {
        "model": os.environ.get("SKILLADAM_MODEL", "").strip() or None,
        "reasoning_effort": (
            os.environ.get("SKILLADAM_REASONING_EFFORT", "").strip() or None
        ),
    }
    if provider == "openai-compatible":
        return build_runtime_config(
            provider=provider,
            api_key_env=(
                os.environ.get("SKILLADAM_API_KEY_ENV", "").strip()
                or "OPENAI_API_KEY"
            ),
            base_url_env=(
                os.environ.get("SKILLADAM_BASE_URL_ENV", "").strip()
                or "OPENAI_BASE_URL"
            ),
            wire_api=(
                os.environ.get("SKILLADAM_WIRE_API", "").strip()
                or "completions"
            ),
            **common,
        )
    return build_runtime_config(
        provider=provider,
        platform=platform,
        **common,
    )


def model_from_runtime(runtime: Mapping[str, Any]) -> ProductModel:
    provider = runtime.get("provider")
    if provider == "fixture":
        return FixtureProductModel.from_path(Path(str(runtime["fixture"])))
    if provider == "openai-compatible":
        return OpenAICompatibleProductModel(
            model=str(runtime["model"]),
            api_key_env=str(runtime.get("api_key_env", "OPENAI_API_KEY")),
            base_url_env=str(runtime.get("base_url_env", "OPENAI_BASE_URL")),
            wire_api=str(runtime.get("wire_api", "completions")),
            reasoning_effort=_optional_runtime_text(
                runtime.get("reasoning_effort")
            ),
        )
    if provider == "platform-cli":
        timeout_seconds = runtime.get(
            "timeout_seconds",
            DEFAULT_PLATFORM_CLI_TIMEOUT_SECONDS,
        )
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, int
        ):
            raise ValueError("timeout_seconds must be an integer")
        return PlatformCliProductModel(
            platform=str(runtime.get("platform", "")),
            model=_optional_runtime_text(runtime.get("model")),
            reasoning_effort=_optional_runtime_text(
                runtime.get("reasoning_effort")
            ),
            executable=_optional_runtime_text(runtime.get("executable")),
            timeout_seconds=timeout_seconds,
        )
    raise ValueError(f"unsupported product provider {provider!r}")


def load_task_manifest(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("task manifest file must contain a JSON object")
    return payload


def _optional_runtime_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = [
    "build_runtime_config",
    "default_runtime_config",
    "load_task_manifest",
    "model_from_runtime",
]
