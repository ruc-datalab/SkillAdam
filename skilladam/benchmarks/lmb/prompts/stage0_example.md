Input (two synthetic mathematical MCQ trajectories):

```
[1] outcome=success | EM=1

The agent checked every quantifier, compared all four statements, and
selected the only option with exactly the proved strength.

---

[2] outcome=failure | EM=0

The agent treated an existence result as a universal statement and chose a
stronger distractor.
```

Output:

```json
{
  "metadata_json": {
    "name": "mathematical-mcq-reasoning",
    "description": "Solves mathematical multiple-choice questions by checking logical scope and theorem strength.",
    "when_to_use": [
      "A mathematical question hinges on exact quantifiers or hypotheses",
      "Several answer choices differ only in logical or theorem strength"
    ]
  },
  "skill_body_md": "# Mathematical MCQ Reasoning\n\n## Workflow\n\n1. **Parse the claim**: Mark every quantifier, domain restriction, hypothesis, and equality condition.\n2. **Calibrate strength**: State exactly what follows, distinguishing existence, uniqueness, bounds, and equality.\n3. **Compare all options**: Test every choice against the derived statement and eliminate each incompatible distractor.\n4. **Return the label**: Recheck the selected option and emit its single label.\n\n## Error Avoidance\n\n- Never replace `there exists` with `for all`, or `at most` with `exactly`.\n- Do not ignore boundary or equality cases.\n- Do not select the first plausible choice before comparing all alternatives."
}
```
