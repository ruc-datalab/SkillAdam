"""Explicit backend configuration, loading, and metadata validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from types import MappingProxyType, ModuleType
from typing import Any, Callable

from skilladam.execution.contracts import (
    BackendInit,
    ExecutionBackend,
)


_MODULE_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)
_ATTRIBUTE_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SENSITIVE_KEY_PARTS = (
    "apikey",
    "authorization",
    "baseurl",
    "credential",
    "endpoint",
    "password",
    "secret",
    "token",
)
_SAFE_PUBLIC_TOKEN_KEYS = frozenset(
    {
        "maxinputtokens",
        "maxoutputtokens",
    }
)
_EMPTY_CONFIG_BYTES = b"{}\n"


class BackendError(RuntimeError):
    """Base error for an invalid execution backend."""


class BackendConfigurationError(BackendError):
    """Raised when backend configuration or fixture data is invalid."""


class BackendLoadError(BackendError):
    """Raised when an explicit backend factory cannot be loaded."""


class BackendProtocolError(BackendError):
    """Raised when a backend violates the public execution contract."""


@dataclass(frozen=True, slots=True)
class BackendConfig:
    """Exact config hash plus immutable parsed values."""

    values: Mapping[str, Any]
    sha256: str


@dataclass(frozen=True, slots=True)
class LoadedBackend:
    """One validated backend and its safe public identity."""

    backend: ExecutionBackend
    spec: str
    config_sha256: str
    public_metadata: Mapping[str, Any]


def load_backend_config(path: Path | None) -> BackendConfig:
    """Load one optional JSON object without exposing its contents."""

    if path is None:
        raw = _EMPTY_CONFIG_BYTES
    else:
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            raise BackendConfigurationError(
                f"could not read backend config: {exc}"
            ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BackendConfigurationError(
            "backend config must be UTF-8"
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BackendConfigurationError(
            f"backend config must contain valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise BackendConfigurationError(
            "backend config must contain a JSON object"
        )
    return BackendConfig(
        values=_freeze_json_mapping(payload),
        sha256=sha256(raw).hexdigest(),
    )


def load_execution_backend(
    spec: str,
    *,
    init: BackendInit,
    importer: Callable[[str], ModuleType] = import_module,
) -> LoadedBackend:
    """Instantiate and validate one explicitly named execution backend."""

    canonical = _backend_spec(spec)
    if canonical == "fixture":
        from skilladam.execution.fixture_backend import FixtureBackend

        factory: Callable[[BackendInit], Any] = FixtureBackend
    else:
        module_name, attribute_name = canonical.split(":", 1)
        try:
            module = importer(module_name)
        except Exception as exc:
            raise BackendLoadError(
                f"could not import backend module {module_name!r}: {exc}"
            ) from exc
        factory = getattr(module, attribute_name, None)
        if not callable(factory):
            raise BackendLoadError(
                f"backend factory {canonical!r} is not callable"
            )

    try:
        backend = factory(init)
    except BackendError:
        raise
    except Exception as exc:
        raise BackendLoadError(
            f"backend factory {canonical!r} failed: {exc}"
        ) from exc
    if not isinstance(backend, ExecutionBackend):
        raise BackendProtocolError(
            f"backend {canonical!r} does not implement ExecutionBackend"
        )
    try:
        raw_metadata = backend.public_metadata()
    except Exception as exc:
        raise BackendProtocolError(
            f"backend {canonical!r} public_metadata failed: {exc}"
        ) from exc
    metadata = validate_public_metadata(raw_metadata)
    config_hash = init.config_sha256 or _canonical_config_hash(init.config)
    return LoadedBackend(
        backend=backend,
        spec=canonical,
        config_sha256=config_hash,
        public_metadata=metadata,
    )


def validate_public_metadata(value: object) -> Mapping[str, Any]:
    """Validate and freeze JSON-safe metadata without sensitive fields."""

    if not isinstance(value, Mapping):
        raise BackendProtocolError(
            "backend public metadata must be a JSON object"
        )
    result = _public_value(value, path="public_metadata")
    assert isinstance(result, Mapping)
    return result


def _backend_spec(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BackendLoadError("backend spec must be non-empty")
    spec = value.strip()
    if spec == "fixture":
        return spec
    if spec.count(":") != 1:
        raise BackendLoadError(
            "backend spec must use module:factory syntax"
        )
    module_name, attribute_name = spec.split(":", 1)
    if not _MODULE_PATTERN.fullmatch(module_name):
        raise BackendLoadError(
            "backend module must be an absolute dotted Python name"
        )
    if not _ATTRIBUTE_PATTERN.fullmatch(attribute_name):
        raise BackendLoadError(
            "backend factory must be one Python attribute name"
        )
    return spec


def _canonical_config_hash(value: Mapping[str, Any]) -> str:
    raw = (
        json.dumps(
            _thaw(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _freeze_json_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze_json(value, path="config")
    assert isinstance(frozen, Mapping)
    return frozen


def _freeze_json(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BackendConfigurationError(
                    f"{path} keys must be strings"
                )
            result[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(result)
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise BackendConfigurationError(
        f"{path} must contain only finite JSON values"
    )


def _public_value(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise BackendProtocolError(
                    f"{path} keys must be non-empty strings"
                )
            normalized = re.sub(r"[^a-z0-9]+", "", key.casefold())
            if (
                normalized not in _SAFE_PUBLIC_TOKEN_KEYS
                and any(
                    part in normalized
                    for part in _SENSITIVE_KEY_PARTS
                )
            ):
                raise BackendProtocolError(
                    f"{path} contains sensitive key {key!r}"
                )
            result[key] = _public_value(item, path=f"{path}.{key}")
        return MappingProxyType(result)
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return tuple(
            _public_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, str):
        if (
            PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).is_absolute()
        ):
            raise BackendProtocolError(
                f"{path} contains an absolute path"
            )
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BackendProtocolError(
                f"{path} numbers must be finite"
            )
        return value
    raise BackendProtocolError(
        f"{path} must contain only JSON-compatible values"
    )


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


__all__ = [
    "BackendConfig",
    "BackendConfigurationError",
    "BackendError",
    "BackendLoadError",
    "BackendProtocolError",
    "LoadedBackend",
    "load_backend_config",
    "load_execution_backend",
    "validate_public_metadata",
]
