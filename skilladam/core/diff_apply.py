"""Minimal unified diff apply engine for canonical skill text.

Uses context-line matching rather than relying on line numbers, since
LLMs frequently produce inaccurate @@ headers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256


class PatchApplyError(Exception):
    """Raised when a unified diff patch cannot be applied."""


@dataclass
class ApplyReport:
    """Result of multi-tier patch application; never raises."""

    text: str
    hunks_total: int = 0
    hunks_strict: int = 0
    hunks_intent: int = 0
    hunks_failed: int = 0
    fail_diagnostics: list[str] = field(default_factory=list)

    @property
    def all_failed(self) -> bool:
        return self.hunks_total > 0 and self.hunks_failed == self.hunks_total

    @property
    def has_degraded(self) -> bool:
        return self.hunks_intent > 0

    def summary(self) -> str:
        return (
            f"hunks: {self.hunks_total} total, "
            f"{self.hunks_strict} strict, "
            f"{self.hunks_intent} intent, "
            f"{self.hunks_failed} failed"
        )


HUNK_SCHEMA_VERSION = "1"
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?\s+"
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)


def diagnose_patch_failure(original: str, patch: str) -> str:
    """Return a detailed diagnosis of why *patch* fails to apply to *original*.

    For each hunk, reports whether it succeeded or failed.  For failed
    hunks it shows:
      - the context lines the hunk expected,
      - the best partial match in the original (if any),
      - the actual original lines around that region.
    """
    patch = patch.strip()
    if not patch:
        return "Patch is empty."

    hunks = _parse_hunks(patch)
    if not hunks:
        return "No valid hunks found in patch."

    lines = original.split("\n")
    parts: list[str] = []

    for idx, hunk in enumerate(hunks, 1):
        pattern = hunk.context_and_deletes
        if not pattern:
            parts.append(f"Hunk {idx}: empty context (trivially OK).")
            continue

        try:
            pos = _find_hunk_position(lines, hunk)
            parts.append(f"Hunk {idx}: OK — matched at line {pos + 1}.")
        except PatchApplyError:
            # Find best partial match
            best_pos, best_score = _best_partial_match(lines, pattern)
            detail_lines = [f"Hunk {idx}: FAILED — cannot locate in original."]
            detail_lines.append(f"  Expected context lines ({len(pattern)} lines):")
            for i, cl in enumerate(pattern[:8]):
                detail_lines.append(f"    [{i + 1}] {cl!r}")
            if len(pattern) > 8:
                detail_lines.append(f"    ... ({len(pattern) - 8} more lines)")

            if best_pos >= 0:
                detail_lines.append(
                    f"  Best partial match at original line {best_pos + 1} "
                    f"({best_score}/{len(pattern)} lines matched):"
                )
                show_start = max(0, best_pos - 1)
                show_end = min(len(lines), best_pos + len(pattern) + 2)
                for j in range(show_start, show_end):
                    marker = ">>>" if best_pos <= j < best_pos + len(pattern) else "   "
                    detail_lines.append(f"    {marker} L{j + 1}: {lines[j]!r}")
                # Show specific mismatches
                detail_lines.append("  Per-line comparison (first 8):")
                for i, cl in enumerate(pattern[:8]):
                    if best_pos + i < len(lines):
                        orig_line = lines[best_pos + i]
                        match = "==" if orig_line == cl else "!="
                        if match == "!=":
                            detail_lines.append(
                                f"    [{i + 1}] expected: {cl!r}"
                            )
                            detail_lines.append(
                                f"         actual:   {orig_line!r}"
                            )
                    else:
                        detail_lines.append(f"    [{i + 1}] expected: {cl!r}  (beyond EOF)")
            else:
                detail_lines.append("  No partial match found in original.")
                # Show first non-empty context line for orientation
                non_empty = [cl for cl in pattern if cl.strip()]
                if non_empty:
                    detail_lines.append(
                        f"  First non-empty expected line: {non_empty[0]!r}"
                    )

            parts.append("\n".join(detail_lines))

    return "\n\n".join(parts)


def _best_partial_match(
    lines: list[str], pattern: list[str]
) -> tuple[int, int]:
    """Find position with highest number of matching context lines.

    Returns (best_pos, best_score). Returns (-1, 0) if no line matches at all.
    """
    pat_len = len(pattern)
    best_pos = -1
    best_score = 0

    for start in range(max(1, len(lines) - pat_len + 1)):
        score = 0
        check_len = min(pat_len, len(lines) - start)
        for j in range(check_len):
            orig = lines[start + j]
            expected = pattern[j]
            if orig == expected or orig.strip() == expected.strip():
                score += 1
        if score > best_score:
            best_score = score
            best_pos = start
    return best_pos, best_score


@dataclass(frozen=True, slots=True)
class PatchHunk:
    """A unified diff hunk with stable serialization and selection."""

    hunk_id: str
    target_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    operations: tuple[tuple[str, str], ...]
    order: int
    schema_version: str = HUNK_SCHEMA_VERSION

    @property
    def context_and_deletes(self) -> list[str]:
        return [line for operation, line in self.operations if operation in (" ", "-")]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "hunk_id": self.hunk_id,
            "target_path": self.target_path,
            "old_start": self.old_start,
            "old_count": self.old_count,
            "new_start": self.new_start,
            "new_count": self.new_count,
            "operations": [list(item) for item in self.operations],
            "order": self.order,
        }


def _hunk_id(
    *,
    base_digest: str,
    target_path: str,
    operations: tuple[tuple[str, str], ...],
    order: int,
) -> str:
    payload = {
        "base_digest": base_digest,
        "operations": [list(item) for item in operations],
        "order": order,
        "schema_version": HUNK_SCHEMA_VERSION,
        "target_path": target_path,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"hunk_{sha256(encoded).hexdigest()}"


def apply_skill_patch(original: str, patch: str) -> str:
    """Apply a unified diff patch to the canonical skill text.

    Tolerates inaccurate line numbers by using context-line matching.
    Returns the patched text. Raises PatchApplyError on failure.
    """
    patch = patch.strip()
    if not patch:
        return original

    hunks = _parse_hunks(patch)
    if not hunks:
        raise PatchApplyError("No valid hunks found in patch.")

    original_is_empty = not original.strip()
    lines = original.split("\n")
    # Apply hunks in reverse order to avoid offset shifts
    hunks_with_pos = []
    for hunk in hunks:
        pos = _find_hunk_position(lines, hunk)
        hunks_with_pos.append((pos, hunk))

    hunks_with_pos.sort(key=lambda x: x[0], reverse=True)

    for pos, hunk in hunks_with_pos:
        lines = _apply_hunk(lines, hunk, pos)

    result = "\n".join(lines)
    # "".split("\n") == [""], so patching an empty original leaves a stray
    # leading newline.  Strip it so callers always get a clean result.
    if original_is_empty:
        result = result.lstrip("\n")
    return result


def apply_patch_hunks(
    original: str,
    hunks: tuple[PatchHunk, ...] | list[PatchHunk],
) -> str:
    """Apply parsed hunks strictly and atomically, without intent fallback."""

    selected = tuple(hunks)
    if not selected:
        return original
    if len({hunk.order for hunk in selected}) != len(selected):
        raise PatchApplyError("Selected hunks contain duplicate order values.")

    original_is_empty = not original.strip()
    lines = original.split("\n")
    positioned: list[tuple[int, PatchHunk]] = []
    occupied: list[tuple[int, int]] = []
    for hunk in selected:
        consumed = len(hunk.context_and_deletes)
        if consumed:
            position = _find_hunk_position(lines, hunk)
        else:
            position = min(max(hunk.old_start - 1, 0), len(lines))
        end = position + consumed
        if consumed and any(position < other_end and start < end for start, other_end in occupied):
            raise PatchApplyError("Selected hunks overlap in the base text.")
        if consumed:
            occupied.append((position, end))
        positioned.append((position, hunk))

    for position, hunk in sorted(positioned, key=lambda item: item[0], reverse=True):
        lines = _apply_hunk(lines, hunk, position)
    result = "\n".join(lines)
    return result.lstrip("\n") if original_is_empty else result


def parse_patch_hunks(
    patch: str,
    *,
    base_digest: str = "",
    default_target: str = "SKILL.md",
    normalize_counts: bool = False,
) -> tuple[PatchHunk, ...]:
    """Parse a unified diff into public hunks in their original order."""

    lines = patch.split("\n")
    hunks: list[PatchHunk] = []
    i = 0
    target_path = default_target

    while i < len(lines):
        if (
            lines[i].startswith("--- ")
            and i + 1 < len(lines)
            and lines[i + 1].startswith("+++ ")
        ):
            old_path = _diff_path(lines[i][4:])
            new_path = _diff_path(lines[i + 1][4:])
            target_path = new_path if new_path != "/dev/null" else old_path
            i += 2
            continue
        match = _HUNK_HEADER_RE.match(lines[i])
        if match is None:
            i += 1
            continue
        old_start = int(match.group("old_start"))
        old_count = int(match.group("old_count") or 1)
        new_start = int(match.group("new_start"))
        new_count = int(match.group("new_count") or 1)
        i += 1
        operations: list[tuple[str, str]] = []
        old_seen = 0
        new_seen = 0
        while i < len(lines):
            line = lines[i]
            if _HUNK_HEADER_RE.match(line) or _is_file_header_at(lines, i):
                break
            if line == "" and _blank_ends_hunk(lines, i):
                break
            if line == r"\ No newline at end of file":
                i += 1
                continue
            if line.startswith("+"):
                operations.append(("+", line[1:]))
                new_seen += 1
            elif line.startswith("-"):
                operations.append(("-", line[1:]))
                old_seen += 1
            elif line.startswith(" "):
                operations.append((" ", line[1:]))
                old_seen += 1
                new_seen += 1
            elif line == "":
                operations.append((" ", ""))
                old_seen += 1
                new_seen += 1
            else:
                operations.append((" ", line))
                old_seen += 1
                new_seen += 1
            i += 1
        if operations:
            frozen_operations = tuple(operations)
            order = len(hunks)
            hunks.append(
                PatchHunk(
                    hunk_id=_hunk_id(
                        base_digest=base_digest,
                        target_path=target_path,
                        operations=frozen_operations,
                        order=order,
                    ),
                    target_path=target_path,
                    old_start=old_start,
                    old_count=old_seen if normalize_counts else old_count,
                    new_start=new_start,
                    new_count=new_seen if normalize_counts else new_count,
                    operations=frozen_operations,
                    order=order,
                )
            )
    return tuple(hunks)


def _is_file_header_at(lines: list[str], index: int) -> bool:
    """Detect file headers between hunks while preserving content starting with ``--``."""

    line = lines[index]
    if line.startswith("--- "):
        if index + 1 < len(lines) and lines[index + 1].startswith("+++ "):
            return True
        return _looks_like_lone_file_header(line[4:])
    if line.startswith("+++ "):
        return _looks_like_lone_file_header(line[4:])
    return False


def _looks_like_lone_file_header(value: str) -> bool:
    path = value.split("\t", 1)[0].strip()
    return path == "/dev/null" or path.startswith(("a/", "b/"))


def _blank_ends_hunk(lines: list[str], index: int) -> bool:
    """Detect unprefixed empty lines before EOF or the next structural header."""

    next_index = index + 1
    while next_index < len(lines) and lines[next_index] == "":
        next_index += 1
    return next_index >= len(lines) or bool(
        _HUNK_HEADER_RE.match(lines[next_index])
        or _is_file_header_at(lines, next_index)
    )


def _parse_hunks(patch: str) -> list[PatchHunk]:
    """Provide mutable-list compatibility for existing core callers."""

    return list(parse_patch_hunks(patch))


def _diff_path(header_value: str) -> str:
    path = header_value.split("\t", 1)[0].strip()
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path or "SKILL.md"


def _find_hunk_position(lines: list[str], hunk: PatchHunk) -> int:
    """Find where a hunk's context+delete lines match in the original."""
    pattern = hunk.context_and_deletes
    if not pattern:
        return len(lines)

    pat_len = len(pattern)
    for start in range(len(lines) - pat_len + 1):
        if _lines_match(lines, start, pattern):
            return start

    # Fallback 1: stripped matching
    stripped_pattern = [ln.strip() for ln in pattern]
    for start in range(len(lines) - pat_len + 1):
        candidate = [lines[start + j].strip() for j in range(pat_len)]
        if candidate == stripped_pattern:
            return start

    # Fallback 2: markdown list ambiguity — when the original line starts
    # with "- " the diff "-" prefix merges with it, losing one character.
    # Try restoring "- " prefix on pattern lines that don't start with it
    # but whose original counterpart does.
    for start in range(len(lines) - pat_len + 1):
        if _lines_match_recover_dash(lines, start, pattern):
            return start

    raise PatchApplyError(
        f"Cannot locate hunk in original text. "
        f"First context line: {pattern[0]!r}"
    )


def _lines_match(lines: list[str], start: int, pattern: list[str]) -> bool:
    for j, pat_line in enumerate(pattern):
        if lines[start + j] != pat_line:
            return False
    return True


def _lines_match_recover_dash(
    lines: list[str], start: int, pattern: list[str]
) -> bool:
    """Match with recovery for markdown list dash ambiguity.

    In unified diff, a line starting with "- " in the original gets its
    leading dash consumed by the diff "-" prefix, leaving " ..." or
    "..." instead of "- ...".  We try recovering by prepending "- "
    when the original line starts with "- " but the pattern line doesn't.
    """
    for j, pat_line in enumerate(pattern):
        orig = lines[start + j]
        if orig == pat_line:
            continue
        if orig.startswith("- ") and not pat_line.startswith("- "):
            if orig == "- " + pat_line or orig == "-" + pat_line:
                continue
        if orig.strip() == pat_line.strip():
            continue
        return False
    return True


def _apply_hunk(lines: list[str], hunk: PatchHunk, pos: int) -> list[str]:
    """Apply one hunk at the given position."""
    result = lines[:pos]
    cursor = pos
    for op, content in hunk.operations:
        if op == " ":
            if cursor < len(lines):
                result.append(lines[cursor])
                cursor += 1
            else:
                result.append(content)
        elif op == "-":
            cursor += 1
        elif op == "+":
            result.append(content)
    result.extend(lines[cursor:])
    return result


# ---------------------------------------------------------------------------
# Multi-tier fallback application (never raises; returns a report)
# ---------------------------------------------------------------------------


def _try_intent_apply(lines: list[str], hunk: PatchHunk) -> list[str] | None:
    """Tier 3: intent-based fallback.

    For pure-add hunks: walk the operations, and at each `+` group, find the
    most recent context line that genuinely exists in ``lines`` (search
    backwards through the operations until we hit a context line whose text
    is present in the current file). Insert the `+` block right after that
    anchor. Hallucinated context lines are silently skipped.

    For pure-delete hunks: search the file for each `-` line as an exact
    match (or stripped match) and remove the first occurrence. Returns None
    if any `-` line cannot be located.

    For mixed `+`/`-` hunks: returns None (too risky to guess placement).
    """
    has_add = any(op == "+" for op, _ in hunk.operations)
    has_del = any(op == "-" for op, _ in hunk.operations)
    if has_add and has_del:
        return None
    if not has_add and not has_del:
        return None

    if has_add and not has_del:
        new_lines = list(lines)
        ops = hunk.operations
        i = 0
        n = len(ops)
        while i < n:
            if ops[i][0] != "+":
                i += 1
                continue
            # Find anchor: the most recent context line above this + group
            # that genuinely exists in new_lines.
            anchor_pos = None
            for j in range(i - 1, -1, -1):
                if ops[j][0] != " ":
                    continue
                cand = ops[j][1]
                # Search new_lines for last occurrence of cand
                for k in range(len(new_lines) - 1, -1, -1):
                    if new_lines[k] == cand or new_lines[k].strip() == cand.strip():
                        anchor_pos = k
                        break
                if anchor_pos is not None:
                    break
            # Collect consecutive + lines starting at i
            add_block: list[str] = []
            while i < n and ops[i][0] == "+":
                add_block.append(ops[i][1])
                i += 1
            if anchor_pos is None:
                # No real context line above this + group — append at EOF as
                # last resort (still better than dropping the patch entirely)
                new_lines = new_lines + add_block
            else:
                new_lines = new_lines[: anchor_pos + 1] + add_block + new_lines[anchor_pos + 1 :]
        return new_lines

    # Pure-delete
    new_lines = list(lines)
    for op, content in hunk.operations:
        if op != "-":
            continue
        removed = False
        for k, ln in enumerate(new_lines):
            if ln == content or ln.strip() == content.strip():
                new_lines.pop(k)
                removed = True
                break
            # Markdown dash recovery: original line is "- foo" but the diff
            # consumed the leading "-", so content is " foo". Match by
            # comparing the original's tail after "- " to content's lstrip.
            if ln.startswith("- ") and (
                ln == "- " + content or ln == "-" + content
                or ln[2:].strip() == content.strip()
            ):
                new_lines.pop(k)
                removed = True
                break
        if not removed:
            return None
    return new_lines


def apply_patch_with_fallback(original: str, patch: str) -> ApplyReport:
    """Multi-tier patch application. Never raises; returns an ``ApplyReport``.

    Tier 1 — strict per-hunk: each hunk is located via context-line matching
        (with whitespace and dash-prefix tolerance) and applied with
        :func:`_apply_hunk`. Failures of one hunk no longer poison the rest.
    Tier 3 — intent extraction: for hunks that fail Tier 1, if the hunk is
        pure-add we insert the `+` lines after the most recent context line
        that genuinely exists in the file (skipping hallucinated context).
        Pure-delete hunks search for each `-` line and remove the first
        match. Mixed hunks fall through to ``hunks_failed``.

    Hunks are processed left-to-right; positions are re-resolved after each
    successful application so subsequent hunks see the updated text.
    """
    patch = patch.strip()
    report = ApplyReport(text=original)
    if not patch:
        return report

    hunks = _parse_hunks(patch)
    if not hunks:
        report.fail_diagnostics.append("No valid hunks parsed.")
        return report

    report.hunks_total = len(hunks)
    original_is_empty = not original.strip()
    text_lines = original.split("\n")

    for hi, hunk in enumerate(hunks, 1):
        # Tier 1: strict per-hunk
        try:
            pos = _find_hunk_position(text_lines, hunk)
            text_lines = _apply_hunk(text_lines, hunk, pos)
            report.hunks_strict += 1
            continue
        except PatchApplyError as exc:
            tier1_err = str(exc)

        # Tier 3: intent extraction
        intent_result = _try_intent_apply(text_lines, hunk)
        if intent_result is not None:
            text_lines = intent_result
            report.hunks_intent += 1
            continue

        # All tiers failed
        report.hunks_failed += 1
        first_ctx = hunk.context_and_deletes[0] if hunk.context_and_deletes else "(empty)"
        report.fail_diagnostics.append(
            f"Hunk {hi}: failed all tiers. {tier1_err} First context: {first_ctx!r}"
        )

    result = "\n".join(text_lines)
    if original_is_empty:
        result = result.lstrip("\n")
    report.text = result
    return report
