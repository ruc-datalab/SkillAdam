"""SpreadsheetBench value normalization and workbook evaluation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import datetime
from importlib import import_module
import math
from pathlib import Path
import re
from typing import Any

from skilladam.benchmarks.base import load_optional_dependency
from skilladam.benchmarks.spreadsheetbench.rollout import INSTALL_HINT


_CELL_RE = re.compile(r"^(?P<column>[A-Za-z]+)(?P<row>[1-9][0-9]*)$")


def compare_cell_value(left: Any, right: Any) -> bool:
    """Compare values with the historical SkillOpt normalization rules."""

    normalized_left = _transform_value(left)
    normalized_right = _transform_value(right)
    if (
        normalized_left == ""
        and normalized_right is None
    ) or (
        normalized_left is None
        and normalized_right == ""
    ):
        return True
    if normalized_left in ("", None) and normalized_right in ("", None):
        return True
    if type(normalized_left) is not type(normalized_right):
        return False
    return bool(normalized_left == normalized_right)


def expand_cells(range_text: str) -> tuple[str, ...]:
    """Expand one A1 range in the historical column-major order."""

    if not isinstance(range_text, str) or not range_text.strip():
        raise ValueError("SpreadsheetBench cell range must be non-empty")
    stripped = range_text.strip().strip("'\"")
    if ":" not in stripped:
        column, row = _parse_cell(stripped)
        return (f"{column}{row}",)
    parts = stripped.split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid SpreadsheetBench cell range {range_text!r}")
    start_column, start_row = _parse_cell(parts[0])
    end_column, end_row = _parse_cell(parts[1])
    start_number = _column_name_to_number(start_column)
    end_number = _column_name_to_number(end_column)
    if end_number < start_number or end_row < start_row:
        raise ValueError(f"reversed SpreadsheetBench range {range_text!r}")
    return tuple(
        f"{_column_number_to_name(column)}{row}"
        for column in range(start_number, end_number + 1)
        for row in range(start_row, end_row + 1)
    )


def score_cell_mapping(
    predicted: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> dict[str, float]:
    """Score an offline synthetic cell mapping with official value rules."""

    if not expected:
        raise ValueError(
            "SpreadsheetBench synthetic reference must not be empty"
        )
    matches = sum(
        compare_cell_value(expected_value, predicted.get(cell))
        for cell, expected_value in expected.items()
    )
    total = len(expected)
    per_cell = matches / total
    return {
        "hard": float(matches == total),
        "per_cell_pass_rate": per_cell,
        "n_cells_total": float(total),
        "n_cells_match": float(matches),
    }


def compare_workbooks(
    predicted_path: str | Path,
    golden_path: str | Path,
    answer_position: str,
    answer_sheet: str = "",
    *,
    importer: Callable[[str], Any] = import_module,
) -> dict[str, Any]:
    """Compare materialized workbooks, importing openpyxl on demand."""

    predicted_path = Path(predicted_path)
    if not predicted_path.is_file():
        return _failed_workbook_result("pred file not exist")
    openpyxl = load_optional_dependency(
        "spreadsheetbench",
        "openpyxl",
        INSTALL_HINT,
        importer=importer,
    )
    try:
        predicted = openpyxl.load_workbook(
            predicted_path,
            data_only=True,
        )
    except Exception as exc:
        return _failed_workbook_result(f"load error: {exc}")
    try:
        golden = openpyxl.load_workbook(
            golden_path,
            data_only=True,
        )
    except Exception as exc:
        predicted.close()
        return _failed_workbook_result(f"load error: {exc}")

    total = 0
    matches = 0
    first_difference = ""
    missing_sheets: list[str] = []
    mismatched_cells: list[str] = []
    try:
        for reference in _answer_references(answer_position):
            sheet_name, cell_range = _split_reference(
                reference,
                answer_sheet=answer_sheet,
                default_sheet=golden.sheetnames[0],
            )
            cells = expand_cells(cell_range)
            total += len(cells)
            if sheet_name not in predicted.sheetnames:
                missing_sheets.append(sheet_name)
                mismatched_cells.extend(
                    f"{sheet_name}!{cell}" for cell in cells
                )
                if not first_difference:
                    first_difference = (
                        f"sheet missing in pred: {sheet_name}"
                    )
                continue
            predicted_sheet = predicted[sheet_name]
            golden_sheet = (
                golden[sheet_name]
                if sheet_name in golden.sheetnames
                else None
            )
            for cell in cells:
                expected = (
                    golden_sheet[cell].value
                    if golden_sheet is not None
                    else None
                )
                actual = predicted_sheet[cell].value
                if compare_cell_value(expected, actual):
                    matches += 1
                else:
                    mismatched_cells.append(f"{sheet_name}!{cell}")
                    if not first_difference:
                        first_difference = (
                            f"{sheet_name}!{cell}: "
                            f"gold={expected!r} pred={actual!r}"
                        )
    finally:
        predicted.close()
        golden.close()
    rate = matches / total if total else 0.0
    return {
        "ok": total > 0 and matches == total,
        "first_diff_msg": first_difference,
        "n_cells_total": total,
        "n_cells_match": matches,
        "per_cell_pass_rate": round(rate, 4),
        "missing_sheets": missing_sheets,
        "mismatched_cells": mismatched_cells,
    }


def _transform_value(value: Any) -> Any:
    if isinstance(value, bool):
        return round(float(value), 2)
    if isinstance(value, (int, float)):
        numeric = float(value)
        return round(numeric, 2) if math.isfinite(numeric) else numeric
    if isinstance(value, datetime.time):
        return str(value)[:-3]
    if isinstance(value, datetime.datetime):
        epoch = datetime.datetime(1899, 12, 30)
        delta = value - epoch
        serial = delta.days + delta.seconds / 86400.0
        return round(serial, 0)
    if isinstance(value, str):
        try:
            return round(float(value), 2)
        except ValueError:
            return value
    return value


def _parse_cell(value: str) -> tuple[str, int]:
    match = _CELL_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid SpreadsheetBench cell {value!r}")
    return match.group("column").upper(), int(match.group("row"))


def _column_name_to_number(name: str) -> int:
    number = 0
    for character in name:
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def _column_number_to_name(number: int) -> str:
    name = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _answer_references(answer_position: str) -> tuple[str, ...]:
    if not isinstance(answer_position, str):
        raise ValueError(
            "SpreadsheetBench answer_position must be text"
        )
    references = tuple(
        item.strip()
        for item in answer_position.split(",")
        if item.strip()
    )
    if not references:
        raise ValueError(
            "SpreadsheetBench answer_position must not be empty"
        )
    return references


def _split_reference(
    reference: str,
    *,
    answer_sheet: str,
    default_sheet: str,
) -> tuple[str, str]:
    if "!" in reference:
        sheet_name, cell_range = reference.split("!", 1)
        return (
            sheet_name.strip().strip("'\""),
            cell_range.strip().strip("'\""),
        )
    return answer_sheet or default_sheet, reference.strip().strip("'\"")


def _failed_workbook_result(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "first_diff_msg": message,
        "n_cells_total": 0,
        "n_cells_match": 0,
        "per_cell_pass_rate": 0.0,
        "missing_sheets": [],
        "mismatched_cells": [],
    }
