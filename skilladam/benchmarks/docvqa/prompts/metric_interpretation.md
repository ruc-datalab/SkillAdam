## Metric Interpretation — DocVQA

Each rollout produces two metrics:

- **`hard`** (binary, 0 or 1): the primary success signal. `hard = 1` iff the
  predicted answer matches one of the gold answers under DocVQA's normalized
  near-exact match (ANLS ≥ 0.999, i.e. character-level Levenshtein distance
  is essentially zero after case-folding and whitespace collapse). This is
  what counts toward the headline accuracy number.

- **`soft`** (float in [0, 1]): the ANLS score itself, i.e.
  `1 − levenshtein(pred, gold) / max(len(pred), len(gold))`, taken as the
  maximum over all gold answers, clipped to 0 when the normalized distance
  is ≥ 0.5. `soft` rewards partial credit: a prediction that is close in
  spelling or formatting to a gold answer scores between 0 and 1.

### Reading outcomes

- `hard = 1`: the answer was correct (or essentially correct after
  normalization). No fix is needed for this case.
- `hard = 0`, `soft > 0`: the answer overlaps with the gold text but is not
  close enough (e.g. extra words, partial substring, slight numeric drift,
  unit mismatch, swapped case in a proper noun the evaluator does not fold).
  These are usually format or precision failures, not perception failures.
- `hard = 0`, `soft = 0`: the answer is unrelated to the gold (wrong field,
  wrong span, hallucinated content), or the agent did not emit
  `<answer>...</answer>` at all. These are usually grounding or extraction
  failures and are the highest-value targets for skill patches.

### What the skill should focus on

- Grounding discipline: read the right region of the image, not memory.
- Answer-span selection: when several candidate spans look plausible, pick
  the one the document supports best (label-to-value alignment, table
  row/column mapping, caption-to-figure correspondence).
- Output formatting: emit exactly one `<answer>...</answer>`; keep the span
  short and verbatim; match the gold's level of detail (number alone vs.
  number plus unit, date format, capitalization conventions of named
  entities).
- Avoid invented content: if the visible evidence is ambiguous or missing,
  prefer the most-supported visible span over a confident guess.

The agent runs a single turn with the document image and the question; it
has no tools, no search, and no second look. The skill must therefore
front-load all decision rules into the answer-time reasoning.
