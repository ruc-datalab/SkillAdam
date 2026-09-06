# SpreadsheetBench Failure Types

The iteration agent and momentum agent both classify every observed
failure into exactly one of the following types. Use these names
character-for-character.

- **rule_missing**: The skill lacks any rule that would cover this class of spreadsheet task, and adding one is the appropriate fix.
- **rule_wrong**: An existing skill rule actively misleads the agent — e.g., advises writing formulas when the evaluator reads literal values; the right fix is to revise or remove that rule.
- **rule_ignored**: The skill already contains a rule that would have prevented the failure, but the rollout agent did not follow it. Strengthen or concretize the existing entry rather than adding another similar one.
- **data_exploration**: The agent did not inspect enough of the workbook before writing code — wrong sheet selected, header row miscounted, target range guessed, governing input cell (year, control, criteria) missed, or example/reference tabs not consulted. The fix is to encourage broader exploration in the skill, not to encode any specific cell address.
- **code_error**: The generated Python code has a bug that is unrelated to the skill — syntax error, openpyxl/pandas API misuse, hard-coded paths, leaving `INPUT_PATH` / `OUTPUT_PATH` placeholders unfilled, etc. **Do NOT propose a skill edit to fix code_error**; the rollout multi-turn retry loop is the right channel.
- **other**: None of the above. Use this when the failure is irreducible (genuinely ambiguous instruction, eval-script edge case, runtime/exec timeout that the skill cannot prevent).
