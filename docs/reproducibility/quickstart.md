# Research Quick Start and CLI Reference

Use this guide for benchmark experiments, not the interactive plugin defaults.
Start with the [project overview](../../README.md) for plugin installation or
the [platform guide](../../integrations/README.md) for interaction details.
Run every command below from the repository root. Keep new data, credentials,
caches, checkpoints, and experiment outputs outside the checkout.

## Installation

SkillAdam requires Python 3.10 or newer. The core package and synthetic
fixtures have no mandatory third-party runtime dependency.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Install only the benchmark you need. Each benchmark keeps a repository-local
requirements entry under its adapter directory; run these commands from the
repository root:

<!-- research-environment -->
```bash
python -m pip install -r skilladam/benchmarks/searchqa/requirements.txt
python -m skilladam check-environment --benchmark searchqa
```

Replace `searchqa` with `alfworld`, `docvqa`, `spreadsheetbench`, `officeqa`,
`lmb`, or `deepplanning`. The requirements files delegate to the canonical
extras in `pyproject.toml`, so dependency versions have one source of truth.
Advanced users may select those extras directly, for example with
`python -m pip install -e '.[backend]'`.
The environment check only verifies Python, installed distributions, imports,
and explicitly selected local tools; it does not read credentials, contact a
provider, or claim that benchmark data is ready.

The `backend` extra installs the official SDK used by the first-party
OpenAI-compatible Chat Completions boundary for the six non-DeepPlanning
benchmarks. ALFWorld additionally needs the `alfworld` extra and
caller-managed game data. SpreadsheetBench uses the local
subprocess mode by default and therefore does not require Docker; an explicit
Docker/Podman configuration remains available for users who prefer container
isolation. DeepPlanning uses its own first-party bridge around a
caller-materialized, revision-pinned Qwen-Agent checkout and official dataset.
See [Benchmark dependencies](../../docs/dependencies.md) for all seven installation
commands, external tools, and the boundary between dependency checks and data
preflight.
The CLI never chooses a provider implicitly. Every real execution backend is
loaded through an explicit `module:factory` reference and an optional JSON
configuration file. [.env.example](../../.env.example) lists placeholder variable
names only; SkillAdam does not load it automatically. See the
[OpenAI-compatible backend guide](../../docs/reproducibility/openai_compatible_backend.md)
for configuration fields and execution safety.

## Offline Quick Start

Inspect the available commands without data or API credentials:

```bash
python -m skilladam list-benchmarks --json
python -m skilladam check-environment --benchmark searchqa
```

Then use the data preparation and paper planning commands below. The
regression suite and test fixtures are maintained separately, so a fresh
checkout does not need them for these checks.
See [validation](../testing.md) for the distinction between configuration
checks and real model evaluation.

CLI discovery is also fully offline:

<!-- offline-help:start -->
```bash
python -m skilladam --help
python -m skilladam run --help
python -m skilladam evaluate --help
python -m skilladam cost-report --help
python -m skilladam download-data --help
python -m skilladam materialize-data --help
python -m skilladam check-environment --help
python -m skilladam list-benchmarks --help
python scripts/reproduce_cost_table.py --help
python scripts/reproduce_main_results.py --help
python scripts/preflight_main_results.py --help
python scripts/plan_api_training_smoke.py --help
python scripts/plan_full_api_smoke.py --help
python scripts/verify_main_results.py --help
python scripts/prepare_benchmark_data.py --help
```
<!-- offline-help:end -->

## Checked-in Best Skills

The package contains one selected SkillAdam artifact for each benchmark.
DeepPlanning keeps four scope-specific files, so the seven benchmarks contain
ten selected artifacts in total. Their provenance is recorded in
[manifest.json](../../skilladam/artifacts/skills/manifest.json).

| Benchmark | Artifact |
|---|---|
| ALFWorld | [Skill](../../skilladam/artifacts/skills/alfworld/best_skill.md) |
| DocVQA | [Skill](../../skilladam/artifacts/skills/docvqa/best_skill.md) |
| SearchQA | [Skill](../../skilladam/artifacts/skills/searchqa/best_skill.md) |
| SpreadsheetBench | [Skill](../../skilladam/artifacts/skills/spreadsheetbench/best_skill.md) |
| OfficeQA | [Skill](../../skilladam/artifacts/skills/officeqa/best_skill.md) |
| LMB | [Skill](../../skilladam/artifacts/skills/lmb/best_skill.md) |
| DeepPlanning | [Shopping L1](../../skilladam/artifacts/skills/deepplanning/shopping_level1.md) · [L2](../../skilladam/artifacts/skills/deepplanning/shopping_level2.md) · [L3](../../skilladam/artifacts/skills/deepplanning/shopping_level3.md) · [Travel EN](../../skilladam/artifacts/skills/deepplanning/travel_en.md) |

```python
from skilladam.artifacts import load_best_skill

searchqa_skill = load_best_skill("searchqa")
travel_skill = load_best_skill("deepplanning", scope="travel_en")
```

The manifest records artifact origins and historical evaluation evidence;
paper scores are recorded separately in the frozen main-result profile.
Final skills are for evaluation and quick starts, not training initializers.
Both methods start from the same newly generated Stage0 skill. DeepPlanning
uses the E2 training framework and E1 selected evaluation artifacts.
SearchQA's selected skill contains optimizer-learned answer-form examples and
must not be described as zero-shot.

## Paper Main-Result Reproduction

The frozen profile in
[main_results.json](../../skilladam/experiments/main_results.json) records the
paper table, seed 42, exact Stage0 IDs, dataset sizes, model IDs,
benchmark-specific prompt profiles, gates, sampling, iteration limits, and
method parameters.
The profile fixes the paper experiment settings. SkillAdam and SkillOpt
always consume the same Stage0 output for a benchmark and scope.

Provider and model identities are part of that immutable profile:

| Benchmarks | Provider | Main model |
|---|---|---|
| ALFWorld, DocVQA, SearchQA, SpreadsheetBench, OfficeQA, LMB | OpenRouter | `openai/gpt-5.5` |
| DeepPlanning | Venus | `claude-4-5-sonnet-20250929` |
| DeepPlanning travel conversion | Venus | `gpt-4.1` |

The six OpenRouter runs require `OPENROUTER_API_KEY` and
`OPENROUTER_BASE_URL`; DeepPlanning requires `VENUS_API_KEY` and
`VENUS_BASE_URL`. No profile silently falls back to another provider or
model.

DeepPlanning deliberately separates executable method behavior from the
published artifact: the reproducible optimizer is E2 (Momentum plus Adaptive
Edit Budget), while the four selected checked-in skills and associated result
artifacts are E1.

All training commands use the frozen configuration and benchmark-specific
prompts from before the selected final skill exists. Every prompt used by the
paper profile is packaged in the repository and indexed in
`skilladam/experiments/prompt_resources.json`; there is no hidden prompt file
needed to reproduce the command matrix. The checked-in selected skill is only
a reproducibility artifact, evaluation input, and quick-start example; it is
never a Stage0 or training input.

Create a local, ignored preflight configuration:

```bash
cp configs/main_results.local.example.json configs/main_results.local.json
```

Fill in only caller-owned data/runtime paths. Backend configurations contain
environment-variable names, never credential values. The complete structural
preflight is read-only and prints the exact 60-command matrix:

```bash
python scripts/preflight_main_results.py \
  --config configs/main_results.local.json \
  --allow-missing-secrets
```

Plan one benchmark without executing it:

```bash
python scripts/reproduce_main_results.py \
  --benchmark searchqa \
  --method all \
  --phase all \
  --data-root <materialized-searchqa-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/gpt55_searchqa_main_result.example.json \
  --output-root <new-output-root>
```

After runs finish, verify complete case coverage and compare the
observed values with the frozen paper table:

```bash
python scripts/verify_main_results.py \
  --output-root <completed-output-root> \
  --max-delta-points <tolerance>
```

Planning, preflight, and verification never call an API. Execution requires
both `--execute` and `--confirm-api-costs`. Check the settings in
[main_results.md](../../docs/reproducibility/main_results.md).

## Benchmark Guides

Prepare each benchmark's external data before running it. Use
`scripts/reproduce_main_results.py --benchmark <benchmark>` with the
provider/backend configuration in [paper settings](main_results.md) to
generate its exact training and evaluation commands.

| Benchmark | Guide | Scope |
|---|---|---|
| ALFWorld | [Setup and commands](../benchmarks/alfworld.md) | all |
| DocVQA | [Setup and commands](../benchmarks/docvqa.md) | all |
| SearchQA | [Setup and commands](../benchmarks/searchqa.md) | all |
| SpreadsheetBench | [Setup and commands](../benchmarks/spreadsheetbench.md) | all |
| OfficeQA | [Setup and commands](../benchmarks/officeqa.md) | all |
| LMB | [Setup and commands](../benchmarks/lmb.md) | all |
| DeepPlanning | [Setup and commands](../benchmarks/deepplanning.md) | `shopping_level1`, `shopping_level2`, `shopping_level3`, `travel_en` |

## SkillAdam and SkillOpt

Both methods use the same adapter, case IDs, evaluator, and usage schema.
Their optimizers and state are deliberately separate:

- one shared Stage0 run writes `initial_skill.md` and the complete raw
  rollout artifact `stage0_evaluation.json`; that exact skill file is passed
  to both training commands;
- SkillAdam writes `checkpoint.json`, `current_skill.md`, and
  `final_skill.md`. Every iteration also preserves
  `training_evaluation.json`,
  `validation_baseline_evaluation.json`, and
  `validation_candidate_evaluation.json` with the complete ordered
  trajectories, metrics, and normalized usage returned by the adapter.
- SkillOpt writes `skillopt_checkpoint.json`, `current_skill.md`,
  `best_skill.md`, and `skillopt_report.json`.
- SkillOpt requires the explicit shared Stage0 `initial_skill.md`; it never
  starts from the packaged final SkillAdam artifact.
- SkillAdam never reads SkillOpt optimizer memory or checkpoint state.

Use `scripts/reproduce_main_results.py` for paper settings. The lower-level CLI
below illustrates the shared initial-skill boundary for custom experiments:

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
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-dir>/skilladam \
  --iterations 10 \
  --train-size 10 \
  --validation-size 10 \
  --edit-budget-metric exact_match \
  --edit-budget-v-max 0.05 \
  --dry-run
```

```bash
python -m skilladam run \
  --benchmark searchqa \
  --method skillopt \
  --split train \
  --validation-split validation \
  --data-root <data-root> \
  --backend <package.module:factory> \
  --backend-config <backend-config.json> \
  --model <model-id> \
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-dir>/skillopt \
  --epochs 4 \
  --scheduler cosine \
  --max-edit-budget 4 \
  --min-edit-budget 2 \
  --gate-metric soft \
  --slow-update-mode force \
  --dry-run
```

See [architecture.md](../../docs/architecture.md),
[unified_execution_cli.md](../../docs/reproducibility/unified_execution_cli.md), and
[skillopt_baseline.md](../../docs/reproducibility/skillopt_baseline.md).

## Cost Comparison

`usage.jsonl` uses one provider-neutral schema for both methods. Inputs are
repeatable, so method runs can remain in separate output
directories:

```bash
python -m skilladam cost-report \
  --input <skilladam-usage.jsonl> \
  --input <skillopt-usage.jsonl> \
  --output <comparison.csv> \
  --pricing <pricing.json>
```

The reporter operates on your run ledgers without making provider calls.
Missing counters remain unknown rather than being converted to zero. Compare
equivalent stages and data scopes; optimizer-only logs are not complete
experiment costs. See [cost comparison](cost_comparison.md).

## Output Layout

New run/evaluate executions require an explicit output directory and refuse
to overwrite a non-empty new-run directory. A baseline/evaluation run writes:

```text
<output-dir>/
  execution_plan.json
  run_manifest.json
  results.jsonl
  metrics.json
  usage.jsonl
```

Optimization adds the method-specific checkpoint and skill files listed
above. Plans and manifests contain hashes and public backend metadata, not
credential values, backend configuration contents, or local data paths.

## Data and Licensing

### External Data Materialization

One command covers planning, pinned acquisition, conversion, and strict
adapter validation. Its default is a zero-write, zero-network plan:

<!-- research-data-plan -->
```bash
python scripts/prepare_benchmark_data.py \
  --benchmark docvqa \
  --destination /absolute/external/data/docvqa
```

After reviewing the printed source, revision, license, and destination, add
`--execute` to acquire public data. The target must be
outside this Git repository and absent or empty; downloads are staged in a
disposable sibling directory and installed only after complete validation.
Create the destination's parent directory before running the command.

```bash
python scripts/prepare_benchmark_data.py \
  --benchmark docvqa \
  --destination /absolute/external/data/docvqa \
  --execute
```

ALFWorld, DocVQA, SpreadsheetBench, and DeepPlanning use pinned public
recipes. SearchQA and LMB additionally require `--accept-terms` because the
source metadata does not establish payload redistribution rights. OfficeQA
requires both `--accept-terms` and an authorized `HF_TOKEN`; the same command
downloads the gated CSV plus only the 285 public-domain Treasury documents
referenced by the frozen 246-case profile and their parsed JSON. Full
main-result preflight verifies every text/JSON pair before API readiness can
be reported. No token value or local source path is written to result
manifests.

For the six non-DeepPlanning benchmarks, an existing authorized local source
can be prepared offline:

```bash
python scripts/prepare_benchmark_data.py \
  --benchmark <benchmark> \
  --source-root <authorized-source-root> \
  --destination <external-empty-data-root> \
  --execute
```

The lower-level local-only command remains available:

```bash
python -m skilladam materialize-data \
  --benchmark <benchmark> \
  --source-root <authorized-source-root> \
  --destination <external-empty-data-root>
```

The destination must be outside this Git repository and absent or empty.
Materialization rejects symlink/path escapes, validates exact split sizes and
dependencies, computes a content digest, and writes a path-free
`materialization_manifest.json`. `--dry-run` performs the same conversion in a
disposable sibling directory and retains no destination. Supported values are
ALFWorld, DocVQA, SearchQA, SpreadsheetBench, OfficeQA, and LMB.

DeepPlanning automatic preparation creates its external Qwen-Agent checkout,
extracts only the three shopping levels and English travel database, writes
the runtime manifest, and refuses completion unless both fixed revisions and
all 240 paper-profile cases pass strict preflight. Local DeepPlanning mode is
validation-only and never copies or rewrites an existing runtime.
For local DeepPlanning validation, set `--source-root` and `--destination`
to the same existing runtime directory, not to a new empty destination.

Read [datasets.md](../../docs/datasets.md), [licensing.md](../../docs/licensing.md),
[LICENSE](../../LICENSE), and [NOTICE](../../NOTICE) before distributing code or data.

## Real API Reproduction

ALFWorld, SearchQA, LMB, DocVQA, OfficeQA, and SpreadsheetBench can use
`skilladam.backends.openai_compatible:create_backend`. ALFWorld runs its
caller-managed relative gamefile in a bounded subprocess environment loop.
SpreadsheetBench uses the paper-profile local-subprocess mode in the checked-in
main-result configuration, so Docker is not required. Generated code has the
permissions of the current user in that mode; use a disposable or trusted
machine. An explicit Docker/Podman configuration is available when stronger
isolation is desired. In both modes the golden workbook is scored only in the
parent process and is never sent to the model.

DeepPlanning uses
`skilladam.backends.deepplanning_official:create_backend`, which validates the
paper-profile 240-case runtime and both immutable upstream revisions before
creating a provider client. See the
[DeepPlanning guide](../../docs/benchmarks/deepplanning.md) for the external layout
and dry-run command.

Every real run requires environment-owned credentials, materialized benchmark
data, an external empty output directory, and a reviewed plan. Before execution
record the provider, public model ID, method, exact split/case IDs, concurrency,
seed, optimization parameters, expected request shape, credential-loading
method, and output path. Optional cumulative request/token ceilings can be
chosen separately; none is added by default. Keep the frozen benchmark turn,
retry, and token settings unchanged when reproducing the paper.

For a low-cost availability check, inspect the one-case plans generated by:

```bash
cp configs/api_training_smoke.local.example.json \
  configs/api_training_smoke.local.json
python scripts/plan_api_training_smoke.py \
  --config configs/api_training_smoke.local.json
```

The planner narrows Stage0, SkillAdam, SkillOpt, and evaluation to explicit
cases. It has no execution mode. Run only the smallest relevant subset needed
to verify loading, provider calls, tool/runtime behavior, checkpoint writing,
restoration, and scoring. A smoke score is not evidence that a skill must solve
the case, outperform baseline, or reproduce the paper table.

Use `scripts/reproduce_main_results.py` only when intentionally reproducing
the full frozen paper configuration. Its default is plan-only; real execution
requires both `--execute` and `--confirm-api-costs` after reviewing the plan and costs.

All datasets, caches, credentials, temporary files, trajectories, checkpoints,
and API results must remain outside the repository. Real host-session records
are private, even when some fields have been redacted. Review selected history
before sending it to a provider; use synthetic examples for public demos.

No real API or official benchmark result is claimed by the offline commands
in this guide. The consolidated per-benchmark prerequisites, smoke-test
boundary, dry-run templates, and configuration fields are in the
[API availability checks](../testing.md#real-api-availability-checks).

## Repository Layout

```text
integrations/              # Four host adapters and shared workflow instructions
skilladam/
  product/                 # Task/evaluation, review, and persistent sessions
  product_cli.py           # Product CLI; MCP entry points are alongside it
  core/                    # Feedback loop, gates, Momentum, and edit budget
  benchmarks/              # Seven adapters, prompts, and requirements
  methods/                 # Research SkillAdam and independent SkillOpt
  backends/                # Fixture and real benchmark execution
  artifacts/skills/        # Ten selected skills
  experiments/             # Frozen paper settings and prompt resource index
scripts/                   # Data preparation and research reproduction
configs/                   # Configuration templates
docs/                      # Usage, testing, and research reproduction guides
```

## License

SkillAdam uses the [MIT License](../../LICENSE), with
Copyright (C) 2026 Tencent. All rights reserved.
Package metadata uses the SPDX expression `MIT`. The distribution also includes
an MIT-licensed SkillOpt adaptation; its original license and attribution remain in
[NOTICE](../../NOTICE) and [the baseline package](../../skilladam/baselines/skillopt/NOTICE). Benchmark data and external
runtimes keep their own licenses and are not redistributed here; review
[licensing.md](../../docs/licensing.md) before downloading or sharing them.
