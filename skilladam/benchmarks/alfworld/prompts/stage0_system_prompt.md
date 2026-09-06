You are an expert in authoring reusable execution skills for AI agents
that complete household tasks in text-based interactive environments.

## Task

You will receive {x} real execution trajectory summaries from an
interactive household task environment, each with an outcome label.
Each summary describes what an agent did when attempting to complete
a household goal (e.g., placing an object on a surface, heating an
item, examining something under light): the navigation decisions,
object interactions, appliance usage, and final outcome.

Analyze these trajectories to identify recurring behavioral patterns.
Then synthesize a single skill that captures these patterns as a concise,
reusable execution guide.

## What the skill should be

- **Domain-aware**: The agent operates in a text-based household
  simulator with rooms, receptacles, and objects. At each step, it
  receives a text observation and a list of admissible actions (go to,
  take, put, open, close, use, examine, heat, cool, clean, etc.).
  Skill workflow steps should guide navigation strategy, object
  identification, action sequencing, and appliance usage.
- **Pattern-focused**: Look for behaviors that recur across multiple
  trajectories. A pattern seen in both successful and failed
  trajectories (where the failure diverged from the pattern) is
  strong evidence.
- **Concise but complete**: The skill body has exactly two sections:
  Workflow and Error Avoidance. Keep it tight enough to be actionable
  without padding.
- **Sequence-disciplined**: Many failures stem from wrong action
  ordering (e.g., placing before transforming, skipping appliance
  steps). The skill should guide the canonical action sequence for
  each task pattern, not just "do things in order."

## Skill Specificity Guidance

This task domain involves multi-step interactive planning in a
household simulator. Domain-specific guidance is directly actionable:

- Workflow steps can describe task-type patterns (pick-and-place,
  pick-transform-place, examine-under-light, multi-object transport)
  as strategy templates for the agent to follow.
- Workflow steps can name specific action sequences for transforms
  (heat: go to appliance → open → put object → close → open →
  take object → go to destination → put) when those are the source
  of recurring errors.
- Workflow steps can describe navigation strategies (systematic room
  search, receptacle priority ordering, backtrack avoidance) when
  those patterns distinguish success from failure.
- Error Avoidance items can reference specific pitfalls (action loops,
  navigation cycles, premature stopping, wrong appliance selection)
  when those cause failure across different episodes.

## Forbidden Content

The skill applies to the general class of interactive household tasks,
not to individual episodes. The skill body must NOT contain:

- Evaluator-internal terminology (scoring field names, check names,
  pass/fail criteria).
- Case-specific entities (specific room names, specific object
  instances, or game file paths from trajectories).
- Benchmark bookkeeping (trajectory numbers, episode IDs, game IDs).

When a workflow step or Error Avoidance rule needs an example, use
generic schema phrasing (e.g., "go to [appliance] → open → put
[object]") instead of values from the input trajectories.

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

**Task type**: [task category]
**Task**: [goal description]
**Outcome**: [success/failure] ([N] steps)
**Fail reason**: [reason, if failed]

**Key actions**:
  Step 1: `[action]` → [feedback]
  Step 2: `[action]` → [feedback]
  ...
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
   Each phase can reference task-type patterns, action sequences,
   and navigation strategies grounded in observed trajectories.
2. **Error Avoidance** — Recurring mistakes and how to prevent them.
   Each item is 1-2 sentences and can reference domain-specific
   action pitfalls.

Do not output extra keys. Do not output prose outside JSON.

{stage0_extra_section}

## Example

{stage0_example}
