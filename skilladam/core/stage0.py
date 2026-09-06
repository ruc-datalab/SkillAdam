"""Benchmark-neutral Stage0 prompt, validation, and execution primitives."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROMPTS_DIR = Path(__file__).with_name("prompts")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TOP_LEVEL_KEYS = {"metadata_json", "skill_body_md"}
METADATA_KEYS = {"name", "description", "when_to_use"}
LEAKAGE_MARKERS = (
    "system_prompt_level",
    "system_prompt_zh",
    "system_prompt_en",
)
LEAKAGE_PATTERNS = (
    re.compile(r"\bcase_[0-9]+\b"),
    re.compile(r"\brepeat_[0-9]+\b"),
    re.compile(r"\bround_[0-9]+\b"),
    re.compile(r"\bpair_[0-9]+\b"),
)


class Stage0ValidationError(ValueError):
    """Raised when a Stage0 completion violates the public output contract."""


class Stage0WordLimitError(Stage0ValidationError):
    """Raised with structured word-count evidence for a repair attempt."""

    def __init__(self, *, actual_words: int, max_words: int) -> None:
        self.actual_words = actual_words
        self.max_words = max_words
        super().__init__(
            f"skill_body_md must contain at most {max_words} words"
        )


@dataclass(frozen=True)
class Stage0Metadata:
    name: str
    description: str
    when_to_use: tuple[str, ...]

    @classmethod
    def from_dict(
        cls,
        payload: object,
        *,
        min_when_to_use: int = 2,
    ) -> "Stage0Metadata":
        if (
            isinstance(min_when_to_use, bool)
            or not isinstance(min_when_to_use, int)
            or min_when_to_use < 1
        ):
            raise ValueError("min_when_to_use must be a positive integer")
        mapping = _mapping(payload, "metadata_json")
        _reject_unknown(mapping, METADATA_KEYS, "metadata_json")
        name = _text(mapping.get("name"), "metadata_json.name")
        if not NAME_RE.fullmatch(name):
            raise Stage0ValidationError(
                "metadata_json.name must use lowercase letters, numbers, "
                "and hyphens and stay within 64 characters"
            )
        description = _text(
            mapping.get("description"),
            "metadata_json.description",
        )
        if len(description) > 1024 or "\n" in description:
            raise Stage0ValidationError(
                "metadata_json.description must be one line and at most "
                "1024 characters"
            )
        raw_conditions = mapping.get("when_to_use")
        if not isinstance(raw_conditions, list):
            raise Stage0ValidationError(
                "metadata_json.when_to_use must be an array"
            )
        conditions = tuple(
            _text(item, f"metadata_json.when_to_use[{index}]")
            for index, item in enumerate(raw_conditions)
        )
        if len(conditions) < min_when_to_use:
            raise Stage0ValidationError(
                "metadata_json.when_to_use must include at least "
                f"{min_when_to_use} item"
                f"{'s' if min_when_to_use != 1 else ''}"
            )
        return cls(
            name=name,
            description=description,
            when_to_use=conditions,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "when_to_use": list(self.when_to_use),
        }


@dataclass(frozen=True)
class Stage0SkillDraft:
    metadata: Stage0Metadata
    skill_body_md: str

    @classmethod
    def from_dict(
        cls,
        payload: object,
        *,
        expected_sections: tuple[str, ...] = (
            "Workflow",
            "Error Avoidance",
        ),
        max_words: int | None = 300,
        min_when_to_use: int = 2,
    ) -> "Stage0SkillDraft":
        mapping = _mapping(payload, "stage0_output")
        _reject_unknown(mapping, TOP_LEVEL_KEYS, "stage0_output")
        metadata = Stage0Metadata.from_dict(
            mapping.get("metadata_json"),
            min_when_to_use=min_when_to_use,
        )
        body = _text(mapping.get("skill_body_md"), "skill_body_md")
        if body.lstrip().startswith("---"):
            raise Stage0ValidationError(
                "skill_body_md must not include YAML frontmatter"
            )
        _validate_skill_body(
            body,
            expected_sections=expected_sections,
            max_words=max_words,
        )
        _reject_leakage(metadata, body)
        return cls(metadata=metadata, skill_body_md=body)

    @classmethod
    def from_raw_text(
        cls,
        raw_text: str,
        *,
        expected_sections: tuple[str, ...] = (
            "Workflow",
            "Error Avoidance",
        ),
        max_words: int | None = 300,
        min_when_to_use: int = 2,
    ) -> "Stage0SkillDraft":
        text = _strip_code_fence(raw_text)
        try:
            payload = json.loads(text, strict=False)
        except json.JSONDecodeError as exc:
            raise Stage0ValidationError(
                f"Stage0 output is not valid JSON: {exc}"
            ) from exc
        return cls.from_dict(
            payload,
            expected_sections=expected_sections,
            max_words=max_words,
            min_when_to_use=min_when_to_use,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata_json": self.metadata.to_dict(),
            "skill_body_md": self.skill_body_md,
        }


@dataclass(frozen=True)
class Stage0PromptContext:
    trajectory_count: int
    benchmark_domain_info_md: str
    vendor_prompt: str
    metric_interpretation: str
    outcome_label_format: str
    extra_section: str = ""
    example: str = ""
    expected_skill_sections: tuple[str, ...] = (
        "Workflow",
        "Error Avoidance",
    )
    max_skill_words: int | None = 300
    min_when_to_use: int = 2

    def __post_init__(self) -> None:
        if self.trajectory_count < 1:
            raise ValueError("trajectory_count must be at least 1")
        for field_name in (
            "benchmark_domain_info_md",
            "vendor_prompt",
            "metric_interpretation",
            "outcome_label_format",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        sections = tuple(self.expected_skill_sections)
        if (
            len(sections) < 2
            or len(set(sections)) != len(sections)
            or any(
                not isinstance(section, str) or not section.strip()
                for section in sections
            )
        ):
            raise ValueError(
                "expected_skill_sections must contain at least two unique "
                "non-empty headings"
            )
        object.__setattr__(self, "expected_skill_sections", sections)
        if self.max_skill_words is not None and (
            isinstance(self.max_skill_words, bool)
            or not isinstance(self.max_skill_words, int)
            or self.max_skill_words < 1
        ):
            raise ValueError(
                "max_skill_words must be a positive integer or null"
            )
        if (
            isinstance(self.min_when_to_use, bool)
            or not isinstance(self.min_when_to_use, int)
            or self.min_when_to_use < 1
        ):
            raise ValueError(
                "min_when_to_use must be a positive integer"
            )


@dataclass(frozen=True)
class Stage0Prompt:
    system_prompt: str
    user_prompt: str
    messages: tuple[dict[str, str], ...]


class Stage0PromptBuilder:
    def __init__(
        self,
        *,
        system_prompt_path: Path | None = None,
        user_prompt_path: Path | None = None,
        skill_policy_path: Path | None = None,
        example_path: Path | None = None,
    ) -> None:
        self._system_template = (
            system_prompt_path or PROMPTS_DIR / "stage0_system_prompt.md"
        ).read_text(encoding="utf-8")
        self._user_template = (
            user_prompt_path or PROMPTS_DIR / "stage0_user_prompt.md"
        ).read_text(encoding="utf-8")
        self._policy = (
            skill_policy_path or PROMPTS_DIR / "skill_writing_policy.md"
        ).read_text(encoding="utf-8").strip()
        self._example = (
            example_path or PROMPTS_DIR / "stage0_example_default.md"
        ).read_text(encoding="utf-8").strip()

    def build(self, context: Stage0PromptContext) -> Stage0Prompt:
        replacements = {
            "{x}": str(context.trajectory_count),
            "{vendor_prompt}": context.vendor_prompt.strip(),
            "{metric_interpretation}": (
                context.metric_interpretation.strip()
            ),
            "{outcome_label_format}": context.outcome_label_format.strip(),
            "{stage0_extra_section}": context.extra_section.strip(),
            "{stage0_example}": context.example.strip() or self._example,
            "{skill_writing_policy_md}": self._policy,
        }
        system_prompt = _render(self._system_template, replacements)
        user_prompt = _render(
            self._user_template,
            {
                "{benchmark_domain_info_md}": (
                    context.benchmark_domain_info_md.strip()
                )
            },
        )
        messages = (
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        )
        return Stage0Prompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            messages=messages,
        )


@dataclass(frozen=True)
class Stage0ExecutionResult:
    prompt: Stage0Prompt
    raw_output_text: str
    draft: Stage0SkillDraft
    attempt_count: int
    executed_at: str


class Stage0Executor:
    def __init__(
        self,
        *,
        invoke_model: Callable[[list[dict[str, str]]], Any],
        prompt_builder: Stage0PromptBuilder | None = None,
        max_attempts: int = 1,
        timestamp_fn: Callable[[], str] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._invoke_model = invoke_model
        self._prompt_builder = prompt_builder or Stage0PromptBuilder()
        self._max_attempts = max_attempts
        self._timestamp_fn = timestamp_fn or _utc_now

    def run(self, context: Stage0PromptContext) -> Stage0ExecutionResult:
        prompt = self._prompt_builder.build(context)
        messages = list(prompt.messages)
        for attempt in range(1, self._max_attempts + 1):
            raw_output = self._invoke_model(messages)
            raw_text = _model_output_text(raw_output)
            try:
                draft = Stage0SkillDraft.from_raw_text(
                    raw_text,
                    expected_sections=context.expected_skill_sections,
                    max_words=context.max_skill_words,
                    min_when_to_use=context.min_when_to_use,
                )
            except Stage0ValidationError as exc:
                if attempt >= self._max_attempts:
                    raise
                messages.extend(
                    [
                        {"role": "assistant", "content": raw_text},
                        {
                            "role": "user",
                            "content": _repair_instruction(exc),
                        },
                    ]
                )
                continue
            return Stage0ExecutionResult(
                prompt=prompt,
                raw_output_text=raw_text,
                draft=draft,
                attempt_count=attempt,
                executed_at=self._timestamp_fn(),
            )
        raise RuntimeError("Stage0Executor reached an invalid terminal state")


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Stage0ValidationError(f"{field_name} must be a JSON object")
    return value


def _reject_unknown(
    payload: dict[str, Any],
    allowed: set[str],
    field_name: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise Stage0ValidationError(
            f"unsupported keys in {field_name}: {', '.join(unknown)}"
        )


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise Stage0ValidationError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise Stage0ValidationError(f"{field_name} must be non-empty")
    return text


def _validate_skill_body(
    body: str,
    *,
    expected_sections: tuple[str, ...],
    max_words: int | None,
) -> None:
    headings = re.findall(r"^##[ \t]+(.+?)[ \t]*$", body, re.MULTILINE)
    required = list(expected_sections)
    if headings != required:
        raise Stage0ValidationError(
            "skill_body_md must contain exactly these level-2 sections in "
            "order: "
            + ", ".join(f"## {heading}" for heading in required)
        )
    word_count = len(
        re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", body)
    )
    if max_words is not None and word_count > max_words:
        raise Stage0WordLimitError(
            actual_words=word_count,
            max_words=max_words,
        )


def _repair_instruction(exc: Stage0ValidationError) -> str:
    if isinstance(exc, Stage0WordLimitError):
        safe_target = max(1, exc.max_words * 9 // 10)
        return (
            "The previous output was invalid. Its skill_body_md contains "
            f"{exc.actual_words} validator-counted words; the hard limit is "
            f"{exc.max_words}. Rewrite the same two sections to no more than "
            f"{safe_target} words by removing redundancy. Preserve the JSON "
            "schema and metadata meaning. Return corrected raw JSON only."
        )
    return (
        "The previous output was invalid. "
        f"Validation error: {exc}. Return corrected raw JSON only."
    )


def _strip_code_fence(value: str) -> str:
    lines = str(value or "").strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _reject_leakage(metadata: Stage0Metadata, body: str) -> None:
    corpus = "\n".join(
        (
            metadata.name,
            metadata.description,
            *metadata.when_to_use,
            body,
        )
    ).lower()
    hits = [marker for marker in LEAKAGE_MARKERS if marker in corpus]
    hits.extend(
        pattern.pattern
        for pattern in LEAKAGE_PATTERNS
        if pattern.search(corpus)
    )
    if hits:
        raise Stage0ValidationError(
            "Stage0 output contains benchmark-local leakage markers: "
            + ", ".join(sorted(set(hits)))
        )


def _model_output_text(raw_output: Any) -> str:
    if isinstance(raw_output, str):
        return raw_output
    if isinstance(raw_output, bytes):
        return raw_output.decode("utf-8", errors="replace")
    if isinstance(raw_output, dict):
        content = raw_output.get("content")
        if isinstance(content, str):
            return content
        return json.dumps(raw_output, ensure_ascii=False)
    choices = getattr(raw_output, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
    return str(raw_output)


def _render(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered.strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
