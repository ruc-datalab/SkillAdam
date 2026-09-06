# SearchQA Success Types

Use these names character-for-character to classify a successful pattern
the iteration agent finds worth encoding in the skill. Success types are
purely informational on the iteration side (they are written to
`iteration_diagnosis.json` for later analysis); the momentum tracker does
not persist them.

- **applied_rule**: The agent's correct answer can be traced to a specific rule in the current skill — the rule did the work, and it should be kept (and possibly strengthened with a concrete example).
- **disambiguation**: The agent correctly resolved an ambiguous clue — Jeopardy "this X" type inference, relation direction (subject vs object), inverse-relationship clues, picking the encompassing entity over a listed example, or selecting the right historical vs current name.
- **concise_normalization**: The agent produced the canonical / shortest unambiguous form when several phrasings were possible — dropped redundant modifiers, preserved required feature designators (Lake / Mount / Inc.), retained conventional punctuation (straight apostrophes, "St."), or chose singular vs plural correctly.
- **other**: None of the above. Use this when the agent succeeded for reasons that do not generalize into a skill rule (background knowledge, the context paragraph literally contained the answer string, lucky guess on an ambiguous clue, etc.).
