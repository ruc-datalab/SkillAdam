You are an expert in authoring reusable execution skills for AI agents
that answer questions over single document images.

## Task

You will receive {x} real execution trajectory summaries from a visual
document QA task, each with an outcome label and metric scores. Each
summary describes what an agent did when answering a question about a
single document image: which question was asked, what the agent predicted,
and how the prediction relates to the gold answer.

Analyze these trajectories to identify recurring behavioral patterns.
Then synthesize a single skill that captures these patterns as a concise,
reusable execution guide.

## What the skill should be

- **Domain-aware**: The agent receives one document image plus one
  question and must produce a single-turn answer with no tools, no
  retries, and no second look at the image. Skill workflow steps describe
  *answer-time decision rules* — how to inspect the image, locate the
  relevant region, select the answer span, and format the output —
  rather than multi-step procedures.
- **Pattern-focused**: Look for behaviors that recur across multiple
  trajectories. A pattern seen in both successful and failed trajectories
  (where the failure diverged from the pattern) is strong evidence.
- **Concise but complete**: The skill body has exactly two sections:
  Workflow and Error Avoidance. Keep it tight enough to be actionable
  without padding.
- **Format-disciplined**: Many failures are not perception errors but
  format errors (extra words, wrong unit, wrong precision, missing
  `<answer>` tag). The skill should guide answer-span selection and
  output formatting as carefully as visual extraction.

## Skill Specificity Guidance

This task domain involves single-image question answering across diverse
document layouts. Domain-specific guidance is directly actionable:

- Workflow steps can describe document-layout patterns (forms with
  label/value pairs, tables with row/column alignment, figures with
  axis labels and legends, free text paragraphs, handwritten content)
  as navigation guidance for "where to look".
- Workflow steps can describe question-type patterns ("what is the value
  of X", "which year/date/page", "yes/no", "list all") to help interpret
  what the question is asking before scanning.
- Workflow steps can name specific answer-format conventions (when to
  include units, currency symbols, punctuation; when to keep original
  capitalization vs normalize; how to format dates and numbers) when
  those are the source of recurring errors.
- Error Avoidance items can reference visual-extraction pitfalls
  (similar glyphs, merged cells, multi-line entries, partial occlusion,
  handwritten ambiguity) when those cause confusion across different
  questions.

## Forbidden Content

The skill applies to the general class of DocVQA questions, not to
individual cases. The skill body must NOT contain:

- Evaluator-internal terminology (e.g. ANLS, Levenshtein, "hard"/"soft" as
  score names) — describe the underlying behavior, not the metric labels.
- Any literal value lifted from an input trajectory's image, question, or
  response: specific entity names, amounts, dates, weights, percentages,
  identifiers, or quoted phrases. These are case data, not patterns.
- Benchmark bookkeeping (trajectory numbers, response excerpts, case IDs).

When a workflow step or Error Avoidance rule needs an example, use
synthetic placeholders or generic schema phrasing instead of values
observed in the input trajectories.

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

**Q**: [question text]

**Image**: [image filename]

**Topic**: [layout tag]

**Gold**: [gold answers]

**Predicted**: [extracted answer span]

**Response Excerpt**: [first ~300 chars of assistant response]
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

1. **Workflow** — Answer-time decision rules: how to inspect the image,
   interpret the question, locate the answer region, select the answer
   span, and format the output. Each rule can reference document-layout
   patterns or answer-format conventions when grounded in observed
   trajectories.
2. **Error Avoidance** — Recurring mistakes and how to prevent them.
   Each item is 1-2 sentences and can reference layout or format pitfalls.

Do not output extra keys. Do not output prose outside JSON.

{stage0_extra_section}

## Example

{stage0_example}
