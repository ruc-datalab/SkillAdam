## Spreadsheet-specific guidance

The public skill format remains exactly `## Workflow` followed by
`## Error Avoidance`. Short reusable openpyxl idioms may be placed under
the relevant workflow step or error-avoidance rule; do not add a third
top-level section.

Generalize from workbook structure and API behavior. Do not preserve literal
case IDs, local paths, one-off sheet names, cell addresses, expected values,
or evaluator thresholds in the skill.
