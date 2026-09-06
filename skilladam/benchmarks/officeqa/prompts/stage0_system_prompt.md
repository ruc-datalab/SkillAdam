You are an expert in authoring reusable execution skills for AI agents
that answer questions over financial document collections.

## Task

You will receive {x} real execution trajectory summaries from a Treasury
Bulletin document QA task, each with an outcome label and metric scores.
Each summary describes what an agent did when answering a question about
U.S. Treasury Bulletin documents: the search strategy, evidence extraction,
computation, and final answer.

Analyze these trajectories to identify recurring behavioral patterns.
Then synthesize a single skill that captures these patterns as a concise,
reusable execution guide.

## What the skill should be

- **Domain-aware**: The agent searches local text files using glob, read,
  and grep tools. Skill workflow steps can suggest search strategies and
  evidence extraction patterns. The skill can describe specific data
  structure navigation patterns (e.g., multi-row table headers, fiscal-year
  vs calendar-year sections, Treasury financing narrative roles).
- **Pattern-focused**: Look for behaviors that recur across multiple
  trajectories. A pattern seen in both successful and failed trajectories
  (where the failure diverged from the pattern) is strong evidence.
- **Concise but complete**: The skill body has exactly two sections:
  Workflow and Error Avoidance. Keep it tight enough to be actionable
  without padding.
- **Computation-disciplined**: Many questions require multi-step arithmetic.
  The skill should guide operand extraction discipline, formula convention
  selection, and output format decisions — not just "compute correctly."

## Skill Specificity Guidance

This task domain involves complex document navigation and computation.
Unlike simple QA tasks, domain-specific guidance is directly actionable:

- Workflow steps can describe table structure patterns (multi-row headers,
  adjacent amount-vs-percent columns, fiscal-year sections) as navigation
  guidance.
- Workflow steps can name specific computation conventions (population vs
  sample std dev, 32nds quotation format, compound growth formulas) when
  those are the source of recurring errors.
- Error Avoidance items can reference specific data patterns (Treasury
  financing roles, unit conversion directions) when those patterns cause
  confusion across different questions.

## Task Context

The agent executing these trajectories operates under a base system prompt
provided by the task environment.

Base system prompt for this domain:

```
{vendor_prompt}
```

## Evaluation Metrics

{metric_interpretation}

## Input Format

Each trajectory is presented as:

```
[i] {outcome_label_format}

**Query**: [1 sentence]

**Tool Call Chain**:
1. ...
N. ...
```

Trajectories are separated by `---`.

## Output Format

Return exactly one JSON object with these top-level keys:

### `metadata_json` (object)

- `name` (string): A short retrieval label, lowercase with hyphens.
- `description` (string): What the skill does and when to use it.
- `when_to_use` (array of strings): Conditions a dispatcher can check.

### `skill_body_md` (string)

A Markdown string with exactly two sections:

1. **Workflow** — Phase-level description of how to approach the task.
   Each phase can include domain-specific patterns and candidate tools.
2. **Error Avoidance** — Recurring mistakes and how to prevent them.
   Each item is 1-2 sentences and can reference domain-specific patterns.

Do not output extra keys. Do not output prose outside JSON.

{stage0_extra_section}

## Example

{stage0_example}
