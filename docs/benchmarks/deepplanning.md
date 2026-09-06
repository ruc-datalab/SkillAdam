# DeepPlanning

## Adapter

DeepPlanning preserves four separate scopes:

| Scope | Train | Validation | Test |
|---|---:|---:|---:|
| `shopping_level1` | 16 | 9 | 25 |
| `shopping_level2` | 16 | 9 | 25 |
| `shopping_level3` | 6 | 4 | 10 |
| `travel_en` | 40 | 20 | 60 |
| Total | 78 | 42 | 120 |

The reproducible SkillAdam optimizer is E2: Momentum, optimization memory, and
Adaptive Edit Budget. Its formal Sonnet 4.5 path also enables prompt cache
breakpoints for the stable system prompt, accumulated message prefix, and
tool schema. The selected four-scope skill and result artifacts are E1.
SkillOpt remains an independently stateful pipeline.

## Rollout

The public adapter creates provider-neutral shopping or travel bootstrap
requests. The first-party
`skilladam.backends.deepplanning_official:create_backend` runs the pinned
official tools and evaluators one case per subprocess. Shopping databases are
copied into the new output tree before mutation; travel includes the official
model-backed conversion step. Case metadata omits local source paths. The travel conversion contract is independently frozen to
`gpt-4.1`, `max_tokens=10240`, temperature 0, and at most 30 parse attempts;
it does not inherit Sonnet reasoning or prompt-cache fields.

## Evaluation and Gate

Both methods consume the same hard/soft result contract. Shopping additionally
tracks match rate; its strict gate uses minimum gains `0.15/0.05` and maximum
drops `0.15/0.05` for case score/match rate. Travel uses commonsense and
personalized score gains `0.03/0.08` with maximum drops `0.05/0.08`.

## Stage0

Stage0 is scope-aware and must never combine shopping levels or travel into
one universal skill. Each scope runs Stage0 once and passes that exact
`initial_skill.md` to both SkillAdam and SkillOpt. The four checked-in best
skills remain separate and are evaluation/quick-start artifacts, not training
initializers.

## Data Boundary

Use the pinned sources described in [datasets](../datasets.md) and verify
their [license terms](../licensing.md). An arbitrary local snapshot without
verifiable code/data revisions is not a supported paper runtime.

- Qwen-Agent: `31a4d36d123688581a9e9744427272b33ce940e0`.
- Qwen/DeepPlanning data: `213876cce679f993a476d01042e13d111c0e3648`.
- Case profile: `skilladam-paper-240-v1` (240 cases across four scopes).

## Runtime Preparation

Install the optional dependencies:

```bash
python -m pip install -e '.[deepplanning]'
```

Materialize the pinned Qwen-Agent checkout and official dataset outside this
repository. The runtime root must contain
`deepplanning_runtime_manifest.json`; start from
`configs/deepplanning_runtime_manifest.example.json` without placing absolute
paths in that file. The four query files and case-database roots must match
the example layout exactly. The Qwen-Agent checkout must remain at the pinned
Git HEAD because the reference backend config enables
`require_git_revision`.

Set only caller-owned paths in the process environment:

```bash
export SKILLADAM_DEEPPLANNING_RUNTIME=<ABSOLUTE_DEEPPLANNING_RUNTIME_ROOT>
export SKILLADAM_DEEPPLANNING_PYTHON=<ABSOLUTE_DEEPPLANNING_PYTHON>
```

The adapter and backend independently perform a read-only preflight. It
checks the two revisions, required official files, all 240 canonical query
IDs and databases, path containment, symlinks, four slices, and bridge
protocol before a provider client is created. The current upstream full
English/Chinese profile is not silently substituted for the paper profile.
For a custom pinned-runtime run, start from
`configs/backends/deepplanning_official.example.json`. The paper profile uses
the stricter `configs/backends/deepplanning_sonnet45_main_result.example.json`
instead.

## Check Data and Configuration

Prepare the dataset using the [data guide](../datasets.md), then use
`--dry-run` to inspect an evaluation or training plan without provider calls.
See [validation checks](../testing.md) for a small end-to-end check.

## Real Run Plans

The backend reference must use the checked-in
`skilladam.backends.deepplanning_official:create_backend` factory, expressed
through the standard `module:factory` CLI boundary. A no-write SkillAdam
configuration check for one scope is:

```bash
python -m skilladam run \
  --benchmark deepplanning \
  --scope travel_en \
  --method skilladam \
  --split train \
  --data-root <deepplanning-runtime-root> \
  --backend skilladam.backends.deepplanning_official:create_backend \
  --backend-config configs/backends/deepplanning_official.example.json \
  --model claude-4-5-sonnet-20250929 \
  --output-dir <new-output-root>/skilladam \
  --dry-run
```

SkillOpt uses the exact same Stage0 file, but its own checkpoint and optimizer
state:

```bash
python -m skilladam run \
  --benchmark deepplanning \
  --scope travel_en \
  --method skillopt \
  --split train \
  --validation-split validation \
  --data-root <deepplanning-runtime-root> \
  --backend skilladam.backends.deepplanning_official:create_backend \
  --backend-config configs/backends/deepplanning_official.example.json \
  --model claude-4-5-sonnet-20250929 \
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-root>/skillopt \
  --dry-run
```

### Paper main-result plan

Generate the exact shared-Stage0, E2 SkillAdam, SkillOpt, and three evaluation
commands for one scope:

```bash
python scripts/reproduce_main_results.py \
  --benchmark deepplanning \
  --scope travel_en \
  --method all \
  --phase all \
  --data-root <deepplanning-runtime-root> \
  --backend skilladam.backends.deepplanning_official:create_backend \
  --backend-config configs/backends/deepplanning_sonnet45_main_result.example.json \
  --output-root <new-output-root>
```

Omit `--scope` to generate all four scope plans. The command is offline by
default. A real paper-profile run also requires environment-owned `VENUS_API_KEY`,
`VENUS_BASE_URL`, `SKILLADAM_DEEPPLANNING_RUNTIME`, and
`SKILLADAM_DEEPPLANNING_PYTHON` values. Every agent request, failed retry, and
travel conversion request contributes to the shared usage ledger; unavailable
provider counters remain `null`. No real API or official split is executed
by the default planning command. Review the
[API availability checks](../testing.md#real-api-availability-checks) before
adding `--execute --confirm-api-costs`.
