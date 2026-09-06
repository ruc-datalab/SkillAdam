"""One-case bridge to a pinned caller-managed Qwen-Agent checkout."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import threading
import time
from typing import Any, TextIO

from skilladam.benchmarks.deepplanning.runtime import (
    BRIDGE_PROTOCOL_VERSION,
    DeepPlanningRuntimeManifest,
    load_runtime_manifest,
    preflight_runtime,
)
from skilladam.types import UsageRecord
from skilladam.usage import normalize_usage


_RUNTIME_ROOT_ENV = "SKILLADAM_DEEPPLANNING_RUNTIME_ROOT"
_ARTIFACT_ROOT_ENV = "SKILLADAM_DEEPPLANNING_ARTIFACT_ROOT"
_REQUEST_KEYS = frozenset(
    {
        "protocol_version",
        "case_id",
        "slice",
        "case_number",
        "method",
        "split",
        "stage",
        "model",
        "reasoning_effort",
        "seed",
        "skill",
        "max_interactions",
        "prompt_profile",
        "evaluator_profile",
        "provider_env",
        "provider_config",
        "conversion_model",
        "conversion_max_tokens",
        "conversion_token_limit_field",
        "conversion_temperature",
        "conversion_max_attempts",
        "artifact_dir",
    }
)
_PROVIDER_ENV_KEYS = frozenset({"api_key", "base_url"})
_PROVIDER_CONFIG_KEYS = frozenset(
    {
        "timeout_seconds",
        "max_retries",
        "max_completion_tokens",
        "token_limit_field",
        "temperature",
        "reasoning_mode",
        "enable_prompt_cache",
        "prompt_cache_min_tokens",
        "cache_session_id",
    }
)
_PROVIDER_BUDGET_KEYS = frozenset(
    {
        "max_total_requests",
        "max_total_input_tokens",
        "max_total_output_tokens",
    }
)
_SLICE_LEVELS = {
    "shopping_level1": 1,
    "shopping_level2": 2,
    "shopping_level3": 3,
}
_LOG_LIMIT = 2 * 1024 * 1024


class DeepPlanningBridgeError(RuntimeError):
    """One secret-safe bridge contract or execution failure."""


@dataclass(frozen=True, slots=True)
class _ProviderSettings:
    api_key_env: str
    base_url_env: str
    timeout_seconds: float
    max_retries: int
    max_completion_tokens: int
    token_limit_field: str
    temperature: float
    reasoning_mode: str
    model: str
    reasoning_effort: str
    enable_prompt_cache: bool
    prompt_cache_min_tokens: int
    cache_session_id: str
    max_total_requests: int | None = None
    max_total_input_tokens: int | None = None
    max_total_output_tokens: int | None = None


class _ProviderSession:
    """OpenAI-compatible caller with explicit retry and usage accounting."""

    def __init__(
        self,
        settings: _ProviderSettings,
        *,
        environ: Mapping[str, str],
        client_factory: Any | None = None,
    ) -> None:
        self.settings = settings
        credential_value = _required_environment(
            environ,
            settings.api_key_env,
        )
        service_address = _required_environment(
            environ,
            settings.base_url_env,
        )
        if client_factory is None:
            try:
                from openai import OpenAI
            except (ImportError, AttributeError):
                raise DeepPlanningBridgeError(
                    "the bridge requires the OpenAI Python client"
                ) from None
            client_factory = OpenAI
        client_options = {
            "api_key": credential_value,
            "base_url": service_address,
            "timeout": settings.timeout_seconds,
            "max_retries": 0,
        }
        self.client = client_factory(**client_options)
        self._usage: list[UsageRecord] = []
        self._requests = 0

    def call(
        self,
        *,
        messages: Sequence[Any],
        tools: Sequence[Mapping[str, Any]] | None = None,
        model: str | None = None,
        max_completion_tokens: int | None = None,
        token_limit_field: str | None = None,
        temperature: float | None = None,
        use_reasoning: bool = True,
        use_prompt_cache: bool | None = None,
    ) -> Any:
        """Call the configured model while counting every attempt."""

        selected_model = model or self.settings.model
        selected_max_tokens = (
            self.settings.max_completion_tokens
            if max_completion_tokens is None
            else max_completion_tokens
        )
        selected_token_field = (
            self.settings.token_limit_field
            if token_limit_field is None
            else token_limit_field
        )
        selected_temperature = (
            self.settings.temperature
            if temperature is None
            else temperature
        )
        cache_enabled = (
            self.settings.enable_prompt_cache
            if use_prompt_cache is None
            else use_prompt_cache
        )
        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": list(messages),
            "temperature": selected_temperature,
            selected_token_field: selected_max_tokens,
        }
        if tools:
            payload["tools"] = list(tools)
        effort = self.settings.reasoning_effort
        if (
            use_reasoning
            and effort != "none"
            and self.settings.reasoning_mode != "omit"
        ):
            if selected_temperature != 1.0:
                raise DeepPlanningBridgeError(
                    "temperature must equal 1 when reasoning is sent"
                )
            if self.settings.reasoning_mode == "top_level":
                payload["reasoning_effort"] = effort
            else:
                payload["extra_body"] = {
                    "reasoning": {"effort": effort}
                }
        if cache_enabled:
            if not self.settings.cache_session_id:
                raise DeepPlanningBridgeError(
                    "prompt cache requires a session ID"
                )
            payload["messages"] = _prompt_cached_messages(
                payload["messages"],
                minimum_tokens=self.settings.prompt_cache_min_tokens,
            )
            if "tools" in payload:
                payload["tools"] = _prompt_cached_tools(
                    payload["tools"],
                    minimum_tokens=(
                        self.settings.prompt_cache_min_tokens
                    ),
                )
            payload["extra_headers"] = {
                "Venus-Session-Id": self.settings.cache_session_id
            }

        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            if (
                self.settings.max_total_requests is not None
                and self._requests >= self.settings.max_total_requests
            ):
                raise DeepPlanningBridgeError(
                    "provider request budget exhausted"
                )
            self._requests += 1
            try:
                response = self.client.chat.completions.create(
                    **payload
                )
            except Exception as exc:
                last_error = exc
                if attempt == self.settings.max_retries:
                    break
                time.sleep(min(2**attempt, 8))
                continue
            usage = normalize_usage(response, requests=1)
            self._usage.append(usage)
            self._enforce_token_budget()
            return response
        raise DeepPlanningBridgeError(
            "provider request failed after configured retries"
        ) from last_error

    def _enforce_token_budget(self) -> None:
        for field_name, limit in (
            ("input_tokens", self.settings.max_total_input_tokens),
            ("output_tokens", self.settings.max_total_output_tokens),
        ):
            if limit is None:
                continue
            total = _sum_known(self._usage, field_name)
            if total is None:
                raise DeepPlanningBridgeError(
                    "provider omitted usage required by the token budget"
                )
            if total > limit:
                raise DeepPlanningBridgeError(
                    f"provider {field_name} budget exceeded"
                )

    def usage_mapping(self) -> dict[str, Any]:
        """Return aggregate usage without inventing unknown token fields."""

        return {
            "input_tokens": _sum_known(
                self._usage,
                "input_tokens",
            ),
            "cached_input_tokens": _sum_known(
                self._usage,
                "cached_input_tokens",
            ),
            "cache_creation_input_tokens": _sum_known(
                self._usage,
                "cache_creation_input_tokens",
            ),
            "output_tokens": _sum_known(
                self._usage,
                "output_tokens",
            ),
            "reasoning_tokens": _sum_known(
                self._usage,
                "reasoning_tokens",
            ),
            "total_tokens": _sum_known(
                self._usage,
                "total_tokens",
            ),
            "requests": self._requests,
        }


def main(
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    client_factory: Any | None = None,
) -> int:
    """Execute exactly one JSON request and emit exactly one JSON result."""

    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    selected_environment = os.environ if environ is None else environ
    log_buffer = io.StringIO()
    artifact_dir: Path | None = None
    provider: _ProviderSession | None = None
    replacements: tuple[tuple[str, str], ...] = ()
    try:
        request = _load_request(input_stream)
        runtime_root = _absolute_environment_directory(
            selected_environment,
            _RUNTIME_ROOT_ENV,
        )
        artifact_root = _absolute_environment_directory(
            selected_environment,
            _ARTIFACT_ROOT_ENV,
        )
        artifact_dir = _artifact_directory(
            artifact_root,
            request["artifact_dir"],
        )
        preflight_runtime(runtime_root)
        manifest = load_runtime_manifest(runtime_root)
        settings = _provider_settings(request)
        provider = _ProviderSession(
            settings,
            environ=selected_environment,
            client_factory=client_factory,
        )
        replacements = _redaction_replacements(
            request,
            selected_environment,
            runtime_root=runtime_root,
            artifact_root=artifact_root,
        )
        with redirect_stdout(log_buffer), redirect_stderr(log_buffer):
            result = _execute_request(
                request,
                manifest=manifest,
                artifact_dir=artifact_dir,
                provider=provider,
            )
        result["usage"] = provider.usage_mapping()
        sanitized = _redact_json(result, replacements)
        _write_log(artifact_dir, log_buffer.getvalue(), replacements)
        _write_bridge_usage(
            artifact_dir,
            status="completed",
            usage=provider.usage_mapping(),
            diagnostic=None,
            replacements=replacements,
        )
        output_stream.write(
            json.dumps(
                sanitized,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        output_stream.flush()
        return 0
    except Exception as exc:
        if artifact_dir is not None:
            try:
                diagnostic = _exception_diagnostic(exc)
                _write_log(
                    artifact_dir,
                    (
                        f"{log_buffer.getvalue()}\n"
                        "[bridge failure]\n"
                        f"{diagnostic}\n"
                    ),
                    replacements,
                )
                _write_bridge_usage(
                    artifact_dir,
                    status="failed",
                    usage=(
                        provider.usage_mapping()
                        if provider is not None
                        else None
                    ),
                    diagnostic=(
                        _exception_diagnostic(exc)
                        if replacements
                        else None
                    ),
                    replacements=replacements,
                )
            except Exception:
                pass
        error_stream.write(
            f"{type(exc).__name__}: DeepPlanning bridge failed\n"
        )
        error_stream.flush()
        return 1


def _execute_request(
    request: Mapping[str, Any],
    *,
    manifest: DeepPlanningRuntimeManifest,
    artifact_dir: Path,
    provider: _ProviderSession,
) -> dict[str, Any]:
    slice_id = str(request["slice"])
    if slice_id in _SLICE_LEVELS:
        output, messages, metrics = _run_shopping(
            request,
            manifest=manifest,
            artifact_dir=artifact_dir,
            provider=provider,
        )
    elif slice_id == "travel_en":
        output, messages, metrics = _run_travel(
            request,
            manifest=manifest,
            artifact_dir=artifact_dir,
            provider=provider,
        )
    else:
        raise DeepPlanningBridgeError(
            "unsupported DeepPlanning bridge slice"
        )
    return {
        "protocol_version": BRIDGE_PROTOCOL_VERSION,
        "case_id": request["case_id"],
        "output": output,
        "messages": messages,
        "metrics": metrics,
    }


def _run_shopping(
    request: Mapping[str, Any],
    *,
    manifest: DeepPlanningRuntimeManifest,
    artifact_dir: Path,
    provider: _ProviderSession,
) -> tuple[str, list[Mapping[str, Any]], dict[str, Any]]:
    slice_id = str(request["slice"])
    case_number = _case_number(request)
    level = _SLICE_LEVELS[slice_id]
    benchmark_root = _benchmark_root(manifest)
    slice_runtime = manifest.slices[slice_id]
    query_path = _resolved_benchmark_path(
        benchmark_root,
        slice_runtime.query_file,
        expected="file",
    )
    source_database_root = _resolved_benchmark_path(
        benchmark_root,
        slice_runtime.database_root,
        expected="directory",
    )
    sample = _query_sample(query_path, case_number)
    working_database = artifact_dir / "working_database"
    working_database.mkdir()
    source_case = source_database_root / f"case_{case_number}"
    destination_case = working_database / f"case_{case_number}"
    shutil.copytree(source_case, destination_case, symlinks=False)

    _prepend_import_paths(
        benchmark_root / "shoppingplanning",
    )
    agent_module = importlib.import_module(
        "agent.shopping_agent"
    )
    prompt_module = importlib.import_module("agent.prompts")
    agent_module.call_llm = (
        lambda config_name, messages, tools=None: provider.call(
            messages=messages,
            tools=tools,
        )
    )
    prompt_name = f"SYSTEM_PROMPT_level{level}"
    system_prompt = getattr(prompt_module.prompt_lib, prompt_name)
    system_prompt = _append_skill(
        system_prompt,
        request.get("skill"),
    )
    agent = agent_module.ShoppingFnAgent(
        model=request["model"],
        sample_id=str(case_number),
        database_base_path=str(working_database),
        tool_schema_path=str(
            benchmark_root
            / "shoppingplanning/tools/shopping_tool_schema.json"
        ),
    )
    messages = agent.run(
        user_query=_text_field(sample, "query"),
        system_prompt=system_prompt,
        max_llm_calls=_positive_integer(
            request["max_interactions"],
            "max_interactions",
        ),
        save_messages=True,
        sample_id=str(case_number),
    )
    portable_messages = [
        item.model_dump() if hasattr(item, "model_dump") else item
        for item in messages
    ]

    evaluator = _load_file_module(
        "skilladam_deepplanning_shopping_evaluator",
        benchmark_root
        / "shoppingplanning/evaluation/evaluation_pipeline.py",
    )
    evaluation = evaluator.evaluate_single_case(destination_case)
    if not isinstance(evaluation, Mapping) or not evaluation.get(
        "success"
    ):
        raise DeepPlanningBridgeError(
            "official shopping evaluation failed"
        )
    score = _finite_metric(evaluation.get("score"), "score")
    metrics = {
        "case_score": _finite_metric(
            evaluation.get("case_score"),
            "case_score",
        ),
        "score": score,
        "match_rate": score,
        "matched_count": _finite_metric(
            evaluation.get("matched_count"),
            "matched_count",
        ),
        "expected_count": _finite_metric(
            evaluation.get("expected_count"),
            "expected_count",
        ),
        "extra_products_count": _finite_metric(
            evaluation.get("extra_products_count"),
            "extra_products_count",
        ),
        "coupon_score": _finite_metric(
            evaluation.get("coupon_score"),
            "coupon_score",
        ),
    }
    return (
        _last_assistant_content(portable_messages),
        portable_messages,
        metrics,
    )


def _run_travel(
    request: Mapping[str, Any],
    *,
    manifest: DeepPlanningRuntimeManifest,
    artifact_dir: Path,
    provider: _ProviderSession,
) -> tuple[str, list[Mapping[str, Any]], dict[str, Any]]:
    case_number = _case_number(request)
    benchmark_root = _benchmark_root(manifest)
    slice_runtime = manifest.slices["travel_en"]
    query_path = _resolved_benchmark_path(
        benchmark_root,
        slice_runtime.query_file,
        expected="file",
    )
    source_database_root = _resolved_benchmark_path(
        benchmark_root,
        slice_runtime.database_root,
        expected="directory",
    )
    sample = _query_sample(query_path, case_number)
    working_database = artifact_dir / "working_database"
    working_database.mkdir()
    shutil.copytree(
        source_database_root / f"id_{case_number}",
        working_database / f"id_{case_number}",
        symlinks=False,
    )

    domain_root = benchmark_root / "travelplanning"
    _prepend_import_paths(domain_root)
    agent_module = importlib.import_module(
        "agent.tools_fn_agent"
    )
    prompt_module = importlib.import_module("agent.prompts")
    agent_module.call_llm = (
        lambda config_name, messages, tools=None: provider.call(
            messages=messages,
            tools=tools,
        )
    )
    agent = agent_module.ToolsFnAgent(
        model=request["model"],
        sample_id=case_number,
        database_base_path=working_database,
        language="en",
    )
    system_prompt = _append_skill(
        prompt_module.get_system_prompt("en"),
        request.get("skill"),
    )
    final_plan, messages = agent.run(
        user_query=_text_field(sample, "query"),
        system_prompt=system_prompt,
        max_llm_calls=_positive_integer(
            request["max_interactions"],
            "max_interactions",
        ),
    )
    portable_messages = agent._serialize_messages(messages)

    format_prompt = prompt_module.get_format_convert_prompt("en")
    converted = _convert_travel_plan(
        str(final_plan),
        format_prompt=format_prompt,
        provider=provider,
        model=_text_field(request, "conversion_model"),
        max_tokens=_positive_integer(
            request["conversion_max_tokens"],
            "conversion_max_tokens",
        ),
        token_limit_field=str(
            request["conversion_token_limit_field"]
        ),
        temperature=_non_negative_number(
            request["conversion_temperature"],
            "conversion_temperature",
        ),
        max_attempts=_positive_integer(
            request["conversion_max_attempts"],
            "conversion_max_attempts",
        ),
    )
    converted_dir = artifact_dir / "converted_plans"
    converted_dir.mkdir()
    converted_path = (
        converted_dir / f"id_{case_number}_converted.json"
    )
    converted_path.write_text(
        json.dumps(converted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    evaluation_dir = artifact_dir / "evaluation"
    evaluation_dir.mkdir()
    evaluator = importlib.import_module(
        "evaluation.eval_converted"
    )
    evaluation = evaluator.process_single_evaluation(
        converted_path,
        [sample],
        evaluation_dir,
        working_database,
        threading.Lock(),
    )
    if not isinstance(evaluation, Mapping) or not evaluation.get(
        "success"
    ):
        raise DeepPlanningBridgeError(
            "official travel evaluation failed"
        )
    scores = evaluation.get("scores")
    if not isinstance(scores, Mapping):
        raise DeepPlanningBridgeError(
            "official travel evaluation omitted scores"
        )
    metrics = {
        name: _finite_metric(scores.get(name), name)
        for name in (
            "case_acc",
            "composite_score",
            "commonsense_weighted_score",
            "personalized_score",
        )
    }
    report_dir = artifact_dir / "reports"
    report_dir.mkdir()
    (report_dir / f"id_{case_number}.txt").write_text(
        str(final_plan),
        encoding="utf-8",
    )
    return str(final_plan), portable_messages, metrics


def _convert_travel_plan(
    plan: str,
    *,
    format_prompt: str,
    provider: _ProviderSession,
    model: str,
    max_tokens: int,
    token_limit_field: str,
    temperature: float,
    max_attempts: int,
) -> Any:
    last_error: Exception | None = None
    for _ in range(max_attempts):
        response = provider.call(
            messages=(
                {"role": "system", "content": format_prompt},
                {"role": "user", "content": plan},
            ),
            model=model,
            max_completion_tokens=max_tokens,
            token_limit_field=token_limit_field,
            temperature=temperature,
            use_reasoning=False,
            use_prompt_cache=False,
        )
        content = response.choices[0].message.content or ""
        try:
            candidate = _extract_json_text(content)
            return json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
    raise DeepPlanningBridgeError(
        "travel plan conversion returned invalid JSON"
    ) from last_error


def _prompt_cached_messages(
    messages: Sequence[Any],
    *,
    minimum_tokens: int,
) -> list[Any]:
    prepared: list[Any] = []
    for item in messages:
        if isinstance(item, Mapping):
            prepared.append(
                json.loads(
                    json.dumps(item, ensure_ascii=False)
                )
            )
        elif hasattr(item, "model_dump"):
            prepared.append(item.model_dump())
        else:
            prepared.append(item)
    for message in prepared:
        if not isinstance(message, dict) or message.get("role") != "system":
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
    messages: list[Any],
    *,
    minimum_tokens: int,
) -> None:
    if (
        not messages
        or not isinstance(messages[-1], dict)
        or messages[-1].get("role") == "system"
    ):
        return
    first_non_system = 0
    while (
        first_non_system < len(messages)
        and isinstance(messages[first_non_system], dict)
        and messages[first_non_system].get("role") == "system"
    ):
        first_non_system += 1
    prefix = "\n".join(
        _content_text(message.get("content", ""))
        for message in messages[first_non_system:]
        if isinstance(message, dict)
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
        json.loads(json.dumps(tool, ensure_ascii=False))
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


def _extract_json_text(value: str) -> str:
    start_tag = "<JSON>"
    end_tag = "</JSON>"
    start = value.find(start_tag)
    end = value.rfind(end_tag)
    if start >= 0 and end > start:
        return value[start + len(start_tag) : end].strip()
    return value.strip()


def _load_request(stream: TextIO) -> dict[str, Any]:
    raw = stream.read()
    if not raw.strip():
        raise DeepPlanningBridgeError("bridge request is empty")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise DeepPlanningBridgeError(
            "bridge request is invalid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise DeepPlanningBridgeError(
            "bridge request must be an object"
        )
    _exact_keys(payload, _REQUEST_KEYS, "request")
    if payload["protocol_version"] != BRIDGE_PROTOCOL_VERSION:
        raise DeepPlanningBridgeError(
            "bridge request protocol is unsupported"
        )
    if payload["method"] not in {"baseline", "skilladam", "skillopt"}:
        raise DeepPlanningBridgeError("bridge method is invalid")
    if payload["split"] not in {"train", "validation", "test"}:
        raise DeepPlanningBridgeError("bridge split is invalid")
    if payload["method"] == "baseline" and payload["skill"] is not None:
        raise DeepPlanningBridgeError(
            "baseline bridge request cannot include a skill"
        )
    if payload["method"] != "baseline":
        _text_field(payload, "skill")
    _text_field(payload, "case_id")
    _text_field(payload, "slice")
    _text_field(payload, "stage")
    _text_field(payload, "model")
    _text_field(payload, "artifact_dir")
    _case_number(payload)
    _positive_integer(payload["max_interactions"], "max_interactions")
    _text_field(payload, "conversion_model")
    _positive_integer(
        payload["conversion_max_tokens"],
        "conversion_max_tokens",
    )
    if payload["conversion_token_limit_field"] not in {
        "max_tokens",
        "max_completion_tokens",
    }:
        raise DeepPlanningBridgeError(
            "conversion_token_limit_field is unsupported"
        )
    _non_negative_number(
        payload["conversion_temperature"],
        "conversion_temperature",
    )
    _positive_integer(
        payload["conversion_max_attempts"],
        "conversion_max_attempts",
    )
    return payload


def _provider_settings(
    request: Mapping[str, Any],
) -> _ProviderSettings:
    provider_env = request["provider_env"]
    if not isinstance(provider_env, Mapping):
        raise DeepPlanningBridgeError(
            "provider_env must be an object"
        )
    _exact_keys(provider_env, _PROVIDER_ENV_KEYS, "provider_env")
    config = _provider_config(request)
    token_field = config["token_limit_field"]
    if token_field not in {"max_tokens", "max_completion_tokens"}:
        raise DeepPlanningBridgeError(
            "token_limit_field is unsupported"
        )
    reasoning_mode = config["reasoning_mode"]
    if reasoning_mode not in {"top_level", "extra_body", "omit"}:
        raise DeepPlanningBridgeError(
            "reasoning_mode is unsupported"
        )
    reasoning_effort = request["reasoning_effort"]
    if reasoning_effort not in {"none", "low", "medium", "high"}:
        raise DeepPlanningBridgeError(
            "reasoning_effort is unsupported"
        )
    return _ProviderSettings(
        api_key_env=_text_field(provider_env, "api_key"),
        base_url_env=_text_field(provider_env, "base_url"),
        timeout_seconds=_positive_number(
            config["timeout_seconds"],
            "timeout_seconds",
        ),
        max_retries=_positive_or_zero_integer(
            config["max_retries"],
            "max_retries",
        ),
        max_completion_tokens=_positive_integer(
            config["max_completion_tokens"],
            "max_completion_tokens",
        ),
        token_limit_field=str(token_field),
        temperature=_non_negative_number(
            config["temperature"],
            "temperature",
        ),
        reasoning_mode=str(reasoning_mode),
        model=_text_field(request, "model"),
        reasoning_effort=str(reasoning_effort),
        enable_prompt_cache=_boolean(
            config["enable_prompt_cache"],
            "enable_prompt_cache",
        ),
        prompt_cache_min_tokens=_positive_integer(
            config["prompt_cache_min_tokens"],
            "prompt_cache_min_tokens",
        ),
        cache_session_id=_cache_session_id(
            config["cache_session_id"],
            enabled=bool(config["enable_prompt_cache"]),
        ),
        max_total_requests=_optional_positive_integer(
            config.get("max_total_requests"),
            "max_total_requests",
        ),
        max_total_input_tokens=_optional_positive_integer(
            config.get("max_total_input_tokens"),
            "max_total_input_tokens",
        ),
        max_total_output_tokens=_optional_positive_integer(
            config.get("max_total_output_tokens"),
            "max_total_output_tokens",
        ),
    )


def _provider_config(
    request: Mapping[str, Any],
) -> Mapping[str, Any]:
    config = request["provider_config"]
    if not isinstance(config, Mapping):
        raise DeepPlanningBridgeError(
            "provider_config must be an object"
        )
    missing = sorted(_PROVIDER_CONFIG_KEYS - set(config))
    unknown = sorted(
        set(config) - _PROVIDER_CONFIG_KEYS - _PROVIDER_BUDGET_KEYS
    )
    if missing or unknown:
        raise DeepPlanningBridgeError(
            "provider_config keys are invalid; "
            f"missing={missing!r}, unknown={unknown!r}"
        )
    return config


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise DeepPlanningBridgeError(
            f"{field_name} must be boolean"
        )
    return value


def _cache_session_id(value: Any, *, enabled: bool) -> str:
    if not isinstance(value, str):
        raise DeepPlanningBridgeError(
            "cache_session_id must be text"
        )
    session_id = value.strip()
    if enabled and not session_id:
        raise DeepPlanningBridgeError(
            "cache_session_id is required when prompt cache is enabled"
        )
    if "\n" in session_id or "\r" in session_id or len(session_id) > 256:
        raise DeepPlanningBridgeError(
            "cache_session_id is invalid"
        )
    return session_id


def _benchmark_root(
    manifest: DeepPlanningRuntimeManifest,
) -> Path:
    return _resolved_root_path(
        manifest.root,
        manifest.benchmark_root,
        expected="directory",
    )


def _resolved_benchmark_path(
    benchmark_root: Path,
    relative: PurePosixPath,
    *,
    expected: str,
) -> Path:
    return _resolved_root_path(
        benchmark_root,
        relative,
        expected=expected,
    )


def _resolved_root_path(
    root: Path,
    relative: PurePosixPath,
    *,
    expected: str,
) -> Path:
    try:
        path = root.joinpath(*relative.parts).resolve(strict=True)
    except OSError:
        raise DeepPlanningBridgeError(
            f"required bridge {expected} is missing"
        ) from None
    try:
        path.relative_to(root)
    except ValueError:
        raise DeepPlanningBridgeError(
            "bridge path escapes its allowed root"
        ) from None
    if expected == "file" and not path.is_file():
        raise DeepPlanningBridgeError(
            "required bridge path is not a file"
        )
    if expected == "directory" and not path.is_dir():
        raise DeepPlanningBridgeError(
            "required bridge path is not a directory"
        )
    return path


def _query_sample(path: Path, case_number: int) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise DeepPlanningBridgeError("query file must be an array")
    matches = [
        row
        for row in payload
        if isinstance(row, Mapping)
        and str(row.get("id")) == str(case_number)
    ]
    if len(matches) != 1:
        raise DeepPlanningBridgeError(
            "query identity is missing or duplicated"
        )
    return matches[0]


def _prepend_import_paths(domain_root: Path) -> None:
    values = (
        domain_root,
        domain_root / "agent",
        domain_root / "tools",
    )
    for value in reversed(values):
        text = str(value)
        if text not in sys.path:
            sys.path.insert(0, text)


def _load_file_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise DeepPlanningBridgeError(
            "could not load the official evaluator"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _append_skill(system_prompt: Any, skill: Any) -> str:
    prompt = _text_value(system_prompt, "official system prompt")
    if skill is None:
        return prompt
    skill_text = _text_value(skill, "skill")
    return (
        f"{prompt}\n\n"
        "# Reusable SkillAdam guidance\n\n"
        f"{skill_text}"
    )


def _last_assistant_content(
    messages: Sequence[Mapping[str, Any]],
) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str):
                return content
    return ""


def _artifact_directory(
    root: Path,
    value: Any,
) -> Path:
    text = _text_value(value, "artifact_dir")
    if "\\" in text:
        raise DeepPlanningBridgeError(
            "artifact_dir must be a portable relative path"
        )
    relative = PurePosixPath(text)
    if relative.is_absolute() or ".." in relative.parts:
        raise DeepPlanningBridgeError(
            "artifact_dir must be a portable relative path"
        )
    try:
        path = root.joinpath(*relative.parts).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError):
        raise DeepPlanningBridgeError(
            "artifact_dir is missing or escapes its root"
        ) from None
    if not path.is_dir():
        raise DeepPlanningBridgeError(
            "artifact_dir must be a directory"
        )
    return path


def _absolute_environment_directory(
    environ: Mapping[str, str],
    name: str,
) -> Path:
    value = _required_environment(environ, name)
    path = Path(value)
    if not path.is_absolute():
        raise DeepPlanningBridgeError(
            f"{name} must contain an absolute path"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise DeepPlanningBridgeError(
            f"{name} directory does not exist"
        ) from None
    if not resolved.is_dir():
        raise DeepPlanningBridgeError(
            f"{name} must contain a directory"
        )
    return resolved


def _redaction_replacements(
    request: Mapping[str, Any],
    environ: Mapping[str, str],
    *,
    runtime_root: Path,
    artifact_root: Path,
) -> tuple[tuple[str, str], ...]:
    provider_env = request["provider_env"]
    assert isinstance(provider_env, Mapping)
    return (
        (
            _required_environment(
                environ,
                _text_field(provider_env, "api_key"),
            ),
            "<REDACTED_CREDENTIAL>",
        ),
        (
            _required_environment(
                environ,
                _text_field(provider_env, "base_url"),
            ),
            "<REDACTED_SERVICE_ADDRESS>",
        ),
        (str(runtime_root), "<RUNTIME_ROOT>"),
        (str(artifact_root), "<ARTIFACT_ROOT>"),
    )


def _redact_json(
    value: Any,
    replacements: Sequence[tuple[str, str]],
) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _redact_json(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [
            _redact_json(item, replacements)
            for item in value
        ]
    if isinstance(value, str):
        return _redact_text(value, replacements)
    return value


def _redact_text(
    value: str,
    replacements: Sequence[tuple[str, str]],
) -> str:
    result = value
    for sensitive, replacement in replacements:
        if sensitive:
            result = result.replace(sensitive, replacement)
    return result


def _exception_diagnostic(exc: Exception) -> str:
    lines: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and len(lines) < 6:
        identity = id(current)
        if identity in seen:
            break
        seen.add(identity)
        message = " ".join(str(current).splitlines()).strip()
        if len(message) > 4096:
            message = f"{message[:4096]} [message truncated]"
        line = type(current).__name__
        if message:
            line = f"{line}: {message}"
        lines.append(line)
        next_error = current.__cause__
        if next_error is None and not current.__suppress_context__:
            next_error = current.__context__
        current = next_error
    return "\ncaused by: ".join(lines)


def _write_log(
    artifact_dir: Path,
    value: str,
    replacements: Sequence[tuple[str, str]],
) -> None:
    path = artifact_dir / "official.log"
    if path.exists():
        return
    redacted = _redact_text(value[:_LOG_LIMIT], replacements)
    if len(value) > _LOG_LIMIT:
        redacted += "\n[official log truncated]\n"
    path.write_text(redacted, encoding="utf-8")


def _write_bridge_usage(
    artifact_dir: Path,
    *,
    status: str,
    usage: Mapping[str, Any] | None,
    diagnostic: str | None,
    replacements: Sequence[tuple[str, str]],
) -> None:
    path = artifact_dir / "bridge_usage.json"
    if path.exists():
        return
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "usage": (
            _redact_json(usage, replacements)
            if usage is not None
            else None
        ),
    }
    if diagnostic is not None:
        payload["diagnostic"] = _redact_text(
            diagnostic,
            replacements,
        )
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())


def _sum_known(
    values: Sequence[UsageRecord],
    field_name: str,
) -> int | None:
    fields = [getattr(item, field_name) for item in values]
    if not fields or any(item is None for item in fields):
        return None
    return sum(int(item) for item in fields if item is not None)


def _exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    label: str,
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        raise DeepPlanningBridgeError(
            f"{label} keys are invalid; "
            f"missing={missing!r}, unknown={unknown!r}"
        )


def _required_environment(
    environ: Mapping[str, str],
    name: str,
) -> str:
    value = environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise DeepPlanningBridgeError(
            f"required environment variable {name!r} is missing"
        )
    return value.strip()


def _text_field(value: Mapping[str, Any], field_name: str) -> str:
    return _text_value(value.get(field_name), field_name)


def _text_value(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeepPlanningBridgeError(
            f"{field_name} must be non-empty text"
        )
    return value.strip()


def _case_number(request: Mapping[str, Any]) -> int:
    value = request.get("case_number")
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeepPlanningBridgeError(
            "case_number must be an integer"
        )
    return value


def _positive_integer(value: Any, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise DeepPlanningBridgeError(
            f"{field_name} must be a positive integer"
        )
    return value


def _optional_positive_integer(
    value: Any,
    field_name: str,
) -> int | None:
    if value is None:
        return None
    return _positive_integer(value, field_name)


def _positive_or_zero_integer(value: Any, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise DeepPlanningBridgeError(
            f"{field_name} must be a non-negative integer"
        )
    return value


def _positive_number(value: Any, field_name: str) -> float:
    number = _finite_metric(value, field_name)
    if number <= 0:
        raise DeepPlanningBridgeError(
            f"{field_name} must be positive"
        )
    return number


def _non_negative_number(value: Any, field_name: str) -> float:
    number = _finite_metric(value, field_name)
    if number < 0:
        raise DeepPlanningBridgeError(
            f"{field_name} must be non-negative"
        )
    return number


def _finite_metric(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeepPlanningBridgeError(
            f"{field_name} must be a finite number"
        )
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        raise DeepPlanningBridgeError(
            f"{field_name} must be a finite number"
        )
    return number


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DeepPlanningBridgeError", "main"]
