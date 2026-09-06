"""Provider-neutral LMB prompt contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from skilladam.types import Method


VENDOR_PROMPT = (
    "You are an expert mathematical reasoning agent solving multiple-choice "
    "questions.\n\n"
    "{skill_section}## Task Format\n"
    "You will receive one mathematics multiple-choice question and its "
    "answer choices.\n"
    "Reason carefully about quantifiers, hypotheses, extremal wording, and "
    "exact equality conditions.\n\n"
    "## Answer Format\n"
    "Think step by step, then provide your final answer inside "
    "<answer>...</answer> tags.\n"
    "Inside the tags, output only the single choice label, such as A or C."
    "\n\nExample:\n<answer>B</answer>"
)


def build_messages(
    *,
    question: str,
    choices: Sequence[Mapping[str, str]],
    method: Method,
    skill: str | None,
) -> tuple[dict[str, str], ...]:
    """Build the shared SkillAdam/SkillOpt target-agent prompt."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("LMB question must be non-empty text")
    clean_skill = skill.strip() if isinstance(skill, str) else ""
    skill_section = (
        f"## Skill\n{clean_skill}\n\n" if clean_skill else ""
    )
    option_lines = "\n".join(
        f"{choice['label']}. {choice['text']}" for choice in choices
    )
    return (
        {
            "role": "system",
            "content": VENDOR_PROMPT.format(skill_section=skill_section),
        },
        {
            "role": "user",
            "content": (
                f"## Question\n{question.strip()}\n\n"
                f"## Choices\n{option_lines}"
            ),
        },
    )


def prompt_profile_for_method(method: Method) -> str:
    """Return the common target prompt profile for public methods."""

    return "lmb-math-mcq"


def question_from_payload(payload: Mapping[str, Any]) -> str:
    """Accept both the historical ``question`` and fixture ``problem`` key."""

    question = payload.get("question", payload.get("problem"))
    if not isinstance(question, str) or not question.strip():
        raise ValueError("LMB question must be non-empty text")
    return question.strip()
