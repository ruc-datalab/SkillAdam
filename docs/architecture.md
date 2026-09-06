# SkillAdam Architecture

## Scope

The public package separates benchmark semantics, method state, execution
backends, and accounting. Its core has no provider dependency; one optional
first-party OpenAI-compatible backend lazily imports the official SDK. The
package contains no secret loader, raw dataset, or benchmark vendor runtime.

```text
CLI
  -> benchmark registry and adapter
  -> explicit execution backend
  -> SkillAdam | SkillOpt | baseline runner
  -> evaluator and method-specific gate
  -> method checkpoint + shared usage ledger
  -> deterministic cost report
```

## Benchmark Layer

Each of the seven adapters provides the same public contract:

- manifest and deterministic split loading;
- rollout-request construction;
- result parsing and trajectory sanitation;
- evaluation;
- benchmark-specific Acceptance Gate;
- Stage0 prompt context and taxonomy resources.

Optional benchmark packages are imported only at their real execution
boundary. Synthetic fixtures, registry discovery, saved-result parsing, and
documentation tests remain dependency-free.

## SkillAdam Feedback Loop V2

Feedback Loop V2 orchestrates:

1. a deterministic Case Sampler chooses patch-generation and gate cases
   according to the selected protocol;
2. rollout callbacks collect baseline/current trajectories;
3. the Trajectory Condenser renders provider-neutral evidence;
4. the patch callback proposes a bounded unified diff;
5. strict diff application builds a candidate skill;
6. the benchmark Acceptance Gate compares baseline and candidate metrics;
7. Momentum records accepted, rejected, resolved, and reopened problems;
8. Adaptive Edit Budget updates its EMA from per-case validation deltas;
9. an atomic checkpoint persists the complete portable state.

Stage0 initializes a skill from sampled trajectories when no explicit initial
skill is supplied. Early stopping cannot occur before the configured minimum
iterations or before the skill has changed successfully.

SkillAdam state uses `checkpoint.json`, `current_skill.md`, and
`final_skill.md`. Resume verifies method, benchmark, model, seed, case order,
initial-skill digest, and optimization parameters before any backend call.

The custom/default research protocol uses disjoint validation cases. Frozen
paper profiles instead use a combined train/selection optimization pool and
a same-batch SkillAdam gate; SkillOpt retains separate train/selection pools.
Product sessions freeze their task suite before candidate generation. These
protocols are distinct: see [paper settings](reproducibility/main_results.md).
Test cases must never be used for optimization or patch selection.

## SkillOpt Baseline

SkillOpt has independent state and does not invoke Feedback Loop V2. It
preserves the method-defining edit scheduler, strict current/best gate,
protected slow update, longitudinal case categories, optimizer memory, and
five optimizer prompt roles.

SkillOpt state uses `skillopt_checkpoint.json`, `current_skill.md`,
`best_skill.md`, and `skillopt_report.json`. Optimizer memory remains outside
the deployed skill. SkillOpt requires an explicit initial skill, and resume
checks its own complete signature before execution.

The two methods share cases and metrics but never share a checkpoint, current
skill, best skill, Momentum state, or optimizer memory.

## Execution Backend Boundary

The CLI accepts either:

- `fixture`, which replays caller-supplied synthetic results; or
- the first-party
  `skilladam.backends.openai_compatible:create_backend`, which supports
  ALFWorld, SearchQA, LMB, DocVQA, OfficeQA, and SpreadsheetBench Chat
  Completions plus their bounded runtimes; or
- the first-party
  `skilladam.backends.deepplanning_official:create_backend`, which combines
  the shared optimizer-generation transport with an isolated, pinned
  Qwen-Agent DeepPlanning bridge; or
- another explicit `module:factory` implementing the public execution
  protocol.

Backend JSON is read locally and recorded only by SHA-256. Public backend
metadata rejects fields that look like credentials, tokens, endpoints, base
URLs, or local filesystem paths. Dry-run loads and validates the backend but
does not call rollout or generation.

The OpenAI-compatible backend reads credential and service-address values only
from explicitly named environment variables. It validates a strict
non-sensitive config, normalizes text, multimodal messages, tool calls, and
usage, bounds batch concurrency, restores case order, and rejects unsupported
runtimes before client construction. OfficeQA adds a bounded read-only local
document loop: real cases preflight a source-file allowlist and a data-root
corpus, while persisted trajectories redact the local root and keep one usage
record per model turn. ALFWorld preflights one relative gamefile below
`ALFWORLD_DATA`, runs the optional upstream environment in a bounded
subprocess, caps an episode at 50 steps, records one usage row per model step,
and serializes environment sessions to avoid TextWorld registry races.
SpreadsheetBench preflights canonical input/golden paths below the data root,
generates a truncated workbook preview in the parent process, and executes
model-generated Python through an explicit execution mode. The paper-profile default is a bounded local subprocess with provider credentials
removed from its environment; it should be run on a disposable or trusted
machine. An optional Docker/Podman mode adds a no-network, read-only,
capability-dropped container with bounded CPU/memory/PIDs and one writable
output mount. The golden workbook is never mounted or sent to the model in
either mode. Only the two training stages may return incorrect-cell
coordinates to the target model; validation, selection, test, and unknown
stages stop after the first successful code execution. DeepPlanning continues
through a dedicated one-case subprocess backend: read-only preflight pins both
upstream revisions and the exact 240-case paper profile, original databases
remain unchanged, and each shopping rollout receives a fresh output-local
copy. The first-party bridge counts agent turns and travel conversion calls in
the shared usage schema.

## Usage and Cost

Both runners normalize provider counters into the same `UsageRecord` and
append JSONL rows with method, benchmark, stage, model, token counters, and
request count. Missing provider counters remain `null`.

Cost reporting accepts one or more distinct ledgers, aggregates them by method,
benchmark, and stage, and optionally applies an explicit price book or prompt
cache-weight book. It never infers current provider prices.

## Persistence and Safety

Run artifacts are written atomically. New runs refuse non-empty output
directories; resume refuses missing or incompatible checkpoints. Execution
plans omit local data/output paths and backend configuration contents.

Keep credentials, local session history, datasets, and generated outputs
outside the repository. Example configurations contain placeholders only.

See [unified execution](reproducibility/unified_execution_cli.md),
[SkillOpt baseline](reproducibility/skillopt_baseline.md), and
[validation and troubleshooting](testing.md).
