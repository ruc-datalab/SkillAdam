# OfficeQA Failure Types

Use these names character-for-character.

- **retrieval_miss**: The agent searched the wrong file or failed to narrow to the relevant Treasury bulletin.
- **evidence_miss**: The agent opened relevant documents but missed the decisive table row, column, chart mark, or paragraph.
- **operand_error**: The agent extracted the wrong value, period, unit, numerator, denominator, or adjacent-column role.
- **calculation_error**: The agent found the correct evidence but applied the wrong formula, conversion, or arithmetic.
- **answer_format**: The computed answer is substantively correct but uses the wrong precision, unit, symbol, or extra text.
- **other**: The failure is ambiguous, infrastructure-related, or not reducible to the types above.
