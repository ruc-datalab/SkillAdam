## Workflow
1. **Load for mutation, optionally load for values**: Use `wb = load_workbook(INPUT_PATH)` for writing. If existing formula results are needed, also open `wbv = load_workbook(INPUT_PATH, data_only=True)` and read cached values from `wbv`, but mutate only `wb`.
2. **Target exact sheets, cells, and qualifying rows**: Pick sheets by visible names when present and write only the requested output range. Avoid broad rewrites unless explicitly requested. For highlight/color-based conditions, inspect the relevant cells' `fill` and mutate only rows with the requested visible formatting.
3. **Translate formula requests into Python values**: Even when the user asks to create or insert a formula, write the evaluated displayed results in target cells. For SUMIFS, INDEX/MATCH, IF, unique lists, category lookup, and row totals, compute the scalar/list values in Python; preserve or recreate formulas only in non-output cells explicitly requested.
4. **Build lookup/aggregation/window structures first**: Read source rows with `iter_rows(..., values_only=True)`, normalize keys/dates, then write outputs. For multi-criteria sums, test each criterion explicitly, including OR lists and special filters such as positive-only amounts. For rectangular numeric lookup/aggregation outputs, write every requested cell and use numeric `0` for missing combinations unless blanks are explicitly requested. For rolling calculations over irregular dates, define the calendar window from each row's date rather than from row counts.
5. **Handle structural edits from stable anchors**: For delete/insert/copy tasks, find the anchor row or header case-insensitively, collect source data before modifying rows, then insert/delete/write with explicit `ws.cell()` coordinates. After row changes, recompute dependent formula/result columns for affected rows as literal values.
6. **Normalize text and separators**: Strip whitespace, compare case-insensitively when matching labels, use substring/regex checks when instructions say contains or partial match, split delimited cells into nonblank parts, and avoid duplicate delimiters when concatenating strings.

## Code Patterns
- **Dual load for existing formula values**:
  `wb = load_workbook(INPUT_PATH); wbv = load_workbook(INPUT_PATH, data_only=True)`
  `cached = wbv[ws.title].cell(r, c).value`
  Reads cached results without losing formulas in the workbook being saved.

- **Write computed values, not formulas**:
  `for r in range(start_row, end_row + 1):`
  `    ws.cell(r, out_col).value = compute_result_for_row(r)`
  Prevents `None` results when a formula-like request targets output cells.

- **SUMIFS with OR criteria**:
  `choices = [c.value for c in ws["C11:E11"][0] if c.value not in (None, "")]`
  `total = sum(amt for prod, reg, amt in rows if prod == product and ("All" in choices or reg in choices))`
  Implements multi-select criteria that Excel SUMIFS cannot express as repeated AND criteria.

- **Multi-key INDEX/MATCH lookup**:
  `default = 0 if numeric_output else None`
  `lookup[(norm(region), norm(item), norm(year))] = value`
  `ws.cell(r, c).value = lookup.get((norm(region), norm(item), norm(year)), default)`
  Avoids returning only the first matching row and keeps missing numeric grid cells from becoming blanks.

- **Safe numeric conversion**:
  `def num(v): return 0 if v in (None, "") else float(v)`
  Keeps sums from failing or concatenating text numbers.

- **Date-window aggregation over irregular rows**:
  `window = [v for d, v in rows if start_date <= d <= current_date]`
  `ws.cell(r, avg_col).value = sum(window) / len(window) if window else None`
  Uses calendar dates rather than row counts; repeated dates can share the same window result.

- **Color-conditioned edits**:
  `fill = ws.cell(r, c).fill; color = fill.fgColor`
  `is_yellow = bool(fill.fill_type) and ((color.type == "rgb" and color.rgb and color.rgb[-6:].upper() == "FFFF00") or (color.type == "indexed" and color.indexed == 6))`
  `if is_yellow: ws.cell(r, out_col).value = new_value`
  Prevents applying a highlight-only change to every formula-matching row.

- **Delimited lookup cell**:
  `codes = [p.strip() for p in str(cell.value).split(";") if p.strip()]`
  Handles cells with one or many semicolon-separated codes.

- **Case-insensitive anchor search**:
  `if value is not None and "invoice no." in str(value).lower(): found_row = r`
  Finds marker rows even with case or surrounding-text variation.

- **Concatenate without duplicate separators**:
  `out = f"{str(base).rstrip('+')}+{str(suffix).lstrip('+')}"`
  Prevents outputs like `base++suffix` when source text already ends with `+`.

## Error Avoidance
- Openpyxl does not calculate formulas. Treat requested formulas as calculations to emulate in Python, then write the displayed result values to target cells.
- Do not rely on existing formula cells in the normal workbook for numeric inputs; they appear as formula strings. Read cached values from a separate `data_only=True` workbook, and if the cache is missing, recompute from precedent cells.
- Repeating the same SUMIFS criteria range with several users creates AND logic, not OR logic. For OR criteria, loop rows in Python and use `value in allowed_values` or `any(...)`.
- INDEX/MATCH-like tasks often need multiple criteria such as category, row label, and year. Do not match only the first label if the source table has repeated labels across groups.
- `ws.append()` writes after the worksheet’s current last used row. For expected output ranges, use `ws.cell(row=r, column=c).value = value` so data lands in the graded cells.
- Row insertion and deletion shift later row numbers. Find all anchors first or iterate bottom-up when editing multiple rows.
- When joining matched header/entry text, strip only the separator edge, not meaningful content. Check expected examples for whether the delimiter should be exactly one `+`, comma, or space.