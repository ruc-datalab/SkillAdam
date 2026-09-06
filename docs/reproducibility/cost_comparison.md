# SkillAdam versus SkillOpt Cost Comparison

## Shared Ledger

Both methods write provider-reported counters to `usage.jsonl`:

```text
method, benchmark, stage, model,
input_tokens, cached_input_tokens, cache_creation_input_tokens,
output_tokens, reasoning_tokens, total_tokens, requests
```

Unavailable counters remain `null`/unknown. The reporter does not turn them
into zero, infer cache decomposition, or double-charge reasoning tokens
already included in output tokens.

## Compare Runs

Keep each run intact and pass both ledgers:

```bash
python -m skilladam cost-report \
  --input <skilladam-usage.jsonl> \
  --input <skillopt-usage.jsonl> \
  --output <comparison.csv> \
  --pricing <pricing.json> \
  --left-method skilladam \
  --right-method skillopt
```

`--input` is repeatable. Duplicate files, hard-link/symlink aliases, and an
output that aliases an input are rejected. Inputs are read-only.

Pricing is optional: omit `--pricing` for token and request comparisons.
Use your provider's applicable prices and record their effective date.
Prompt billing-equivalent tokens require a separate explicit weight book:

```bash
python scripts/reproduce_cost_table.py \
  --input <skilladam-usage.jsonl> \
  --input <skillopt-usage.jsonl> \
  --output <comparison.csv> \
  --prompt-weights <prompt-weights.json>
```

Run ledgers and reference test tables are not bundled. Supply ledgers from
the runs you want to compare; the scripts calculate the table without
making provider calls. See `python -m skilladam cost-report --help` for
available grouping and pricing options.

## Comparable Scope

Compare equivalent benchmark scopes, data, models, and stages. In particular,
optimizer-only usage is not the total cost of an experiment: target-agent
rollouts, selection, initialization, and testing can contribute substantially.
Incomplete logs cannot be used as complete total-cost measurements, and
missing provider counters cannot be reconstructed by the reporter.

SkillAdam records training/validation rollouts, Stage0, iteration patching,
and Momentum. SkillOpt records target/selection/slow-update/test rollouts,
reflection, merge, select, slow-update generation, and optimizer memory.
The report preserves those stage distinctions rather than equating unlike
operations. State which stages are included when reporting paper comparisons.

See the [unified CLI](unified_execution_cli.md) and
[SkillOpt baseline](skillopt_baseline.md).
