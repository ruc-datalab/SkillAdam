## Skill Writing Policy

### 1. Body Structure

The skill body has exactly three sections:

1. **Workflow** — A description of how to approach the task, organized by
   phases. Each phase describes the goal in plain language. Phases can
   include candidate openpyxl methods or patterns where they help clarify
   the approach — for example, "Read data rows, skipping the header, and
   build an intermediate structure before writing. One option is
   `iter_rows(min_row=2, values_only=True)`."
2. **Code Patterns** — Short openpyxl idioms that have prevented mistakes
   in observed trajectories. Each pattern is a brief code snippet (1-3
   lines) with a one-line explanation of what it prevents. Patterns can
   target specific API behaviors when those behaviors cause recurring
   failures across different workbooks.
3. **Error Avoidance** — A short list of rules that prevent recurring
   mistakes. Each rule is a concise sentence (under 30 words). Rules can
   reference specific API behaviors when the behavior itself is the
   source of confusion (e.g. how `ws.append()` chooses its write
   position).

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

## Code Patterns
...

## Error Avoidance
...
```

You may modify both the frontmatter metadata and the body content in your
patches.

### 3. Edit Balance: Additions vs. Removals

Every patch should be evaluated for edit balance:

- **Before adding a new rule**, check whether the skill already contains
  guidance that covers the same situation. If it does, revise the existing
  rule rather than adding a duplicate.
- **When a failure is caused by an existing rule** (the agent followed the
  rule and it led to a wrong outcome), the correct fix is to modify or
  delete that rule — not to add a new rule that contradicts it.
- **When the skill has grown verbose**, remove or generalize low-value
  content, even if no failure directly points to it.
- **Pure additions** (adding content without removing anything) should be
  the exception, not the default.

### 4. Content Boundaries

Do not include:

- Evaluator-internal terminology (scoring field names, check names,
  judgment logic, pass/fail criteria).
- Case-specific data (specific cell values, specific row numbers from a
  particular workbook, literal file paths from the trajectories).
- Benchmark bookkeeping (evaluation metric formulas, scoring thresholds).

openpyxl method names, argument patterns, and short code snippets are not
considered case-specific data — they describe library behavior that
applies across workbooks.

### 5. Generalization

- Rules should cover classes of spreadsheet operations that recur across
  workbooks, not a single observed case.
- A rule that targets a specific API behavior is acceptable when that
  behavior causes confusion across different tasks — for example, a note
  on how `iter_rows` handles empty trailing rows applies to any workbook,
  not just the one where the failure was first observed.

### 6. Code Examples

Code examples (1-3 lines of Python) can be embedded directly under any
Workflow phase, Code Pattern, or Error Avoidance item. Keep examples
grounded in the batch's observed failures and focused on illustrating a
general pattern, not replaying a specific case's data.
