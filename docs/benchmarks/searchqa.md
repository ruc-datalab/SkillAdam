# SearchQA

## Adapter

The adapter accepts a normalized public fixture or one caller-materialized
`searchqa_manifest.json` containing explicit train, validation, and test
records. It does not import Hugging Face datasets or perform network access.

## Rollout

SkillAdam uses the verbose search-answer prompt profile; SkillOpt
uses the aligned terse profile. Both extract the last complete
`<answer>...</answer>` block, with the final non-empty line as fallback.
Verbatim question/answer pairs are never an allowed public training option.

## Evaluation and Gate

Primary metric is normalized exact match and auxiliary metric is token F1.
The paper gate requires EM gain `0.04` or F1 gain `0.025`, while disallowing
EM drop `0.04` or F1 drop `0.05` at the boundary. Lower-level CLI examples
are custom plans; use `scripts/reproduce_main_results.py` for these frozen
values.

## Stage0

Stage0 receives SearchQA metric semantics, failure/success taxonomy, and
sampled trajectories. The paper profile runs it once and passes the same
`initial_skill.md` to SkillAdam and SkillOpt.

## Data Boundary

The configured source is `lucadiliello/searchqa`, but it does not declare clear
data redistribution terms. The BSD-3-Clause license of the historical code
repository does not automatically cover Jeopardy! content. Raw question,
context, and answer rows are not distributed; the split profile contains IDs
only. The selected skill contains optimizer-learned answer-form examples and
must not be described as zero-shot. See [datasets](../datasets.md),
[licensing](../licensing.md), and [paper settings](../reproducibility/main_results.md).

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
  --benchmark searchqa \
  --method skilladam \
  --split train \
  --validation-split validation \
  --data-root <searchqa-data-root> \
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
  --benchmark searchqa \
  --method skillopt \
  --split train \
  --validation-split validation \
  --data-root <searchqa-data-root> \
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
