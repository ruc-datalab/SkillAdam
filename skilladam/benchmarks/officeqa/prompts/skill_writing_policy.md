## Skill Writing Policy

### 1. Body Structure

The skill body has exactly two sections:

1. **Workflow** — How to approach Treasury Bulletin QA tasks, organized by
   phases. Each phase describes the goal and approach, then suggests
   candidate tools. Unlike generic extraction tasks, this domain requires
   precise table alignment, multi-step arithmetic, and format-sensitive
   output — Workflow steps should include enough domain detail to prevent
   recurring errors. For example, concrete data-structure patterns,
   computation conventions, or output format decision logic when those are
   grounded in observed failure modes.
2. **Error Avoidance** — Rules that prevent recurring mistakes. Each rule
   should be concise but complete enough to be actionable. Rules can
   reference domain-specific data patterns when those patterns are the
   source of recurring confusion.

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

- No evaluator-internal terminology.
- No case-specific cell values, file paths, or specific bulletin issue
  dates from trajectories.
- No benchmark bookkeeping.

**Permitted domain-specific content** (not considered case-specific):
- Treasury financing terminology: offered amount, tenders received,
  tenders accepted, competitive/noncompetitive, refunding, new cash
  raised — when describing role disambiguation as a general skill.
- Table structure patterns: multi-row headers, fiscal-vs-calendar sections,
  amount-vs-percent adjacent columns — when describing alignment rules.
- Statistical/financial method conventions: population vs sample std dev,
  32nds quotation format, compound growth formula — when describing
  computation discipline.
- Output format heuristics: when to include/exclude %, unit words, commas.

### 5. Generalization

Rules must apply to the general class of tasks represented by the
trajectories, not to individual cases. Extract the underlying principle
from a specific failure — do not encode a case-specific patch.

### 6. Concrete Examples for Persistent Disobedience

If prior iterations show the agent repeating a mistake despite a rule
already existing in the skill, make the rule more specific by adding a
brief inline example or tightening the condition. Do not add a second
overlapping rule — revise the existing one.
