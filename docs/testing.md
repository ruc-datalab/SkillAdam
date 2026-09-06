# Validation and Troubleshooting

Configuration checks do not measure model quality or reproduce paper scores.
Start with offline checks, then use a small case set for end-to-end validation.

## Offline Checks

From the repository root, inspect the installed CLI and available adapters:

```bash
python -m skilladam --help
python -m skilladam list-benchmarks --json
python -m skilladam check-environment --benchmark searchqa
python scripts/prepare_benchmark_data.py --benchmark searchqa --destination /absolute/external/data/searchqa
```

The data command above prints a plan without downloading or creating files.
Use a prepared dataset with a run/evaluation command's `--dry-run` to check
case selection and configuration. Dependency and data checks are distinct:
an installed package does not prove that a dataset or runtime is complete.

The automated regression suite and its fixtures are maintained separately
and are not part of this distribution. The commands above work without them.
See [dependencies](dependencies.md) and [data preparation](datasets.md).

## Real API Availability Checks

Use the smallest case set that exercises the behavior you need to verify.
An unsolved task or a skill that does not beat baseline is not automatically
an infrastructure failure. Check requests, tools, termination, scoring,
usage, and saved state.

| Benchmarks | Backend | Paper provider/model |
|---|---|---|
| ALFWorld, DocVQA, SearchQA, SpreadsheetBench, OfficeQA, LMB | `skilladam.backends.openai_compatible:create_backend` | OpenRouter / `openai/gpt-5.5` |
| DeepPlanning: Shopping L1/L2/L3, Travel EN | `skilladam.backends.deepplanning_official:create_backend` | Venus / `claude-4-5-sonnet-20250929`; Travel conversion: `gpt-4.1` |

OpenRouter uses medium reasoning at temperature 1. DeepPlanning omits
reasoning, uses temperature 0 and prompt caching, and requires pinned code/data.
SpreadsheetBench defaults to local-subprocess execution without Docker.
That mode is not a security sandbox; use a disposable or trusted machine.
Optional container mode requires a separately supplied image and strict
preflight, with no fallback.

Keep the 60-command paper matrix unchanged when designing smaller checks.
The plan-only `scripts/plan_api_training_smoke.py` and
`scripts/plan_full_api_smoke.py` describe reduced checks separately:

1. Evaluate one explicit case with baseline or a selected skill.
2. If initialization needs checking, use one explicit Stage0 case.
3. Check SkillAdam training, gate, saved state, and resume with minimal cases.
4. Check SkillOpt with a train case, a separate selection case, and
   `--max-batches 1`; the result remains `incomplete_smoke=true`.
5. Evaluate the generated skill on one explicit test case.

These planners only print commands and never execute requests. Review the
generated commands and run only the operations needed for your check.
Smoke overrides are not the paper protocol. Both methods share a fresh
Stage0 output; selected skills are evaluation inputs, not initializers.
DeepPlanning trains with E2 and evaluates the selected E1 artifacts.

Before a paid run, review provider/model, cases, method, scope, split,
data/runtime revisions, concurrency, retries, token/turn settings, cost
exposure, and a new external output directory. Credentials belong in
environment variables. No cumulative request/input/output cap is added by
default; record any optional limit separately from paper settings.
Review the data, skills, tools, and history sent to the provider. On protocol
or runtime errors, stop the affected run rather than silently changing
providers, models, data, execution modes, or scoring.

## Host Acceptance Tests

Use a disposable workspace and a synthetic skill. Test automatic application,
explicit hunk review, rejection, and interrupted-run resume. Keep the MCP
workflow as the only writer of the skill during a run and inspect the gate
and saved-state records afterward.

Host permission modes do not provide equivalent filesystem isolation. Do not
weaken permissions in a normal workspace to make a test pass. Inner tasks
must be self-contained: project source files are not copied into their
temporary directories. Do not publish real host history, responses, or logs;
redaction may not remove all business context.
