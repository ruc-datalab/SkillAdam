### Evaluation Metrics

The evaluator compares the workbook produced by the agent's `solution.py`
against a golden workbook on the cells of `answer_position` (a range or
comma-separated set of ranges, optionally sheet-prefixed). Numbers are
quantized to 2 decimals, datetimes to integer Excel serial days, `""` and
`None` are treated as equivalent, and type mismatches fail. Cells outside
`answer_position` are NOT graded.

**Metrics in the trajectory label:**

- `outcome`: `success` if every cell in `answer_position` matches the
  golden workbook (`hard=1`); `failure` otherwise.
- `hard`: 0/1 — primary metric, matches official SpreadsheetBench scoring.
- `per_cell`: fraction of cells in `answer_position` that match the gold.
  Continuous in [0, 1]. Captures partial progress when `hard=0`.
- `exec`: 0/1 — whether the agent's last `solution.py` ran without raising
  and produced an output file. Distinguishes "code never ran" from "code
  ran but answered wrong".
- `turns`: number of LLM turns the agent used (≤ 30).
- `type`: `Cell-Level` (small target range, often a single computed value)
  or `Sheet-Level` (larger structural change). Per-type pattern
  differences can suggest where the skill is missing coverage.

**How to read:**

- `hard=1 | per_cell=1.00 | exec=1 | turns=N` → task fully solved.
- `hard=0 | per_cell=0.85 | exec=1 | turns=N` → code ran cleanly but most
  cells were correct except a small/local error (off-by-one, wrong type,
  missing edge case).
- `hard=0 | per_cell=0.00 | exec=0 | turns=N` → the script never executed
  cleanly within the turn budget. The agent may be stuck on a syntax
  error, a workbook exception, or a wrong sheet/range reference.
- `hard=0 | per_cell=0.00 | exec=1` → code ran but produced output that
  matches no graded cell (wrong sheet, wrong range, output overwritten).

## Train/Eval Condition Asymmetry

Each batch trajectory header carries two derived flags:

- `first_turn_correct=True`: the agent passed all graded cells on its
  first code generation attempt. From the gate's perspective (single-shot,
  no eval feedback), this case will also pass — it is stable.
- `required_recovery=True`: the agent eventually passed, **but only after
  receiving cell-level eval feedback and revising its code**. The gate
  phase is single-shot with no feedback, so these cases will most likely
  **fail** at gate time — unless the skill teaches the agent to avoid the
  first-turn mistake.

When diagnosing failure patterns, **treat `required_recovery=True` traces
as first-turn failures** — their "recovery" depended on training-phase
feedback that the gate does not provide. Patches targeting these cases
should aim to **improve first-turn code correctness**.

Similarly, **deprioritize `first_turn_correct=True` success cases as
improvement targets** — they are already stable through the gate and do
not require skill revision.

## Skill Specificity Guidance

SpreadsheetBench tasks are code-generation tasks: the agent writes
`solution.py` using `openpyxl`. Unlike dialogue-based benchmarks where
skills guide conversation strategy, here code-level guidance is directly
actionable:

- Workflow steps can include specific openpyxl method calls as candidate
  approaches (e.g. `iter_rows(min_row=2, values_only=True)` for reading
  data, `ws.cell(row=r, column=c).value` for targeted writes).
- The skill can include a Code Patterns section with short openpyxl
  idioms that have prevented failures in observed trajectories.
- Error Avoidance items can reference specific API behaviors when those
  behaviors are the source of recurring confusion (e.g. how `ws.append()`
  chooses its write position).
