## Format Note: Concrete (gold, predicted) example pairs (this benchmark)

The `skill_body_md` output may **optionally** attach concrete `(gold,
predicted)` example pairs as sub-bullets directly under Error Avoidance
rules. (See "Term Definitions" in the Evaluation Metrics section above for
what gold and predicted mean.)

### Format

Each pair is a sub-bullet of the form:

    - 'GOLD' not 'PREDICTED'

The first quoted string is always the verbatim gold answer; the second is
always the verbatim predicted answer that motivated the rule. Do not invert
this order, and do not rephrase or paraphrase either string.

### When to use

Judged per-rule, not across trajectories: for each Error Avoidance rule you
intend to write, scan the failure trajectories you received. If at least
one trajectory contains a `(gold, predicted)` mismatch that directly
exemplifies the rule, you may attach 1–2 such pairs as sub-bullets to that
rule. Pull the strings verbatim from the trajectory's `**Gold answer**`
and `**Predicted**` fields. If a rule has no clear single-trajectory
example, omit the sub-bullets for that rule — do not force inclusion.

This is the only place verbatim trajectory entity names are permitted in
the skill body.
