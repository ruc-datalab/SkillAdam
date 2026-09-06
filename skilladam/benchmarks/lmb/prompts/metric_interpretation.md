### Metric: Exact Match (EM)

EM is a binary metric for multiple-choice question answering:
- EM = 1 when the predicted label exactly matches the gold answer label (e.g., both are "B")
- EM = 0 otherwise

### Interpreting Trajectory Labels

Each trajectory is labeled as:
```
outcome=success | EM=1    — correct answer
outcome=failure | EM=0    — wrong answer
```

### Failure Type Taxonomy

Mathematical MCQ failures fall into five categories. Use these to diagnose patterns:

| Type | Description |
|------|-------------|
| `quantifier_miss` | Missed exact quantifiers, scope, or existence/uniqueness conditions (e.g., confusing "for all" with "there exists", or "at most" with "exactly") |
| `strength_mismatch` | Preferred a weaker or stronger statement than what was actually proved (e.g., choosing "≥" when the theorem proves "=") |
| `condition_miss` | Ignored hypotheses, equality cases, or domain restrictions stated in the problem |
| `option_confusion` | Confused similar answer choices or failed to compare them precisely against each other |
| `other` | None of the above |

### Hidden Reference

Each failed trajectory includes a **Hidden Reference** section containing the theorem statement and/or proof sketch from the source paper. This is NOT shown to the agent during rollout — it is provided exclusively to you (the optimizer) so you can perform precise error attribution. Use it to understand WHY the agent chose incorrectly (e.g., the theorem says "exactly N=3" but the agent interpreted it as "at least 3").

### What the Iteration Agent Should Focus On

Since this is mathematical reasoning with multiple-choice answers:
- **Quantifier sensitivity**: Mathematical statements depend heavily on quantifiers (for all, there exists, at most, exactly). Errors often stem from misreading these.
- **Strength calibration**: Does the model consistently pick options that are too strong or too weak relative to what the theorem actually proves?
- **Condition awareness**: Does the model check all hypotheses and boundary cases before committing to an answer?
- **Precise comparison**: The model must compare all options against the theorem to eliminate distractors — not just find one that "looks right."
- **Answer extraction**: Did the model correctly select and output the choice label in `<answer>X</answer>` format?
