"""Provider-neutral OfficeQA prompt contract and answer parsing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from skilladam.types import BenchmarkCase, Method


TOOL_SCHEMAS = (
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": (
                "Find candidate local document files by filename or "
                "relative-path glob pattern."
            ),
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read a local text document excerpt by path and line window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search a local text document for a literal pattern and "
                "return matching lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern", "path"],
            },
        },
    },
)

VENDOR_PROMPT = """\
You are an expert OfficeQA agent working over local Treasury bulletin text files.

{skill_section}## Rules
1. Use only the provided local document tools to inspect candidate files.
2. Narrow to the most relevant file before reading long passages.
3. Prefer short targeted searches, then small reads around matching evidence.
4. Do not invent values that are not grounded in the retrieved text.
5. When the question requires arithmetic, compute only after extracting the exact operands.
6. If you have enough evidence, return the final answer inside <answer>...</answer>.

## Tool Use
Use the provided function tools directly when you need them. Prefer searching and small reads before answering. Do not ask the user for permission to use tools; just call the tools.

## Final Answer Format
When you are ready to answer, emit the final answer inside <answer>...</answer> and do not request another tool."""

_FINAL_RE = re.compile(
    r"<answer>(.*?)</answer>",
    flags=re.IGNORECASE | re.DOTALL,
)


def build_messages(
    case: BenchmarkCase,
    *,
    method: Method,
    skill: str | None,
) -> tuple[dict[str, str], ...]:
    """Build the shared SkillAdam/SkillOpt offline local-tool prompt."""

    clean_skill = skill.strip() if isinstance(skill, str) else ""
    skill_section = (
        f"## Skill\n{clean_skill}\n\n" if clean_skill else ""
    )
    system_prompt = VENDOR_PROMPT.format(skill_section=skill_section)
    user_prompt = _build_user_prompt(case.payload)
    return (
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    )


def prompt_profile_for_method(method: Method) -> str:
    """Return the shared target-agent profile for every public method."""

    return "officeqa-offline-local-tools"


def extract_answer(raw_result: Mapping[str, Any]) -> tuple[str, str]:
    """Extract an explicit answer or the first historical answer tag."""

    for key in ("answer", "predicted_answer"):
        if key in raw_result:
            answer = raw_result[key]
            if not isinstance(answer, str):
                raise ValueError("OfficeQA answer must be text")
            return answer.strip(), str(raw_result.get("response", answer))

    response = raw_result.get("response")
    if not isinstance(response, str):
        raise ValueError(
            "OfficeQA raw result must contain a text answer or response"
        )
    match = _FINAL_RE.search(response)
    if match is not None:
        return match.group(1).strip(), response
    lines = [line.strip() for line in response.splitlines() if line.strip()]
    if lines:
        return lines[-1], response
    return response.strip(), response


def _build_user_prompt(payload: Mapping[str, Any]) -> str:
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("OfficeQA question must be non-empty text")
    parts = [f"## Question\n{question.strip()}"]

    document_text = payload.get("document_text")
    if document_text is not None:
        if not isinstance(document_text, str) or not document_text.strip():
            raise ValueError("OfficeQA document_text must be non-empty text")
        parts.append(
            "## Oracle Parsed Pages\n"
            + document_text.strip()
        )

    source_files = _optional_text_sequence(
        payload.get("source_files"),
        "source_files",
    )
    file_block = (
        "\n".join(f"- {path}" for path in source_files[:20])
        if source_files
        else "- none resolved"
    )
    parts.append(f"## Candidate Files\n{file_block}")

    source_docs = _optional_text_sequence(
        payload.get("source_docs"),
        "source_docs",
    )
    if source_docs:
        parts.append(
            "## Source Hints\n"
            + "\n".join(f"- {hint}" for hint in source_docs)
        )
    return "\n\n".join(parts)


def _optional_text_sequence(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(
            f"OfficeQA {field_name} must be a string sequence"
        )
    return tuple(item.strip() for item in value)
