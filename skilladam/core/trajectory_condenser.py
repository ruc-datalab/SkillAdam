"""Condense raw benchmark trajectories into Stage1-ready markdown.

Renders the agreed-upon format:
  ## Query
  [original user query]

  ## Execution Trace
  ### Turn 1
  **Reasoning:** ...
  **Tool Calls:**
  - `tool_name(args)` -> [result summary]
  ...
  ### Turn N (Final)
  **Final Answer:** ...

  ## Outcome
  - Label: success / failure
  - Failure reason: ...
"""

from __future__ import annotations

import json
from typing import Any

_TOOL_RESULT_HEAD = 800
_TOOL_RESULT_TAIL = 150
_REASONING_MAX_CHARS = 2000
_FINAL_ANSWER_MAX_CHARS = 3000


def condense_trajectory_md(
    *,
    messages: list[dict[str, Any]],
    outcome_label: str,
    query: str = "",
    final_answer: str = "",
    judgment_reason: str = "",
) -> str:
    """Render messages into a condensed markdown trajectory."""
    sections: list[str] = []

    user_query = query or _extract_user_query(messages)
    sections.append(f"## Query\n\n{user_query.strip()}")

    turns = _build_turns(messages)
    sections.append("## Execution Trace")
    for i, turn in enumerate(turns, 1):
        is_final = i == len(turns)
        header = f"### Turn {i}" + (" (Final)" if is_final else "")
        sections.append(header)

        if turn["reasoning"]:
            reasoning = _truncate(turn["reasoning"], _REASONING_MAX_CHARS)
            sections.append(f"**Reasoning:** {reasoning}")

        if turn["tool_calls"]:
            sections.append("**Tool Calls:**")
            for tc in turn["tool_calls"]:
                sections.append(tc)

    if final_answer:
        truncated = _truncate(final_answer, _FINAL_ANSWER_MAX_CHARS)
        sections.append(f"## Final Answer\n\n{truncated}")

    outcome_lines = [
        "## Outcome",
        f"- Label: {str(outcome_label or 'unknown').strip()}",
    ]
    if judgment_reason:
        outcome_lines.append(
            f"- Judgment: {_truncate(judgment_reason.strip(), _REASONING_MAX_CHARS)}"
        )
    sections.append("\n".join(outcome_lines))

    return "\n\n".join(sections)


def _extract_user_query(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return "(no user query found)"


def _build_turns(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group messages into assistant turns with their tool results."""
    turns: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") != "assistant":
            i += 1
            continue

        reasoning = str(msg.get("content", "")).strip()
        raw_tool_calls = msg.get("tool_calls") or []
        tool_call_names = []
        for tc in raw_tool_calls:
            func = tc.get("function", {}) if isinstance(tc, dict) else {}
            name = func.get("name", "")
            args = func.get("arguments", "")
            tool_call_names.append((name, _compact_args(args)))

        # Collect tool results
        tool_results: list[str] = []
        j = i + 1
        tc_idx = 0
        while j < len(messages) and messages[j].get("role") == "tool":
            result_content = str(messages[j].get("content", ""))
            result_summary = _summarize_tool_result(result_content)
            if tc_idx < len(tool_call_names):
                tc_name, tc_args = tool_call_names[tc_idx]
                tool_results.append(f"- `{tc_name}({tc_args})` -> {result_summary}")
            else:
                tool_name = str(messages[j].get("name", "unknown"))
                tool_results.append(f"- `{tool_name}(...)` -> {result_summary}")
            tc_idx += 1
            j += 1

        # Handle tool calls without results (if more calls than results)
        while tc_idx < len(tool_call_names):
            tc_name, tc_args = tool_call_names[tc_idx]
            tool_results.append(f"- `{tc_name}({tc_args})` -> (no result)")
            tc_idx += 1

        turns.append({
            "reasoning": reasoning,
            "tool_calls": tool_results,
        })
        i = j
    return turns


def _compact_args(args: Any) -> str:
    """Compact tool call arguments to a short string."""
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return _compact_dict(parsed)
        except (json.JSONDecodeError, TypeError):
            return _truncate(args, 120)
    if isinstance(args, dict):
        return _compact_dict(args)
    return str(args)[:120]


def _compact_dict(d: dict[str, Any]) -> str:
    parts = []
    for k, v in d.items():
        v_str = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else f'"{v}"'
        if len(v_str) > 60:
            v_str = v_str[:57] + "..."
        parts.append(f"{k}={v_str}")
    result = ", ".join(parts)
    if len(result) > 200:
        result = result[:197] + "..."
    return result


def _summarize_tool_result(content: str) -> str:
    """Truncate long tool results with head/tail."""
    content = content.strip()
    if not content:
        return "(empty)"
    limit = _TOOL_RESULT_HEAD + _TOOL_RESULT_TAIL
    if len(content) <= limit:
        return f"[{len(content)} chars] {content}"
    head = content[:_TOOL_RESULT_HEAD]
    tail = content[-_TOOL_RESULT_TAIL:]
    return f"[{len(content)} chars, truncated] {head}\n...[truncated]...\n{tail}"


def turns_to_messages(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct OpenAI-style messages from canonical RawLikeTurn dicts.

    This is the inverse of ``trace_converter._build_turns()`` — it converts
    the compact ``turns`` stored in ``canonical_traces.jsonl`` back into a
    flat list of ``assistant`` and ``tool`` role messages that
    ``condense_trajectory_md`` and the Stage1 prompt builder can consume.
    """
    messages: list[dict[str, Any]] = []
    for turn in turns:
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": turn.get("assistant_text_raw", ""),
        }
        raw_tool_calls = turn.get("tool_calls") or []
        if raw_tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "function": {
                        "name": tc.get("tool_name", ""),
                        "arguments": json.dumps(
                            tc.get("args_raw_compact", {}), ensure_ascii=False
                        ),
                    }
                }
                for tc in raw_tool_calls
            ]
        messages.append(assistant_msg)

        for tr in turn.get("tool_results") or []:
            content = tr.get("result_raw_compact", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            messages.append({
                "role": "tool",
                "content": content,
                "name": tr.get("tool_name", ""),
            })
    return messages


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 20] + "\n...[truncated]..."
