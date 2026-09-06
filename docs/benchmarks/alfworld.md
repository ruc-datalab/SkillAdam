# ALFWorld

## Adapter

The adapter loads either user-supplied synthetic cases or caller-managed
relative gamefile splits. The configured real split is 39 train, 18 validation,
and 134 out-of-distribution test cases.

## Rollout

Real execution requires the optional upstream `alfworld` package and game
payload below `ALFWORLD_DATA`. The public adapter constructs step-level
think/action requests, limits history to two steps, and caps an episode at 50
steps. The first-party OpenAI-compatible backend resolves each relative
gamefile before the first provider call, runs the upstream TextWorld
environment in a bounded subprocess, and always closes it. Environment
sessions are serial even when the shared CLI receives a larger `--workers`
value; each successful model turn contributes one usage record per environment step.

## Evaluation and Gate

Primary metric is hard episode success. The TextWorld runtime reports a
binary soft value equal to hard success rather than inventing a partial
progress score; average turns are also recorded. The public default is the
relaxed r3/r4 gate
`hard_gain >= 0`. The r2 reproduction profile uses
`min_hard_gain=0.05`. These profiles must not be merged silently.

## Stage0

Stage0 receives domain context, sampled trajectories, metric semantics, and
the ALFWorld skill-writing policy. The paper profile runs it once and passes
the same `initial_skill.md` to SkillAdam and SkillOpt.

## Data Boundary

Package code is an optional MIT dependency; downloaded game payloads are not
redistributed. Only the non-payload relative split profile is packaged, not
machine-specific paths or an existing vendor environment. See [datasets](../datasets.md),
[licensing](../licensing.md), and [paper settings](../reproducibility/main_results.md).
Maintainer tests use a fake client and environment subprocess, not real games
or an API. They verify the execution contract, not benchmark performance.

## Check Data and Configuration

Prepare the dataset using the [data guide](../datasets.md), then use
`--dry-run` to inspect an evaluation or training plan without provider calls.
See [validation checks](../testing.md) for a small end-to-end check.

## Real Run Plans

Use `scripts/reproduce_main_results.py` for the exact paper configuration.
The lower-level examples below are custom experiment templates and must use
one shared Stage0 output.

Install the adjacent requirements, prepare the external data root, and set
`ALFWORLD_DATA` to that root. The backend configuration contains only the
environment variable name, not its path:

```bash
python -m pip install -r skilladam/benchmarks/alfworld/requirements.txt
python scripts/prepare_benchmark_data.py \
  --benchmark alfworld \
  --destination /absolute/external/data/alfworld \
  --execute
export ALFWORLD_DATA=/absolute/external/data/alfworld
```

The first-party backend is
`skilladam.backends.openai_compatible:create_backend`, configured by
`configs/backends/alfworld_openai_compatible.example.json`. A custom
`module:factory` remains supported for alternate environments.

SkillAdam dry-run:

```bash
python -m skilladam run \
  --benchmark alfworld \
  --method skilladam \
  --split train \
  --validation-split validation \
  --data-root <alfworld-data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/alfworld_openai_compatible.example.json \
  --model <model-id> \
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-dir> \
  --iterations 10 \
  --train-size 10 \
  --validation-size 10 \
  --workers 1 \
  --edit-budget-metric hard \
  --edit-budget-v-max 0.05 \
  --dry-run
```

Independent SkillOpt dry-run:

```bash
python -m skilladam run \
  --benchmark alfworld \
  --method skillopt \
  --split train \
  --validation-split validation \
  --data-root <alfworld-data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/alfworld_openai_compatible.example.json \
  --model <model-id> \
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-dir> \
  --epochs 4 \
  --scheduler cosine \
  --max-edit-budget 4 \
  --min-edit-budget 2 \
  --gate-metric soft \
  --slow-update-mode force \
  --workers 1 \
  --dry-run
```

These are execution previews only. No real API, game environment, official split,
or historical metric is executed by `--dry-run`.
