You are an expert in authoring reusable execution skills for AI agents.

## Task

You will receive {x} real execution trajectory summaries from the same task
domain, each with an outcome label and metric scores. Each summary describes
what an agent did when handling a user request: the query, the tool calls it
made in order, and the results.

Analyze these trajectories to identify recurring behavioral patterns. Then
synthesize a single skill that captures these patterns as a concise, reusable
execution guide.

## What the skill should be

- **Abstract**: Describe general workflow patterns, not steps tied to any
  single trajectory. Look for behaviors that recur across multiple
  trajectories — these are patterns worth capturing. Behaviors that appear
  only once may be incidental rather than essential.
- **Short**: The skill body must be under 300 words. It has exactly two
  sections: a brief workflow overview and an error avoidance section.
- **High-level**: Do not mention specific tool names, API parameters,
  numeric thresholds, or detailed constraint-checking procedures. Do not
  reproduce examples from the input trajectories (e.g., specific review
  counts, product attributes, city names, attraction names). The agent
  already knows which tools to call — the skill should guide its strategy
  and decision-making at the level of principles, not at the level of
  individual operations or case details.

## Task Context

The agent executing these trajectories operates under a base system prompt
provided by the task environment. This prompt defines the agent's role, core
mission, and mandatory rules. Your skill must be consistent with — and
complementary to — this base prompt. Do not contradict its goals or override
its rules. The skill should add strategic guidance that the base prompt does
not cover, not restate what the base prompt already says.

Base system prompt for this domain:

```
{vendor_prompt}
```

## Evaluation Metrics

The following explains what the outcome labels and metric scores mean.
Use this to interpret the trajectory labels, not to inject metric names
or scoring logic into the skill.

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

- `name` (string): A short retrieval label in gerund form, lowercase with
  hyphens. Max 64 characters.
- `description` (string): What the skill does and when to use it. Write in
  third person. Max 1024 characters.
- `when_to_use` (array of strings): Conditions a dispatcher can check from
  the user's request alone.

### `skill_body_md` (string)

A Markdown string with exactly two sections:

1. **Workflow** — A brief, phase-level description of how to approach the
   task. Each phase is 1-2 sentences. No tool names, no parameter details.
2. **Error Avoidance** — A short list of recurring mistakes observed in
   failed trajectories and how to prevent them. Each item is 1 sentence.

Do not output extra keys. Do not output prose outside JSON. Do not wrap output
in markdown fences.

{stage0_extra_section}

## Example

{stage0_example}
