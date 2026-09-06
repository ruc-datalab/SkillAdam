# Unified Execution CLI

## Capabilities

The CLI provides seven benchmark adapters, baseline and selected-skill
evaluation, SkillAdam Stage0 and optimization, independent SkillOpt
optimization, checkpoint resume, and a shared usage/cost schema.

Use an explicit backend for real execution:

- `skilladam.backends.openai_compatible:create_backend`: SearchQA and LMB
  text requests, DocVQA multimodal requests, OfficeQA read-only document tools,
  ALFWorld's TextWorld subprocess loop, and SpreadsheetBench code execution.
- `skilladam.backends.deepplanning_official:create_backend`: a pinned
  external Qwen-Agent/data runtime, official tools and evaluator, and a
  per-case subprocess bridge.

SpreadsheetBench defaults to `local-subprocess`. Docker/Podman isolation is
optional and requires a user-supplied image. Grading runs in the parent process.
Local mode does not isolate generated code from the user's filesystem.

The `fixture` backend is for user-supplied synthetic cases and scripted
generation queues. It is not a model or an official benchmark runtime.
Credentials and service addresses come from explicit environment variables,
not repository files. See the [backend guide](openai_compatible_backend.md)
and [DeepPlanning guide](../benchmarks/deepplanning.md).

## Preview a Run

Add `--dry-run` to validate the adapter, data, case pool, initial skill,
backend factory, hyperparameters, and output conflicts without rollouts,
model generation, or output-directory creation.

```bash
python -m skilladam run \
  --benchmark searchqa \
  --method skilladam \
  --split train \
  --validation-split validation \
  --data-root <data-root> \
  --backend <package.module:factory> \
  --backend-config <backend-config.json> \
  --model <model-id> \
  --output-dir <new-output-dir> \
  --iterations 10 \
  --train-size 10 \
  --validation-size 10 \
  --dry-run
```

The plan records case IDs, skill/config digests, model IDs, portable backend
metadata, and hyperparameters. It excludes local data/output paths and
configuration contents.

## SkillAdam

Custom `run --method skilladam` commands use `--split train` and either
disjoint validation IDs from train or a separate `validation` split.
The frozen [paper profile](main_results.md) instead selects its configured
optimization pool and same-batch gate. DeepPlanning always requires `--scope`.

Without `--initial-skill`, the runner performs Stage0. Providing that option
skips Stage0. Key options include:

```text
--iterations
--train-size
--validation-size
--validation-split train|validation
--stage0-size
--stage0-attempts
--min-iterations
--max-consecutive-failures
--patch-attempts
--sampling-strategy random|sequential
--edit-budget-metric
--edit-budget-v-max
--edit-budget-base
--edit-budget-minimum
--edit-budget-beta
--resume
```

Adaptive Edit Budget requires both a metric and `v-max`. SkillAdam writes
`checkpoint.json`, `current_skill.md`, and `final_skill.md`, not SkillOpt
state files.

Stage0 preserves baseline results in `stage0_evaluation.json` and each
generation attempt in `stage0_attempt_NN.json`. Completed rollouts, responses,
and usage survive JSON, section, or word-limit validation failures. A valid
result also writes `stage0.json`, `stage0_manifest.json`, and
`initial_skill.md`. Word-limit repair reports the measured count and asks
for a margin below the limit.

## SkillOpt

SkillOpt requires an explicit `--initial-skill`; it never loads the packaged
SkillAdam best skill as an initializer. Train, validation, and test IDs must
be disjoint. DeepPlanning requires one scope.

```bash
python -m skilladam run \
  --benchmark searchqa \
  --method skillopt \
  --split train \
  --validation-split validation \
  --data-root <data-root> \
  --backend <package.module:factory> \
  --model <model-id> \
  --initial-skill <initial-skill.md> \
  --output-dir <new-output-dir> \
  --epochs 4 \
  --scheduler cosine \
  --max-edit-budget 4 \
  --min-edit-budget 2 \
  --gate-metric soft \
  --slow-update-mode force \
  --dry-run
```

Remove `--dry-run` only when ready to execute the configured requests.

Adapter metrics map deterministically to SkillOpt's `hard/soft` space:
ALFWorld, DocVQA, and DeepPlanning provide these fields directly; SearchQA and
LMB use exact-match/token-F1, OfficeQA uses EM/F1, and SpreadsheetBench uses
hard/per-cell pass rate. Output includes `skillopt_checkpoint.json`,
`current_skill.md`, `best_skill.md`, and `skillopt_report.json`.
The runner does not read or write SkillAdam checkpoints, Momentum, or gates.

## Resume and Overwrite Protection

A new run rejects a non-empty output directory. `--resume` applies only to
SkillAdam or SkillOpt training and requires the matching checkpoint.
Before any backend call, the runner checks its portable signature: model,
seed, case pool, scope, hyperparameters, and initial-skill digest. Mismatches
reject resume without changing the checkpoint.

Older SkillOpt checkpoints missing only the initial-skill digest can resume
if the remaining signature matches; the next atomic save migrates that field.
Other differences remain errors.

## Outputs and Usage

The CLI writes sanitized `execution_plan.json` and `run_manifest.json`.
Both methods record backend counters in `usage.jsonl`, with distinct stages:

- SkillAdam: training/validation rollout, Stage0, iteration patch, Momentum.
- SkillOpt: target/selection/slow-update/test rollout and five optimizer roles.

`cost-report` reads their shared ledger schema. Missing provider counters
remain unknown rather than becoming zero. Keep raw responses and trajectories
outside Git even when the accompanying manifests are sanitized.

## Validation Limits

Planning checks configuration and data compatibility, not model quality.
For a real availability check, start with the smallest explicit case set that
exercises provider calls, tools, scoring, checkpoint writing, and resume.
A failed task or lack of improvement does not by itself indicate broken
infrastructure. See [validation checks](../testing.md).
