# SkillOpt Baseline

## Overview

The public SkillOpt baseline is an independently stateful reproduction for
controlled comparison with SkillAdam. It shares benchmark cases, evaluator
results, and the usage/cost schema, but it does not import or reuse the
SkillAdam Feedback Loop.

The baseline retains only the method-defining behavior:

- constant, linear, cosine, and autonomous edit schedules;
- strict held-out current/best selection;
- protected epoch-level slow-update guidance;
- optimizer memory kept outside the deployed skill;
- immutable current/best state and candidate records;
- atomic checkpoint/resume;
- provider-neutral callback contracts;
- shared usage-ledger rows and deterministic sanitized reports.

## Source and attribution

The exact MIT text is retained at `skilladam/baselines/skillopt/LICENSE`, and the
adaptation scope is recorded in `skilladam/baselines/skillopt/NOTICE`.

| Public area | SkillOpt behavior | Public treatment |
|---|---|---|
| scheduler | retains the original rounding and schedule behavior | behavioral port under MIT attribution |
| strict selection gate | retains `candidate > current`, then `candidate > best` semantics | behavioral port under MIT attribution |
| slow-update markers and longitudinal categories | retains the method-defining protected-block behavior | behavioral port under MIT attribution |
| DeepPlanning analyst trajectory formatter | retains per-field clipping and the 12,000-character whole-trajectory boundary | behavioral port under MIT attribution |
| optimizer prompt roles | retains reflection/merge/select/slow-update/memory responsibilities | generic public wording plus upstream benchmark error/success analyst prompts under MIT attribution |
| contracts, runner, checkpoint, report, usage integration | no monolithic trainer code copied | original provider-neutral implementation |

## Edit scheduler

The scheduler treats the textual edit limit as an optimization learning rate.
All schedules are stateful and checkpointable:

| Mode | Behavior |
|---|---|
| `constant` | fixed maximum edit count |
| `linear` | rounded linear decay from maximum to minimum |
| `cosine` | rounded cosine decay from maximum to minimum |
| `autonomous` | no-limit sentinel `999` |

For the formal four-epoch DeepPlanning profile with maximum `4` and minimum
`2`, cosine produces:

```text
[4, 3, 2, 2]
```

Configuration rejects non-positive bounds, `max < min`, unknown modes, and
invalid or out-of-range restored steps.

## Strict current/best gate

The selection metric is projected from one shared `MetricResult`:

- `hard`: use `metrics["hard"]`;
- `soft`: use `metrics["soft"]`;
- `mixed`: `(1 - w) * hard + w * soft`, with `w` in `[0, 1]`.

The state transition is intentionally strict:

```text
candidate <= current  -> reject; preserve current and best
current < candidate <= best -> accept as current only
candidate > best -> accept as both current and new best
```

Equality never accepts. Hard and soft inputs must be finite unit scores.
SkillAdam's benchmark-specific acceptance gates are separate and are not
called by this baseline.

## Independent state and candidate evidence

Current and best are separate immutable skill versions. Each stores:

- skill text;
- selection score;
- `base_origin`, identifying the accepted normal candidate;
- `slow_update_origin`, identifying the attached guidance epoch.

Normal candidate records store action, score, origin, edit budget, final
current/best scores, slow-update action, and a SHA-256 digest. Rejected
candidate text is not installed and is not copied into the public report, but
the digest keeps different rejected candidates distinguishable.

Optimizer memory is checkpointed separately and is never inserted into the
deployed skill text.

## Protected slow update

Only the slow-update process may replace text between the exact markers:

```text
<!-- SLOW_UPDATE_START -->
<!-- SLOW_UPDATE_END -->
```

Missing markers can be injected. Orphaned, reversed, or duplicate markers are
rejected rather than silently repaired.

Adjacent-epoch case outcomes are compared on identical ordered IDs:

| Previous hard | Current hard | Category |
|---:|---:|---|
| 0 | 1 | `improved` |
| 1 | 0 | `regressed` |
| 0 | 0 | `persistent_failure` |
| 1 | 1 | `stable_success` |

Two acceptance modes remain explicit:

- `force`: replace guidance in both current and best while preserving their
  base scores and origins;
- `gated`: evaluate the guided current candidate on the held-out validation
  IDs and apply the same strict selection gate.

The DeepPlanning paper profile uses force mode
(`slow_update_gate_with_selection=false`). Therefore a best skill whose
`base_origin` is `initial_skill` may still contain guidance from a later
`slow_update_origin`; this composite provenance is intentional and is now
reported explicitly.

## Runner, checkpoint, and resume

Each epoch performs:

1. target rollouts on ordered training IDs;
2. separate failed and successful trajectories, deterministically shuffle
   each group from the configured seed, and form reflection minibatches
   without mixing outcomes;
3. route each reflection to the packaged benchmark-specific error or success analyst
   prompt where one is provided;
4. merge and select callbacks with the scheduled edit budget;
5. current and candidate rollouts on identical validation IDs;
6. strict current/best selection;
7. configured epoch-boundary slow update;
8. optimizer-memory update outside the deployed skill;
9. atomic checkpoint persistence.

The callback layer accepts and returns public contracts only. Provider clients,
raw prompts, secret loading, retry policy, and benchmark processes remain
outside the state machine.

For DeepPlanning, the optimizer-facing reflection copy reproduces SkillOpt's
bounded trajectory formatter: commands and ordinary messages are clipped to
500 characters, tool observations to 800, verification messages to 2,000, and
each formatted trajectory to 12,000 characters using deterministic head/tail
retention. This does not change the saved rollout evidence or the reflection
minibatch size. Complete raw trajectories remain in the external run
artifacts, while the analyst receives the same bounded representation used by
the SkillOpt baseline.

`skillopt_checkpoint.json` stores a versioned portable signature containing:

- method, benchmark, slice, model, and seed;
- ordered train, validation, and test case IDs;
- epoch count and complete scheduler configuration;
- gate metric and mixed weight;
- slow-update enablement, mode, start epoch, and sample budget.

Resume refuses a mismatched signature. A new run refuses to overwrite an
existing checkpoint or orphaned managed artifacts such as `best_skill.md`,
`current_skill.md`, `skillopt_report.json`, or its local usage ledger.

## Shared usage ledger and reports

SkillOpt writes the same `UsageRecord` and JSONL ledger schema used by
SkillAdam. The public runner assigns rows to:

- `target_train_rollout`;
- `optimizer_reflection`;
- `optimizer_merge`;
- `optimizer_select`;
- `selection_rollout`;
- `slow_update_rollout`;
- `slow_update_generation`;
- `optimizer_memory`;
- `test_rollout`.

Unknown provider counters remain JSON `null`; they are never converted to
zero. Reports contain scores, origins, candidate hashes, portable relative
artifact names, and sanitized configuration. They exclude skill text,
optimizer responses, raw prompts, credentials, endpoints, and local absolute
paths.

For cost comparisons, include the same stages for both methods. An
optimizer-only ledger is not a complete experiment total, and missing
provider counters cannot be reconstructed. See [cost comparison](cost_comparison.md).

## Package Layout

Retained public modules:

```text
skilladam/baselines/skillopt/
  contracts.py
  gate.py
  scheduler.py
  slow_update.py
  reflection_trace.py
  runner.py
  report.py
  prompts/{reflection,merge,select,slow_update,memory}.md
  prompts/benchmarks/<benchmark>/{analyst_error,analyst_success}.md
```

Upstream provider clients, Web UI, datasets, and generated outputs are not
bundled. Providers and benchmark environments are configured separately.

## Validation

Use a small case set to check selection, saved state, and resume before
a full run. See [validation checks](../testing.md) for execution planning
and its limits. The regression suite is maintained separately from the
distributed package.
