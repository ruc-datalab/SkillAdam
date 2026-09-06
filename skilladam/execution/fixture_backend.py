"""Offline backend for checked-in Apache-2.0 synthetic fixtures."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from skilladam.execution.backend import (
    BackendConfigurationError,
    BackendProtocolError,
)
from skilladam.execution.contracts import (
    BackendInit,
    BatchExecution,
    GenerationRequest,
    GenerationResult,
    RawRollout,
    ToolCall,
)
from skilladam.usage import normalize_usage


class FixtureBackend:
    """Replay exact synthetic raw results and scripted generations."""

    def __init__(self, init: BackendInit) -> None:
        self._context = init.context
        self._results = _load_fixture_results(
            init.data_root / "cases.json",
            benchmark=init.context.benchmark,
        )
        unknown = sorted(set(init.config) - {"generations"})
        if unknown:
            raise BackendConfigurationError(
                f"fixture backend config has unknown keys: {unknown!r}"
            )
        generations = init.config.get("generations", ())
        if (
            isinstance(generations, (str, bytes))
            or not isinstance(generations, Sequence)
            or any(not isinstance(item, Mapping) for item in generations)
        ):
            raise BackendConfigurationError(
                "fixture generations must be an array of objects"
            )
        self._generations = tuple(generations)
        self._generation_index = 0

    def execute_batch(
        self,
        execution: BatchExecution,
    ) -> tuple[RawRollout, ...]:
        if execution.context != self._context:
            raise BackendProtocolError(
                "fixture batch context does not match backend initialization"
            )
        results: list[RawRollout] = []
        for request in execution.requests:
            case_id = request.case.case_id
            try:
                raw_result = self._results[case_id]
            except KeyError as exc:
                raise BackendProtocolError(
                    f"fixture has no raw result for case {case_id!r}"
                ) from exc
            results.append(
                RawRollout(case_id=case_id, result=raw_result)
            )
        return tuple(results)

    def generate(
        self,
        request: GenerationRequest,
    ) -> GenerationResult:
        if request.context != self._context:
            raise BackendProtocolError(
                "fixture generation context does not match initialization"
            )
        if not self._generations:
            raise BackendProtocolError(
                "fixture backend requires a scripted generation"
            )
        if self._generation_index >= len(self._generations):
            raise BackendProtocolError(
                "fixture scripted generation queue is exhausted"
            )
        item = self._generations[self._generation_index]
        expected_stage = item.get("stage")
        if expected_stage != request.stage:
            raise BackendProtocolError(
                "fixture generation expected "
                f"{expected_stage!r}, got {request.stage!r}"
            )
        self._generation_index += 1

        text = item.get("text", "")
        if not isinstance(text, str):
            raise BackendConfigurationError(
                "fixture generation text must be text"
            )
        usage_value = item.get("usage")
        usage = (
            (normalize_usage(usage_value),)
            if usage_value is not None
            else ()
        )
        raw_tool_calls = item.get("tool_calls", ())
        if (
            isinstance(raw_tool_calls, (str, bytes))
            or not isinstance(raw_tool_calls, Sequence)
            or any(not isinstance(call, Mapping) for call in raw_tool_calls)
        ):
            raise BackendConfigurationError(
                "fixture generation tool_calls must be an array of objects"
            )
        tool_calls: list[ToolCall] = []
        for index, call in enumerate(raw_tool_calls):
            arguments = call.get("arguments", {})
            try:
                tool_calls.append(
                    ToolCall(
                        name=call.get("name"),
                        arguments=arguments,
                    )
                )
            except ValueError as exc:
                raise BackendConfigurationError(
                    f"invalid fixture tool call at index {index}: {exc}"
                ) from exc
        return GenerationResult(
            text=text,
            usage=usage,
            tool_calls=tuple(tool_calls),
        )

    def public_metadata(self) -> Mapping[str, Any]:
        return {
            "kind": "synthetic_fixture",
            "network": False,
        }


def _load_fixture_results(
    path: Path,
    *,
    benchmark: str,
) -> dict[str, Mapping[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackendConfigurationError(
            f"could not load synthetic fixture: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise BackendConfigurationError(
            "synthetic fixture must contain a JSON object"
        )
    schema_version = payload.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise BackendConfigurationError(
            "synthetic fixture must use schema_version 1"
        )
    if payload.get("benchmark") != benchmark:
        raise BackendConfigurationError(
            "synthetic fixture benchmark does not match backend context"
        )
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping) or (
        provenance.get("kind") != "synthetic"
        or provenance.get("license") != "Apache-2.0"
        or provenance.get("contains_official_benchmark_data") is not False
    ):
        raise BackendConfigurationError(
            "fixture backend accepts only Apache-2.0 synthetic data"
        )
    cases = payload.get("cases")
    if (
        isinstance(cases, (str, bytes))
        or not isinstance(cases, Sequence)
        or not cases
        or any(not isinstance(item, Mapping) for item in cases)
    ):
        raise BackendConfigurationError(
            "synthetic fixture cases must be a non-empty array of objects"
        )
    results: dict[str, Mapping[str, Any]] = {}
    for index, case in enumerate(cases):
        case_id = case.get("case_id")
        raw_result = case.get("raw_result")
        if not isinstance(case_id, str) or not case_id.strip():
            raise BackendConfigurationError(
                f"synthetic fixture case {index} has invalid case_id"
            )
        if case_id in results:
            raise BackendConfigurationError(
                f"duplicate synthetic fixture case_id {case_id!r}"
            )
        if not isinstance(raw_result, Mapping):
            raise BackendConfigurationError(
                f"synthetic fixture case {case_id!r} has no raw_result"
            )
        results[case_id] = raw_result
    return results


__all__ = ["FixtureBackend"]
