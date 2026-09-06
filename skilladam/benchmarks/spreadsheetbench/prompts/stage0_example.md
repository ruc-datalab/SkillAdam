Input (3 trajectories from the SpreadsheetBench domain):

```
[1] outcome=success | hard=1 | per_cell=1.00 | exec=1 | turns=1 | type=Cell-Level

## Instruction
Calculate the total revenue for Q3 by summing cells D2:D45 and write the result to cell F2.

## Execution Trace
### Turn 1
**Solution.py:**
```python
from openpyxl import load_workbook
wb = load_workbook('input.xlsx')
ws = wb.active
total = sum(ws.cell(row=r, column=4).value or 0 for r in range(2, 46))
ws.cell(row=2, column=6).value = total
wb.save('output.xlsx')
```

## Outcome
- outcome: success
- hard: 1

---

[2] outcome=failure | hard=0 | per_cell=0.00 | exec=0 | turns=3 | type=Sheet-Level

## Instruction
Sort all data rows by column C (descending) and write the sorted table back to the same sheet.

## Execution Trace
### Turn 1
**Solution.py:**
```python
from openpyxl import load_workbook
wb = load_workbook('input.xlsx')
ws = wb.active
data = list(ws.values)
header = data[0]
rows = sorted(data[1:], key=lambda x: x[2], reverse=True)
ws.delete_rows(1, ws.max_row)
ws.append(header)
for row in rows:
    ws.append(row)
wb.save('output.xlsx')
```
**Feedback:**
TypeError: '<' not supported between instances of 'NoneType' and 'str'

### Turn 2
**Solution.py:**
```python
...  # added None handling but still used ws.append()
```
**Feedback:**
Execution succeeded but output mismatch.

### Turn 3 (Final)
**Solution.py:**
```python
...  # same approach, still wrong cell positions
```

## Outcome
- outcome: failure
- hard: 0

---

[3] outcome=failure | hard=0 | per_cell=0.85 | exec=1 | turns=2 | type=Cell-Level

## Instruction
For each row in column A, look up the matching price from Sheet2 column B and write it to column D.

## Execution Trace
### Turn 1
**Solution.py:**
```python
from openpyxl import load_workbook
wb = load_workbook('input.xlsx')
ws1 = wb['Sheet1']
ws2 = wb['Sheet2']
lookup = {row[0]: row[1] for row in ws2.iter_rows(values_only=True)}
for row in range(2, ws1.max_row + 1):
    key = ws1.cell(row=row, column=1).value
    ws1.cell(row=row, column=4).value = lookup.get(key)
wb.save('output.xlsx')
```
**Feedback:**
Cell D7: got=None, expected=29.99

### Turn 2 (Final)
**Solution.py:**
```python
...  # added str() conversion but missed header row in Sheet2 lookup
```

## Outcome
- outcome: failure
- hard: 0
- per_cell_pass_rate: 0.85
```

Output:

```json
{
  "metadata_json": {
    "name": "spreadsheet-data-transformation",
    "description": "Generate Python scripts that read, transform, and write Excel workbook data using openpyxl. Use when the task requires computing values, sorting rows, performing lookups, or restructuring spreadsheet content.",
    "when_to_use": [
      "The task asks to transform, compute, sort, or restructure spreadsheet data",
      "The output must be written back to specific cells or ranges in an Excel file"
    ]
  },
  "skill_body_md": "# Spreadsheet Data Transformation\n\n## Workflow\n1. **Inspect structure**: Load the workbook and identify source/destination sheets, header rows, and the target output range (`answer_position`). `wb.sheetnames` and `ws.iter_rows(max_row=2)` can help understand layout before coding logic.\n2. **Read data explicitly**: Read source data with `iter_rows(min_row=2, values_only=True)` to skip headers. Build intermediate structures (lists, dicts) rather than operating on the worksheet object in-place.\n3. **Handle types**: Cast cell values before comparisons or arithmetic — cells may contain None, mixed types, or formatted strings. `or 0` can work for numeric defaults, `str(v)` for key lookups.\n4. **Write to exact cells**: For known target ranges, write results with `ws.cell(row=r, column=c).value = val`. Consider clearing the target range first if overwriting existing content.\n5. **Verify before saving**: Spot-check a few written cells before `wb.save()`.\n\n## Code Patterns\n- **Skip-header read**:\n  `data = list(ws.iter_rows(min_row=2, values_only=True))`\n  Avoids accidentally including headers in sort/filter/computation.\n- **Explicit cell write**:\n  `ws.cell(row=start+i, column=col).value = val`\n  Avoids stale-data contamination from append-based writes.\n- **Safe lookup dict** (cross-sheet):\n  `lookup = {str(r[0]): r[1] for r in ws2.iter_rows(min_row=2, values_only=True)}`\n  Skips header row and normalizes keys to avoid None/type mismatches.\n- **None-safe sort key**:\n  `sorted(rows, key=lambda x: (x[col] is None, x[col]))`\n  Puts None values last instead of raising TypeError.\n\n## Error Avoidance\n- `ws.append()` writes after the last used row, not at a caller-specified position — use explicit cell assignment when targeting a known range.\n- `iter_rows(values_only=True)` without `min_row` includes the header row — set `min_row=2` when building data lists or lookup dicts.\n- Comparing or sorting cells that may contain None raises TypeError — use a fallback or filter first.\n- Writing outside `answer_position` does not affect grading but can corrupt other data in the workbook."
}
```
