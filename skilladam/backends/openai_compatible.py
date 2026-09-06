"""Secret-safe OpenAI-compatible Chat Completions backend."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
    as_completed,
)
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import threading
import time
from typing import Any

from skilladam.execution.backend import (
    BackendConfigurationError,
    BackendError,
    BackendLoadError,
    BackendProtocolError,
)
from skilladam.execution.contracts import (
    BackendContext,
    BackendInit,
    BatchExecution,
    GenerationRequest,
    GenerationResult,
    RawRollout,
    ToolCall,
)
from skilladam.types import RolloutRequest, UsageRecord
from skilladam.usage import normalize_usage


_ENV_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
_SUPPORTED_BENCHMARKS = (
    "alfworld",
    "docvqa",
    "lmb",
    "officeqa",
    "searchqa",
    "spreadsheetbench",
)
_TOKEN_LIMIT_FIELDS = ("max_completion_tokens", "max_tokens")
_REASONING_MODES = ("extra_body", "omit", "top_level")
_SPREADSHEET_EXECUTION_MODES = ("local-subprocess", "container")
_SPREADSHEET_SANDBOX_ENGINES = ("docker", "podman")
_SPREADSHEET_SANDBOX_SECURITY_PROFILES = (
    "engine-security-opt",
    "setpriv-wrapper",
)
_SPREADSHEET_ENGINE_ENVIRONMENT_NAMES = (
    "PATH",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "DOCKER_CONTEXT",
    "CONTAINER_HOST",
    "CONTAINER_CONNECTION",
)
_SPREADSHEET_LOCAL_ENVIRONMENT_NAMES = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
)
_SPREADSHEET_GOLD_FEEDBACK_STAGES = frozenset(
    {"training_rollout", "target_train_rollout"}
)
_SAFE_ERROR_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_CACHE_SESSION_PREFIX_PATTERN = re.compile(r"[A-Za-z0-9_.:-]+")
_MAX_OFFICEQA_TOOL_TURNS = 24
_MAX_OFFICEQA_TOOL_CALLS_PER_TURN = 8
_MAX_OFFICEQA_TOTAL_TOOL_CALLS = 96
_MISSING = object()
_CONFIG_KEYS = frozenset(
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
        "alfworld_data_env",
        "alfworld_startup_timeout_seconds",
        "alfworld_step_timeout_seconds",
        "spreadsheet_execution_mode",
        "spreadsheet_sandbox_engine",
        "spreadsheet_sandbox_image_env",
        "spreadsheet_sandbox_security_profile",
        "spreadsheet_exec_timeout_seconds",
        "spreadsheet_task_timeout_seconds",
        "spreadsheet_max_turns",
        "max_total_requests",
        "max_total_input_tokens",
        "max_total_output_tokens",
    }
)


class OpenAICompatibleRequestError(BackendError):
    """One redacted provider request failure."""


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    """Validated non-secret transport settings."""

    api_key_env: str = "OPENAI_API_KEY"
    base_url_env: str = "OPENAI_BASE_URL"
    timeout_seconds: float = 120.0
    max_retries: int = 2
    max_completion_tokens: int = 4096
    token_limit_field: str = "max_completion_tokens"
    temperature: float = 1.0
    reasoning_mode: str = "top_level"
    enable_prompt_cache: bool = False
    prompt_cache_min_tokens: int = 1024
    cache_session_prefix: str = ""
    alfworld_data_env: str = "ALFWORLD_DATA"
    alfworld_startup_timeout_seconds: float = 120.0
    alfworld_step_timeout_seconds: float = 60.0
    spreadsheet_execution_mode: str = "local-subprocess"
    spreadsheet_sandbox_engine: str = "docker"
    spreadsheet_sandbox_image_env: str = (
        "SKILLADAM_SPREADSHEET_SANDBOX_IMAGE"
    )
    spreadsheet_sandbox_security_profile: str = "engine-security-opt"
    spreadsheet_exec_timeout_seconds: float = 120.0
    spreadsheet_task_timeout_seconds: float = 600.0
    spreadsheet_max_turns: int = 30
    max_total_requests: int | None = None
    max_total_input_tokens: int | None = None
    max_total_output_tokens: int | None = None

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> OpenAICompatibleConfig:
        """Parse one strict JSON-derived backend configuration."""

        unknown = sorted(set(value) - _CONFIG_KEYS)
        if unknown:
            raise BackendConfigurationError(
                f"OpenAI-compatible config has unknown keys: {unknown!r}"
            )
        enable_prompt_cache = _boolean(
            value.get("enable_prompt_cache", False),
            "enable_prompt_cache",
        )
        cache_session_prefix = _cache_session_prefix(
            value.get("cache_session_prefix", ""),
        )
        if enable_prompt_cache and not cache_session_prefix:
            raise BackendConfigurationError(
                "cache_session_prefix is required when prompt cache is enabled"
            )
        return cls(
            api_key_env=_environment_name(
                value.get("api_key_env", "OPENAI_API_KEY"),
                "api_key_env",
            ),
            base_url_env=_environment_name(
                value.get("base_url_env", "OPENAI_BASE_URL"),
                "base_url_env",
            ),
            timeout_seconds=_bounded_number(
                value.get("timeout_seconds", 120.0),
                "timeout_seconds",
                minimum=0.0,
                maximum=3600.0,
                minimum_inclusive=False,
            ),
            max_retries=_bounded_integer(
                value.get("max_retries", 2),
                "max_retries",
                minimum=0,
                maximum=10,
            ),
            max_completion_tokens=_bounded_integer(
                value.get("max_completion_tokens", 4096),
                "max_completion_tokens",
                minimum=1,
                maximum=1_000_000,
            ),
            token_limit_field=_enum(
                value.get(
                    "token_limit_field",
                    "max_completion_tokens",
                ),
                "token_limit_field",
                _TOKEN_LIMIT_FIELDS,
            ),
            temperature=_bounded_number(
                value.get("temperature", 1.0),
                "temperature",
                minimum=0.0,
                maximum=2.0,
            ),
            reasoning_mode=_enum(
                value.get("reasoning_mode", "top_level"),
                "reasoning_mode",
                _REASONING_MODES,
            ),
            enable_prompt_cache=enable_prompt_cache,
            prompt_cache_min_tokens=_bounded_integer(
                value.get("prompt_cache_min_tokens", 1024),
                "prompt_cache_min_tokens",
                minimum=1,
                maximum=1_000_000,
            ),
            cache_session_prefix=cache_session_prefix,
            alfworld_data_env=_environment_name(
                value.get("alfworld_data_env", "ALFWORLD_DATA"),
                "alfworld_data_env",
            ),
            alfworld_startup_timeout_seconds=_bounded_number(
                value.get("alfworld_startup_timeout_seconds", 120.0),
                "alfworld_startup_timeout_seconds",
                minimum=0.0,
                maximum=3600.0,
                minimum_inclusive=False,
            ),
            alfworld_step_timeout_seconds=_bounded_number(
                value.get("alfworld_step_timeout_seconds", 60.0),
                "alfworld_step_timeout_seconds",
                minimum=0.0,
                maximum=3600.0,
                minimum_inclusive=False,
            ),
            spreadsheet_execution_mode=_enum(
                value.get(
                    "spreadsheet_execution_mode",
                    "local-subprocess",
                ),
                "spreadsheet_execution_mode",
                _SPREADSHEET_EXECUTION_MODES,
            ),
            spreadsheet_sandbox_engine=_enum(
                value.get("spreadsheet_sandbox_engine", "docker"),
                "spreadsheet_sandbox_engine",
                _SPREADSHEET_SANDBOX_ENGINES,
            ),
            spreadsheet_sandbox_image_env=_environment_name(
                value.get(
                    "spreadsheet_sandbox_image_env",
                    "SKILLADAM_SPREADSHEET_SANDBOX_IMAGE",
                ),
                "spreadsheet_sandbox_image_env",
            ),
            spreadsheet_sandbox_security_profile=_enum(
                value.get(
                    "spreadsheet_sandbox_security_profile",
                    "engine-security-opt",
                ),
                "spreadsheet_sandbox_security_profile",
                _SPREADSHEET_SANDBOX_SECURITY_PROFILES,
            ),
            spreadsheet_exec_timeout_seconds=_bounded_number(
                value.get("spreadsheet_exec_timeout_seconds", 120.0),
                "spreadsheet_exec_timeout_seconds",
                minimum=0.0,
                maximum=3600.0,
                minimum_inclusive=False,
            ),
            spreadsheet_task_timeout_seconds=_bounded_number(
                value.get("spreadsheet_task_timeout_seconds", 600.0),
                "spreadsheet_task_timeout_seconds",
                minimum=0.0,
                maximum=3600.0,
                minimum_inclusive=False,
            ),
            spreadsheet_max_turns=_bounded_integer(
                value.get("spreadsheet_max_turns", 30),
                "spreadsheet_max_turns",
                minimum=1,
                maximum=30,
            ),
            max_total_requests=_optional_bounded_integer(
                value.get("max_total_requests"),
                "max_total_requests",
                maximum=1_000_000,
            ),
            max_total_input_tokens=_optional_bounded_integer(
                value.get("max_total_input_tokens"),
                "max_total_input_tokens",
                maximum=10_000_000_000,
            ),
            max_total_output_tokens=_optional_bounded_integer(
                value.get("max_total_output_tokens"),
                "max_total_output_tokens",
                maximum=10_000_000_000,
            ),
        )


@dataclass(frozen=True, slots=True)
class _PreparedRollout:
    request: RolloutRequest
    messages: tuple[dict[str, Any], ...]
    retained: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _OfficeQAToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, Any]
    raw_arguments: str


@dataclass(frozen=True, slots=True)
class _OfficeQATurn:
    text: str
    usage: UsageRecord
    tool_calls: tuple[_OfficeQAToolCall, ...]


class OpenAICompatibleBackend:
    """ExecutionBackend shell initialized without implicit provider choices."""

    def __init__(
        self,
        *,
        init: BackendInit,
        config: OpenAICompatibleConfig,
        client: Any,
        environ: Mapping[str, str],
        alfworld_environment_factory: Callable[..., Any],
        spreadsheet_sandbox: Any | None,
        spreadsheet_workbook_evaluator: Callable[..., Mapping[str, Any]],
        spreadsheet_preview_factory: Callable[[Path], str],
    ) -> None:
        self._init = init
        self._config = config
        self._client = client
        self._environ = dict(environ)
        self._alfworld_environment_factory = (
            alfworld_environment_factory
        )
        self._spreadsheet_sandbox = spreadsheet_sandbox
        self._spreadsheet_workbook_evaluator = (
            spreadsheet_workbook_evaluator
        )
        self._spreadsheet_preview_factory = spreadsheet_preview_factory
        self._budget_lock = threading.Lock()
        self._budget_requests = 0
        self._budget_input_tokens = 0
        self._budget_output_tokens = 0

    def execute_batch(
        self,
        execution: BatchExecution,
    ) -> tuple[RawRollout, ...]:
        """Execute one bounded batch while preserving request order."""

        if execution.context != self._init.context:
            raise BackendProtocolError(
                "batch context does not match backend initialization"
            )
        case_ids = tuple(
            request.case.case_id
            for request in execution.requests
        )
        if len(set(case_ids)) != len(case_ids):
            raise BackendProtocolError(
                "batch rollout case IDs must be unique"
            )
        prepared: tuple[_PreparedRollout, ...] = tuple(
            _prepare_rollout(
                request,
                context=self._init.context,
            )
            for request in execution.requests
        )
        if self._init.context.benchmark == "spreadsheetbench":
            prepared = tuple(
                self._prepare_spreadsheet_rollout(item)
                for item in prepared
            )
            if self._spreadsheet_sandbox is None:
                raise BackendConfigurationError(
                    "SpreadsheetBench executor is unavailable"
                )
            try:
                self._spreadsheet_sandbox.preflight()
            except Exception as exc:
                from skilladam.benchmarks.spreadsheetbench.runtime import (
                    SpreadsheetSandboxRuntimeError,
                )

                if isinstance(exc, SpreadsheetSandboxRuntimeError):
                    raise BackendConfigurationError(str(exc)) from None
                raise BackendConfigurationError(
                    "SpreadsheetBench executor preflight failed"
                ) from None

        worker_count = (
            1
            if self._init.context.benchmark == "alfworld"
            else min(
                execution.context.workers,
                len(prepared),
            )
        )
        executor = ThreadPoolExecutor(max_workers=worker_count)
        futures: dict[Future[RawRollout], int] = {}
        results: list[RawRollout | None] = [None] * len(prepared)
        try:
            for index, rollout in enumerate(prepared):
                future = executor.submit(
                    self._execute_rollout,
                    rollout,
                    execution.stage,
                )
                futures[future] = index
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            executor.shutdown(
                wait=True,
                cancel_futures=True,
            )
            raise
        else:
            executor.shutdown(wait=True)

        completed = tuple(
            item
            for item in results
            if item is not None
        )
        if (
            len(completed) != len(case_ids)
            or tuple(item.case_id for item in completed) != case_ids
        ):
            raise BackendProtocolError(
                "batch rollout did not preserve exact case coverage"
            )
        return completed

    def _execute_rollout(
        self,
        rollout: _PreparedRollout,
        stage: str,
    ) -> RawRollout:
        if (
            self._init.context.benchmark == "alfworld"
            and rollout.request.metadata.get(
                "requires_environment_reset"
            )
            is True
        ):
            return self._execute_alfworld_rollout(rollout)
        if self._init.context.benchmark == "officeqa":
            return self._execute_officeqa_rollout(rollout)
        if self._init.context.benchmark == "spreadsheetbench":
            return self._execute_spreadsheet_rollout(
                rollout,
                stage=stage,
            )
        request = GenerationRequest(
            stage="rollout",
            messages=rollout.messages,
            context=self._init.context,
            metadata={},
        )
        payload = _generation_payload(
            request,
            self._config,
            cache_session_id=self._prompt_cache_session_id(
                request.stage
            ),
        )
        response = self._provider_create(payload, stage=request.stage)
        text, tool_calls, usage = _generation_components(response)
        if not text.strip() and tool_calls:
            raise BackendProtocolError(
                "benchmark rollout requires a text response"
            )

        messages = rollout.messages + (
            {
                "role": "assistant",
                "content": text,
            },
        )
        raw_result: dict[str, Any] = {
            "response": text,
            "messages": messages,
            "usage": usage,
            "n_turns": 1,
        }
        if not text.strip():
            raw_result.update(
                {
                    "termination_reason": "empty_response",
                    "fail_reason": "Provider returned no text response",
                }
            )
        raw_result.update(rollout.retained)
        return RawRollout(
            case_id=rollout.request.case.case_id,
            result=raw_result,
        )

    def _prepare_spreadsheet_rollout(
        self,
        rollout: _PreparedRollout,
    ) -> _PreparedRollout:
        from skilladam.benchmarks.spreadsheetbench.runtime import (
            SpreadsheetSandboxRuntimeError,
            resolve_workbooks,
        )

        reference = rollout.request.case.reference
        if not isinstance(reference, Mapping):
            raise BackendProtocolError(
                "SpreadsheetBench reference must be an object"
            )
        try:
            input_path, golden_path = resolve_workbooks(
                self._init.data_root,
                rollout.request.case.payload.get("input_path"),
                reference.get("golden_path"),
            )
        except SpreadsheetSandboxRuntimeError as exc:
            raise BackendConfigurationError(str(exc)) from None
        try:
            preview = self._spreadsheet_preview_factory(input_path)
        except Exception:
            raise BackendLoadError(
                "SpreadsheetBench workbook preview failed; install "
                "skilladam[spreadsheetbench]"
            ) from None
        if (
            not isinstance(preview, str)
            or not preview.strip()
            or len(preview) > 100_000
        ):
            raise BackendProtocolError(
                "SpreadsheetBench workbook preview is invalid"
            )
        messages = [dict(message) for message in rollout.messages]
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") != "user":
                continue
            content = messages[index].get("content")
            if not isinstance(content, str):
                raise BackendProtocolError(
                    "SpreadsheetBench user prompt must be text"
                )
            messages[index] = {
                **messages[index],
                "content": (
                    content
                    + "\n\n# Materialized workbook preview\n"
                    + preview.strip()
                ),
            }
            break
        else:
            raise BackendProtocolError(
                "SpreadsheetBench rollout requires a user prompt"
            )
        answer_position = reference.get(
            "answer_position",
            rollout.request.case.payload.get("answer_position"),
        )
        answer_sheet = reference.get(
            "answer_sheet",
            rollout.request.case.payload.get("answer_sheet", ""),
        )
        if (
            not isinstance(answer_position, str)
            or not answer_position.strip()
            or not isinstance(answer_sheet, str)
        ):
            raise BackendProtocolError(
                "SpreadsheetBench reference coordinates are invalid"
            )
        return _PreparedRollout(
            request=rollout.request,
            messages=tuple(messages),
            retained={
                **dict(rollout.retained),
                "_spreadsheet_input_path": input_path,
                "_spreadsheet_golden_path": golden_path,
                "_spreadsheet_answer_position": answer_position.strip(),
                "_spreadsheet_answer_sheet": answer_sheet.strip(),
            },
        )

    def _execute_alfworld_rollout(
        self,
        rollout: _PreparedRollout,
    ) -> RawRollout:
        from skilladam.benchmarks.alfworld.rollout import (
            build_step_messages,
            extract_action,
            extract_reasoning,
        )
        from skilladam.benchmarks.alfworld.runtime import (
            ALFWorldRuntimeError,
            ALFWorldState,
            resolve_gamefile,
        )

        metadata = rollout.request.metadata
        raw_gamefile = metadata.get("gamefile")
        try:
            gamefile = resolve_gamefile(
                raw_gamefile,
                environ=self._environ,
                data_env=self._config.alfworld_data_env,
            )
        except ALFWorldRuntimeError as exc:
            raise BackendConfigurationError(str(exc)) from None

        max_steps = _alfworld_bounded_integer(
            metadata.get("max_steps"),
            "max_steps",
            minimum=1,
            maximum=50,
        )
        history_length = _alfworld_bounded_integer(
            metadata.get("history_length"),
            "history_length",
            minimum=0,
            maximum=10,
        )
        environment_split = metadata.get("environment_split")
        if environment_split not in {
            "train",
            "eval_in_distribution",
            "eval_out_of_distribution",
        }:
            raise BackendProtocolError(
                "ALFWorld environment_split is unsupported"
            )
        session: Any | None = None
        usage_records: list[UsageRecord] = []
        conversation: list[dict[str, Any]] = []
        task_description = ""
        state: ALFWorldState | None = None
        try:
            session = self._alfworld_environment_factory(
                gamefile,
                environment_split=environment_split,
                seed=rollout.request.seed,
                max_steps=max_steps,
                startup_timeout_seconds=(
                    self._config.alfworld_startup_timeout_seconds
                ),
                step_timeout_seconds=(
                    self._config.alfworld_step_timeout_seconds
                ),
            )
            state = session.reset()
            if not isinstance(state, ALFWorldState):
                raise BackendProtocolError(
                    "ALFWorld environment reset returned invalid state"
                )
            task_description = _alfworld_task_description(
                state.observation
            )
            for step_index in range(max_steps):
                messages = build_step_messages(
                    task_description=task_description,
                    current_observation=state.observation,
                    admissible_actions=state.admissible_actions,
                    history=conversation,
                    method=rollout.request.method,
                    skill=rollout.request.skill,
                    history_length=history_length,
                )
                generated = self.generate(
                    GenerationRequest(
                        stage="rollout",
                        messages=messages,
                        context=self._init.context,
                        metadata={},
                    )
                )
                if len(generated.usage) != 1:
                    raise BackendProtocolError(
                        "ALFWorld rollout requires one usage record per step"
                    )
                usage_records.append(generated.usage[0])
                try:
                    action = extract_action(generated.text)
                    used_fallback = False
                except ValueError:
                    action = "look"
                    used_fallback = True
                reasoning = extract_reasoning(generated.text)
                next_state = session.step(action)
                if not isinstance(next_state, ALFWorldState):
                    raise BackendProtocolError(
                        "ALFWorld environment step returned invalid state"
                    )
                conversation.append(
                    {
                        "step": step_index,
                        "action": action,
                        "reasoning": reasoning,
                        "env_feedback": next_state.observation,
                        "reward": next_state.reward,
                        "done": next_state.done,
                        "action_fallback": used_fallback,
                    }
                )
                state = next_state
                if state.done:
                    break
        except ALFWorldRuntimeError as exc:
            raise BackendError(str(exc)) from None
        finally:
            if session is not None:
                session.close()

        assert state is not None
        won = bool(state.won)
        fail_reason = ""
        if not won:
            if len(conversation) >= max_steps:
                fail_reason = f"Timeout after {max_steps} steps"
            else:
                fail_reason = (
                    "Episode ended without completing the task"
                )
        return RawRollout(
            case_id=rollout.request.case.case_id,
            result={
                "hard": 1 if won else 0,
                "soft": 1.0 if won else 0.0,
                "n_turns": len(conversation),
                "fail_reason": fail_reason,
                "task_type": metadata.get("task_type", ""),
                "task_description": task_description,
                "gamefile": raw_gamefile,
                "conversation": conversation,
                "usage_records": usage_records,
            },
        )

    def _execute_spreadsheet_rollout(
        self,
        rollout: _PreparedRollout,
        *,
        stage: str,
    ) -> RawRollout:
        from skilladam.benchmarks.spreadsheetbench.rollout import (
            extract_python_code,
        )
        from skilladam.benchmarks.spreadsheetbench.runtime import (
            SandboxExecutionResult,
            SpreadsheetSandboxRuntimeError,
        )

        if self._spreadsheet_sandbox is None:
            raise BackendConfigurationError(
                "SpreadsheetBench sandbox is unavailable"
            )
        input_path = rollout.retained.get("_spreadsheet_input_path")
        golden_path = rollout.retained.get("_spreadsheet_golden_path")
        answer_position = rollout.retained.get(
            "_spreadsheet_answer_position"
        )
        answer_sheet = rollout.retained.get("_spreadsheet_answer_sheet")
        if (
            not isinstance(input_path, Path)
            or not isinstance(golden_path, Path)
            or not isinstance(answer_position, str)
            or not isinstance(answer_sheet, str)
        ):
            raise BackendProtocolError(
                "SpreadsheetBench preflight state is invalid"
            )

        messages = [dict(message) for message in rollout.messages]
        usage_records: list[UsageRecord] = []
        artifact_ids: list[str] = []
        code = ""
        last_execution: SandboxExecutionResult | None = None
        last_evaluation: Mapping[str, Any] | None = None
        deadline = (
            time.monotonic()
            + self._config.spreadsheet_task_timeout_seconds
        )

        for turn_index in range(
            1,
            self._config.spreadsheet_max_turns + 1,
        ):
            if turn_index > 1 and time.monotonic() >= deadline:
                break
            generated = self.generate(
                GenerationRequest(
                    stage="rollout",
                    messages=tuple(messages),
                    context=self._init.context,
                    metadata={},
                )
            )
            if len(generated.usage) != 1:
                raise BackendProtocolError(
                    "SpreadsheetBench rollout requires one usage record "
                    "per model turn"
                )
            usage_records.append(generated.usage[0])
            code = extract_python_code(generated.text)
            messages.append(
                {
                    "role": "assistant",
                    "content": generated.text,
                }
            )
            try:
                execution = self._spreadsheet_sandbox.execute(
                    code,
                    input_path=input_path,
                    case_id=rollout.request.case.case_id,
                    attempt_index=turn_index,
                )
            except SpreadsheetSandboxRuntimeError as exc:
                raise BackendError(str(exc)) from None
            if not isinstance(execution, SandboxExecutionResult):
                raise BackendProtocolError(
                    "SpreadsheetBench sandbox returned an invalid result"
                )
            last_execution = execution
            artifact_ids.append(execution.artifact_dir.name)
            if not execution.ok:
                if turn_index < self._config.spreadsheet_max_turns:
                    messages.append(
                        {
                            "role": "user",
                            "content": _spreadsheet_execution_feedback(
                                execution
                            ),
                        }
                    )
                    continue
                break

            if execution.output_path is None:
                raise BackendProtocolError(
                    "SpreadsheetBench successful sandbox result "
                    "requires an output"
                )
            try:
                raw_evaluation = self._spreadsheet_workbook_evaluator(
                    execution.output_path,
                    golden_path,
                    answer_position,
                    answer_sheet,
                )
                evaluation = _spreadsheet_evaluation(raw_evaluation)
            except BackendProtocolError:
                raise
            except Exception:
                raise BackendError(
                    "SpreadsheetBench workbook evaluation failed"
                ) from None
            last_evaluation = evaluation
            if bool(evaluation["ok"]):
                break
            if (
                stage in _SPREADSHEET_GOLD_FEEDBACK_STAGES
                and turn_index < self._config.spreadsheet_max_turns
            ):
                messages.append(
                    {
                        "role": "user",
                        "content": _spreadsheet_coordinate_feedback(
                            evaluation
                        ),
                    }
                )
                continue
            break

        executed = bool(
            last_execution is not None and last_execution.ok
        )
        evaluation = last_evaluation or {
            "ok": False,
            "n_cells_total": 0,
            "n_cells_match": 0,
            "per_cell_pass_rate": 0.0,
            "missing_sheets": (),
            "mismatched_cells": (),
        }
        hard = 1 if evaluation["ok"] else 0
        if hard:
            fail_reason = ""
        elif not executed and last_execution is not None:
            fail_reason = last_execution.error_type or "execution_failed"
        elif not executed:
            fail_reason = "task_timeout"
        else:
            fail_reason = "workbook output did not match graded cells"
        return RawRollout(
            case_id=rollout.request.case.case_id,
            result={
                "hard": hard,
                "per_cell_pass_rate": evaluation[
                    "per_cell_pass_rate"
                ],
                "exec_pass": 1 if executed else 0,
                "n_turns": len(usage_records),
                "n_cells_total": evaluation["n_cells_total"],
                "n_cells_match": evaluation["n_cells_match"],
                "fail_reason": fail_reason,
                "code": code,
                "messages": messages,
                "usage_records": usage_records,
                "artifact_ids": artifact_ids,
            },
        )

    def _execute_officeqa_rollout(
        self,
        rollout: _PreparedRollout,
    ) -> RawRollout:
        messages = list(rollout.messages)
        tools = rollout.request.metadata.get("tools")
        max_tool_turns = _officeqa_max_tool_turns(
            rollout.request.metadata
        )
        allowed_roots: list[str] | None = None
        allowed_files = _officeqa_allowed_files(
            rollout.request.case.payload
        )
        if not _officeqa_has_inline_document(
            rollout.request.case.payload
        ):
            if not allowed_files:
                raise BackendConfigurationError(
                    "OfficeQA real execution requires a "
                    "source-file allowlist"
                )
            allowed_roots = _officeqa_docs_roots(
                self._init.data_root
            )
            messages = _officeqa_add_oracle_context(
                messages,
                payload=rollout.request.case.payload,
                allowed_roots=allowed_roots,
            )
        redactions = _officeqa_redactions(
            data_root=self._init.data_root,
            allowed_roots=allowed_roots or (),
        )
        n_tool_calls = 0
        usage_records: list[UsageRecord] = []

        for turn_index in range(1, max_tool_turns + 1):
            turn = self._officeqa_chat_turn(
                messages=messages,
                tools=tools,
            )
            usage_records.append(turn.usage)
            if not turn.tool_calls:
                if not turn.text.strip():
                    raise BackendProtocolError(
                        "OfficeQA rollout requires a text response"
                    )
                messages.append(
                    {
                        "role": "assistant",
                        "content": turn.text,
                    }
                )
                safe_response = _redact_officeqa_value(
                    turn.text,
                    redactions,
                )
                safe_messages = _redact_officeqa_value(
                    messages,
                    redactions,
                )
                return RawRollout(
                    case_id=rollout.request.case.case_id,
                    result={
                        "response": safe_response,
                        "messages": safe_messages,
                        "usage_records": usage_records,
                        "n_turns": turn_index,
                        "n_tool_calls": n_tool_calls,
                    },
                )
            if (
                len(turn.tool_calls)
                > _MAX_OFFICEQA_TOOL_CALLS_PER_TURN
            ):
                raise BackendProtocolError(
                    "OfficeQA exceeded per-turn tool-call limit"
                )
            if (
                n_tool_calls + len(turn.tool_calls)
                > _MAX_OFFICEQA_TOTAL_TOOL_CALLS
            ):
                raise BackendProtocolError(
                    "OfficeQA exceeded total tool-call limit"
                )
            if allowed_roots is None:
                allowed_roots = _officeqa_docs_roots(
                    self._init.data_root
                )
                redactions = _officeqa_redactions(
                    data_root=self._init.data_root,
                    allowed_roots=allowed_roots,
                )
            assistant_tool_calls = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.raw_arguments,
                    },
                }
                for call in turn.tool_calls
            ]
            messages.append(
                {
                    "role": "assistant",
                    "content": turn.text or None,
                    "tool_calls": assistant_tool_calls,
                }
            )
            for call in turn.tool_calls:
                redactions.extend(
                    _officeqa_external_path_redactions(
                        call.arguments,
                        allowed_roots=allowed_roots,
                    )
                )
                observation = _run_officeqa_tool(
                    call,
                    allowed_roots=allowed_roots,
                    allowed_files=allowed_files,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "name": call.name,
                        "content": observation,
                    }
                )
                n_tool_calls += 1
        safe_messages = _redact_officeqa_value(
            messages,
            redactions,
        )
        return RawRollout(
            case_id=rollout.request.case.case_id,
            result={
                "response": "",
                "messages": safe_messages,
                "usage_records": usage_records,
                "n_turns": max_tool_turns,
                "n_tool_calls": n_tool_calls,
                "termination_reason": "max_tool_turns",
                "fail_reason": (
                    "Exceeded tool-turn budget "
                    f"({max_tool_turns})"
                ),
            },
        )

    def _officeqa_chat_turn(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: object,
    ) -> _OfficeQATurn:
        request = GenerationRequest(
            stage="rollout",
            messages=tuple(messages),
            context=self._init.context,
            metadata={"tools": tools},
        )
        payload = _generation_payload(
            request,
            self._config,
            cache_session_id=self._prompt_cache_session_id(
                request.stage
            ),
        )
        response = self._provider_create(payload, stage="rollout")
        return _officeqa_turn(response)

    def generate(
        self,
        request: GenerationRequest,
    ) -> GenerationResult:
        """Generate one normalized optimizer result."""

        if request.context != self._init.context:
            raise BackendProtocolError(
                "generation context does not match backend initialization"
            )
        payload = _generation_payload(
            request,
            self._config,
            cache_session_id=self._prompt_cache_session_id(
                request.stage
            ),
        )
        response = self._provider_create(payload, stage=request.stage)
        return _generation_result(response)

    def _provider_create(
        self,
        payload: Mapping[str, Any],
        *,
        stage: str,
    ) -> Any:
        with self._budget_lock:
            limit = self._config.max_total_requests
            if limit is not None and self._budget_requests >= limit:
                raise OpenAICompatibleRequestError(
                    "provider request budget exhausted"
                )
            self._budget_requests += 1
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:
            raise _safe_request_error(exc, stage=stage) from None
        usage = normalize_usage(response, requests=1)
        with self._budget_lock:
            self._record_budget_usage(usage)
        return response

    def _record_budget_usage(self, usage: UsageRecord) -> None:
        for field_name, limit, current_name in (
            (
                "input_tokens",
                self._config.max_total_input_tokens,
                "_budget_input_tokens",
            ),
            (
                "output_tokens",
                self._config.max_total_output_tokens,
                "_budget_output_tokens",
            ),
        ):
            value = getattr(usage, field_name)
            if limit is not None and value is None:
                raise OpenAICompatibleRequestError(
                    "provider omitted usage required by the token budget"
                )
            if value is None:
                continue
            current = int(getattr(self, current_name)) + int(value)
            setattr(self, current_name, current)
            if limit is not None and current > limit:
                raise OpenAICompatibleRequestError(
                    f"provider {field_name} budget exceeded"
                )

    def _prompt_cache_session_id(self, stage: str) -> str | None:
        if not self._config.enable_prompt_cache:
            return None
        identity = ":".join(
            (
                self._init.context.benchmark,
                self._init.context.method,
                stage,
            )
        )
        suffix = sha256(identity.encode("utf-8")).hexdigest()[:16]
        return f"{self._config.cache_session_prefix}:{suffix}"

    def public_metadata(self) -> Mapping[str, Any]:
        """Return a fixed, path-free, secret-free capability description."""

        metadata: dict[str, Any] = {
            "kind": "openai_compatible",
            "network": True,
            "api_surface": "chat_completions",
            "supported_benchmarks": _SUPPORTED_BENCHMARKS,
            "config_schema_version": 1,
        }
        budget = {
            "max_requests": self._config.max_total_requests,
            "max_input_tokens": self._config.max_total_input_tokens,
            "max_output_tokens": self._config.max_total_output_tokens,
        }
        if any(value is not None for value in budget.values()):
            metadata["safety_budget"] = budget
        if self._init.context.benchmark == "spreadsheetbench":
            metadata["spreadsheet_execution_mode"] = (
                self._config.spreadsheet_execution_mode
            )
        return metadata


def create_backend(
    init: BackendInit,
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
    alfworld_environment_factory: Callable[..., Any] | None = None,
    spreadsheet_sandbox_factory: Callable[..., Any] | None = None,
    spreadsheet_local_executor_factory: Callable[..., Any] | None = None,
    spreadsheet_workbook_evaluator: (
        Callable[..., Mapping[str, Any]] | None
    ) = None,
    spreadsheet_preview_factory: Callable[[Path], str] | None = None,
) -> OpenAICompatibleBackend:
    """Build the explicit backend without exposing runtime credentials."""

    benchmark = init.context.benchmark
    if benchmark not in _SUPPORTED_BENCHMARKS:
        raise BackendConfigurationError(
            "OpenAI-compatible backend does not support "
            f"benchmark {benchmark!r}"
        )

    config = OpenAICompatibleConfig.from_mapping(init.config)
    selected_environment = os.environ if environ is None else environ
    selected_spreadsheet_sandbox: Any | None = None
    selected_spreadsheet_evaluator = (
        spreadsheet_workbook_evaluator
        or _unused_spreadsheet_evaluator
    )
    selected_spreadsheet_preview = (
        spreadsheet_preview_factory
        or _unused_spreadsheet_preview
    )
    if benchmark == "spreadsheetbench":
        from skilladam.benchmarks.spreadsheetbench.runtime import (
            LocalSubprocessExecutor,
            OCISandboxExecutor,
            SpreadsheetSandboxRuntimeError,
            validate_image_reference,
        )

        try:
            artifact_root = (
                init.output_dir / "runtime" / "spreadsheetbench"
            )
            if config.spreadsheet_execution_mode == "container":
                raw_image_reference = _required_environment_value(
                    selected_environment,
                    config.spreadsheet_sandbox_image_env,
                )
                image_reference = validate_image_reference(
                    raw_image_reference
                )
                sandbox_factory = (
                    spreadsheet_sandbox_factory or OCISandboxExecutor
                )
                selected_spreadsheet_sandbox = sandbox_factory(
                    artifact_root=artifact_root,
                    engine=config.spreadsheet_sandbox_engine,
                    image_reference=image_reference,
                    security_profile=(
                        config.spreadsheet_sandbox_security_profile
                    ),
                    timeout_seconds=(
                        config.spreadsheet_exec_timeout_seconds
                    ),
                    host_environ={
                        name: selected_environment[name]
                        for name in _SPREADSHEET_ENGINE_ENVIRONMENT_NAMES
                        if isinstance(selected_environment.get(name), str)
                        and selected_environment[name]
                    },
                )
            else:
                local_factory = (
                    spreadsheet_local_executor_factory
                    or LocalSubprocessExecutor
                )
                selected_spreadsheet_sandbox = local_factory(
                    artifact_root=artifact_root,
                    timeout_seconds=(
                        config.spreadsheet_exec_timeout_seconds
                    ),
                    host_environ={
                        name: selected_environment[name]
                        for name in _SPREADSHEET_LOCAL_ENVIRONMENT_NAMES
                        if isinstance(selected_environment.get(name), str)
                        and selected_environment[name]
                    },
                )
        except SpreadsheetSandboxRuntimeError as exc:
            raise BackendConfigurationError(str(exc)) from None
        except Exception:
            raise BackendLoadError(
                "SpreadsheetBench executor initialization failed"
            ) from None
        if spreadsheet_workbook_evaluator is None:
            from skilladam.benchmarks.spreadsheetbench.evaluation import (
                compare_workbooks,
            )

            selected_spreadsheet_evaluator = compare_workbooks
        if spreadsheet_preview_factory is None:
            from skilladam.benchmarks.spreadsheetbench.rollout import (
                preview_workbook,
            )

            selected_spreadsheet_preview = preview_workbook

    credential_value = _required_environment_value(
        selected_environment,
        config.api_key_env,
    )
    service_address = _required_environment_value(
        selected_environment,
        config.base_url_env,
    )
    factory = client_factory or _load_client_factory()
    client_options = {
        "api_key": credential_value,
        "base_url": service_address,
        "timeout": config.timeout_seconds,
        "max_retries": config.max_retries,
    }
    try:
        client = factory(**client_options)
    except Exception:
        raise BackendLoadError(
            "OpenAI-compatible client initialization failed"
        ) from None
    if alfworld_environment_factory is None:
        from skilladam.benchmarks.alfworld.runtime import open_environment

        selected_alfworld_environment_factory = open_environment
    else:
        selected_alfworld_environment_factory = (
            alfworld_environment_factory
        )
    return OpenAICompatibleBackend(
        init=init,
        config=config,
        client=client,
        environ=selected_environment,
        alfworld_environment_factory=(
            selected_alfworld_environment_factory
        ),
        spreadsheet_sandbox=selected_spreadsheet_sandbox,
        spreadsheet_workbook_evaluator=(
            selected_spreadsheet_evaluator
        ),
        spreadsheet_preview_factory=selected_spreadsheet_preview,
    )


def create_generation_transport(
    init: BackendInit,
    *,
    config: OpenAICompatibleConfig,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> OpenAICompatibleBackend:
    """Create only the shared optimizer-generation transport."""

    if not isinstance(config, OpenAICompatibleConfig):
        raise BackendConfigurationError(
            "generation transport config is invalid"
        )
    selected_environment = os.environ if environ is None else environ
    credential_value = _required_environment_value(
        selected_environment,
        config.api_key_env,
    )
    service_address = _required_environment_value(
        selected_environment,
        config.base_url_env,
    )
    factory = client_factory or _load_client_factory()
    client_options = {
        "api_key": credential_value,
        "base_url": service_address,
        "timeout": config.timeout_seconds,
        "max_retries": config.max_retries,
    }
    try:
        client = factory(**client_options)
    except Exception:
        raise BackendLoadError(
            "OpenAI-compatible client initialization failed"
        ) from None
    return OpenAICompatibleBackend(
        init=init,
        config=config,
        client=client,
        environ=selected_environment,
        alfworld_environment_factory=_unused_environment_factory,
        spreadsheet_sandbox=None,
        spreadsheet_workbook_evaluator=(
            _unused_spreadsheet_evaluator
        ),
        spreadsheet_preview_factory=_unused_spreadsheet_preview,
    )


def _unused_environment_factory(
    *args: object,
    **kwargs: object,
) -> Any:
    raise BackendProtocolError(
        "benchmark environment is unavailable"
    )


def _unused_spreadsheet_evaluator(
    *args: object,
    **kwargs: object,
) -> Mapping[str, Any]:
    raise BackendProtocolError(
        "SpreadsheetBench evaluator is unavailable"
    )


def _unused_spreadsheet_preview(path: Path) -> str:
    raise BackendProtocolError(
        "SpreadsheetBench preview is unavailable"
    )


def _load_client_factory() -> Callable[..., Any]:
    try:
        from openai import OpenAI
    except (ImportError, AttributeError):
        raise BackendLoadError(
            "OpenAI-compatible backend requires the optional "
            "skilladam[backend] dependency"
        ) from None
    return OpenAI


def _generation_payload(
    request: GenerationRequest,
    config: OpenAICompatibleConfig,
    *,
    cache_session_id: str | None = None,
) -> dict[str, Any]:
    messages = [
        _json_value(message, field_name="messages")
        for message in request.messages
    ]
    payload: dict[str, Any] = {
        "model": request.context.model,
        "messages": messages,
        "temperature": config.temperature,
        config.token_limit_field: config.max_completion_tokens,
    }

    reasoning_effort = request.context.reasoning_effort
    if reasoning_effort != "none" and config.reasoning_mode != "omit":
        if config.temperature != 1.0:
            raise BackendConfigurationError(
                "temperature must equal 1 when reasoning is sent"
            )
        if config.reasoning_mode == "top_level":
            payload["reasoning_effort"] = reasoning_effort
        else:
            payload["extra_body"] = {
                "reasoning": {"effort": reasoning_effort}
            }

    raw_tools = request.metadata.get("tools", _MISSING)
    if raw_tools is not _MISSING:
        payload["tools"] = _request_tools(raw_tools)
    if config.enable_prompt_cache:
        if not cache_session_id:
            raise BackendConfigurationError(
                "prompt cache requires a non-empty session ID"
            )
        payload["messages"] = _prompt_cached_messages(
            payload["messages"],
            minimum_tokens=config.prompt_cache_min_tokens,
        )
        if "tools" in payload:
            payload["tools"] = _prompt_cached_tools(
                payload["tools"],
                minimum_tokens=config.prompt_cache_min_tokens,
            )
        payload["extra_headers"] = {
            "Venus-Session-Id": cache_session_id
        }
    return payload


def _prompt_cached_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    minimum_tokens: int,
) -> list[dict[str, Any]]:
    prepared = [
        _json_value(message, field_name="messages")
        for message in messages
    ]
    for message in prepared:
        if message.get("role") != "system":
            continue
        content = message.get("content", "")
        if _estimated_tokens(_content_text(content)) < minimum_tokens:
            break
        if isinstance(content, str):
            message["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif isinstance(content, list):
            for block in reversed(content):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                ):
                    block["cache_control"] = {"type": "ephemeral"}
                    break
            else:
                content.append(
                    {
                        "type": "text",
                        "text": "",
                        "cache_control": {"type": "ephemeral"},
                    }
                )
        break
    _cache_latest_message_prefix(
        prepared,
        minimum_tokens=minimum_tokens,
    )
    return prepared


def _cache_latest_message_prefix(
    messages: list[dict[str, Any]],
    *,
    minimum_tokens: int,
) -> None:
    if not messages or messages[-1].get("role") == "system":
        return
    first_non_system = 0
    while (
        first_non_system < len(messages)
        and messages[first_non_system].get("role") == "system"
    ):
        first_non_system += 1
    prefix = "\n".join(
        _content_text(message.get("content", ""))
        for message in messages[first_non_system:]
    )
    if _estimated_tokens(prefix) < minimum_tokens:
        return
    message = messages[-1]
    content = message.get("content", "")
    if isinstance(content, str):
        message["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        return
    if isinstance(content, list):
        for block in reversed(content):
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
            ):
                block["cache_control"] = {"type": "ephemeral"}
                return
        content.append(
            {
                "type": "text",
                "text": "",
                "cache_control": {"type": "ephemeral"},
            }
        )


def _prompt_cached_tools(
    tools: Sequence[Mapping[str, Any]],
    *,
    minimum_tokens: int,
) -> list[dict[str, Any]]:
    prepared = [
        _json_value(tool, field_name="tools")
        for tool in tools
    ]
    rendered = json.dumps(
        prepared,
        ensure_ascii=False,
        sort_keys=True,
    )
    if prepared and _estimated_tokens(rendered) >= minimum_tokens:
        function = prepared[-1].get("function")
        if isinstance(function, dict):
            function["cache_control"] = {"type": "ephemeral"}
    return prepared


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return "\n".join(
            str(item.get("text", ""))
            for item in value
            if isinstance(item, Mapping) and item.get("type") == "text"
        )
    return ""


def _estimated_tokens(value: str) -> int:
    return 0 if not value else max(1, len(value) // 4)


def _prepare_rollout(
    request: RolloutRequest,
    *,
    context: BackendContext,
) -> _PreparedRollout:
    if request.method != context.method:
        raise BackendProtocolError(
            "rollout request method does not match backend context"
        )
    raw_messages = request.metadata.get("messages", _MISSING)
    interactive_alfworld = (
        context.benchmark == "alfworld"
        and request.metadata.get("requires_environment_reset") is True
    )
    if interactive_alfworld:
        if (
            not isinstance(raw_messages, Sequence)
            or isinstance(raw_messages, (str, bytes, bytearray))
            or raw_messages
        ):
            raise BackendProtocolError(
                "interactive ALFWorld rollout messages must be empty"
            )
        _validate_alfworld_metadata(request.metadata)
    elif (
        not isinstance(raw_messages, Sequence)
        or isinstance(raw_messages, (str, bytes, bytearray))
        or not raw_messages
    ):
        raise BackendProtocolError(
            "rollout metadata messages must be a non-empty sequence"
        )
    messages: list[dict[str, Any]] = []
    for message in raw_messages:
        if not isinstance(message, Mapping):
            raise BackendProtocolError(
                "rollout metadata messages must contain only objects"
            )
        normalized = _json_value(
            message,
            field_name="messages",
        )
        assert isinstance(normalized, dict)
        messages.append(normalized)

    retained: dict[str, Any] = {}
    if context.benchmark == "lmb":
        retained["request_metadata"] = _lmb_evaluation_context(
            request.metadata
        )
    elif context.benchmark == "docvqa":
        image_path = request.metadata.get("image_path")
        if image_path is not None and (
            not isinstance(image_path, str)
            or not image_path.strip()
        ):
            raise BackendProtocolError(
                "DocVQA rollout image_path must be text or null"
            )
        retained["image_path"] = image_path
    return _PreparedRollout(
        request=request,
        messages=tuple(messages),
        retained=retained,
    )


def _validate_alfworld_metadata(
    metadata: Mapping[str, Any],
) -> None:
    gamefile = metadata.get("gamefile")
    if (
        not isinstance(gamefile, str)
        or not gamefile.strip()
        or "\\" in gamefile
    ):
        raise BackendProtocolError(
            "ALFWorld gamefile must be a relative POSIX path"
        )
    path = PurePosixPath(gamefile)
    if path.is_absolute() or ".." in path.parts:
        raise BackendProtocolError(
            "ALFWorld gamefile must be a relative POSIX path"
        )
    if metadata.get("environment_split") not in {
        "train",
        "eval_in_distribution",
        "eval_out_of_distribution",
    }:
        raise BackendProtocolError(
            "ALFWorld environment_split is unsupported"
        )
    _alfworld_bounded_integer(
        metadata.get("max_steps"),
        "max_steps",
        minimum=1,
        maximum=50,
    )
    _alfworld_bounded_integer(
        metadata.get("history_length"),
        "history_length",
        minimum=0,
        maximum=10,
    )


def _alfworld_bounded_integer(
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
        raise BackendProtocolError(
            f"ALFWorld {field_name} must be an integer within "
            f"[{minimum}, {maximum}]"
        )
    return value


def _alfworld_task_description(observation: str) -> str:
    marker = "Your task is to:"
    _, separator, suffix = observation.partition(marker)
    if not separator or not suffix.strip():
        raise BackendProtocolError(
            "ALFWorld reset observation is missing task description"
        )
    return suffix.strip()


def _spreadsheet_evaluation(
    value: object,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BackendProtocolError(
            "SpreadsheetBench evaluator result must be an object"
        )
    ok = value.get("ok")
    total = value.get("n_cells_total")
    matches = value.get("n_cells_match")
    rate = value.get("per_cell_pass_rate")
    if not isinstance(ok, bool):
        raise BackendProtocolError(
            "SpreadsheetBench evaluator ok must be boolean"
        )
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or isinstance(matches, bool)
        or not isinstance(matches, int)
        or not 0 <= matches <= total
    ):
        raise BackendProtocolError(
            "SpreadsheetBench evaluator cell counts are invalid"
        )
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not math.isfinite(float(rate))
        or not 0.0 <= float(rate) <= 1.0
    ):
        raise BackendProtocolError(
            "SpreadsheetBench evaluator pass rate is invalid"
        )
    missing_sheets = _spreadsheet_feedback_items(
        value.get("missing_sheets", ()),
        field_name="missing_sheets",
    )
    mismatched_cells = _spreadsheet_feedback_items(
        value.get("mismatched_cells", ()),
        field_name="mismatched_cells",
    )
    return {
        "ok": ok,
        "n_cells_total": total,
        "n_cells_match": matches,
        "per_cell_pass_rate": float(rate),
        "missing_sheets": missing_sheets,
        "mismatched_cells": mismatched_cells,
    }


def _spreadsheet_feedback_items(
    value: object,
    *,
    field_name: str,
) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or len(value) > 1000
    ):
        raise BackendProtocolError(
            f"SpreadsheetBench evaluator {field_name} is invalid"
        )
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 200
            or "\n" in item
            or "\r" in item
            or "=" in item
        ):
            raise BackendProtocolError(
                f"SpreadsheetBench evaluator {field_name} is invalid"
            )
        result.append(item.strip())
    return tuple(result)


def _spreadsheet_execution_feedback(execution: object) -> str:
    error_type = getattr(execution, "error_type", "")
    if (
        not isinstance(error_type, str)
        or not _SAFE_ERROR_NAME_PATTERN.fullmatch(error_type)
    ):
        error_type = "execution_error"
    error_message = getattr(execution, "error_message", "")
    if not isinstance(error_message, str):
        error_message = ""
    detail = error_message.strip()[:3000]
    rendered_detail = f"\n\n```\n{detail}\n```" if detail else ""
    return (
        "The sandboxed code failed with "
        f"{error_type}.{rendered_detail}\n\n"
        "Fix the code and return a complete corrected Python script "
        "inside one ```python``` block."
    )


def _spreadsheet_coordinate_feedback(
    evaluation: Mapping[str, Any],
) -> str:
    cells = tuple(evaluation.get("mismatched_cells", ()))[:100]
    sheets = tuple(evaluation.get("missing_sheets", ()))[:100]
    lines = [
        "The sandboxed code executed, but the output is incorrect.",
        "Golden and predicted values are hidden.",
    ]
    if sheets:
        lines.append("Missing sheets:")
        lines.extend(f"- {name}" for name in sheets)
    if cells:
        lines.append("Incorrect cell coordinates:")
        lines.extend(f"- {coordinate}" for coordinate in cells)
    if not sheets and not cells:
        lines.append("One or more graded cells are incorrect.")
    lines.append(
        "Fix the code and return a complete corrected Python script "
        "inside one ```python``` block."
    )
    return "\n".join(lines)


def _lmb_evaluation_context(
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    raw_choices = metadata.get("evaluation_choices", _MISSING)
    if (
        not isinstance(raw_choices, Sequence)
        or isinstance(raw_choices, (str, bytes, bytearray))
        or not raw_choices
        or any(not isinstance(item, Mapping) for item in raw_choices)
    ):
        raise BackendProtocolError(
            "LMB rollout requires evaluation_choices"
        )
    choices = _json_value(
        raw_choices,
        field_name="evaluation_choices",
    )
    correct_label = metadata.get("correct_label")
    if not isinstance(correct_label, str) or not correct_label.strip():
        raise BackendProtocolError(
            "LMB rollout requires a correct_label"
        )
    return {
        "evaluation_choices": choices,
        "correct_label": correct_label,
    }


def _request_tools(value: object) -> list[dict[str, Any]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not value
    ):
        raise BackendProtocolError(
            "generation tools must be a non-empty sequence of objects"
        )
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise BackendProtocolError(
                "generation tools must contain only objects"
            )
        normalized = _json_value(item, field_name="tools")
        assert isinstance(normalized, dict)
        result.append(normalized)
    return result


def _generation_components(
    response: object,
) -> tuple[str, tuple[ToolCall, ...], UsageRecord]:
    choices = _field(response, "choices", _MISSING)
    if (
        not isinstance(choices, Sequence)
        or isinstance(choices, (str, bytes, bytearray))
        or len(choices) != 1
    ):
        raise BackendProtocolError(
            "generation response must contain exactly one choice"
        )
    message = _field(choices[0], "message", _MISSING)
    if message is _MISSING:
        raise BackendProtocolError(
            "generation choice must contain a message"
        )

    content = _field(message, "content", None)
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    else:
        raise BackendProtocolError(
            "generation message content must be text or null"
        )

    tool_calls = _response_tool_calls(
        _field(message, "tool_calls", None)
    )

    raw_usage = _field(response, "usage", None)
    try:
        usage = normalize_usage(raw_usage, requests=1)
    except ValueError:
        raise BackendProtocolError(
            "generation response contains invalid usage"
        ) from None
    return text, tool_calls, usage


def _generation_result(response: object) -> GenerationResult:
    text, tool_calls, usage = _generation_components(response)
    if not text.strip() and not tool_calls:
        raise BackendProtocolError(
            "generation result requires text or tool calls"
        )
    return GenerationResult(
        text=text,
        usage=(usage,),
        tool_calls=tool_calls,
    )


def _officeqa_turn(response: object) -> _OfficeQATurn:
    choices = _field(response, "choices", _MISSING)
    if (
        not isinstance(choices, Sequence)
        or isinstance(choices, (str, bytes, bytearray))
        or len(choices) != 1
    ):
        raise BackendProtocolError(
            "OfficeQA response must contain exactly one choice"
        )
    message = _field(choices[0], "message", _MISSING)
    if message is _MISSING:
        raise BackendProtocolError(
            "OfficeQA choice must contain a message"
        )
    content = _field(message, "content", None)
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    else:
        raise BackendProtocolError(
            "OfficeQA message content must be text or null"
        )
    tool_calls = _officeqa_tool_calls(
        _field(message, "tool_calls", None)
    )
    if not text.strip() and not tool_calls:
        raise BackendProtocolError(
            "OfficeQA response requires text or tool calls"
        )
    try:
        usage = normalize_usage(_field(response, "usage", None), requests=1)
    except ValueError:
        raise BackendProtocolError(
            "OfficeQA response contains invalid usage"
        ) from None
    return _OfficeQATurn(
        text=text,
        usage=usage,
        tool_calls=tool_calls,
    )


def _officeqa_tool_calls(
    value: object,
) -> tuple[_OfficeQAToolCall, ...]:
    if value is None:
        return ()
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise BackendProtocolError(
            "OfficeQA tool_calls must be a sequence"
        )
    result: list[_OfficeQAToolCall] = []
    for item in value:
        if _field(item, "type", _MISSING) != "function":
            raise BackendProtocolError(
                "OfficeQA tool call must have type 'function'"
            )
        call_id = _field(item, "id", _MISSING)
        if not isinstance(call_id, str) or not call_id.strip():
            raise BackendProtocolError(
                "OfficeQA tool call id must be non-empty text"
            )
        function = _field(item, "function", _MISSING)
        if function is _MISSING:
            raise BackendProtocolError(
                "OfficeQA tool call must contain a function"
            )
        name = _field(function, "name", _MISSING)
        if not isinstance(name, str) or not name.strip():
            raise BackendProtocolError(
                "OfficeQA tool call name must be non-empty text"
            )
        raw_arguments = _field(function, "arguments", _MISSING)
        if not isinstance(raw_arguments, str):
            raise BackendProtocolError(
                "OfficeQA tool call arguments must be JSON text"
            )
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            raise BackendProtocolError(
                "OfficeQA tool call arguments must be valid JSON"
            ) from None
        if not isinstance(arguments, Mapping):
            raise BackendProtocolError(
                "OfficeQA tool call arguments must be a JSON object"
            )
        result.append(
            _OfficeQAToolCall(
                call_id=call_id.strip(),
                name=name.strip(),
                arguments=arguments,
                raw_arguments=raw_arguments,
            )
        )
    return tuple(result)


def _officeqa_max_tool_turns(metadata: Mapping[str, Any]) -> int:
    value = metadata.get("max_tool_turns")
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _MAX_OFFICEQA_TOOL_TURNS
    ):
        raise BackendProtocolError(
            "OfficeQA max_tool_turns must be an integer within "
            f"[1, {_MAX_OFFICEQA_TOOL_TURNS}]"
        )
    return value


def _officeqa_has_inline_document(payload: Mapping[str, Any]) -> bool:
    value = payload.get("document_text")
    return isinstance(value, str) and bool(value.strip())


def _officeqa_allowed_files(payload: Mapping[str, Any]) -> list[str]:
    value = payload.get("source_files", ())
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise BackendProtocolError(
            "OfficeQA source_files must be a string sequence"
        )
    return sorted({Path(item).name for item in value})


def _officeqa_add_oracle_context(
    messages: Sequence[Mapping[str, Any]],
    *,
    payload: Mapping[str, Any],
    allowed_roots: list[str],
) -> list[dict[str, Any]]:
    from skilladam.benchmarks.officeqa.tool_runtime import (
        build_oracle_parsed_pages_context,
    )

    context = build_oracle_parsed_pages_context(
        payload.get("source_files"),
        payload.get("source_docs"),
        allowed_roots,
    )
    result = [dict(message) for message in messages]
    if not context:
        return result
    for index in range(len(result) - 1, -1, -1):
        message = result[index]
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            raise BackendProtocolError(
                "OfficeQA user message content must be text"
            )
        result[index] = {
            **message,
            "content": (
                content
                + "\n\n## Oracle Parsed Pages\n"
                + context
            ),
        }
        return result
    raise BackendProtocolError(
        "OfficeQA rollout requires a user message"
    )


def _officeqa_docs_roots(data_root: Path) -> list[str]:
    from skilladam.benchmarks.officeqa.tool_runtime import (
        resolve_docs_roots,
    )

    candidates = [
        str(data_root / "docs"),
        str(data_root / "docs_official"),
        str(data_root / "raw" / "treasury_bulletins_parsed"),
    ]
    try:
        return resolve_docs_roots(candidates, environ={})
    except FileNotFoundError:
        raise BackendConfigurationError(
            "OfficeQA document root is unavailable"
        ) from None


def _run_officeqa_tool(
    call: _OfficeQAToolCall,
    *,
    allowed_roots: list[str],
    allowed_files: list[str],
) -> str:
    from skilladam.benchmarks.officeqa.tool_runtime import run_tool

    if not allowed_files:
        raise BackendConfigurationError(
            "OfficeQA tool execution requires a source-file allowlist"
        )
    _, observation = run_tool(
        call.name,
        dict(call.arguments),
        allowed_roots=allowed_roots,
        allowed_files=allowed_files,
    )
    return observation


def _officeqa_redactions(
    *,
    data_root: Path,
    allowed_roots: Sequence[str],
) -> list[tuple[str, str]]:
    values = [
        (str(Path(root).resolve()), "<officeqa-docs>")
        for root in allowed_roots
    ]
    values.append(
        (str(data_root.resolve()), "<officeqa-data>")
    )
    return sorted(
        {item for item in values if item[0]},
        key=lambda item: len(item[0]),
        reverse=True,
    )


def _officeqa_external_path_redactions(
    arguments: Mapping[str, Any],
    *,
    allowed_roots: Sequence[str],
) -> list[tuple[str, str]]:
    value = arguments.get("path")
    if not isinstance(value, str) or not value.strip():
        return []
    candidate = Path(value)
    if not candidate.is_absolute():
        return []
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        resolved = candidate
    if any(
        resolved.is_relative_to(Path(root).resolve(strict=False))
        for root in allowed_roots
    ):
        return []
    name = resolved.name or "external"
    return [(str(resolved), f"<officeqa-docs>/{name}")]


def _redact_officeqa_value(
    value: Any,
    redactions: Sequence[tuple[str, str]],
) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _redact_officeqa_value(item, redactions)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [
            _redact_officeqa_value(item, redactions)
            for item in value
        ]
    if isinstance(value, str):
        rendered = value
        for source, replacement in redactions:
            rendered = rendered.replace(source, replacement)
        return rendered
    return value


def _response_tool_calls(value: object) -> tuple[ToolCall, ...]:
    if value is None:
        return ()
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise BackendProtocolError(
            "generation tool_calls must be a sequence"
        )

    result: list[ToolCall] = []
    for item in value:
        if _field(item, "type", _MISSING) != "function":
            raise BackendProtocolError(
                "generation tool call must have type 'function'"
            )
        function = _field(item, "function", _MISSING)
        if function is _MISSING:
            raise BackendProtocolError(
                "generation tool call must contain a function"
            )
        name = _field(function, "name", _MISSING)
        if not isinstance(name, str) or not name.strip():
            raise BackendProtocolError(
                "generation tool call name must be non-empty text"
            )
        raw_arguments = _field(function, "arguments", _MISSING)
        if not isinstance(raw_arguments, str):
            raise BackendProtocolError(
                "generation tool call arguments must be JSON text"
            )
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            raise BackendProtocolError(
                "generation tool call arguments must be valid JSON"
            ) from None
        if not isinstance(arguments, Mapping):
            raise BackendProtocolError(
                "generation tool call arguments must be a JSON object"
            )
        result.append(ToolCall(name=name, arguments=arguments))
    return tuple(result)


def _json_value(value: object, *, field_name: str) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BackendProtocolError(
                    f"generation {field_name} keys must be text"
                )
            result[key] = _json_value(
                item,
                field_name=field_name,
            )
        return result
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [
            _json_value(item, field_name=field_name)
            for item in value
        ]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise BackendProtocolError(
        f"generation {field_name} must contain only JSON values"
    )


def _field(value: object, name: str, default: object) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _safe_request_error(
    error: Exception,
    *,
    stage: str,
) -> OpenAICompatibleRequestError:
    error_name = type(error).__name__
    if not _SAFE_ERROR_NAME_PATTERN.fullmatch(error_name):
        error_name = "provider_error"
    status = getattr(error, "status_code", None)
    status_text = (
        f" status={status}"
        if isinstance(status, int) and not isinstance(status, bool)
        else ""
    )
    return OpenAICompatibleRequestError(
        "OpenAI-compatible request failed at "
        f"stage {stage!r}: {error_name}{status_text}"
    )


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


def _environment_name(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _ENV_NAME_PATTERN.fullmatch(value)
    ):
        raise BackendConfigurationError(
            f"{field_name} must be an uppercase environment variable name"
        )
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise BackendConfigurationError(
            f"{field_name} must be boolean"
        )
    return value


def _cache_session_prefix(value: object) -> str:
    if not isinstance(value, str):
        raise BackendConfigurationError(
            "cache_session_prefix must be text"
        )
    prefix = value.strip()
    if prefix and (
        len(prefix) > 128
        or not _CACHE_SESSION_PREFIX_PATTERN.fullmatch(prefix)
    ):
        raise BackendConfigurationError(
            "cache_session_prefix contains unsupported characters"
        )
    return prefix


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


def _optional_bounded_integer(
    value: object,
    field_name: str,
    *,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    return _bounded_integer(
        value,
        field_name,
        minimum=1,
        maximum=maximum,
    )


def _bounded_number(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
    minimum_inclusive: bool = True,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise BackendConfigurationError(
            f"{field_name} must be a finite number"
        )
    number = float(value)
    minimum_ok = (
        number >= minimum
        if minimum_inclusive
        else number > minimum
    )
    if not minimum_ok or number > maximum:
        opening = "[" if minimum_inclusive else "("
        raise BackendConfigurationError(
            f"{field_name} must be within "
            f"{opening}{minimum}, {maximum}]"
        )
    return number


def _enum(
    value: object,
    field_name: str,
    allowed: tuple[str, ...],
) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise BackendConfigurationError(
            f"{field_name} must be one of {allowed!r}"
        )
    return value


__all__ = [
    "OpenAICompatibleBackend",
    "OpenAICompatibleConfig",
    "OpenAICompatibleRequestError",
    "create_backend",
    "create_generation_transport",
]
