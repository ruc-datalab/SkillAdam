# SpreadsheetBench

## Adapter

The adapter joins Verified-400 metadata, 80/40/280 split IDs, and one
input/golden workbook pair per task. Workbook access is delayed so registry,
fixtures, and saved-result evaluation do not require spreadsheet packages.

## Rollout

The target request asks for one fenced Python program that reads `INPUT_PATH`
and writes `OUTPUT_PATH`. The parent process first validates the input and
golden workbooks below the explicit data root and appends a bounded workbook
preview. SkillAdam and SkillOpt use the same target-agent prompt and adapter;
their skill text and optimizer state remain separate.

The first-party
`skilladam.backends.openai_compatible:create_backend` executes generated code
through the execution mode selected in backend JSON. The checked-in custom and
paper configurations use the `local-subprocess` mode, which does
not require Docker. The optional `container` mode supports Docker or Podman
when stronger isolation is desired.

## Evaluation and Gate

Primary metric is hard all-cell correctness; auxiliary metric is per-cell pass
rate. The gate requires hard gain `0.06` or per-cell gain `0.03`, while hard
drop must stay below `0.08` and per-cell drop below `0.06`.

Grading always occurs in the parent process. The golden workbook is never
mounted into the sandbox or inserted into a model prompt. Only
`training_rollout` and `target_train_rollout` may return incorrect cell
coordinates and missing sheet names for another code attempt; expected and
predicted values remain hidden. Validation, selection, test, baseline,
evaluation, and unknown stages stop after the first successful execution.

## Stage0

Stage0 uses spreadsheet-specific metric interpretation, workflow policy, and
trajectory taxonomy. The paper profile runs it once and passes the same
`initial_skill.md` to SkillAdam and SkillOpt.

## Data Boundary

The configured source is `KAKA22/SpreadsheetBench` revision
`ab0b742b0fc95b946f212d80ac7771b5531272e4`, licensed CC-BY-SA-4.0.
Workbooks are not included. See [datasets](../datasets.md),
[licensing](../licensing.md), and [paper settings](../reproducibility/main_results.md).
IDs accept strings or non-boolean integers and normalize to strings. Grading
rounds numbers to two decimals, normalizes dates to Excel serial days, and
preserves column-major cell-range order. A candidate must reach at least one
gain threshold without reaching either maximum-drop boundary.

## Execution Boundary

The default local-subprocess mode launches the generated program with a
minimal child environment that removes provider credentials and service
addresses, applies execution and output-size bounds, and retains each attempt
below the external run output. It still has the current user's operating-system
permissions: it is not a security sandbox, so run it only on a disposable or
otherwise trusted machine.

Each code attempt is written to a new directory below the run output. Failed
and successful attempts are retained and never overwrite one another. The
portable result exposes attempt IDs but no host paths or golden values.

Container isolation is optional. It requires a separately built local image
pinned by `sha256` digest. The fixed command uses no network, a read-only root,
non-root UID/GID, dropped capabilities, `no-new-privileges`, bounded
CPU/memory/PIDs/time/output size, read-only input/code mounts, and one unique
writable output mount. Only `PATH` and allowlisted engine connection variables
cross into the engine invocation.

Container recipes and images are not bundled. If you select container mode,
supply your own immutable image with Python 3.10, `openpyxl`, and `pandas`,
and ensure it passes the runtime safety checks. See the
[backend guide](../reproducibility/openai_compatible_backend.md#spreadsheetbench-execution-modes)
for the image contract.

## Check Data and Configuration

Prepare the dataset using the [data guide](../datasets.md), then use
`--dry-run` to inspect an evaluation or training plan without provider calls.
See [validation checks](../testing.md) for a small end-to-end check.

## Real Run Plans

Use `scripts/reproduce_main_results.py` for the exact paper configuration.
The lower-level examples below are custom experiment templates and must use
one shared Stage0 output.

Install `skilladam[backend,spreadsheetbench]` and materialize the official
data. The default config below uses local-subprocess mode and needs no
container engine. To opt into container isolation, use
`configs/backends/spreadsheetbench_container.example.json`, separately build
and audit the image, and set `SKILLADAM_SPREADSHEET_SANDBOX_IMAGE` to its
immutable local digest. The commands below intentionally retain `--dry-run`;
they validate the configuration without executing model-generated code or
calling a provider.

SkillAdam dry-run:

```bash
python -m skilladam run \
  --benchmark spreadsheetbench \
  --method skilladam \
  --split train \
  --validation-split validation \
  --data-root <spreadsheetbench-data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/spreadsheetbench_openai_compatible.example.json \
  --model <model-id> \
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-dir> \
  --iterations 10 \
  --train-size 15 \
  --validation-size 15 \
  --edit-budget-metric hard \
  --edit-budget-v-max 0.05 \
  --dry-run
```

Independent SkillOpt dry-run:

```bash
python -m skilladam run \
  --benchmark spreadsheetbench \
  --method skillopt \
  --split train \
  --validation-split validation \
  --data-root <spreadsheetbench-data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/spreadsheetbench_openai_compatible.example.json \
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

The generic `module:factory` extension remains available for independently
configured runtimes, but the same golden-isolation, usage, and stage-policy
requirements still apply.
