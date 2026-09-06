"""Discover, redact, rank, and audit tasks from host history."""

# Redaction patterns and placeholders, not real user paths.
# public-audit: allow=private-absolute-path

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from skilladam.product.history_adapters import (
    HISTORY_PLATFORMS,
    RawHistoryPrompt,
    scan_host_history,
)


HISTORY_DISCOVERY_FILENAME = "history_discovery.json"
HISTORY_DISCOVERY_SCHEMA_VERSION = "1"
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_MAX_CANDIDATES = 12
MAX_CANDIDATES = 30
MAX_FILES = 200
MAX_RECORDS = 50_000
MAX_RECORDS_PER_FILE = 250
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PROMPT_CHARS = 8_000
MIN_RELEVANCE_SCORE = 4.0

_ENGLISH_WORD = re.compile(r"[a-z][a-z0-9_-]{2,}")
_CHINESE_CHUNK = re.compile(r"[\u4e00-\u9fff]{2,}")
_FRONTMATTER_FIELD = re.compile(
    r"^(name|description)\s*:\s*(.+?)\s*$",
    re.MULTILINE,
)
_HEADING = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.MULTILINE)
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_SPACES = re.compile(r"\s+")
_META_PATTERNS = (
    re.compile(r"\bskilladam\b", re.IGNORECASE),
    re.compile(r"\btask_manifest\b", re.IGNORECASE),
    re.compile(r"\bhistory_discovery\b", re.IGNORECASE),
    re.compile(r"(?:优化|改进|进化).{0,12}(?:skill|技能)", re.IGNORECASE),
    re.compile(r"(?:optimi[sz]e|improve).{0,20}\bskill\b", re.IGNORECASE),
    re.compile(r"(?:安装|卸载|更新).{0,40}(?:skill|技能)", re.IGNORECASE),
    re.compile(
        r"(?:install|uninstall|update).{0,60}\bskill\b",
        re.IGNORECASE,
    ),
)
_HOST_CONTEXT_PREFIXES = (
    "<codex_internal_context",
    "<oai-mem-citation",
    "# AGENTS.md instructions",
    "<environment_context",
    "<permissions instructions",
    "<collaboration_mode",
    "<app-context",
)
_STOP_WORDS = frozenset(
    {
        "about",
        "all",
        "and",
        "after",
        "asked",
        "agent",
        "also",
        "before",
        "claude",
        "code",
        "communications",
        "company",
        "create",
        "current",
        "description",
        "etc",
        "existing",
        "from",
        "help",
        "how",
        "instructions",
        "into",
        "kinds",
        "more",
        "need",
        "output",
        "please",
        "project",
        "reports",
        "resources",
        "set",
        "skill",
        "should",
        "some",
        "sort",
        "status",
        "that",
        "the",
        "this",
        "update",
        "updates",
        "use",
        "user",
        "using",
        "when",
        "whenever",
        "with",
        "write",
        "your",
        "准确",
        "格式",
        "更新",
        "符合",
        "内部",
        "清晰",
        "撰写",
        "项目",
        "状态",
        "组织",
        "任务",
        "使用",
        "进行",
        "需要",
        "一个",
        "这个",
        "可以",
        "用户",
        "相关",
        "内容",
    }
)

_REDACTIONS = (
    (
        "private_path",
        re.compile(
            r'''(["'])(?:[A-Z]:[\\/]|/(?:home|Users|root|data/workspace)/|\\\\)'''
            r'''[^"'\r\n]*\1''',
            re.IGNORECASE,
        ),
        r"\1[REDACTED_PATH]\1",
    ),
    (
        "private_key",
        re.compile(
            r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
            r"-----END [^-\r\n]*PRIVATE KEY-----",
            re.IGNORECASE | re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
        "Bearer [REDACTED]",
    ),
    (
        "credential",
        re.compile(
            r"(?i)\b((?:[a-z][a-z0-9]*_)*(?:api[_-]?key|access[_-]?token|"
            r"auth[_-]?token|password|passwd|secret))\b"
            r"([\"']?\s*[:=]\s*)[\"']?[^\s\"',;]{6,}"
        ),
        r"\1\2[REDACTED]",
    ),
    (
        "provider_token",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,})\b"
        ),
        "[REDACTED_TOKEN]",
    ),
    (
        "email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
        "[REDACTED_EMAIL]",
    ),
    (
        "user_path",
        re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s\"'<>]+"),
        r"C:\\Users\\[USER]",
    ),
    (
        "user_path",
        re.compile(r"(?i)(?<![\w/])/(?:home|Users)/[^/\s\"'<>]+"),
        "/home/[USER]",
    ),
    (
        "private_path",
        re.compile(
            r"(?i)(?<![\w/])/(?:root|data/workspace)(?:/[^\s\"'<>]*)?"
            r"|\b[A-Z]:[\\/][^\s\"'<>]+"
            r"|\\\\[^\\\s]+\\[^\s\"'<>]+"
        ),
        "[REDACTED_PATH]",
    ),
)


def discover_history_tasks(
    *,
    skill_path: Path,
    intent: str,
    output_dir: Path,
    platform: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    history_root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Find, rank, and persist real local requests relevant to the target skill."""

    platform = platform.strip().lower()
    if platform not in HISTORY_PLATFORMS:
        raise ValueError(
            f"platform must be one of {sorted(HISTORY_PLATFORMS)}"
        )
    skill_path = Path(skill_path).resolve()
    if not skill_path.is_file():
        raise ValueError(f"skill_path does not exist: {skill_path}")
    intent = intent.strip()
    if not intent:
        raise ValueError("intent must be non-empty text")
    if (
        isinstance(lookback_days, bool)
        or not isinstance(lookback_days, int)
        or not 1 <= lookback_days <= 3650
    ):
        raise ValueError("lookback_days must be between 1 and 3650")
    if (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or not 1 <= max_candidates <= MAX_CANDIDATES
    ):
        raise ValueError(
            f"max_candidates must be between 1 and {MAX_CANDIDATES}"
        )

    destination = Path(output_dir).resolve() / HISTORY_DISCOVERY_FILENAME
    existing = _matching_existing_report(
        destination,
        skill_path=skill_path,
        intent=intent,
        platform=platform,
        lookback_days=lookback_days,
        max_candidates=max_candidates,
    )
    if existing is not None:
        return existing

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    skill_text = skill_path.read_text(encoding="utf-8")
    skill_fields = _skill_fields(skill_text)
    query_terms = _query_terms(skill_fields, intent, skill_text)
    scan = scan_host_history(
        platform=platform,
        root=history_root,
        since=current - timedelta(days=lookback_days),
        max_files=MAX_FILES,
        max_records=MAX_RECORDS,
        max_records_per_file=MAX_RECORDS_PER_FILE,
        max_file_bytes=MAX_FILE_BYTES,
    )
    candidates = _rank_candidates(
        scan.prompts,
        query_terms=query_terms,
        max_candidates=max_candidates,
    )
    diagnostics = dict(scan.diagnostics)
    status = "completed"
    if not diagnostics["root_available"]:
        status = "unavailable"
    elif diagnostics["errors"] or diagnostics["invalid_records"]:
        status = "partial"
    report: dict[str, Any] = {
        "schema_version": HISTORY_DISCOVERY_SCHEMA_VERSION,
        "discovery_id": str(uuid.uuid4()),
        "created_at": current.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "platform": platform,
        "skill_path": str(skill_path),
        "skill_name": skill_fields.get("name", skill_path.parent.name),
        "intent": intent,
        "lookback_days": lookback_days,
        "max_candidates": max_candidates,
        "query_terms": [term for term, _weight in query_terms],
        "scan": {"status": status, **diagnostics},
        "candidate_count": len(candidates),
        "candidates": candidates,
        "selection": {
            "status": "pending",
            "selected_source_refs": [],
        },
    }
    _write_json(destination, report)
    return report


def validate_history_discovery(
    *,
    output_dir: Path,
    skill_path: Path,
    intent: str,
    platform: str,
    task_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a discovery report to the task manifest and record the history tasks used."""

    path = Path(output_dir).resolve() / HISTORY_DISCOVERY_FILENAME
    if not path.is_file():
        raise ValueError(
            "New optimization tasks must call skilladam_discover_history first; "
            f"missing {HISTORY_DISCOVERY_FILENAME}"
        )
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("history_discovery.json is not valid JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("history_discovery.json must contain an object")
    if report.get("schema_version") != HISTORY_DISCOVERY_SCHEMA_VERSION:
        raise ValueError("unsupported history discovery schema")
    if report.get("platform") != platform:
        raise ValueError("history discovery platform does not match this host")
    if Path(str(report.get("skill_path", ""))).resolve() != Path(
        skill_path
    ).resolve():
        raise ValueError("history discovery skill_path does not match")
    if report.get("intent") != intent.strip():
        raise ValueError("history discovery intent does not match")

    candidate_refs = {
        str(item.get("source_ref"))
        for item in report.get("candidates", [])
        if isinstance(item, Mapping) and item.get("source_ref")
    }
    tasks = task_manifest.get("tasks", [])
    selected_refs = []
    if isinstance(tasks, list):
        selected_refs = [
            str(task.get("source_ref"))
            for task in tasks
            if isinstance(task, Mapping)
            and task.get("source") == "local_history"
        ]
    unknown = sorted(set(selected_refs) - candidate_refs)
    if unknown:
        raise ValueError(
            "task_manifest contains local_history references absent from "
            f"history discovery: {unknown}"
        )
    report["selection"] = {
        "status": "selected" if selected_refs else "none_selected",
        "selected_source_refs": selected_refs,
    }
    _write_json(path, report)
    return report


def _skill_fields(skill_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _FRONTMATTER_FIELD.finditer(skill_text):
        fields[match.group(1)] = match.group(2).strip(" \"'")
    return fields


def _matching_existing_report(
    path: Path,
    *,
    skill_path: Path,
    intent: str,
    platform: str,
    lookback_days: int,
    max_candidates: int,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(
            "existing history_discovery.json is not valid JSON"
        ) from exc
    if not isinstance(report, dict):
        raise ValueError("existing history_discovery.json must be an object")
    expected = {
        "schema_version": HISTORY_DISCOVERY_SCHEMA_VERSION,
        "platform": platform,
        "skill_path": str(skill_path),
        "intent": intent,
        "lookback_days": lookback_days,
        "max_candidates": max_candidates,
    }
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError(
            "output_dir already contains history discovery for different inputs"
        )
    return report


def _query_terms(
    fields: Mapping[str, str],
    intent: str,
    skill_text: str,
) -> tuple[tuple[str, float], ...]:
    weighted: dict[str, float] = {}
    sources = (
        (fields.get("name", ""), 5.0),
        (intent, 3.0),
        (fields.get("description", ""), 1.5),
        (" ".join(_HEADING.findall(skill_text)[:20]), 1.0),
    )
    for value, weight in sources:
        for term in _terms(value):
            weighted[term] = max(weighted.get(term, 0.0), weight)
    ordered = sorted(weighted.items(), key=lambda item: (-item[1], item[0]))
    return tuple(ordered[:80])


def _terms(value: str) -> set[str]:
    lowered = value.lower().replace("_", "-")
    terms = {
        token
        for token in _ENGLISH_WORD.findall(lowered)
        if token not in _STOP_WORDS
    }
    for token in tuple(terms):
        terms.update(
            part
            for part in token.split("-")
            if len(part) >= 3 and part not in _STOP_WORDS
        )
    for chunk in _CHINESE_CHUNK.findall(value):
        if chunk not in _STOP_WORDS and len(chunk) <= 12:
            terms.add(chunk)
        blocked_positions = _chinese_stop_positions(chunk)
        terms.update(
            chunk[index : index + 2]
            for index in range(len(chunk) - 1)
            if (
                chunk[index : index + 2] not in _STOP_WORDS
                and not blocked_positions[index]
                and not blocked_positions[index + 1]
            )
        )
    return terms


def _chinese_stop_positions(chunk: str) -> list[bool]:
    blocked = [False] * len(chunk)
    for stop_word in _STOP_WORDS:
        if not _CHINESE_CHUNK.fullmatch(stop_word):
            continue
        start = chunk.find(stop_word)
        while start >= 0:
            for index in range(start, start + len(stop_word)):
                blocked[index] = True
            start = chunk.find(stop_word, start + 1)
    return blocked


def _rank_candidates(
    prompts: tuple[RawHistoryPrompt, ...],
    *,
    query_terms: tuple[tuple[str, float], ...],
    max_candidates: int,
) -> list[dict[str, Any]]:
    ranked: list[tuple[float, str, RawHistoryPrompt, list[str], list[str]]] = []
    seen: set[str] = set()
    for prompt in prompts:
        text = _clean_prompt(prompt.text)
        if not text or any(pattern.search(text) for pattern in _META_PATTERNS):
            continue
        redacted, redactions = _redact(text)
        normalized = _normalized(redacted)
        if normalized in seen:
            continue
        seen.add(normalized)
        matched = [
            term for term, _weight in query_terms if term in normalized
        ]
        score = sum(
            weight
            for term, weight in query_terms
            if term in normalized
        )
        if 40 <= len(redacted) <= 3_000:
            score += 0.5
        if score < MIN_RELEVANCE_SCORE:
            continue
        ranked.append(
            (score, prompt.occurred_at, prompt, matched[:12], redactions)
        )
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    candidates = []
    for score, _timestamp_value, prompt, matched, redactions in ranked[
        :max_candidates
    ]:
        session = _SAFE_ID.sub("-", prompt.session_id).strip("-") or "session"
        source_ref = (
            f"local-history://{prompt.platform}/"
            f"{quote(session, safe='._-')}/{prompt.ordinal}"
        )
        candidates.append(
            {
                "candidate_id": f"history-{len(candidates) + 1}",
                "prompt": _redacted_limit(prompt.text)[0],
                "source_ref": source_ref,
                "occurred_at": prompt.occurred_at,
                "relevance_score": round(score, 2),
                "matched_terms": matched,
                "redactions": redactions,
            }
        )
    return candidates


def _clean_prompt(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or len(normalized) < 12 or len(normalized) > MAX_PROMPT_CHARS:
        return ""
    if normalized.startswith(_HOST_CONTEXT_PREFIXES):
        return ""
    return normalized


def _redacted_limit(text: str) -> tuple[str, list[str]]:
    cleaned = _clean_prompt(text)
    return _redact(cleaned)


def _redact(text: str) -> tuple[str, list[str]]:
    redactions: list[str] = []
    value = text
    for name, pattern, replacement in _REDACTIONS:
        value, count = pattern.subn(replacement, value)
        if count:
            if name not in redactions:
                redactions.append(name)
    return value, redactions


def _normalized(text: str) -> str:
    return _SPACES.sub(" ", text).strip().lower()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_MAX_CANDIDATES",
    "HISTORY_DISCOVERY_FILENAME",
    "discover_history_tasks",
    "validate_history_discovery",
]
