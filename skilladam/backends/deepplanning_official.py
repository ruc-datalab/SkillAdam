"""Pinned external DeepPlanning bridge plus shared optimizer transport."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Any

from skilladam.backends.openai_compatible import (
    OpenAICompatibleBackend,
    OpenAICompatibleConfig,
    create_generation_transport,
)
from skilladam.benchmarks.deepplanning.runtime import (
    BRIDGE_PROTOCOL_VERSION,
    DeepPlanningRuntimePreflight,
    PAPER_PROFILE,
    preflight_runtime,
)
from skilladam.execution.backend import (
    BackendConfigurationError,
    BackendLoadError,
    BackendProtocolError,
)
from skilladam.execution.contracts import (
    BackendInit,
    BatchExecution,
    GenerationRequest,
    GenerationResult,
    RawRollout,
)
from skilladam.types import RolloutRequest, UsageRecord
from skilladam.usage import normalize_usage


_PACKAGE_IMPORT_ROOT = Path(__file__).resolve().parents[2]
_ENV_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
_SAFE_COMPONENT_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_GENERATION_CONFIG_KEYS = frozenset(
    {
        "api_key_env",
        "base_url_env",
        "timeout_seconds",
        "max_retries",
        "max_completion_tokens",
        "token_limit_field",
        "temperature",
        "reasoning_mode",
        "enable_prompt_cache",
        "prompt_cache_min_tokens",
        "cache_session_prefix",
        "max_total_requests",
        "max_total_input_tokens",
        "max_total_output_tokens",
    }
)
_CONFIG_KEYS = _GENERATION_CONFIG_KEYS | frozenset(
    {
        "runtime_root_env",
        "bridge_python_env",
        "bridge_timeout_seconds",
        "bridge_protocol_version",
        "require_git_revision",
        "max_bridge_output_bytes",
        "conversion_model",
        "conversion_max_tokens",
        "conversion_token_limit_field",
        "conversion_temperature",
        "conversion_max_attempts",
    }
)
_RESPONSE_KEYS = frozenset(
    {
        "protocol_version",
        "case_id",
        "output",
        "messages",
        "metrics",
        "usage",
    }
)
_ARTIFACT_ROOT_ENV = "SKILLADAM_DEEPPLANNING_ARTIFACT_ROOT"
_RUNTIME_ROOT_ENV = "SKILLADAM_DEEPPLANNING_RUNTIME_ROOT"
_STDERR_LIMIT = 16_384


@dataclass(frozen=True, slots=True)
class DeepPlanningOfficialConfig:
    """Strict non-secret backend settings."""

    generation: OpenAICompatibleConfig
    runtime_root_env: str = "SKILLADAM_DEEPPLANNING_RUNTIME"
    bridge_python_env: str = "SKILLADAM_DEEPPLANNING_PYTHON"
    bridge_timeout_seconds: float = 7200.0
    bridge_protocol_version: int = BRIDGE_PROTOCOL_VERSION
    require_git_revision: bool = True
    max_bridge_output_bytes: int = 64 * 1024 * 1024
    conversion_model: str = "gpt-4.1"
    conversion_max_tokens: int = 10_240
    conversion_token_limit_field: str = "max_tokens"
    conversion_temperature: float = 0.0
    conversion_max_attempts: int = 30

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> DeepPlanningOfficialConfig:
        """Parse one exact JSON-derived configuration."""

        unknown = sorted(set(value) - _CONFIG_KEYS)
        if unknown:
            raise BackendConfigurationError(
                "DeepPlanning official config has unknown keys: "
                f"{unknown!r}"
            )
        protocol_version = value.get(
            "bridge_protocol_version",
            BRIDGE_PROTOCOL_VERSION,
        )
        if protocol_version != BRIDGE_PROTOCOL_VERSION:
            raise BackendConfigurationError(
                "DeepPlanning bridge_protocol_version is unsupported"
            )
        require_git_revision = value.get(
            "require_git_revision",
            True,
        )
        if not isinstance(require_git_revision, bool):
            raise BackendConfigurationError(
                "require_git_revision must be boolean"
            )
        generation_values = {
            key: item
            for key, item in value.items()
            if key in _GENERATION_CONFIG_KEYS
        }
        return cls(
            generation=OpenAICompatibleConfig.from_mapping(
                generation_values
            ),
            runtime_root_env=_environment_name(
                value.get(
                    "runtime_root_env",
                    "SKILLADAM_DEEPPLANNING_RUNTIME",
                ),
                "runtime_root_env",
            ),
            bridge_python_env=_environment_name(
                value.get(
                    "bridge_python_env",
                    "SKILLADAM_DEEPPLANNING_PYTHON",
                ),
                "bridge_python_env",
            ),
            bridge_timeout_seconds=_bounded_number(
                value.get("bridge_timeout_seconds", 7200.0),
                "bridge_timeout_seconds",
                minimum=1.0,
                maximum=86_400.0,
            ),
            bridge_protocol_version=BRIDGE_PROTOCOL_VERSION,
            require_git_revision=require_git_revision,
            max_bridge_output_bytes=_bounded_integer(
                value.get(
                    "max_bridge_output_bytes",
                    64 * 1024 * 1024,
                ),
                "max_bridge_output_bytes",
                minimum=1024,
                maximum=512 * 1024 * 1024,
            ),
            conversion_model=_nonempty_text(
                value.get("conversion_model", "gpt-4.1"),
                "conversion_model",
            ),
            conversion_max_tokens=_bounded_integer(
                value.get("conversion_max_tokens", 10_240),
                "conversion_max_tokens",
                minimum=1,
                maximum=1_000_000,
            ),
            conversion_token_limit_field=_token_limit_field(
                value.get(
                    "conversion_token_limit_field",
                    "max_tokens",
                )
            ),
            conversion_temperature=_bounded_number(
                value.get("conversion_temperature", 0.0),
                "conversion_temperature",
                minimum=0.0,
                maximum=2.0,
            ),
            conversion_max_attempts=_bounded_integer(
                value.get("conversion_max_attempts", 30),
                "conversion_max_attempts",
                minimum=1,
                maximum=100,
            ),
        )


class DeepPlanningOfficialBackend:
    """Execute exact one-case bridge processes and optimizer generations."""

    def __init__(
        self,
        *,
        init: BackendInit,
        config: DeepPlanningOfficialConfig,
        runtime_root: Path,
        bridge_python: Path,
        preflight: DeepPlanningRuntimePreflight,
        generation_transport: OpenAICompatibleBackend,
        environ: Mapping[str, str],
        preflight_reader: Callable[
            [Path], DeepPlanningRuntimePreflight
        ],
        subprocess_runner: Callable[..., Any],
    ) -> None:
        self._init = init
        self._config = config
        self._runtime_root = runtime_root
        self._bridge_python = bridge_python
        self._preflight = preflight
        self._generation_transport = generation_transport
        self._environ = dict(environ)
        self._preflight_reader = preflight_reader
        self._subprocess_runner = subprocess_runner
        self._batch_lock = threading.Lock()
        self._batch_number = 0

    def execute_batch(
        self,
        execution: BatchExecution,
    ) -> tuple[RawRollout, ...]:
        """Run an ordered batch through isolated one-case processes."""

        if execution.context != self._init.context:
            raise BackendProtocolError(
                "batch context does not match backend initialization"
            )
        case_ids = tuple(
            request.case.case_id for request in execution.requests
        )
        if len(set(case_ids)) != len(case_ids):
            raise BackendProtocolError(
                "batch rollout case IDs must be unique"
            )
        current_preflight = self._preflight_reader(
            self._runtime_root
        )
        if (
            current_preflight.identity_sha256
            != self._preflight.identity_sha256
            or current_preflight.bridge_module
            != self._preflight.bridge_module
        ):
            raise BackendConfigurationError(
                "DeepPlanning runtime identity changed after "
                "backend initialization"
            )
        if (
            self._config.require_git_revision
            and not current_preflight.git_revision_verified
        ):
            raise BackendConfigurationError(
                "DeepPlanning runtime requires a verifiable pinned "
                "Qwen-Agent checkout"
            )

        batch_number = self._next_batch_number()
        artifact_root = self._artifact_root()
        worker_count = min(
            execution.context.workers,
            len(execution.requests),
        )
        executor = ThreadPoolExecutor(max_workers=worker_count)
        futures: dict[Future[RawRollout], int] = {}
        ordered: list[RawRollout | None] = [
            None
        ] * len(execution.requests)
        try:
            for index, request in enumerate(execution.requests):
                future = executor.submit(
                    self._execute_one,
                    request,
                    execution=execution,
                    batch_number=batch_number,
                    artifact_root=artifact_root,
                )
                futures[future] = index
            for future in as_completed(futures):
                ordered[futures[future]] = future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

        completed = tuple(
            item for item in ordered if item is not None
        )
        if tuple(item.case_id for item in completed) != case_ids:
            raise BackendProtocolError(
                "DeepPlanning bridge did not preserve exact case order"
            )
        return completed

    def generate(
        self,
        request: GenerationRequest,
    ) -> GenerationResult:
        """Use the shared OpenAI-compatible optimizer transport."""

        return self._generation_transport.generate(request)

    def public_metadata(self) -> Mapping[str, Any]:
        """Return a path-free and secret-free runtime identity."""

        metadata: dict[str, Any] = {
            "kind": "deepplanning_official",
            "network": True,
            "api_surface": "chat_completions_and_external_bridge",
            "supported_benchmarks": ("deepplanning",),
            "config_schema_version": 1,
            "profile": PAPER_PROFILE,
            "qwen_agent_revision": (
                self._preflight.qwen_agent_revision
            ),
            "dataset_revision": self._preflight.dataset_revision,
            "bridge_module": self._preflight.bridge_module,
            "bridge_protocol_version": (
                self._preflight.bridge_protocol_version
            ),
            "case_count": self._preflight.case_count,
            "git_revision_verified": (
                self._preflight.git_revision_verified
            ),
            "rollout_usage_required": True,
            "conversion_model": self._config.conversion_model,
            "prompt_cache": (
                self._config.generation.enable_prompt_cache
            ),
        }
        budget = {
            "max_requests": (
                self._config.generation.max_total_requests
            ),
            "max_input_tokens": (
                self._config.generation.max_total_input_tokens
            ),
            "max_output_tokens": (
                self._config.generation.max_total_output_tokens
            ),
        }
        if any(value is not None for value in budget.values()):
            metadata["safety_budget"] = budget
        return metadata

    def _execute_one(
        self,
        request: RolloutRequest,
        *,
        execution: BatchExecution,
        batch_number: int,
        artifact_root: Path,
    ) -> RawRollout:
        identity = request.metadata.get("task_identity")
        if not isinstance(identity, Mapping):
            raise BackendProtocolError(
                "DeepPlanning request is missing task_identity"
            )
        case_id = request.case.case_id
        _safe_component(case_id, "case_id")
        stage = _safe_component(execution.stage, "stage")
        relative_artifact = Path(
            f"batch_{batch_number:06d}",
            stage,
            case_id,
        )
        artifact_dir = artifact_root / relative_artifact
        if artifact_dir.exists():
            raise BackendProtocolError(
                "DeepPlanning bridge artifact directory already exists"
            )
        artifact_dir.mkdir(parents=True)

        payload = {
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "case_id": case_id,
            "slice": identity.get("slice"),
            "case_number": request.metadata.get("case_number"),
            "method": request.method,
            "split": request.split,
            "stage": execution.stage,
            "model": execution.context.model,
            "reasoning_effort": execution.context.reasoning_effort,
            "seed": request.seed,
            "skill": request.skill,
            "max_interactions": request.metadata.get(
                "max_interactions"
            ),
            "prompt_profile": request.metadata.get("prompt_profile"),
            "evaluator_profile": request.metadata.get(
                "evaluator_profile"
            ),
            "provider_env": {
                "api_key": self._config.generation.api_key_env,
                "base_url": self._config.generation.base_url_env,
            },
            "provider_config": {
                "timeout_seconds": (
                    self._config.generation.timeout_seconds
                ),
                "max_retries": self._config.generation.max_retries,
                "max_completion_tokens": (
                    self._config.generation.max_completion_tokens
                ),
                "token_limit_field": (
                    self._config.generation.token_limit_field
                ),
                "temperature": self._config.generation.temperature,
                "reasoning_mode": (
                    self._config.generation.reasoning_mode
                ),
                "enable_prompt_cache": (
                    self._config.generation.enable_prompt_cache
                ),
                "prompt_cache_min_tokens": (
                    self._config.generation.prompt_cache_min_tokens
                ),
                "cache_session_id": self._bridge_cache_session_id(
                    request,
                    stage=stage,
                ),
                "max_total_requests": (
                    self._config.generation.max_total_requests
                ),
                "max_total_input_tokens": (
                    self._config.generation.max_total_input_tokens
                ),
                "max_total_output_tokens": (
                    self._config.generation.max_total_output_tokens
                ),
            },
            "conversion_model": self._config.conversion_model,
            "conversion_max_tokens": (
                self._config.conversion_max_tokens
            ),
            "conversion_token_limit_field": (
                self._config.conversion_token_limit_field
            ),
            "conversion_temperature": (
                self._config.conversion_temperature
            ),
            "conversion_max_attempts": (
                self._config.conversion_max_attempts
            ),
            "artifact_dir": relative_artifact.as_posix(),
        }
        child_environment = self._child_environment(artifact_root)
        command = (
            str(self._bridge_python),
            "-m",
            self._preflight.bridge_module,
        )
        try:
            completed = self._subprocess_runner(
                command,
                input=(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                ),
                capture_output=True,
                text=True,
                timeout=self._config.bridge_timeout_seconds,
                cwd=self._runtime_root,
                env=child_environment,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise BackendProtocolError(
                "DeepPlanning bridge timed out"
            ) from None
        except OSError:
            raise BackendLoadError(
                "DeepPlanning bridge process could not start"
            ) from None
        except Exception:
            raise BackendProtocolError(
                "DeepPlanning bridge process failed"
            ) from None

        stdout = completed.stdout
        stderr = completed.stderr
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            raise BackendProtocolError(
                "DeepPlanning bridge must use UTF-8 text streams"
            )
        if stderr:
            self._write_redacted_stderr(
                artifact_dir / "bridge.stderr.log",
                stderr,
                artifact_root=artifact_root,
            )
        if completed.returncode != 0:
            raise BackendProtocolError(
                "DeepPlanning bridge exited unsuccessfully"
            )
        if (
            len(stdout.encode("utf-8"))
            > self._config.max_bridge_output_bytes
        ):
            raise BackendProtocolError(
                "DeepPlanning bridge response exceeds the configured limit"
            )
        return RawRollout(
            case_id=case_id,
            result=_parse_bridge_response(stdout, case_id=case_id),
        )

    def _bridge_cache_session_id(
        self,
        request: RolloutRequest,
        *,
        stage: str,
    ) -> str:
        generation = self._config.generation
        if not generation.enable_prompt_cache:
            return ""
        identity = request.metadata.get("task_identity")
        assert isinstance(identity, Mapping)
        scope = _safe_component(identity.get("slice"), "slice")
        return ":".join(
            (
                generation.cache_session_prefix,
                "rollout",
                scope,
                request.method,
                stage,
            )
        )

    def _child_environment(
        self,
        artifact_root: Path,
    ) -> dict[str, str]:
        generation = self._config.generation
        return {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(_PACKAGE_IMPORT_ROOT),
            generation.api_key_env: _required_environment_value(
                self._environ,
                generation.api_key_env,
            ),
            generation.base_url_env: _required_environment_value(
                self._environ,
                generation.base_url_env,
            ),
            self._config.runtime_root_env: str(self._runtime_root),
            _RUNTIME_ROOT_ENV: str(self._runtime_root),
            _ARTIFACT_ROOT_ENV: str(artifact_root),
        }

    def _artifact_root(self) -> Path:
        output_root = self._init.output_dir
        if output_root.is_symlink():
            raise BackendConfigurationError(
                "DeepPlanning output root must not be a symlink"
            )
        if not output_root.exists():
            if not output_root.parent.is_dir():
                raise BackendConfigurationError(
                    "DeepPlanning output parent must exist"
                )
            output_root.mkdir()
        if not output_root.is_dir():
            raise BackendConfigurationError(
                "DeepPlanning output root must be a directory"
            )
        resolved_output = output_root.resolve(strict=True)
        artifact_root = output_root / "runtime" / "deepplanning"
        artifact_root.mkdir(parents=True, exist_ok=True)
        resolved_artifact = artifact_root.resolve(strict=True)
        try:
            resolved_artifact.relative_to(resolved_output)
        except ValueError:
            raise BackendConfigurationError(
                "DeepPlanning artifact root escapes the output root"
            ) from None
        return resolved_artifact

    def _next_batch_number(self) -> int:
        with self._batch_lock:
            self._batch_number += 1
            return self._batch_number

    def _write_redacted_stderr(
        self,
        path: Path,
        value: str,
        *,
        artifact_root: Path,
    ) -> None:
        if path.exists():
            raise BackendProtocolError(
                "DeepPlanning bridge diagnostic already exists"
            )
        redacted = value[:_STDERR_LIMIT]
        replacements = (
            (
                self._environ.get(
                    self._config.generation.api_key_env,
                    "",
                ),
                "<REDACTED_CREDENTIAL>",
            ),
            (
                self._environ.get(
                    self._config.generation.base_url_env,
                    "",
                ),
                "<REDACTED_SERVICE_ADDRESS>",
            ),
            (str(self._runtime_root), "<RUNTIME_ROOT>"),
            (str(artifact_root), "<ARTIFACT_ROOT>"),
            (str(_PACKAGE_IMPORT_ROOT), "<PACKAGE_ROOT>"),
        )
        for sensitive, replacement in replacements:
            if sensitive:
                redacted = redacted.replace(sensitive, replacement)
        if len(value) > _STDERR_LIMIT:
            redacted += "\n[stderr truncated]\n"
        path.write_text(redacted, encoding="utf-8")


def create_backend(
    init: BackendInit,
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
    preflight_reader: Callable[
        [Path], DeepPlanningRuntimePreflight
    ] = preflight_runtime,
    subprocess_runner: Callable[..., Any] = subprocess.run,
) -> DeepPlanningOfficialBackend:
    """Build the pinned backend after read-only runtime preflight."""

    if init.context.benchmark != "deepplanning":
        raise BackendConfigurationError(
            "DeepPlanning official backend only supports 'deepplanning'"
        )
    config = DeepPlanningOfficialConfig.from_mapping(init.config)
    selected_environment = (
        os.environ if environ is None else environ
    )
    runtime_value = _required_environment_value(
        selected_environment,
        config.runtime_root_env,
    )
    runtime_root = _absolute_directory(
        runtime_value,
        "DeepPlanning runtime root",
    )
    bridge_python = _absolute_executable(
        _required_environment_value(
            selected_environment,
            config.bridge_python_env,
        )
    )
    try:
        preflight = preflight_reader(runtime_root)
    except BackendConfigurationError:
        raise
    except Exception as exc:
        raise BackendConfigurationError(str(exc)) from None
    if (
        config.require_git_revision
        and not preflight.git_revision_verified
    ):
        raise BackendConfigurationError(
            "DeepPlanning runtime requires a verifiable pinned "
            "Qwen-Agent checkout"
        )
    if (
        init.context.reasoning_effort != "none"
        and config.generation.reasoning_mode != "omit"
        and config.generation.temperature != 1.0
    ):
        raise BackendConfigurationError(
            "temperature must equal 1 when reasoning is sent"
        )
    generation_transport = create_generation_transport(
        init,
        config=config.generation,
        environ=selected_environment,
        client_factory=client_factory,
    )
    return DeepPlanningOfficialBackend(
        init=init,
        config=config,
        runtime_root=runtime_root,
        bridge_python=bridge_python,
        preflight=preflight,
        generation_transport=generation_transport,
        environ=selected_environment,
        preflight_reader=preflight_reader,
        subprocess_runner=subprocess_runner,
    )


def _parse_bridge_response(
    value: str,
    *,
    case_id: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        raise BackendProtocolError(
            "DeepPlanning bridge returned invalid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise BackendProtocolError(
            "DeepPlanning bridge response must be an object"
        )
    missing = sorted(_RESPONSE_KEYS - set(payload))
    unknown = sorted(set(payload) - _RESPONSE_KEYS)
    if missing or unknown:
        raise BackendProtocolError(
            "DeepPlanning bridge response keys are invalid; "
            f"missing={missing!r}, unknown={unknown!r}"
        )
    if payload["protocol_version"] != BRIDGE_PROTOCOL_VERSION:
        raise BackendProtocolError(
            "DeepPlanning bridge response protocol is unsupported"
        )
    if payload["case_id"] != case_id:
        raise BackendProtocolError(
            "DeepPlanning bridge response case ID does not match"
        )
    messages = payload["messages"]
    if (
        isinstance(messages, (str, bytes))
        or not isinstance(messages, Sequence)
        or any(not isinstance(item, Mapping) for item in messages)
    ):
        raise BackendProtocolError(
            "DeepPlanning bridge messages must be an array of objects"
        )
    metrics = payload["metrics"]
    if not isinstance(metrics, Mapping) or not metrics:
        raise BackendProtocolError(
            "DeepPlanning bridge metrics must be a non-empty object"
        )
    raw_usage = payload["usage"]
    if not isinstance(raw_usage, Mapping):
        raise BackendProtocolError(
            "DeepPlanning bridge usage must be an object"
        )
    try:
        usage = normalize_usage(raw_usage)
    except ValueError as exc:
        raise BackendProtocolError(
            f"DeepPlanning bridge usage is invalid: {exc}"
        ) from None
    if usage.requests < 1:
        raise BackendProtocolError(
            "DeepPlanning bridge usage must count provider requests"
        )
    return {
        "output": payload["output"],
        "messages": list(messages),
        "metrics": dict(metrics),
        "usage": _usage_mapping(usage),
    }


def _usage_mapping(value: UsageRecord) -> dict[str, Any]:
    return {
        "input_tokens": value.input_tokens,
        "cached_input_tokens": value.cached_input_tokens,
        "cache_creation_input_tokens": (
            value.cache_creation_input_tokens
        ),
        "output_tokens": value.output_tokens,
        "reasoning_tokens": value.reasoning_tokens,
        "total_tokens": value.total_tokens,
        "requests": value.requests,
    }


def _required_environment_value(
    environ: Mapping[str, str],
    name: str,
) -> str:
    value = environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise BackendConfigurationError(
            f"required environment variable {name!r} is missing or blank"
        )
    return value.strip()


def _absolute_directory(value: str, field_name: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise BackendConfigurationError(
            f"{field_name} must be an absolute path"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise BackendConfigurationError(
            f"{field_name} does not exist"
        ) from None
    if not resolved.is_dir():
        raise BackendConfigurationError(
            f"{field_name} must be a directory"
        )
    return resolved


def _absolute_executable(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise BackendConfigurationError(
            "DeepPlanning bridge Python must be an absolute path"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise BackendConfigurationError(
            "DeepPlanning bridge Python does not exist"
        ) from None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise BackendConfigurationError(
            "DeepPlanning bridge Python must be executable"
        )
    return resolved


def _environment_name(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _ENV_NAME_PATTERN.fullmatch(value)
    ):
        raise BackendConfigurationError(
            f"{field_name} must be an uppercase environment variable name"
        )
    return value


def _nonempty_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BackendConfigurationError(
            f"{field_name} must be non-empty text"
        )
    return value.strip()


def _token_limit_field(value: object) -> str:
    if value not in {"max_tokens", "max_completion_tokens"}:
        raise BackendConfigurationError(
            "conversion_token_limit_field is unsupported"
        )
    return str(value)


def _bounded_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise BackendConfigurationError(
            f"{field_name} must be an integer within "
            f"[{minimum}, {maximum}]"
        )
    return value


def _bounded_number(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise BackendConfigurationError(
            f"{field_name} must be a finite number within "
            f"[{minimum}, {maximum}]"
        )
    return float(value)


def _safe_component(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_COMPONENT_PATTERN.fullmatch(value)
    ):
        raise BackendProtocolError(
            f"DeepPlanning {field_name} is not path-safe"
        )
    return value


__all__ = [
    "DeepPlanningOfficialBackend",
    "DeepPlanningOfficialConfig",
    "create_backend",
]
