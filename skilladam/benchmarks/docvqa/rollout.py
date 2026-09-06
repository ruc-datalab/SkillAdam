"""Provider-neutral DocVQA multimodal request construction."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
import mimetypes
from pathlib import Path
from typing import Any

from skilladam.types import BenchmarkCase, Method, PUBLIC_METHODS


PROMPTS_DIR = Path(__file__).with_name("prompts")
ROLLOUT_SYSTEM_TEMPLATE = (
    PROMPTS_DIR / "rollout_system.md"
).read_text(encoding="utf-8")
_ANSWER_INSTRUCTION = (
    "Return the final answer inside <answer>...</answer>."
)
_IMAGE_DETAILS = {"auto", "low", "high"}


def build_messages(
    case: BenchmarkCase,
    *,
    method: Method,
    skill: str | None,
    image_detail: str = "auto",
    image_root: Path | None = None,
) -> tuple[dict[str, Any], ...]:
    """Build the historical single-turn text-or-image message pair."""

    if method not in PUBLIC_METHODS:
        raise ValueError(f"unsupported DocVQA method {method!r}")
    if method == "baseline" and isinstance(skill, str) and skill.strip():
        raise ValueError("DocVQA baseline cannot include a skill")
    if image_detail not in _IMAGE_DETAILS:
        raise ValueError(
            "DocVQA image_detail must be one of auto, low, or high"
        )
    question = _non_empty_text(
        case.payload.get("question"),
        "question",
    )
    document = case.payload.get("document")
    if not isinstance(document, Mapping):
        raise ValueError("DocVQA document must be an object")

    system = {
        "role": "system",
        "content": _system_prompt(method, skill),
    }
    user_text = f"{question}\n\n{_ANSWER_INSTRUCTION}"
    document_text = document.get("text")
    image_path = _resolve_image_path(document, image_root)
    if image_path is None:
        text = _non_empty_text(document_text, "document.text")
        user: dict[str, Any] = {
            "role": "user",
            "content": f"{user_text}\n\nDocument text fixture:\n{text}",
        }
    else:
        image_url: dict[str, str] = {
            "url": _image_to_data_uri(image_path),
        }
        if image_detail != "auto":
            image_url["detail"] = image_detail
        user = {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": image_url},
            ],
        }
    return system, user


def prompt_profile_for_method(method: Method) -> str:
    if method not in PUBLIC_METHODS:
        raise ValueError(f"unsupported DocVQA method {method!r}")
    return f"docvqa-system-{method}"


def sanitize_messages(
    messages: Sequence[Mapping[str, Any]],
    image_basename: str,
) -> tuple[dict[str, Any], ...]:
    """Replace inline image data with a small trace-safe placeholder."""

    sanitized: list[dict[str, Any]] = []
    for message in messages:
        copied = dict(message)
        content = copied.get("content")
        if isinstance(content, Sequence) and not isinstance(
            content,
            (str, bytes, bytearray),
        ):
            parts: list[Any] = []
            for part in content:
                if (
                    isinstance(part, Mapping)
                    and part.get("type") == "image_url"
                ):
                    parts.append(
                        {
                            "type": "text",
                            "text": f"[image: {image_basename}]",
                        }
                    )
                elif isinstance(part, Mapping):
                    parts.append(dict(part))
                else:
                    parts.append(part)
            copied["content"] = parts
        sanitized.append(copied)
    return tuple(sanitized)


def _system_prompt(method: Method, skill: str | None) -> str:
    skill_section = ""
    if method != "baseline" and isinstance(skill, str) and skill.strip():
        skill_section = f"## Skill\n{skill.strip()}\n\n"
    return ROLLOUT_SYSTEM_TEMPLATE.replace(
        "{skill_section}",
        skill_section,
    )


def _resolve_image_path(
    document: Mapping[str, Any],
    image_root: Path | None,
) -> Path | None:
    absolute = document.get("image_path_abs")
    if absolute is not None:
        return Path(_non_empty_text(absolute, "document.image_path_abs"))
    relative = document.get("image_path")
    if relative is None:
        return None
    relative_text = _non_empty_text(relative, "document.image_path")
    if image_root is None:
        raise ValueError(
            "DocVQA image_root is required for a relative image path"
        )
    return Path(image_root) / relative_text


def _image_to_data_uri(path: Path) -> str:
    try:
        image_bytes = path.read_bytes()
    except OSError as exc:
        raise ValueError(
            f"DocVQA image could not be read at {path}: {exc}"
        ) from exc
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"DocVQA {field_name} must be non-empty text")
    return value.strip()
