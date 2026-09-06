# OpenAI-compatible Backend

This document describes the optional first-party Chat Completions boundary.
It is an execution transport, not a provider selection policy: callers choose
the model, service address, credentials, reasoning mapping, and token-limit
field explicitly.

The dry-run examples validate configuration without evaluating model quality.

## Supported Benchmarks

The backend supports ALFWorld, SearchQA, LMB, DocVQA, OfficeQA, and
SpreadsheetBench for baseline, SkillAdam, and SkillOpt rollout. Its
`generate()` path also serves the SkillAdam and SkillOpt optimizer requests
used with those benchmarks.

DeepPlanning is rejected before client creation because it requires the
official external tool chain and evaluator. Use the dedicated first-party
`skilladam.backends.deepplanning_official:create_backend`, which composes this
backend's optimizer-generation transport with a pinned one-case subprocess
bridge. See the [DeepPlanning guide](../benchmarks/deepplanning.md).

ALFWorld uses a separate optional runtime module around the upstream MIT
package. Install the environment and game data separately.

OfficeQA is deliberately narrower than a general agent sandbox. It can only
invoke the checked-in read-only `glob`, `read`, and `grep` tools over
case-allowlisted local Treasury Bulletin files. It cannot write files, execute
code, launch a process, or access a path outside the derived corpus root.

## Installation and Configuration

Install the optional official SDK without adding it to the dependency-free
core:

```bash
python -m pip install -e '.[backend]'
```

The public factory is:

```text
skilladam.backends.openai_compatible:create_backend
```

Use
`configs/backends/openai_compatible.example.json` as the strict allowlisted
configuration. It contains environment variable names, not their values.
Set the required values in the calling process:

```bash
export OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>
export OPENAI_BASE_URL=<YOUR_OPENAI_BASE_URL>
```

SkillAdam does not load `.env.example` or any other environment file.
The backend JSON rejects literal credentials, service addresses, headers,
authorization values, arbitrary request bodies, and unknown fields. A dry-run
constructs and validates the client but does not call Chat Completions and
does not create the requested output directory.

For ALFWorld, use
`configs/backends/alfworld_openai_compatible.example.json`. It extends the
same eight fields with exactly three non-secret runtime settings:

- `alfworld_data_env`, defaulting to the environment-variable name
  `ALFWORLD_DATA`;
- `alfworld_startup_timeout_seconds`;
- `alfworld_step_timeout_seconds`.

The environment-variable value is never copied into the config, plan,
manifest, trajectory, or usage ledger.

The dedicated DeepPlanning main-result configuration enables the
Venus Claude cache contract. Cache activation requires an explicit stable
session prefix. Requests add `cache_control` only after the configured token
threshold to the system message, latest accumulated message prefix, and final
tool definition, while the session identifier is sent only as a provider
header. The travel conversion request explicitly disables this cache path.

For SpreadsheetBench, install both optional extras and use
`configs/backends/spreadsheetbench_openai_compatible.example.json`:

```bash
python -m pip install -e '.[backend,spreadsheetbench]'
```

The specialized config adds these non-secret runtime settings:

- `spreadsheet_execution_mode`: `local-subprocess` or `container`;
- `spreadsheet_exec_timeout_seconds`;
- `spreadsheet_task_timeout_seconds`;
- `spreadsheet_max_turns`, bounded to 30.

`local-subprocess` is the paper-profile default. It needs no
Docker or Podman, but generated code has the current user's operating-system
permissions; use a disposable or trusted machine.

The optional `configs/backends/spreadsheetbench_container.example.json` adds
an engine, image environment-variable name, and security profile. Set the
image reference outside Git. It must be either a raw `sha256:<64-hex>` image
ID or a local name pinned as `name@sha256:<64-hex>`. The runtime uses
`--pull never`.

```bash
export SKILLADAM_SPREADSHEET_SANDBOX_IMAGE=<LOCAL_IMAGE@sha256:64_HEX_DIGEST>
```

A SpreadsheetBench dry-run validates config, credentials, dataset selection,
and output conflicts. In container mode it also validates image-reference
syntax. It constructs no output, executes no generated code, does not start a
container engine, and makes no provider call. Removing `--dry-run` first
performs the selected runtime and workbook preflight before the first provider
request.

In optional container mode, `engine-security-opt` is the portable default. It
requires the container
engine to accept both `--security-opt no-new-privileges` and a bounded `/tmp`
tmpfs. The alternative `setpriv-wrapper` profile must be selected explicitly;
the immutable image must contain the trusted `setpriv` executable, and
preflight must prove the resulting process has `NoNewPrivs=1`, zero effective
capabilities, UID/GID 65534, a read-only root filesystem, and no interface
other than loopback. Failure of any assertion blocks container-mode execution
before a provider client is created; it never changes modes automatically.

## Dry-run Plans

Prepare the benchmark's dataset first, then use the following commands to
validate case selection and configuration. They do not call a model.

<!-- compatible-dry-run:alfworld -->
```bash
python -m skilladam evaluate \
  --benchmark alfworld \
  --method baseline \
  --split test \
  --data-root <data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/alfworld_openai_compatible.example.json \
  --model <model-id> \
  --reasoning-effort none \
  --output-dir <new-output-dir>/alfworld \
  --dry-run
```

<!-- compatible-dry-run:searchqa -->
```bash
python -m skilladam evaluate \
  --benchmark searchqa \
  --method baseline \
  --split test \
  --data-root <data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/openai_compatible.example.json \
  --model <model-id> \
  --reasoning-effort none \
  --output-dir <new-output-dir>/searchqa \
  --dry-run
```

<!-- compatible-dry-run:lmb -->
```bash
python -m skilladam evaluate \
  --benchmark lmb \
  --method baseline \
  --split test \
  --data-root <data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/openai_compatible.example.json \
  --model <model-id> \
  --reasoning-effort none \
  --output-dir <new-output-dir>/lmb \
  --dry-run
```

<!-- compatible-dry-run:docvqa -->
```bash
python -m skilladam evaluate \
  --benchmark docvqa \
  --method baseline \
  --split test \
  --data-root <data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/openai_compatible.example.json \
  --model <model-id> \
  --reasoning-effort none \
  --output-dir <new-output-dir>/docvqa \
  --dry-run
```

<!-- compatible-dry-run:officeqa -->
```bash
python -m skilladam evaluate \
  --benchmark officeqa \
  --method baseline \
  --split test \
  --data-root <data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/openai_compatible.example.json \
  --model <model-id> \
  --reasoning-effort none \
  --output-dir <new-output-dir>/officeqa \
  --dry-run
```

<!-- compatible-dry-run:spreadsheetbench -->
```bash
python -m skilladam evaluate \
  --benchmark spreadsheetbench \
  --method baseline \
  --split test \
  --data-root <data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/spreadsheetbench_openai_compatible.example.json \
  --model <model-id> \
  --reasoning-effort none \
  --output-dir <new-output-dir>/spreadsheetbench \
  --dry-run
```

Replace placeholders with your prepared data and configuration. Check the
case IDs, output location, and costs before removing `--dry-run`: that change
executes requests and, where applicable, model-generated code.

### ALFWorld environment

A real ALFWorld case must contain a relative POSIX gamefile from the configured
path split. Before the first provider call, the runtime canonicalizes both
the `ALFWORLD_DATA` root and gamefile, then rejects missing files, absolute
paths, traversal, and symlinks that resolve outside the root.

The optional upstream environment starts in a non-daemon subprocess so
TextWorld may create its own asynchronous worker. Startup, reset, step, and
close are bounded; failures expose only a safe stage and exception type.
Every exit path closes or terminates the environment worker. The episode is
capped at 50 environment steps and uses two-step prompt history. A successful
provider response without an `<action>` tag uses the `look`
fallback; a failed provider request still fails the whole semantic batch.

TextWorld supplies binary completion, so `soft` equals `hard` rather than
claiming an unavailable partial-progress score. The backend keeps one usage
record per environment step and persists only the relative gamefile plus
portable conversation fields. ALFWorld environment sessions execute serially
even if a larger shared `--workers` value is requested; optimizer generation
continues through the normal transport.

### OfficeQA local corpus

A real OfficeQA case requires a non-empty `source_files` basename allowlist
and one of these directories below the explicit data root:

```text
docs/transformed/
docs_official/
raw/treasury_bulletins_parsed/
```

The runtime does not use an ambient working-directory corpus or
`OFFICEQA_DOCS_DIR`. Before the first provider call it resolves the corpus and
may add only the parsed page explicitly referenced by a case source URL.

One case permits at most 24 tool turns, 8 tool calls in one response, and
96 tool calls in total. A final answer may follow the last allowed tool turn.
Paths remain available inside the live provider conversation, but persisted
tool arguments, observations, and responses replace the local root with
`<officeqa-docs>/`. Outside paths and symlink escapes remain unreadable.

### SpreadsheetBench execution modes

Generated Python is untrusted. The paper-profile
configuration selects `local-subprocess`, which runs isolated Python with a
small allowlisted environment (`PATH`, locale, and timezone), bounded time and
output size, and one retained directory per attempt. Provider credentials,
and service addresses are removed from the child environment, and the golden
workbook is not passed as an input. This is not a security sandbox: generated
code still has the current user's filesystem permissions.

The optional container mode accepts Docker or Podman and invokes a fixed
command with:

- an immutable image digest and `--pull never`;
- no network, a read-only root, dropped capabilities, and
  `no-new-privileges`;
- non-root UID/GID plus CPU, memory, PID, execution-time, task-time, and
  output-size limits;
- read-only input and solution mounts and one unique writable output mount;
- an engine environment containing only `PATH` and allowlisted Docker/Podman
  connection variables, never provider credentials, model-service addresses,
  data-root variables, or other host environment values.

The default container profile supplies a bounded `/tmp` tmpfs through the engine. The
`setpriv-wrapper` profile instead sets `TMPDIR=/output/tmp` inside the unique
per-attempt output mount, disables common numerical-library worker pools, and
starts isolated Python through `setpriv --no-new-privs`. The same fixed image,
network/read-only/capability/user/resource limits, mount allowlist, and parent
process scoring contract apply to both container profiles. The runtime never
falls back between local and container modes.

The parent process canonicalizes the input and golden `.xlsx` files below the
explicit data root before any provider request. It copies only the input into
each retained attempt directory. The golden workbook, repository, data root,
home directory, environment files, and container-engine socket are never
mounted. Output must be a non-symlink regular `output.xlsx` no larger than
64 MiB.

Each model turn contributes one `UsageRecord`. Syntax/runtime/timeout/output
errors may request corrected code in every stage, up to the configured turn
limit. Only SkillAdam `training_rollout` and SkillOpt
`target_train_rollout` may continue after a successfully generated workbook
fails grading; their feedback contains incorrect cell coordinates and missing
sheet names only. Golden and predicted values are hidden.
`stage0_baseline_rollout`, `validation_rollout`, `selection_rollout`,
`slow_update_rollout`, `test_rollout`, `baseline_rollout`,
`evaluation_rollout`, and unknown future stages stop after the first
successful execution and score in the parent.

Every attempt receives a new directory below the run output and is retained;
the runtime never overwrites or deletes an earlier workbook/code attempt. The
portable result records only attempt IDs, code, safe messages, execution
status, counts, metrics, and usage—not host paths or golden values.

For optional container mode, supply an independently built local image;
image recipes and images are not part of this distribution. It must provide
Python 3.10, `openpyxl`, and `pandas`, and support execution as UID/GID
65534 with the mounts and restrictions above. The `setpriv-wrapper` profile
also requires `setpriv`. Preflight checks the runtime environment and fails
closed if requirements are unmet.

Set `SKILLADAM_SPREADSHEET_SANDBOX_IMAGE` to the verified local image digest.
The runtime does not build images, pull missing images, or fall back to local
execution. Use the default local mode only on an appropriately trusted host.

## SkillAdam and SkillOpt

Both optimizers use the same backend and normalized usage schema, but their
state machines remain independent.

SkillAdam may start from Stage0 or an explicit initial skill:

```bash
python -m skilladam run \
  --benchmark searchqa \
  --method skilladam \
  --split train \
  --validation-split validation \
  --data-root <data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/openai_compatible.example.json \
  --model <model-id> \
  --output-dir <new-output-dir>/skilladam \
  --iterations 10 \
  --train-size 10 \
  --validation-size 10 \
  --dry-run
```

SkillOpt requires its own explicit initial skill and writes only SkillOpt
state:

```bash
python -m skilladam run \
  --benchmark searchqa \
  --method skillopt \
  --split train \
  --validation-split validation \
  --data-root <data-root> \
  --backend skilladam.backends.openai_compatible:create_backend \
  --backend-config configs/backends/openai_compatible.example.json \
  --model <model-id> \
  --initial-skill <shared-stage0-output>/initial_skill.md \
  --output-dir <new-output-dir>/skillopt \
  --epochs 4 \
  --scheduler cosine \
  --dry-run
```

The backend does not share checkpoints, current/best skill state, Momentum,
or optimizer memory between methods.

## Reasoning and Token Limits

`reasoning_mode` is explicit and never inferred from a provider or model
name:

- `top_level` sends `reasoning_effort` as a top-level request field;
- `extra_body` sends it in the SDK extension body;
- `omit` sends no reasoning field.

An effort of `none` always omits reasoning. When `top_level` or `extra_body`
sends reasoning, `temperature` must be exactly `1`; invalid combinations fail
before a request.

`token_limit_field` chooses either `max_completion_tokens` or `max_tokens`.
`max_completion_tokens` in the configuration supplies the integer value for
the selected field. There is no model-name guessing or automatic fallback.

DocVQA converts the selected local image to an in-memory data URI for the
request. The raw value is never written by the CLI: the adapter replaces it
with `[image: <basename>]` before trajectory serialization.

## Usage, Retries, and Batch Failure

Every accepted response contributes one provider-neutral `UsageRecord` and
one logical request. OfficeQA and SpreadsheetBench each keep one usage record per model turn.
ALFWorld writes one usage record per environment step.
SDK-level HTTP retries do not create additional logical rows because they
belong to that same model operation.

Input, cached-input, cache-creation, output, reasoning, and total counters are
copied when supplied. Missing counters remain `null`; they are not invented as
zero. This preserves the shared SkillAdam/SkillOpt ledger contract.

Batch concurrency is bounded by `--workers`, except ALFWorld environment
sessions, which are deliberately serial; returned cases are restored to
request order. If one request fails after retries, the semantic batch fails,
pending work is cancelled where possible, and no partial benchmark score
enters a gate. A provider may omit trustworthy usage for a failed call, so
its missing token cost cannot be reconstructed.

## Before Real Execution

Before removing `--dry-run`, review and record:

- service/provider identity and public model ID;
- benchmark, method, split, scope, and exact case IDs;
- data source, license, and materialized root;
- concurrency, seed, reasoning mode/effort, temperature, and token limit;
- optimizer iterations/epochs, sampling sizes, and gate configuration;
- projected logical request shape and cost exposure, plus any cumulative hard
  limit you explicitly choose;
- environment-only credential loading method;
- for SpreadsheetBench, the explicit execution mode and trusted-host boundary;
  when container mode is selected, also the selected engine, verified local
  image digest, resource limits, and image-build provenance;
- a new, empty output directory.

Do not paste credential or service-address values into a command, config,
report, issue, or Git history. See the
[unified CLI contract](unified_execution_cli.md) and
[dataset boundaries](../datasets.md) for data and execution requirements.
