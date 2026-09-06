"""Read real user input from supported host history formats."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HISTORY_PLATFORMS = frozenset(
    {"codex", "claude-code", "github-copilot"}
)


@dataclass(frozen=True)
class RawHistoryPrompt:
    platform: str
    session_id: str
    ordinal: int
    occurred_at: str
    text: str


@dataclass(frozen=True)
class HistoryScan:
    prompts: tuple[RawHistoryPrompt, ...]
    diagnostics: dict[str, Any]


def default_history_root(platform: str) -> Path:
    """Return host history roots, respecting official configuration overrides."""

    home = Path(os.environ.get("USERPROFILE") or Path.home())
    if platform == "codex":
        return Path(os.environ.get("CODEX_HOME") or home / ".codex")
    if platform == "claude-code":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude")
    if platform == "github-copilot":
        return Path(os.environ.get("COPILOT_HOME") or home / ".copilot")
    raise ValueError(f"unsupported history platform {platform!r}")


def scan_host_history(
    *,
    platform: str,
    root: Path | None,
    since: datetime,
    max_files: int,
    max_records: int,
    max_records_per_file: int,
    max_file_bytes: int,
) -> HistoryScan:
    """Scan known host history files with bounds and without following symlinks."""

    if platform not in HISTORY_PLATFORMS:
        raise ValueError(f"unsupported history platform {platform!r}")
    selected_root = (root or default_history_root(platform)).expanduser()
    diagnostics: dict[str, Any] = {
        "root_kind": platform,
        "root_available": selected_root.is_dir(),
        "files_discovered": 0,
        "files_scanned": 0,
        "records_read": 0,
        "old_records_skipped": 0,
        "invalid_records": 0,
        "truncated_files": 0,
        "errors": [],
    }
    if not selected_root.is_dir():
        return HistoryScan(prompts=(), diagnostics=diagnostics)

    files = _history_files(platform, selected_root, since)
    diagnostics["files_discovered"] = len(files)
    files = files[:max_files]
    prompts: list[RawHistoryPrompt] = []
    for path in files:
        remaining_records = max_records - diagnostics["records_read"]
        if remaining_records <= 0:
            break
        diagnostics["files_scanned"] += 1
        try:
            records, invalid, truncated = _read_json_lines(
                path,
                max_bytes=max_file_bytes,
                max_records=min(max_records_per_file, remaining_records),
            )
            diagnostics["records_read"] += len(records) + invalid
            diagnostics["invalid_records"] += invalid
            diagnostics["truncated_files"] += int(truncated)
            extracted = tuple(
                _extract_prompts(platform, path, records, root=selected_root)
            )
            recent = tuple(
                prompt
                for prompt in extracted
                if _occurred_since(prompt.occurred_at, since)
            )
            diagnostics["old_records_skipped"] += len(extracted) - len(recent)
            prompts.extend(recent)
        except OSError as exc:
            diagnostics["errors"].append(
                f"{path.name}: {exc.__class__.__name__}"
            )
    return HistoryScan(
        prompts=tuple(prompts),
        diagnostics=diagnostics,
    )


def _history_files(
    platform: str,
    root: Path,
    since: datetime,
) -> list[Path]:
    patterns: tuple[str, ...]
    if platform == "codex":
        patterns = (
            "history.jsonl",
            "sessions/**/*.jsonl",
            "archived_sessions/**/*.jsonl",
        )
    elif platform == "claude-code":
        patterns = ("history.jsonl", "projects/**/*.jsonl")
    else:
        patterns = ("session-state/*/events.jsonl",)

    threshold = since.timestamp()
    found: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            try:
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or path.stat().st_mtime < threshold
                ):
                    continue
            except OSError:
                continue
            found[str(path)] = path
    return sorted(
        found.values(),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def _read_json_lines(
    path: Path,
    *,
    max_bytes: int,
    max_records: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    records: list[dict[str, Any]] = []
    invalid = 0
    consumed = 0
    truncated = False
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            consumed += len(line.encode("utf-8", errors="replace"))
            if consumed > max_bytes or len(records) + invalid >= max_records:
                truncated = True
                break
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                invalid += 1
                continue
            if isinstance(value, dict):
                records.append(value)
            else:
                invalid += 1
    return records, invalid, truncated


def _extract_prompts(
    platform: str,
    path: Path,
    records: Iterable[dict[str, Any]],
    *,
    root: Path,
) -> Iterator[RawHistoryPrompt]:
    if platform == "codex":
        yield from _codex_prompts(path, records, root=root)
    elif platform == "claude-code":
        yield from _claude_prompts(path, records, root=root)
    else:
        yield from _copilot_prompts(path, records)


def _codex_prompts(
    path: Path,
    records: Iterable[dict[str, Any]],
    *,
    root: Path,
) -> Iterator[RawHistoryPrompt]:
    is_global = path == root / "history.jsonl"
    for ordinal, record in enumerate(records):
        if is_global:
            text = record.get("text")
            session_id = str(record.get("session_id", path.stem))
            occurred_at = _timestamp(record.get("ts"))
        else:
            payload = record.get("payload")
            if (
                record.get("type") != "response_item"
                or not isinstance(payload, dict)
                or payload.get("type") != "message"
                or payload.get("role") != "user"
                or payload.get("internal_chat_message") is True
            ):
                continue
            text = _content_text(payload.get("content"), text_type="input_text")
            session_id = _session_from_filename(path)
            occurred_at = _timestamp(record.get("timestamp"))
        if isinstance(text, str) and text.strip():
            yield RawHistoryPrompt(
                platform="codex",
                session_id=session_id,
                ordinal=ordinal,
                occurred_at=occurred_at,
                text=text,
            )


def _claude_prompts(
    path: Path,
    records: Iterable[dict[str, Any]],
    *,
    root: Path,
) -> Iterator[RawHistoryPrompt]:
    is_global = path == root / "history.jsonl"
    for ordinal, record in enumerate(records):
        if is_global:
            text = record.get("display")
            pasted = record.get("pastedContents")
            additions = tuple(_nested_text(pasted))
            if additions:
                text = "\n\n".join((str(text or ""), *additions))
            session_id = str(record.get("sessionId", path.stem))
            occurred_at = _timestamp(record.get("timestamp"))
        else:
            message = record.get("message")
            origin = record.get("origin")
            if (
                record.get("type") != "user"
                or record.get("isMeta") is True
                or not isinstance(message, dict)
                or message.get("role") != "user"
                or (
                    isinstance(origin, dict)
                    and origin.get("kind") not in {None, "human"}
                )
            ):
                continue
            text = _content_text(message.get("content"), text_type="text")
            session_id = str(record.get("sessionId", path.stem))
            occurred_at = _timestamp(record.get("timestamp"))
        if isinstance(text, str) and text.strip():
            yield RawHistoryPrompt(
                platform="claude-code",
                session_id=session_id,
                ordinal=ordinal,
                occurred_at=occurred_at,
                text=text,
            )


def _copilot_prompts(
    path: Path,
    records: Iterable[dict[str, Any]],
) -> Iterator[RawHistoryPrompt]:
    for ordinal, record in enumerate(records):
        data = record.get("data")
        if (
            record.get("type") != "user.message"
            or not isinstance(data, dict)
            or str(data.get("source", "")).strip()
        ):
            continue
        text = data.get("content")
        if isinstance(text, str) and text.strip():
            yield RawHistoryPrompt(
                platform="github-copilot",
                session_id=path.parent.name,
                ordinal=ordinal,
                occurred_at=_timestamp(record.get("timestamp")),
                text=text,
            )


def _content_text(value: Any, *, text_type: str) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    blocks = []
    for item in value:
        if (
            isinstance(item, dict)
            and item.get("type") == text_type
            and isinstance(item.get("text"), str)
        ):
            blocks.append(item["text"])
    return "\n\n".join(blocks)


def _nested_text(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        if value.strip():
            yield value
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _nested_text(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nested_text(item)


def _timestamp(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 100_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(
                seconds,
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
        except (OSError, OverflowError, ValueError):
            return ""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _occurred_since(value: str, since: datetime) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= since


def _session_from_filename(path: Path) -> str:
    stem = path.stem
    marker = "rollout-"
    return stem[len(marker) :] if stem.startswith(marker) else stem


__all__ = [
    "HISTORY_PLATFORMS",
    "HistoryScan",
    "RawHistoryPrompt",
    "default_history_root",
    "scan_host_history",
]
