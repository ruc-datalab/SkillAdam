### Metrics: Exact Match (EM) and Token F1

This benchmark uses two metrics for free-form answer evaluation:

- **EM (Exact Match)**: Binary. EM = 1 when the normalized prediction equals the normalized gold answer; EM = 0 otherwise.
- **Token F1**: Soft partial-credit metric. Splits both prediction and gold into tokens, then computes precision (overlap / predicted tokens), recall (overlap / gold tokens), and F1 = 2PR/(P+R).

Both metrics apply normalization before comparison: lowercase, strip whitespace, remove commas, strip trailing punctuation (preserving digits, ".", "-", "%"), remove unit words ("million", "millions", "billion", "billions", "dollars", "dollar", "nominal"), and collapse whitespace.

A prediction can score F1 > 0 but EM = 0 when it partially overlaps the gold answer (e.g., predicting "5.2 million" when the gold is "5.2" yields F1 > 0 but EM = 0 after unit removal).

### Interpreting Trajectory Labels

Each trajectory is labeled as:
```
outcome=success | EM=1, F1=1.00    — exact match
outcome=partial | EM=0, F1=0.XX    — partial token overlap
outcome=failure | EM=0, F1=0.00    — completely wrong
```

### Failure Type Taxonomy

Document retrieval + calculation failures fall into six categories. Use these to diagnose patterns:

| Type | Description |
|------|-------------|
| `retrieval_miss` | The agent searched the wrong file or failed to narrow to the relevant document |
| `evidence_miss` | The agent read documents but missed the decisive evidence span (e.g., skipped the correct table row or paragraph) |
| `operand_error` | The agent extracted the wrong value or confused operands (e.g., read a different year's figure, swapped numerator and denominator) |
| `calculation_error` | The agent identified the right evidence but computed the result incorrectly (arithmetic mistake, wrong formula) |
| `answer_format` | The agent reached the correct result but formatted it wrong (e.g., included extra units, wrong precision, percentage vs decimal) |
| `other` | None of the above |

### What the Iteration Agent Should Focus On

Since this is multi-turn document retrieval with tool use:
- **Retrieval discipline**: Does the agent narrow to the right file before reading? Does it use glob patterns effectively, or does it read large passages blindly?
- **Evidence targeting**: After finding the right document, does the agent locate the precise evidence span (table row, paragraph) needed for the answer?
- **Operand extraction**: When arithmetic is required, does the agent extract the correct operands from the text? Common failure: reading a different row/column or confusing similar-looking values.
- **Calculation accuracy**: Does the agent compute correctly after extraction? Watch for sign errors, unit conversions, and off-by-one in time periods.
- **Answer formatting**: Does the model output answers in the expected format? Normalization removes units and commas, but precision mismatches (e.g., "5.20" vs "5.2") or extra text can still cause EM=0.
- **Tool efficiency**: The agent has a limited turn budget (24 tool turns). Wasted turns on irrelevant files reduce the chance of finding evidence.
