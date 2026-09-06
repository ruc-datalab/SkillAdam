# LMB

## Adapter

The LMB adapter reads four monthly JSON collections, normalizes choices, and
uses either explicit split IDs or a deterministic `2:1:7` fallback. The
configured split contains 35 train, 18 validation, and 124 test cases.

## Rollout

The shared target prompt requests one final answer label. The theorem
statement, proof sketch, source link, and optimizer diagnostics remain hidden
optimizer references and are never injected into target-agent rollout.

## Evaluation and Gate

Primary metric is exact match; token F1 remains available for common reporting
and SkillOpt soft-score projection. The LMB Acceptance Gate requires exact
match gain `0.08` and rejects at the maximum drop boundary `0.08`.

## Stage0

Stage0 uses the public problem/choice representation, metric semantics, and
failure/success taxonomy. The paper profile runs it once and passes the same
`initial_skill.md` to SkillAdam and SkillOpt.

## Data Boundary

The configured upstream revision is
`b72450f6ce96c26158d64d945a5d31ef7727be41`, but local evidence did not
establish a dataset redistribution license. Monthly JSON data remains
external. See [datasets](../datasets.md), [licensing](../licensing.md), and
[paper settings](../reproducibility/main_results.md).

## Check Data and Configuration

Prepare the dataset using the [data guide](../datasets.md), then use
`--dry-run` to inspect an evaluation or training plan without provider calls.
See [validation checks](../testing.md) for a small end-to-end check.

## Real Run Plans

Use `scripts/reproduce_main_results.py` for the exact paper configuration.
The lower-level examples below are custom experiment templates and must use
one shared Stage0 output.

The checked-in first-party backend is
`skilladam.backends.openai_compatible:create_backend`, configured by
`configs/backends/openai_compatible.example.json`. A custom
`module:factory` remains supported for alternate transports.

SkillAdam dry-run:

```bash
python -m skilladam run \
  --benchmark lmb \
  --method skilladam \
  --split train \
  --validation-split validation \
  --data-root <lmb-data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/openai_compatible.example.json \
  --model <model-id> \
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-dir> \
  --iterations 10 \
  --train-size 10 \
  --validation-size 10 \
  --edit-budget-metric exact_match \
  --edit-budget-v-max 0.05 \
  --dry-run
```

Independent SkillOpt dry-run:

```bash
python -m skilladam run \
  --benchmark lmb \
  --method skillopt \
  --split train \
  --validation-split validation \
  --data-root <lmb-data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/openai_compatible.example.json \
  --model <model-id> \
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-dir> \
  --epochs 4 \
  --scheduler cosine \
  --max-edit-budget 4 \
  --min-edit-budget 2 \
  --gate-metric soft \
  --slow-update-mode force \
  --dry-run
```
