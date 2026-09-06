# DocVQA

## Adapter

The adapter supports user-supplied text-only synthetic cases and a validated
materialized image layout. The configured 107/53/374 split is a project-specific
partition of a ten-percent sample from upstream DocVQA validation. The 374
cases are a project-specific held-out split, not the official test set.

## Rollout

The target request contains the common Skill block, one question, the answer
tag instruction, and one inline document image. SkillAdam and SkillOpt share
this target prompt; their optimizer state remains independent. Base64 image
data is removed from saved trajectory evidence.

## Evaluation and Gate

Primary metric is hard exact answer match; soft metric is ANLS. The public
paper profile uses hard/soft minimum gains `0.01/0.01` and maximum drops
`0.05/0.05`. Lower-level CLI examples are custom plans; use
`scripts/reproduce_main_results.py` for these frozen values.

## Stage0

Stage0 analyzes sampled document trajectories and writes a reusable workflow
plus error-avoidance section. The paper profile runs it once and passes the
same `initial_skill.md` to SkillAdam and SkillOpt.

## Data Boundary

The pinned source is `lmms-lab/DocVQA` revision
`539088ef8a8ada01ac8e2e6d4e372586748a265e`, config `DocVQA`, upstream
validation split. Images and split files remain external. See
[datasets](../datasets.md), [licensing](../licensing.md), and
[paper settings](../reproducibility/main_results.md).
The shared rollout system prompt retains SkillOpt's MIT attribution. Historical
usage records may be incomplete; missing counters are unknown, not zero.

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
  --benchmark docvqa \
  --method skilladam \
  --split train \
  --validation-split validation \
  --data-root <docvqa-data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/openai_compatible.example.json \
  --model <model-id> \
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-dir> \
  --iterations 10 \
  --train-size 10 \
  --validation-size 10 \
  --edit-budget-metric hard \
  --edit-budget-v-max 0.05 \
  --dry-run
```

Independent SkillOpt dry-run:

```bash
python -m skilladam run \
  --benchmark docvqa \
  --method skillopt \
  --split train \
  --validation-split validation \
  --data-root <docvqa-data-root> \
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
