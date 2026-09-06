"""Bounded trajectory rendering for SkillOpt reflection calls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any


DEEPPLANNING_TRAJECTORY_MAX_CHARS = 12_000
_TRUNCATION_MARKER = "\n...[middle truncated]...\n"


def prepare_reflection_traces(
    benchmark: str,
    traces: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    """Return optimizer-facing traces without changing rollout artifacts.

    The historical SkillOpt analyst formatted DeepPlanning conversations
    with per-field limits and a 12,000-character whole-trajectory limit.
    Other benchmarks retain their already-portable trace representation.
    """

    if benchmark != "deepplanning":
        return traces
    prepared: list[Mapping[str, Any]] = []
    for trace in traces:
        trajectory = trace.get("trajectory")
        if trajectory is None:
            prepared.append(dict(trace))
            continue
        if (
            isinstance(trajectory, (str, bytes))
            or not isinstance(trajectory, Sequence)
        ):
            raise ValueError(
                "DeepPlanning SkillOpt trajectory must be a sequence"
            )
        compact: dict[str, Any] = {
            "case_id": trace.get("case_id"),
            "trajectory": format_deepplanning_trajectory(trajectory),
        }
        for key in ("metrics", "metadata"):
            if key in trace:
                compact[key] = trace[key]
        prepared.append(compact)
    return tuple(prepared)


def format_deepplanning_trajectory(
    conversation: Sequence[object],
    *,
    max_chars: int = DEEPPLANNING_TRAJECTORY_MAX_CHARS,
) -> str:
    """Behaviorally port SkillOpt's bounded analyst trace formatter."""

    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise ValueError("DeepPlanning trajectory limit must be an integer")
    if max_chars < 1:
        raise ValueError("DeepPlanning trajectory limit must be positive")

    lines: list[str] = []
    for item in conversation:
        if not isinstance(item, Mapping):
            lines.append(f"[agent] {_clip_text(item, 500)}")
            continue
        if item.get("type") == "tool_call":
            command = _format_explicit_tool_call(item)
            lines.append(f"[action] {_clip_text(command, 500)}")
            lines.append(
                f"[obs]    {_clip_text(_tool_observation(item), 800)}"
            )
            continue
        if "action" in item and "env_feedback" in item:
            step = item.get("step", "?")
            reasoning = _clip_text(item.get("reasoning"), 300)
            action = _clip_text(item.get("action"), 200)
            feedback = _clip_text(item.get("env_feedback"), 500)
            if reasoning:
                lines.append(f"[step {step} think] {reasoning}")
            lines.append(f"[step {step} action] {action}")
            lines.append(f"[step {step} obs]    {feedback}")
            continue

        role = str(item.get("role") or "agent")
        content = _content_to_text(item.get("content", ""))
        if role == "system":
            lines.append(f"[verification] {_clip_text(content, 2000)}")
            continue
        if role == "assistant":
            if content:
                lines.append(f"[assistant] {_clip_text(content, 500)}")
            for tool_call in _tool_calls(item):
                command = _format_assistant_tool_call(tool_call)
                lines.append(f"[action] {_clip_text(command, 500)}")
                lines.append("[obs]    ")
            continue
        if role == "tool":
            name = str(
                item.get("name")
                or item.get("tool_name")
                or "tool"
            )
            lines.append(f"[action] {_clip_text(name, 500)}")
            lines.append(f"[obs]    {_clip_text(content, 800)}")
            continue
        lines.append(f"[{role}] {_clip_text(content, 500)}")

    rendered = "\n".join(lines)
    if len(rendered) > max_chars:
        half = max_chars // 2
        rendered = (
            rendered[:half]
            + _TRUNCATION_MARKER
            + rendered[-half:]
        )
    return rendered


def _tool_calls(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = item.get("tool_calls")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(call for call in raw if isinstance(call, Mapping))


def _format_assistant_tool_call(
    tool_call: Mapping[str, Any],
) -> str:
    function = tool_call.get("function")
    if not isinstance(function, Mapping):
        function = {}
    name = str(function.get("name") or tool_call.get("name") or "tool")
    arguments = function.get(
        "arguments",
        tool_call.get("arguments", ""),
    )
    return _format_command(name, arguments)


def _format_explicit_tool_call(item: Mapping[str, Any]) -> str:
    name = str(
        item.get("name")
        or item.get("tool")
        or item.get("cmd")
        or "tool"
    )
    arguments = item.get("arguments", item.get("args", ""))
    return _format_command(name, arguments)


def _tool_observation(item: Mapping[str, Any]) -> str:
    return _content_to_text(
        item.get("observation", item.get("obs", ""))
    )


def _format_command(name: str, arguments: object) -> str:
    if arguments in ("", None, {}):
        return name
    if isinstance(arguments, str):
        rendered = arguments
    else:
        rendered = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
        )
    return f"{name} {rendered}"


def _content_to_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(
        content,
        (str, bytes),
    ):
        parts = []
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(_content_to_text(item))
        return "\n".join(part for part in parts if part)
    if isinstance(content, Mapping):
        return json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
        )
    return str(content)


def _clip_text(value: object, limit: int) -> str:
    if value is None:
        return ""
    return str(value)[:limit]
