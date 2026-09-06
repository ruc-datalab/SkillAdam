"""Semantic safeguards for evaluation selection."""

from __future__ import annotations

import json
import re
from typing import Any


_EXACT_REFERENCE_METHODS = frozenset({"exact", "normalized_exact"})
_OPEN_ENDED_ENGLISH = re.compile(
    r"\b(?:analy[sz]e|create|draft|explain|generate|recommend|review|"
    r"rewrite|summari[sz]e|write)\b",
    re.IGNORECASE,
)
_OPEN_ENDED_CJK = (
    "分析",
    "创建",
    "改写",
    "概括",
    "解释",
    "生成",
    "审查",
    "评审",
    "推荐",
    "摘要",
    "总结",
    "撰写",
    "重写",
)
_FIXED_ANSWER_ENGLISH = re.compile(
    r"\b(?:fixed string|literal string|verbatim|unique correct answer)\b",
    re.IGNORECASE,
)
_FIXED_ANSWER_CJK = (
    "固定字符串",
    "唯一标准答案",
    "原样返回",
    "逐字返回",
    "逐字输出",
)
_WORD = re.compile(r"[A-Za-z0-9]+(?:['_-][A-Za-z0-9]+)*")
_CJK = re.compile(r"[\u3400-\u9fff]")


def open_ended_exact_text_reason(
    *,
    prompt: str,
    reference: Any,
    method: str,
) -> str | None:
    """Explain clearly invalid exact-reference configurations for open-ended tasks."""

    if method not in _EXACT_REFERENCE_METHODS:
        return None
    if not isinstance(reference, str) or not _is_long_natural_language(reference):
        return None
    if _declares_fixed_answer(prompt) or not _requests_open_ended_text(prompt):
        return None
    return (
        "open-ended natural-language generation cannot use a long exact or "
        "normalized_exact reference; use rubric_judge with observable "
        "dimensions, or use deterministic checks only for uniquely correct "
        "outputs"
    )


def _requests_open_ended_text(prompt: str) -> bool:
    return bool(_OPEN_ENDED_ENGLISH.search(prompt)) or any(
        marker in prompt for marker in _OPEN_ENDED_CJK
    )


def _declares_fixed_answer(prompt: str) -> bool:
    return bool(_FIXED_ANSWER_ENGLISH.search(prompt)) or any(
        marker in prompt for marker in _FIXED_ANSWER_CJK
    )


def _is_long_natural_language(reference: str) -> bool:
    text = reference.strip()
    if not text or _is_number(text) or _is_json(text):
        return False
    return len(_WORD.findall(text)) >= 4 or len(_CJK.findall(text)) >= 12


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _is_json(value: str) -> bool:
    if not value.startswith(("{", "[")):
        return False
    try:
        json.loads(value)
    except json.JSONDecodeError:
        return False
    return True


__all__ = ["open_ended_exact_text_reason"]
