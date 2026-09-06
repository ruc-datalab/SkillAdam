# Paper Main-Result Reproduction

## Versioned Profile

`skilladam/experiments/main_results.json` contains the versioned
`paper-main-results-v1` profile with seed `42`: reference paper scores,
models, split sizes, Stage0 case IDs, sampling, batches, iteration limits,
gates, benchmark-specific prompt profiles, context handling, and SkillOpt
parameters. Packaged prompts are indexed by digest; script defaults do not
override the frozen profile. The paper link will be added to the README.

Reference scores, training configuration, and selected-skill evidence are
separate records. A new run is compared against the reference scores, not
assumed to reproduce them.

## Reference Results

All values below are percentages:

| Benchmark | Baseline | SkillOpt | SkillAdam |
|---|---:|---:|---:|
| ALFWorld | 83.6 | 87.3 | 89.6 |
| DocVQA | 90.0 | 91.2 | 92.3 |
| SearchQA | 72.1 | 87.3 | 87.5 |
| SpreadsheetBench | 67.9 | 80.7 | 81.1 |
| OfficeQA | 65.7 | 72.1 | 72.1 |
| LMB | 67.7 | 66.9 | 67.7 |
| DeepPlanning | 15.8 | 21.7 | 28.3 |

The profile also records DeepPlanning domain results:

| Method | Shopping | Travel |
|---|---:|---:|
| Baseline | 31.7 | 0.0 |
| SkillOpt | 41.7 | 1.7 |
| SkillAdam | 45.0 | 11.7 |

`verify_main_results.py` recomputes metrics from complete test outputs.
DeepPlanning weights its four scopes by 25/25/10/60 cases, not a simple
average of scope scores.

## Splits and Scopes

| Benchmark | Train | Selection | SkillAdam optimization | Test |
|---|---:|---:|---:|---:|
| ALFWorld | 39 | 18 | 57 | 134 |
| DocVQA | 107 | 53 | 160 | 374 |
| SearchQA | 400 | 200 | 600 | 1400 |
| SpreadsheetBench | 80 | 40 | 120 | 280 |
| OfficeQA | 50 | 24 | 74 | 172 |
| LMB | 35 | 18 | 53 | 124 |

The paper protocol combines train and selection into SkillAdam's optimization
pool and evaluates its gate on the same training batch. SkillOpt keeps train
and selection separate. DeepPlanning uses:

| Scope | SkillOpt train | SkillOpt selection | SkillAdam optimization | Test |
|---|---:|---:|---:|---:|
| `shopping_level1` | 16 | 9 | 25 | 25 |
| `shopping_level2` | 16 | 9 | 25 | 25 |
| `shopping_level3` | 6 | 4 | 10 | 10 |
| `travel_en` | 40 | 20 | 60 | 60 |

Odd DeepPlanning IDs form the optimization pool; even IDs form test.

## Shared Stage0

Run Stage0 once per benchmark/scope. Pass the resulting
`shared_stage0/initial_skill.md` to both methods through `--initial-skill`.
The profile fixes case IDs, success/failure quotas, temperature, reasoning,
and output limits.

Do not generate separate initial skills for the two methods or initialize
paper training with a packaged final skill.

## SkillAdam Settings

The six non-DeepPlanning benchmarks use OpenRouter with `openai/gpt-5.5`,
`reasoning_effort=medium`, and `temperature=1`.

The paper's primary comparison records one training epoch; the iteration
settings below describe feedback updates rather than a separate epoch count.

| Benchmark | Batch | Max/min iterations | Failure stop | Sampling | Compression | Context limit | Adaptive budget |
|---|---:|---:|---:|---|---|---:|---|
| ALFWorld | 10 | 6/6 | 99 | sequential | LLM | 110000 | base 4, min 1 |
| DocVQA | 15 | 11/11 | 11 | sequential | deterministic | 110000 | base 4, min 1 |
| SearchQA | 15 | 60/40 | 10 | sequential | deterministic | 200000 | base 4, min 1 |
| SpreadsheetBench | 15 | 15/15 | 99 | seed-42 pool shuffle + sequential | LLM | 96000 | base 4, min 2 |
| OfficeQA | 10 | 15/5 | 3 | sequential | LLM | 110000 | off |
| LMB | 10 | 15/5 | 3 | sequential | deterministic | 150000 | off |

The context limit uses a conservative character-based estimate. Exceeding it
resets only the optimizer conversation, preserving the current skill,
Momentum, edit-budget state, and iteration count. ALFWorld additionally keeps
only the last two completed optimizer iterations in conversation history.

DeepPlanning uses Venus with `claude-4-5-sonnet-20250929`; Travel conversion
uses `gpt-4.1` on the same provider. Its E2 training framework includes:

- Feedback Loop V2, Momentum, and cross-iteration optimization memory.
- Adaptive Edit Budget: base 4, minimum 1, beta 0.9.
- Venus Claude prompt caching at system, message-prefix, and tools breakpoints.
- Shopping: `score`, `v_max=0.05`, up to 12 iterations.
- Travel: `composite_score`, `v_max=0.1`, up to 20 iterations.
- Scope-specific metrics, labels, compression examples, and E2 templates.
- Same-batch gate.

The four selected DeepPlanning skills and associated evaluation artifacts
are E1. E2 identifies the training framework; it does not change the selected
E1 evaluation input. Across all benchmarks, Stage0 and training use the
configuration and prompts fixed before final-skill selection. Packaged final
skills are for artifact evaluation and quick starts, not training.

## SkillOpt Settings

All scopes start from the shared Stage0 skill. General settings:

```text
epochs=4
batch_size=40
reflection_minibatch_size=8
merge_batch_size=8
max_analyst_rounds=3
analyst_workers=16
max_edit_budget=4
min_edit_budget=2
scheduler=cosine
patch_mode=true
slow_update_samples=20
meta_skill=true
strict gate=true
seed=42
```

DeepPlanning uses scope batch sizes 16/16/6/40 and slow-update sample budgets
16/16/6/20. Current/best skills, optimizer memory, candidates, and checkpoints
remain separate from SkillAdam. Resume does not replay completed batches.

Each rollout batch separates failed and successful outcomes, deterministically
shuffles each group using seed 42, and forms reflection minibatches without
mixing outcomes in one analyst request. The six non-DeepPlanning benchmarks
use 12 packaged benchmark-specific error/success analyst prompts.

## Prompt Integrity

Prompt profiles select the packaged benchmark/scope-specific templates.
All prompts needed for reproduction are included. Earlier profile names
remain accepted when loading existing run configurations.

`skilladam/experiments/prompt_resources.json` indexes 79 SkillAdam benchmark
prompts, six shared prompts, and 12 SkillOpt benchmark analyst prompts by
SHA-256. Of the 79 benchmark prompts, 75 are used in the main-result path:

- Stage0 system, example, and extra sections.
- Benchmark rollout systems where applicable.
- Iteration systems/examples and skill-writing policies.
- Metric interpretation and failure/success taxonomy.
- Trajectory labels and compression systems/examples.
- DeepPlanning's four scope-specific metrics and labels.
- SkillOpt error/success analyst prompts for six benchmarks.

`preflight_main_results.py` checks prompt identities before data/backend
validation and rejects missing, changed, or unregistered files.

## Generate the Command Matrix

Create a local configuration and fill in your external data/runtime paths:

```bash
cp configs/main_results.local.example.json configs/main_results.local.json
```

Run preflight without a provider call:

```bash
python scripts/preflight_main_results.py \
  --config configs/main_results.local.json \
  --allow-missing-secrets \
  --report <preflight-report.json>
```

The complete matrix contains ten scopes and six phases per scope: 60 commands.
To plan one benchmark:

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

By default this prints JSON without creating outputs, loading credentials, or
calling an API. To execute, review the model, data, concurrency, request
settings, cost exposure, and output location, then add
`--execute --confirm-api-costs`. Optional cumulative limits are separate from
the frozen model/benchmark settings; they are not added by default.

## Verify Results

```bash
python scripts/verify_main_results.py \
  --output-root <completed-output-root> \
  --max-delta-points <tolerance> \
  --report <verification-report.json>
```

Every test case must occur exactly once, and metric fields must match the
profile. The report separates observed values, paper references, and deltas.
Without `--max-delta-points`, differences are reported without requiring an
exact match to the paper.

Configuration, prompt, checkpoint, and output checks do not establish model
quality. Use [availability checks](../testing.md#real-api-availability-checks)
for a small end-to-end check before a full run.
