"""Provider-neutral ALFWorld step-prompt contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from skilladam.types import Method


SYSTEM_PROMPT = (
    "You are an expert agent operating in the ALFRED Embodied Environment."
)

USER_PROMPT_TEMPLATE = (
    "Your task is to: {task_description}\n"
    "Prior to this step, you have already taken {step_count} step(s).\n"
    "Below are the most recent observations and actions:\n"
    "{action_history}\n\n"
    "You are now at step {current_step} and your current observation is: "
    "{current_observation}\n"
    "Your admissible actions of the current situation are: "
    "[{admissible_actions}].\n\n"
    "Now it's your turn to take an action.\n"
    "You should first reason step-by-step about the current situation. "
    "This reasoning process MUST be enclosed within <think> </think> tags.\n"
    "Once you've finished your reasoning, you should choose an admissible "
    "action for current step and present it within <action> </action> tags."
)

_ACTION_RE = re.compile(
    r"<action>(.*?)</action>",
    flags=re.IGNORECASE | re.DOTALL,
)
_THINK_RE = re.compile(
    r"<think>(.*?)</think>",
    flags=re.IGNORECASE | re.DOTALL,
)


def build_step_messages(
    *,
    task_description: str,
    current_observation: str,
    admissible_actions: Sequence[str],
    history: Sequence[Mapping[str, Any]],
    method: Method,
    skill: str | None,
    history_length: int = 2,
) -> tuple[dict[str, str], ...]:
    """Build one audited ALFWorld target-agent interaction."""

    task = _non_empty_text(task_description, "task description")
    observation = _non_empty_text(
        current_observation,
        "current observation",
    )
    actions = _text_sequence(admissible_actions, "admissible actions")
    if (
        isinstance(history_length, bool)
        or not isinstance(history_length, int)
        or history_length < 0
    ):
        raise ValueError(
            "ALFWorld history_length must be a non-negative integer"
        )
    clean_skill = skill.strip() if isinstance(skill, str) else ""
    skill_section = _build_skill_section(clean_skill)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        task_description=task,
        step_count=len(history),
        action_history=_format_history(
            history,
            max_recent=history_length,
        ),
        current_step=len(history) + 1,
        current_observation=observation,
        admissible_actions=", ".join(actions),
    )

    if method == "skilladam":
        system_prompt = SYSTEM_PROMPT + skill_section
    else:
        system_prompt = SYSTEM_PROMPT
    if method == "skillopt" and skill_section:
        user_prompt = skill_section + "\n" + user_prompt
    return (
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    )


def prompt_profile_for_method(method: Method) -> str:
    """Return the historical skill-placement profile."""

    if method == "skilladam":
        return "alfworld-system-skill"
    if method == "skillopt":
        return "alfworld-user-skillopt"
    return "alfworld-baseline"


def extract_action(text: str) -> str:
    """Extract the first historical action tag and normalize its action."""

    if not isinstance(text, str):
        raise ValueError("ALFWorld response must be text")
    match = _ACTION_RE.search(text)
    if match is None:
        raise ValueError(
            "ALFWorld response must contain <action>...</action>"
        )
    return normalize_action(match.group(1))


def extract_reasoning(text: str) -> str:
    """Extract the first optional think block."""

    if not isinstance(text, str):
        raise ValueError("ALFWorld response must be text")
    match = _THINK_RE.search(text)
    return match.group(1).strip() if match is not None else ""


def normalize_action(value: str) -> str:
    """Normalize an ALFWorld text action for fixture comparison."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("ALFWorld action must be non-empty text")
    return " ".join(value.strip().lower().split())


def _build_skill_section(skill: str) -> str:
    if not skill:
        return ""
    return (
        "\n\n## Skill Knowledge\n"
        "Below is a skill document with learned strategies. "
        "Use these guidelines to inform your decisions:\n\n"
        f"{skill}\n"
    )


def _format_history(
    history: Sequence[Mapping[str, Any]],
    *,
    max_recent: int,
) -> str:
    if (
        isinstance(history, (str, bytes))
        or not isinstance(history, Sequence)
        or any(not isinstance(item, Mapping) for item in history)
    ):
        raise ValueError(
            "ALFWorld history must be a sequence of objects"
        )
    if not history or max_recent == 0:
        return "(none)"
    lines: list[str] = []
    for index, record in enumerate(history[-max_recent:]):
        step = record.get("step", len(history) - max_recent + index)
        if isinstance(step, bool) or not isinstance(step, int):
            raise ValueError("ALFWorld history step must be an integer")
        action = record.get("action", "")
        feedback = record.get("env_feedback", "")
        if not isinstance(action, str) or not isinstance(feedback, str):
            raise ValueError(
                "ALFWorld history action and env_feedback must be text"
            )
        if len(feedback) > 200:
            feedback = feedback[:200] + "..."
        lines.append(
            f"[Step {step}: action='{action}', "
            f"observation='{feedback}']"
        )
    return "\n".join(lines)


def _text_sequence(
    value: Any,
    field_name: str,
) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(
            f"ALFWorld {field_name} must be a non-empty string sequence"
        )
    return tuple(item.strip() for item in value)


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"ALFWorld {field_name} must be non-empty text"
        )
    return value.strip()
