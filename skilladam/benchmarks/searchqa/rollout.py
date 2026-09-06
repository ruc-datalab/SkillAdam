"""Provider-neutral SearchQA rollout prompt and answer parsing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from skilladam.types import BenchmarkCase, Method


SKILLADAM_SYSTEM_PROMPT = (
    "You are an expert question answering agent.\n\n"
    "## Task Format\n"
    "You will receive a CONTEXT containing document passages and a "
    "QUESTION. Read the context carefully and answer the question based "
    "on the information provided.\n\n"
    "{skill_section}"
    "## Answer Format\n"
    "Think step by step, then provide your final answer inside "
    "<answer>...</answer> tags. Keep your answer concise — typically a "
    "few words or a short phrase. Do not repeat the question. Do not "
    "include unnecessary explanation in the answer tags.\n\n"
    "Example:\n"
    "<answer>Abraham Lincoln</answer>"
)

SKILLOPT_SYSTEM_PROMPT = (
    "You are answering a question based on the provided search context.\n\n"
    "{skill_section}"
    "Read the context carefully and output only the answer text.\n"
    "Do not explain, do not repeat the question, do not add any prefix."
)

_ANSWER_TAG_RE = re.compile(
    r"<answer>(.*?)</answer>",
    flags=re.IGNORECASE | re.DOTALL,
)


def build_messages(
    case: BenchmarkCase,
    *,
    method: Method,
    skill: str | None,
) -> tuple[dict[str, str], ...]:
    """Build the historical SkillAdam or SkillOpt single-turn prompt."""

    prompt_profile = prompt_profile_for_method(method)
    clean_skill = skill.strip() if isinstance(skill, str) else ""
    if prompt_profile == "skillopt":
        template = SKILLOPT_SYSTEM_PROMPT
        skill_section = (
            f"## Skill\n{clean_skill}\n\n" if clean_skill else ""
        )
    else:
        template = SKILLADAM_SYSTEM_PROMPT
        skill_section = f"{clean_skill}\n\n" if clean_skill else ""

    system_prompt = template.replace("{skill_section}", skill_section)
    user_prompt = (
        f"## Context\n{render_context(case.payload)}\n\n"
        f"## Question\n{question_text(case.payload)}"
    )
    return (
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    )


def prompt_profile_for_method(method: Method) -> str:
    """Return the explicit prompt profile used by one public method."""

    return "skillopt" if method == "skillopt" else "skilladam"


def question_text(payload: Mapping[str, Any]) -> str:
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("SearchQA question must be non-empty text")
    return question


def render_context(payload: Mapping[str, Any]) -> str:
    context = payload.get("context")
    if isinstance(context, str) and context.strip():
        return context
    documents = payload.get("documents")
    if (
        isinstance(documents, (str, bytes))
        or not isinstance(documents, Sequence)
        or not documents
    ):
        raise ValueError("SearchQA payload must contain context or documents")
    rendered: list[str] = []
    for document in documents:
        if not isinstance(document, str) or not document.strip():
            raise ValueError("SearchQA documents must contain non-empty text")
        text = document.strip()
        rendered.append(text if text.startswith("[DOC]") else f"[DOC] {text}")
    return "\n".join(rendered)


def extract_answer(raw_result: Mapping[str, Any]) -> tuple[str, str]:
    """Extract answer text and retain the original response when present."""

    if "answer" in raw_result:
        answer = raw_result["answer"]
        if not isinstance(answer, str):
            raise ValueError("SearchQA answer must be text")
        return answer.strip(), str(raw_result.get("response", answer))

    response = raw_result.get("response")
    if not isinstance(response, str):
        raise ValueError(
            "SearchQA raw result must contain a text answer or response"
        )
    matches = _ANSWER_TAG_RE.findall(response)
    if matches:
        return matches[-1].strip(), response
    lines = [line.strip() for line in response.splitlines() if line.strip()]
    if lines:
        return lines[-1], response
    return response.strip(), response
