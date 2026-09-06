"""Provider-neutral SpreadsheetBench prompt and runtime boundaries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from typing import Any

from skilladam.benchmarks.base import load_optional_dependency
from skilladam.types import BenchmarkCase, Method


INSTALL_HINT = "pip install 'skilladam[spreadsheetbench]'"

VENDOR_PROMPT = (
    "You are an expert Python programmer specializing in spreadsheet "
    "manipulation. You will be given a user instruction together with a "
    "preview of an input .xlsx file. Your job is to write a single "
    "self-contained Python script that reads the input file at the path "
    "stored in the variable INPUT_PATH, performs the requested "
    "manipulation, and saves the result to OUTPUT_PATH. Use only the "
    "standard library, openpyxl, and pandas. Do not print anything. Do "
    "not use input(). Do not hardcode file paths. Return ONLY the Python "
    "code inside a single ```python ... ``` fenced block.\n\n"
    "## Library Selection\n\n"
    "| Use case | Library |\n"
    "|----------|---------|\n"
    "| Preserve formulas, formatting, named ranges | `openpyxl` |\n"
    "| Bulk data transformation, aggregation, sorting | `pandas` → "
    "write back with `openpyxl` |\n"
    "| Simple cell read/write | `openpyxl` |\n\n"
    "**Warning**: `pandas.to_excel()` silently destroys existing formulas "
    "and named ranges. When writing back to a spreadsheet that contains "
    "formulas, always load with `openpyxl.load_workbook(...)`, mutate "
    "values in place, and persist via `wb.save(OUTPUT_PATH)`.\n\n"
    "## Output Requirements\n\n"
    "- Save the result to `OUTPUT_PATH` (variable already bound by the "
    "runner).\n"
    "- Do not hardcode row counts or column letters — iterate over actual "
    "rows in the workbook (`ws.max_row`, `ws.iter_rows(...)`).\n"
    "- Preserve sheets and cells not mentioned in the instruction.\n"
    "- When in doubt about which sheet to mutate, use `wb.active` only if "
    "the workbook has a single sheet; otherwise explicitly pick by name."
)


def build_messages(
    case: BenchmarkCase,
    *,
    method: Method,
    skill: str | None,
) -> tuple[dict[str, str], ...]:
    """Build the shared SkillAdam/SkillOpt code-generation prompt."""

    clean_skill = skill.strip() if isinstance(skill, str) else ""
    skill_section = f"\n\n## Skill\n{clean_skill}" if clean_skill else ""
    return (
        {
            "role": "system",
            "content": VENDOR_PROMPT + skill_section,
        },
        {
            "role": "user",
            "content": _build_user_prompt(case.payload),
        },
    )


def extract_python_code(text: str) -> str:
    """Extract the first fenced block, matching the historical runner."""

    if not isinstance(text, str):
        raise ValueError("SpreadsheetBench code must be text")
    if "```" not in text:
        return text.strip()
    start = text.find("```")
    newline = text.find("\n", start)
    end = text.find("```", newline + 1)
    if newline == -1 or end == -1:
        return text.strip()
    return text[newline + 1 : end].strip()


def prompt_profile_for_method(method: Method) -> str:
    """Both methods historically use the same target-agent prompt."""

    return "spreadsheetbench-shared"


def load_execution_dependencies(
    *,
    importer: Callable[[str], Any] = import_module,
) -> dict[str, Any]:
    """Load workbook execution dependencies only when execution starts."""

    return {
        "openpyxl": load_optional_dependency(
            "spreadsheetbench",
            "openpyxl",
            INSTALL_HINT,
            importer=importer,
        ),
        "pandas": load_optional_dependency(
            "spreadsheetbench",
            "pandas",
            INSTALL_HINT,
            importer=importer,
        ),
    }


def preview_workbook(
    path: str | Path,
    *,
    max_rows: int = 5,
    max_cols: int = 20,
    importer: Callable[[str], Any] = import_module,
) -> str:
    """Render the historical workbook preview behind a lazy import."""

    openpyxl = load_optional_dependency(
        "spreadsheetbench",
        "openpyxl",
        INSTALL_HINT,
        importer=importer,
    )
    workbook = openpyxl.load_workbook(path, data_only=False)
    chunks: list[str] = []
    try:
        for sheet_name in workbook.sheetnames:
            worksheet = workbook[sheet_name]
            chunks.append(
                f"## Sheet: {sheet_name} "
                f"(dim={worksheet.dimensions}, "
                f"max_row={worksheet.max_row}, "
                f"max_col={worksheet.max_column})"
            )
            for row in worksheet.iter_rows(
                min_row=1,
                max_row=min(worksheet.max_row, max_rows),
                max_col=min(worksheet.max_column, max_cols),
                values_only=False,
            ):
                chunks.append(
                    " | ".join(
                        f"{cell.coordinate}={_short_value(cell.value)}"
                        for cell in row
                    )
                )
            if worksheet.max_row > max_rows:
                chunks.append(
                    f"... ({worksheet.max_row - max_rows} more rows)"
                )
            chunks.append("")
    finally:
        workbook.close()
    return "\n".join(chunks)


def _build_user_prompt(payload: Mapping[str, Any]) -> str:
    instruction = payload.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError(
            "SpreadsheetBench instruction must be non-empty text"
        )
    extras: list[str] = []
    instruction_type = payload.get("instruction_type")
    if isinstance(instruction_type, str) and instruction_type.strip():
        extras.append(f"Instruction type: {instruction_type}")
    answer_position = payload.get("answer_position")
    answer_sheet = payload.get("answer_sheet")
    if isinstance(answer_position, str) and answer_position.strip():
        rendered_position = answer_position
        if (
            isinstance(answer_sheet, str)
            and answer_sheet.strip()
            and "!" not in answer_position
        ):
            rendered_position = f"{answer_sheet}!{answer_position}"
        extras.append(f"Expected answer position: {rendered_position}")
    extra_block = f"\n{chr(10).join(extras)}" if extras else ""
    preview = _payload_preview(payload)
    return (
        f"# Instruction\n{instruction}{extra_block}\n\n"
        f"# Input spreadsheet preview\n{preview}\n\n"
        "# Task\n"
        "Write a Python script that reads the workbook from `INPUT_PATH`, "
        "applies the instruction, and writes the modified workbook to "
        "`OUTPUT_PATH`. Preserve all other cells unchanged. The preview "
        "may be truncated — do not hardcode row counts or assume the data "
        "ends at the last previewed row; iterate over all actual rows in "
        "the workbook instead. Return only a ```python``` code block."
    )


def _payload_preview(payload: Mapping[str, Any]) -> str:
    workbook = payload.get("workbook")
    if isinstance(workbook, Mapping):
        chunks: list[str] = []
        for sheet_name in sorted(workbook, key=str):
            cells = workbook[sheet_name]
            if not isinstance(cells, Mapping):
                raise ValueError(
                    "SpreadsheetBench synthetic workbook sheets "
                    "must be objects"
                )
            chunks.append(f"## Sheet: {sheet_name}")
            chunks.append(
                " | ".join(
                    f"{sheet_name}!{cell}={_short_value(value)}"
                    for cell, value in sorted(
                        cells.items(),
                        key=lambda item: str(item[0]),
                    )
                )
                or "(empty sheet)"
            )
        return "\n".join(chunks)
    explicit_preview = payload.get("workbook_preview")
    if isinstance(explicit_preview, str) and explicit_preview.strip():
        return explicit_preview
    return (
        "[The execution runner must generate the workbook preview from "
        "the validated input workbook before invoking the model.]"
    )


def _short_value(value: Any) -> str:
    if value is None:
        return ""
    rendered = str(value)
    return rendered if len(rendered) <= 40 else rendered[:37] + "..."
