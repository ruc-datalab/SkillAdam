"""Safe offline glob/read/grep helpers for local OfficeQA documents."""

from __future__ import annotations

import fnmatch
from functools import lru_cache
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlparse


_MAX_READ_CHARS = 4000
_MAX_GREP_MATCHES = 20
_MAX_GLOB_MATCHES = 50
_MAX_ORACLE_PAGE_CHARS = 24000
_MAX_ORACLE_CONTEXT_CHARS = 80000


def resolve_docs_roots(
    data_dirs: list[str] | str | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> list[str]:
    """Resolve explicit, environment, or conventional document roots."""

    candidates = _path_values(data_dirs)
    environment = os.environ if environ is None else environ
    candidates.extend(
        _path_values(environment.get("OFFICEQA_DOCS_DIR", ""))
    )
    if not candidates:
        current = Path.cwd()
        candidates.extend(
            [
                str(current / "data/officeqa/docs"),
                str(current / "data/officeqa/docs_official"),
                str(
                    current
                    / "data/officeqa/raw/treasury_bulletins_parsed"
                ),
            ]
        )

    roots: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.is_dir():
            continue
        transformed = path / "transformed"
        selected = transformed if transformed.is_dir() else path
        resolved = str(selected.resolve())
        if resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)
    if not roots:
        raise FileNotFoundError(
            "OfficeQA docs directory not found. Set OFFICEQA_DOCS_DIR "
            "or pass data_dirs."
        )
    return roots


def resolve_candidate_files(
    source_files: list[str],
    allowed_roots: list[str],
) -> list[str]:
    """Return deterministic files matching the source filename hints."""

    allowed_names = {
        Path(name).name
        for name in source_files
        if isinstance(name, str) and name.strip()
    }
    resolved: set[str] = set()
    for root_text in allowed_roots:
        root = Path(root_text).resolve()
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            resolved_path = _safe_enumerated_file(path, root)
            if resolved_path is None:
                continue
            if allowed_names and path.name not in allowed_names:
                continue
            resolved.add(str(resolved_path))
    return sorted(resolved)


def run_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    allowed_roots: list[str],
    allowed_files: list[str],
) -> tuple[str, str]:
    """Execute one bounded local document tool call."""

    if name == "glob":
        pattern = str(arguments.get("pattern") or "*")
        matches: list[str] = []
        for root_text in sorted(allowed_roots):
            root = Path(root_text).resolve()
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                resolved_path = _safe_enumerated_file(path, root)
                if resolved_path is None:
                    continue
                if allowed_files and path.name not in allowed_files:
                    continue
                relative = str(path.relative_to(root))
                if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(
                    path.name,
                    pattern,
                ):
                    matches.append(str(resolved_path))
                    if len(matches) >= _MAX_GLOB_MATCHES:
                        break
            if len(matches) >= _MAX_GLOB_MATCHES:
                break
        return (
            f"glob(pattern={pattern!r})",
            "\n".join(matches) if matches else "[no matches]",
        )

    if name == "read":
        path_text = str(arguments.get("path") or "")
        if not path_text:
            return "read(path='')", "[read error: missing path]"
        if not _is_allowed(path_text, allowed_roots, allowed_files):
            return (
                f"read(path={path_text!r})",
                "[read error: path not allowed]",
            )
        try:
            start = max(int(arguments.get("start") or 1), 1)
            limit = max(int(arguments.get("limit") or 80), 1)
        except (TypeError, ValueError) as exc:
            return (
                f"read(path={path_text!r})",
                f"[read error: invalid line window: {exc}]",
            )
        try:
            lines = Path(path_text).read_text(encoding="utf-8").splitlines(
                keepends=True
            )
        except OSError as exc:
            return f"read(path={path_text!r})", f"[read error: {exc}]"
        excerpt = "".join(lines[start - 1 : start - 1 + limit])
        return (
            f"read(path={path_text!r}, start={start}, limit={limit})",
            excerpt[:_MAX_READ_CHARS] or "[empty file]",
        )

    if name == "grep":
        pattern = str(arguments.get("pattern") or "").lower()
        path_text = str(arguments.get("path") or "")
        command = f"grep(pattern={pattern!r}, path={path_text!r})"
        if not pattern or not path_text:
            return command, "[grep error: missing pattern or path]"
        if not _is_allowed(path_text, allowed_roots, allowed_files):
            return command, "[grep error: path not allowed]"
        matches: list[str] = []
        try:
            with Path(path_text).open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if pattern in line.lower():
                        matches.append(f"{line_number}: {line.rstrip()}")
                    if len(matches) >= _MAX_GREP_MATCHES:
                        break
        except OSError as exc:
            return command, f"[grep error: {exc}]"
        return command, "\n".join(matches) if matches else "[no matches]"

    return name, f"[tool error: unknown tool {name}]"


def build_oracle_parsed_pages_context(
    source_files: object,
    source_docs: object,
    docs_roots: list[str],
    *,
    max_page_chars: int = _MAX_ORACLE_PAGE_CHARS,
    max_total_chars: int = _MAX_ORACLE_CONTEXT_CHARS,
) -> str:
    """Render only parsed pages explicitly referenced by source URLs."""

    refs = _iter_oracle_refs(source_files, source_docs)
    if not refs:
        return ""
    blocks: list[str] = []
    total_chars = 0
    seen_pages: set[tuple[str, int]] = set()
    for source_file, page_number, source_doc in refs:
        json_path = _locate_parsed_json(source_file, docs_roots)
        if json_path is None:
            continue
        page_key = (str(json_path.resolve()), page_number)
        if page_key in seen_pages:
            continue
        seen_pages.add(page_key)
        page_text = _render_parsed_page(page_key[0], page_number)
        if not page_text:
            continue
        if len(page_text) > max_page_chars:
            omitted = len(page_text) - max_page_chars
            page_text = (
                page_text[:max_page_chars].rstrip()
                + f"\n\n[... {omitted} characters omitted ...]"
            )
        block = (
            f"### {source_file} page {page_number}\n"
            f"Source URL: {source_doc}\n\n"
            f"{page_text}"
        )
        if total_chars + len(block) > max_total_chars:
            remaining = max_total_chars - total_chars
            if remaining <= 0:
                break
            blocks.append(
                block[:remaining].rstrip()
                + "\n\n[... oracle context truncated ...]"
            )
            break
        blocks.append(block)
        total_chars += len(block)
    if not blocks:
        return ""
    return (
        "The following content is pre-parsed from the oracle OfficeQA "
        "source page(s). Treat it as primary document evidence and combine "
        "it with local tool evidence when useful.\n\n"
        + "\n\n".join(blocks)
    )


def _path_values(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [
            item.strip()
            for item in value.replace(os.pathsep, ",").split(",")
            if item.strip()
        ]
    return [
        str(item).strip()
        for item in value
        if str(item).strip()
    ]


def _is_allowed(
    path_text: str,
    allowed_roots: list[str],
    allowed_files: list[str],
) -> bool:
    try:
        resolved = Path(path_text).resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    in_root = any(
        resolved.is_relative_to(Path(root).resolve(strict=False))
        for root in allowed_roots
    )
    if not in_root:
        return False
    return not allowed_files or resolved.name in allowed_files


def _safe_enumerated_file(path: Path, root: Path) -> Path | None:
    """Resolve one enumerated file without following an escape symlink."""

    if path.is_symlink() or not path.is_file():
        return None
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_relative_to(root) else None


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [
            str(item).strip()
            for item in parsed
            if str(item).strip()
        ]
    if "\n" in text:
        return [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]
    return [text]


def _extract_page_number(source_doc: str) -> int | None:
    text = str(source_doc or "").strip()
    if not text:
        return None
    query = parse_qs(urlparse(text).query)
    for key in ("page", "pagenum", "page_id"):
        for raw_value in query.get(key, []):
            try:
                return int(str(raw_value).strip())
            except ValueError:
                continue
    match = re.search(r"(?:[?&]|^)page=(\d+)", text)
    return int(match.group(1)) if match is not None else None


def _iter_oracle_refs(
    source_files: object,
    source_docs: object,
) -> list[tuple[str, int, str]]:
    files = _as_list(source_files)
    docs = _as_list(source_docs)
    if not files or not docs:
        return []
    refs: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for index, source_doc in enumerate(docs):
        page_number = _extract_page_number(source_doc)
        if page_number is None:
            continue
        if index < len(files):
            source_file = files[index]
        elif len(files) == 1:
            source_file = files[0]
        else:
            continue
        item = (source_file, page_number, source_doc)
        if item not in seen:
            seen.add(item)
            refs.append(item)
    return refs


def _locate_parsed_json(
    source_file: str,
    docs_roots: list[str],
) -> Path | None:
    source_path = Path(str(source_file).strip())
    stem = source_path.stem if source_path.suffix else source_path.name
    if not stem:
        return None
    candidate_names = [stem + ".json"]
    if source_path.suffix == ".json":
        candidate_names.insert(0, source_path.name)
    for root_text in docs_roots:
        root = Path(root_text)
        search_dirs = [root, root / "jsons"]
        if root.name == "transformed":
            search_dirs.append(root.parent / "jsons")
        for search_dir in search_dirs:
            if search_dir.is_symlink():
                continue
            safe_root = search_dir.resolve()
            for name in candidate_names:
                candidate = search_dir / name
                resolved = _safe_enumerated_file(candidate, safe_root)
                if resolved is not None:
                    return resolved
    return None


class _TableMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if (
            normalized in {"td", "th"}
            and self._cell is not None
            and self._row is not None
        ):
            cell = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(cell)
            self._cell = None
        elif normalized == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def _html_table_to_markdown(raw_html: str) -> str:
    parser = _TableMarkdownParser()
    try:
        parser.feed(raw_html)
    except Exception:
        parser.rows = []
    if not parser.rows:
        text = re.sub(r"(?is)<[^>]+>", " ", raw_html)
        return re.sub(r"\s+", " ", html.unescape(text)).strip()
    width = max(len(row) for row in parser.rows)
    rows = [row + [""] * (width - len(row)) for row in parser.rows]
    lines = [
        "| " + " | ".join(_escape_cell(cell) for cell in rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_escape_cell(cell) for cell in row) + " |"
        for row in rows[1:]
    )
    return "\n".join(lines)


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").strip()


def _render_parsed_content(content: str) -> str:
    text = content.strip()
    if not text:
        return ""
    if "<table" in text.lower():
        return _html_table_to_markdown(text)
    normalized = html.unescape(text)
    normalized = re.sub(r"\r\n?", "\n", normalized)
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def _element_page_ids(element: dict[str, Any]) -> set[int]:
    page_ids: set[int] = set()
    bbox = element.get("bbox")
    if not isinstance(bbox, list):
        return page_ids
    for box in bbox:
        if not isinstance(box, dict):
            continue
        try:
            page_ids.add(int(box.get("page_id")))
        except (TypeError, ValueError):
            continue
    return page_ids


@lru_cache(maxsize=256)
def _load_parsed_elements(json_path: str) -> tuple[dict[str, Any], ...]:
    with Path(json_path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    document = payload.get("document") if isinstance(payload, dict) else {}
    elements = document.get("elements") if isinstance(document, dict) else []
    if not isinstance(elements, list):
        return ()
    return tuple(item for item in elements if isinstance(item, dict))


@lru_cache(maxsize=2048)
def _render_parsed_page(json_path: str, page_number: int) -> str:
    rendered: list[str] = []
    for element in _load_parsed_elements(json_path):
        if page_number not in _element_page_ids(element):
            continue
        content = element.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        section = _render_parsed_content(content)
        if section:
            rendered.append(section)
    return "\n\n".join(rendered).strip()
