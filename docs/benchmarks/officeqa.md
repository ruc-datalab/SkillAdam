# OfficeQA

## Adapter

The adapter loads the configured 50/24/172 question split and exposes a bounded
local search/read tool protocol over a caller-allowlisted Treasury Bulletin
corpus. Validation maps to `val`.

## Rollout

The first-party OpenAI-compatible runtime supports literal glob/grep/read
operations, at most 24 tool turns, at most 8 tool calls in one turn, and at
most 96 tool calls across one case. It extracts `<answer>...</answer>` after
the tool loop. Every resolved file must remain inside the allowed corpus root;
symlink escapes are rejected.

Each successful model turn contributes one usage record per model turn to the
shared SkillAdam/SkillOpt ledger. Provider HTTP retries remain part of the
same logical call. Before persistence, local roots in assistant tool
arguments, observations, and responses become `<officeqa-docs>/` placeholders.

## Evaluation and Gate

Primary metric is exact match and auxiliary metric is token F1. The gate
requires EM gain `0.08` or F1 gain `0.05`, while disallowing EM drop `0.08`
or F1 drop `0.05` at the boundary.

## Stage0

Stage0 combines OfficeQA metric semantics, domain policy, and sampled
tool-use trajectories. The paper profile runs it once and passes the same
`initial_skill.md` to SkillAdam and SkillOpt.

## Data Boundary

`databricks/officeqa` is gated. The question CSV is CC-BY-SA-4.0, conversion
code is Apache-2.0, and U.S. Treasury Bulletin material is public domain.
SkillAdam includes none of the CSV, answers, or parsed corpus. See
[datasets](../datasets.md), [licensing](../licensing.md), and
[paper settings](../reproducibility/main_results.md).
After gated access is approved, the unified data command downloads only the
285 Treasury documents referenced by the frozen 246-case profile and their
parsed JSON into the external data root.

The real runtime derives its read-only corpus only from these locations below
the explicit `<officeqa-data-root>`:

```text
docs/transformed/
docs_official/
raw/treasury_bulletins_parsed/
```

The first existing directory is resolved without consulting the current
working directory or `OFFICEQA_DOCS_DIR`. Each real case must also provide a
non-empty `source_files` basename allowlist. A referenced parsed page may be
added as oracle context, but unrelated pages are not loaded.

## Check Data and Configuration

Prepare the dataset using the [data guide](../datasets.md), then use
`--dry-run` to inspect an evaluation or training plan without provider calls.
See [validation checks](../testing.md) for a small end-to-end check.

## Real Run Plans

Use `scripts/reproduce_main_results.py` for the exact paper configuration.
The lower-level examples below are custom experiment templates and must use
one shared Stage0 output.

The checked-in first-party factory is an explicit `module:factory` backend:

```text
skilladam.backends.openai_compatible:create_backend
```

SkillAdam dry-run:

```bash
python -m skilladam run \
  --benchmark officeqa \
  --method skilladam \
  --split train \
  --validation-split validation \
  --data-root <officeqa-data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/openai_compatible.example.json \
  --model <model-id> \
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-dir> \
  --iterations 10 \
  --train-size 10 \
  --validation-size 10 \
  --edit-budget-metric em \
  --edit-budget-v-max 0.05 \
  --dry-run
```

Independent SkillOpt dry-run:

```bash
python -m skilladam run \
  --benchmark officeqa \
  --method skillopt \
  --split train \
  --validation-split validation \
  --data-root <officeqa-data-root> \
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

These commands only construct execution plans while `--dry-run` is present.
No real API or official OfficeQA benchmark has been run by the public
repository verification.
