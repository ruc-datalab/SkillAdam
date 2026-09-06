### Term Definitions

- **gold answer** (referred to as **gold** in shorthand): the canonical
  correct answer for a question, as recorded in the dataset. A question may
  have multiple acceptable gold forms — these are the strings against which
  any predicted answer is compared. In a trajectory's structured fields, the
  gold answer appears under `**Gold answer**`.
- **predicted answer** (referred to as **predicted** in shorthand): the
  string the agent actually produced as its final answer for the question.
  In a trajectory's structured fields, it appears under `**Predicted**`.

EM/F1 are computed by comparing the predicted answer to the gold answer(s)
after normalization. When a (gold, predicted) pair is referenced as
`'GOLD' not 'PREDICTED'`, the first quoted string is always the gold (the
correct answer), and the second is always the predicted (the wrong form
the agent produced).

### Metric Definitions

- **exact_match (EM)**: 1 if the predicted answer, after normalization
  (lowercase, strip articles a/an/the, strip punctuation, collapse whitespace),
  matches any of the gold answers exactly. 0 otherwise.
  This is the **primary metric** — it determines success/failure.

- **token_f1**: Token-level F1 overlap between the normalized predicted answer
  and the best-matching gold answer. Ranges from 0.0 to 1.0.
  This is an **auxiliary metric** — it indicates partial correctness when EM=0.

### Interpreting trajectory labels

- `outcome=success | EM=1 | F1=1.000` — perfect answer.
- `outcome=failure | EM=0 | F1=0.xxx` — wrong answer but partial overlap.
- `outcome=failure | EM=0 | F1=0.000` — completely wrong answer.

### What matters for the gate

The acceptance gate compares **average EM** between baseline and candidate.
A candidate skill is accepted when it improves EM by at least the threshold
(typically one additional correct answer in the batch).
