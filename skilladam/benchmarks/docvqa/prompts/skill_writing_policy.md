## Skill Writing Policy

### 1. Body Structure

The skill body has exactly two sections:

1. **Workflow** — How to approach a DocVQA question at answer time. Because
   each rollout is a single turn (one image + one question, no tools, no
   retries), Workflow steps are not a multi-step procedure but a sequence
   of *answer-time decision rules* for inspecting the image and selecting
   the answer span. Steps may reference document-layout patterns
   (forms, tables, figures, free text, handwritten content) and
   answer-format conventions when those are grounded in observed failure
   modes.
2. **Error Avoidance** — Rules that prevent recurring mistakes. Each rule
   should be concise but complete enough to be actionable. Rules can
   reference document-layout patterns or answer-format pitfalls when those
   patterns are the source of recurring confusion.

No other top-level sections are permitted.

### 2. Skill Format

The final skill file must follow this structure:

```yaml
---
name: <short-label>
description: <one sentence>
when_to_use:
  - <condition>
---
```

Followed by a Markdown body with exactly: `## Workflow` and `## Error Avoidance`.

### 3. Edit Balance

When proposing a patch:

- Clearly state how many existing items you **revise** vs **add** vs **remove**.
- Adding new items is fine when warranted by observed failures. Removing
  items is acceptable when they conflict with new evidence.
- Every patch must leave the skill internally consistent — no contradictions
  between Workflow steps and Error Avoidance rules.

### 4. Forbidden Content

- No evaluator-internal terminology (e.g. "ANLS", "Levenshtein").
- No case-specific document content from trajectories: no specific company
  names, dollar amounts, dates, person names, or other values that appeared
  in a single trajectory's image or response.
- No benchmark bookkeeping.

**Permitted domain-specific content** (not considered case-specific):
- Document-layout patterns: forms (label/value pairs), tables (row/column
  alignment, merged headers), figures and charts (axis labels, legend,
  caption), free text paragraphs, handwritten annotations, signatures,
  stamps — when describing where to look for the answer.
- Question-type patterns: "what is the value of X" (label-to-value lookup),
  "which year/date/page" (numeric span), "yes/no" (binary), "list all"
  (enumerate from a region) — when describing how to interpret the question.
- Answer-format conventions: when to include/omit units, currency symbols,
  punctuation; when to keep original capitalization vs normalize; how to
  format numbers (decimals, thousands separators, dates).
- Visual extraction discipline: read the smallest sufficient region, prefer
  printed text over inferred values, distinguish similar-looking glyphs
  (`O` vs `0`, `l` vs `1`, `,` vs `.`) — when grounded in observed errors.

### 5. Generalization

Rules must apply to the general class of DocVQA questions represented by
the trajectories, not to individual cases. Extract the underlying principle
from a specific failure — do not encode a case-specific patch (e.g. "if the
question asks about ITC Limited, answer 1992" is forbidden).

### 6. Concrete Examples for Persistent Disobedience

If prior iterations show the agent repeating a mistake despite a rule
already existing in the skill, make the rule more specific by adding a
brief inline example or tightening the condition. Do not add a second
overlapping rule — revise the existing one.
