You are an expert in authoring reusable execution skills for AI agents
that generate Python code using openpyxl.

## Task

You will receive {x} real execution trajectory summaries from the
SpreadsheetBench domain, each with an outcome label and metric scores.
Each summary describes what an agent did when generating a Python script
to transform an Excel workbook: the instruction, the code it wrote in
each turn, any error feedback, and the final cell-level evaluation result.

Analyze these trajectories to identify recurring code-generation patterns.
Then synthesize a single skill that captures these patterns as a reusable
execution guide.

## What the skill should be

- **Code-aware**: The agent writes `solution.py` using openpyxl, so
  library-level guidance is directly useful. Workflow steps can name
  candidate openpyxl methods. The skill can include a "Code Patterns"
  section with short reusable idioms (1-3 lines each) that have
  prevented failures in the observed trajectories.
- **Pattern-focused**: Look for behaviors that recur across multiple
  trajectories. A pattern seen in both successful and failed trajectories
  (where the failure diverged from the pattern) is strong evidence.
- **Concise**: Every sentence should earn its place. Remove or condense
  content that does not address observed failures.
- **Three sections**: The skill body has exactly three sections:
  1. **Workflow** — Phase-level description of how to approach the task.
     Phases can include candidate openpyxl patterns where they clarify
     the approach.
  2. **Code Patterns** — Short openpyxl idioms (1-3 lines each) that
     have prevented mistakes, with a brief explanation of what each
     addresses.
  3. **Error Avoidance** — Recurring mistakes and how to prevent them.
     Each item is 1-2 sentences. Items can reference specific API
     behaviors when those behaviors are the source of confusion.

## Task Context

The agent executing these trajectories operates under a base system prompt
provided by the task environment. Your skill is appended to it.

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

## Instruction
...

## Workbook Structure
...

## Execution Trace
### Turn 1
**Solution.py:**
...
**Feedback:**
...

## Outcome
...
```

Trajectories are separated by `---`.

## Output Format

Return exactly one JSON object with these top-level keys:

### `metadata_json` (object)

- `name` (string): A short retrieval label in gerund form, lowercase with
  hyphens. Max 64 characters.
- `description` (string): What the skill does and when to use it. Write in
  third person. Max 1024 characters.
- `when_to_use` (array of strings): Conditions a dispatcher can check from
  the user's request alone.

### `skill_body_md` (string)

A Markdown string with exactly three sections: Workflow, Code Patterns,
Error Avoidance.

Do not output extra keys. Do not output prose outside JSON. Do not wrap output
in markdown fences.

{stage0_extra_section}

## Example

{stage0_example}
