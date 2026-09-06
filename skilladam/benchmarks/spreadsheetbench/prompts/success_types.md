# SpreadsheetBench Success Types

Use these names character-for-character to classify a successful pattern
the iteration agent finds worth encoding in the skill. Success types are
purely informational on the iteration side (written to
`iteration_diagnosis.json`); the momentum tracker does not persist them.

- **applied_rule**: The agent's correct output can be traced to a specific rule in the current skill — that rule did the work and should be kept, possibly strengthened with a concrete code snippet.
- **effective_exploration**: Before writing any code, the agent thoroughly read the workbook — listed sheets, located headers, identified the governing input cell, distinguished source tables from destination tables, or consulted reference / example tabs. Worth reinforcing whenever exploration was the load-bearing step.
- **iterative_recovery**: The agent produced a first solution that failed, then used the per-turn eval feedback to correctly diagnose and fix the bug within `max_turns`. Worth encoding when the recovery loop demonstrates a reusable debugging pattern (e.g., re-reading saved workbook to verify written values).
- **other**: None of the above. Use this when the agent's success does not point to a generalizable skill rule (task happened to be trivial, model's default codegen sufficed, lucky exec without verification).
