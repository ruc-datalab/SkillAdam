## Skill Writing Policy

Follow this as a strict writing policy for all skill edits.

### 1. Body Structure

The skill body has exactly two sections:

1. **Workflow** — A description of how to approach the task, organized by
   phases. Phases may reference tool names, workflow details, and decision
   logic relevant to the domain. Do not expand into detailed parameter
   lists or multi-branch condition trees.
2. **Error Avoidance** — A list of rules that prevent recurring mistakes.

No other top-level sections are permitted.

### 2. Skill Format

The skill is presented in YAML frontmatter + Markdown body format:

```
---
name: ...
description: ...
when_to_use:
  - ...
---

# Title

## Workflow
...

## Error Avoidance
...
```

You may modify both the frontmatter metadata and the body content in your
patches.

### 3. Length Constraint

The skill body must stay under 800 words. If a patch would push the body
over 800 words, you must offset the addition by removing or condensing
existing content of equal or greater length.

### 4. Edit Balance: Additions vs. Removals

Every patch should be evaluated for edit balance:

- **Before adding a new rule**, check whether the skill already contains
  guidance that covers the same situation. If it does, revise the existing
  rule rather than adding a duplicate.
- **When a failure is caused by an existing rule** (the agent followed the
  rule and it led to a wrong outcome), the correct fix is to modify or
  delete that rule — not to add a new rule that contradicts it.
- **When the skill is too long or too specific**, the correct fix is to
  remove or generalize low-value content, even if no failure directly
  points to it. A concise skill is more effective than a comprehensive one.
- **Pure additions** (adding content without removing anything) should be
  the exception, not the default. Most improvements to a maturing skill
  come from refinement (rewriting existing content to be more precise) or
  pruning (removing content that proved unhelpful or harmful).

### 5. Forbidden Content

- No evaluator-internal terminology (scoring field names, check names,
  judgment logic, pass/fail criteria).
- No case-specific entities (product IDs, case IDs, concrete prices,
  specific hotel/restaurant/attraction names).
- No benchmark bookkeeping (evaluation metric formulas, scoring thresholds).
- Tool names from the task environment are permitted in both Workflow and
  Error Avoidance when they make guidance more actionable. Use them to
  suggest candidate operations for a workflow step, not to prescribe a
  fixed sequence. Do not list tool parameters, argument formats, or
  response field names.

### 6. Generalization

- Every rule must apply to the general class of tasks in this domain, not
  to a single observed case.
- If a rule would only help with one specific trajectory, it is too narrow
  and should not be added.
