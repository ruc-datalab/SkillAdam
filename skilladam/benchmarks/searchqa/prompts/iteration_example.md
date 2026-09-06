The domain and content below are illustrative only — do not transfer any
of it into your output.

```json
{
  "reasoning": "## Observations\n\n[1] (failure): Question 'What does CPU stand for?'. Gold='Central Processing Unit'. Predicted='Central Processing Unit (CPU) — the main processor'. Over-expansion with parenthetical description.\n\n[2] (failure): Question 'What does USB stand for?'. Gold='Universal Serial Bus'. Predicted='Universal Serial Bus (USB), a standard interface for connecting peripherals'. Same over-expansion pattern.\n\n[3] (success): Question 'What does ROM stand for?'. Gold='Read-Only Memory'. Predicted='Read-Only Memory'. Clean canonical form.\n\n## Patterns\n\nTwo failures share the same over-expansion mode: appending '(ABBREV)' followed by a descriptive clause. The skill already has an Error Avoidance rule 'Do not append parenthetical descriptions or definitions to the canonical form.' that logically covers this failure, but the rollout model is not following it.\n\n## Proposed Changes\n\n1. **Strengthen** the existing Error Avoidance rule by attaching concrete (correct, incorrect) example pairs from the current batch directly under it as sub-bullets. Do not add a new abstract rule.\n\nEdit balance: 1 augmentation (no new top-level rule).",
  "patch": "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -10,6 +10,8 @@\n ## Error Avoidance\n \n - Do not append parenthetical descriptions or definitions to the canonical form.\n+  - 'Central Processing Unit' not 'Central Processing Unit (CPU) — the main processor'   (clue: 'What does CPU stand for?')\n+  - 'Universal Serial Bus' not 'Universal Serial Bus (USB), a standard interface for connecting peripherals'   (clue: 'What does USB stand for?')\n"
}
```

Note the patch attaches concrete `(correct, incorrect)` example pairs as
sub-bullets under an existing Error Avoidance rule, rather than adding a
new abstract rule. Use this pattern whenever a failure pattern recurs
across trajectories AND the corresponding skill content already exists:
the most effective fix is to ground the existing rule with concrete
training-batch examples, not to layer more abstract restatements.
