"""Failure/success type taxonomy — per-adapter loader and tool-schema builder.

Each adapter that opts into the type-taxonomy mechanism must provide two
markdown files in its ``prompts/`` directory:

    failure_types.md
    success_types.md

Both follow the same loose-markdown convention::

    - **type_name**: One-line description in any language.

The leading bullet, the bold-wrapped name, and the colon are required.
Extra body lines beneath an entry are ignored. Blank lines and prose
headings are skipped — a forgiving parser by design, since the file is
hand-edited.

Public API
----------
- ``TypeEntry`` — one `(name, description)` row.
- ``TaxonomySpec`` — bundle of failure + success entries with helpers
  for prompt rendering and tool schema enum extraction.
- ``load_taxonomy(prompts_dir)`` — strict loader. Raises
  ``FileNotFoundError`` if either file is missing and ``ValueError`` if
  a file parses to zero entries.
- ``render_taxonomy_section(...)`` — builds the markdown block injected
  into both iteration_system_prompt and momentum_update_system.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


_TYPE_LINE_RE = re.compile(
    r"^\s*-\s*\*\*\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\*\*\s*:\s*(?P<desc>.+?)\s*$"
)


@dataclass(frozen=True)
class TypeEntry:
    """One entry in a taxonomy: the enum name plus a human-readable
    one-line description. Both fields are required."""

    name: str
    description: str


@dataclass
class TaxonomySpec:
    """Bundle of failure types + success types loaded for one adapter."""

    failure_types: list[TypeEntry] = field(default_factory=list)
    success_types: list[TypeEntry] = field(default_factory=list)
    source_dir: str = ""

    @property
    def failure_type_names(self) -> list[str]:
        return [t.name for t in self.failure_types]

    @property
    def success_type_names(self) -> list[str]:
        return [t.name for t in self.success_types]

    def render_failure_block(self) -> str:
        """Markdown bullet list of failure types, ready to drop into a prompt."""
        return "\n".join(f"- **{t.name}**: {t.description}" for t in self.failure_types)

    def render_success_block(self) -> str:
        return "\n".join(f"- **{t.name}**: {t.description}" for t in self.success_types)


def _parse_types_file(path: Path) -> list[TypeEntry]:
    """Parse one markdown file with ``- **name**: description`` bullets.

    Lines that do not match the bullet shape are skipped silently — this
    keeps headings and explanatory prose ignored without forcing the
    author to maintain strict YAML.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Type taxonomy file not found: {path}. When --enable-type-taxonomy "
            f"is on (default), every adapter must provide failure_types.md "
            f"and success_types.md alongside its other prompt files."
        )
    entries: list[TypeEntry] = []
    seen: set[str] = set()
    text = path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        m = _TYPE_LINE_RE.match(raw_line)
        if not m:
            continue
        name = m.group("name").strip()
        desc = m.group("desc").strip()
        if name in seen:
            logger.warning("Duplicate type name %r in %s — keeping first.", name, path)
            continue
        seen.add(name)
        entries.append(TypeEntry(name=name, description=desc))
    if not entries:
        raise ValueError(
            f"Type taxonomy file {path} parsed to zero entries. "
            f"Expected at least one bullet of the form '- **name**: description'."
        )
    return entries


def load_taxonomy(prompts_dir: str | Path) -> TaxonomySpec:
    """Load failure_types.md + success_types.md from ``prompts_dir``.

    Strict: missing files raise ``FileNotFoundError``; empty files raise
    ``ValueError``. The caller is expected to translate these into a
    pipeline-exit error so the run config stays consistent with the
    actual content the LLMs see.
    """
    prompts_dir = Path(prompts_dir)
    failures = _parse_types_file(prompts_dir / "failure_types.md")
    successes = _parse_types_file(prompts_dir / "success_types.md")
    return TaxonomySpec(
        failure_types=failures,
        success_types=successes,
        source_dir=str(prompts_dir),
    )


def render_taxonomy_section(spec: TaxonomySpec) -> str:
    """Build the markdown block that gets dropped into iteration_system_prompt
    and momentum_update_system whenever the taxonomy flag is on."""
    parts: list[str] = [
        "## Failure Type Taxonomy",
        "",
        "When classifying a failure pattern, choose exactly one of the following",
        "labels. These labels are the controlled vocabulary for the `failure_type`",
        "field on every finding and `report_problem` call.",
        "",
        spec.render_failure_block(),
        "",
        "## Success Type Taxonomy",
        "",
        "When classifying a success pattern, choose exactly one of the following",
        "labels for the `success_type` field on every finding.",
        "",
        spec.render_success_block(),
    ]
    return "\n".join(parts)
