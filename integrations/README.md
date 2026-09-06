# SkillAdam Platform Adapters

SkillAdam provides the same Skill optimization workflow for Claude Code, Codex, Cursor, and GitHub Copilot. All four platforms share task evaluation, patch review, selected-candidate validation, the gate, and session management.

## Prerequisites

- Python 3.10 or later.
- At least one supported platform CLI installed and signed in: `claude`, `codex`,
  Cursor Agent (`cursor-agent`), or `copilot`. The desktop `cursor` command is
  not a substitute for the Cursor Agent CLI.
- The GitHub Copilot installer also requires the VS Code `code` CLI by default.
  For a Copilot CLI-only setup, pass `--skip-vscode-registration`.
- Run the installation scripts from the SkillAdam repository root.

## Models and Authentication

The default workflow uses the `platform-cli` provider: SkillAdam invokes the same
platform's local CLI as an inner process, reusing the user's existing OAuth,
subscription, or keychain sign-in. Standard Claude Code, Codex, Cursor, and
Copilot plugin setups do not need a separate `OPENAI_API_KEY` or
`OPENAI_BASE_URL`. API key and base URL settings are read only when the user
explicitly selects the `openai-compatible` provider.
Reusing a sign-in does not make model calls free or guarantee that the inner
model matches the model selected in the outer chat. The inner process uses
the platform adapter's default model or an explicit `SKILLADAM_MODEL` setting.

The plugin's MCP process can also override its default model runtime through
private environment variables. Configuration persists environment variable
names rather than writing credentials into the SkillAdam project:

- `SKILLADAM_PROVIDER=openai-compatible`.
- `SKILLADAM_MODEL`, `SKILLADAM_WIRE_API`, and
  `SKILLADAM_REASONING_EFFORT`.
- `SKILLADAM_API_KEY_ENV` and `SKILLADAM_BASE_URL_ENV`, which name the
  environment variables containing the actual key and endpoint.

Copilot CLI natively supports `COPILOT_PROVIDER_*` variables to route both the
outer host and inner rollouts to an OpenAI-compatible endpoint. Cursor Agent
CLI's `--endpoint` uses the Cursor Agent server protocol, not the OpenAI API;
it cannot replace a model base URL. To use a custom endpoint with Cursor,
override only the inner provider in SkillAdam MCP. The outer Cursor host
continues to use a model available through the Cursor account.

The outer host reads the Skill, calls MCP, advances the session, and displays
results. It must not bypass SkillAdam by editing the source Skill directly
with its file tools or shell. Claude Code advances each iteration synchronously
with `skilladam_prepare`; hosts that support persistent polling, such as Codex
and Cursor, use `skilladam_start` / `skilladam_status(compact=true)`.
The source Skill is updated only after the selected candidate has been
validated against the frozen evaluation and accepted by the gate.

## Tasks and Evaluation Signals

The host's `skilladam-optimize` Skill builds tasks before calling the optimization
MCP; SkillAdam does not launch a separate internal task generator. Before the
first task manifest, Codex, Claude Code, and GitHub Copilot must call
`skilladam_discover_history` once. The plugin reads a bounded amount of real
user input in known host formats, redacts it, removes duplicates, ranks it
for relevance, and saves an audit record in the optimization output directory.
Cursor does not yet support local history discovery and must skip both this
tool and its CLI fallback. It uses the other sources below and must not invent
history records or `local_history` provenance. The host combines evidence in
this order:

- Real tasks from local history that are closely related to the target Skill
  and can be evaluated independently.
- Tasks explicitly supplied by the user.
- Tasks visible in the current conversation that actually used the Skill.
- Public benchmark cases found through the host's existing search tools.
- Additional tasks derived from the target Skill and its intended use.

History discovery accepts only recognized host formats. Unknown formats
produce recorded errors rather than guessed fields. Tasks from history and
the current conversation must have secrets and personal information removed.
Public benchmark sources must include a direct URL; recording the name,
version, and license in metadata is also recommended. If no suitable history
or public data is available, tasks may be derived from the Skill and user
evidence, but their provenance must not be fabricated.

The host submits a complete `task_manifest` in one call. Each task must have
a stable ID, a self-contained prompt, a short capability slug, difficulty,
source, source_ref, and an explicit evaluation. The default set of 8 tasks
must cover at least two capabilities and include a boundary, adversarial,
or regression task. Every evaluation, including each hybrid component, must
also provide `failure_feedback`: actionable guidance for correcting an
evaluation failure. It enters optimization feedback only when the evaluation
fails. Before writing the project, MCP strictly validates task counts,
duplicates, sources, and evaluation settings. Missing or invalid fields
produce a specific error; it does not ask a model to fill in tasks or rubrics,
or switch to an external fallback.

The inner rollout model runs in a separate process and does not inherit
restrictions applied to the outer host by automated acceptance tests.
Copilot's inner process explicitly retains `--allow-all-tools` and
`--allow-all-urls`. It disables only:

- The `skilladam` MCP server, to prevent a model being optimized from
  recursively starting another SkillAdam run.
- `ask_user`, to prevent unattended task and evaluation calls from waiting
  for interactive input.
- Outer custom instructions, to prevent host project rules from altering
  frozen evaluation prompts.

The current entry point optimizes a single specified `SKILL.md`, not an
arbitrary project or a complete Skill resource bundle. Inner calls run in
temporary working directories; source project files and bundled Skill
resources are not copied automatically. Tasks must be self-contained and
must not assume access to the original workspace's files or tools. Claude,
Codex, and Cursor inner calls use their adapters' read-only/safe/ask modes.
The Copilot permissions above still apply; these modes should not collectively
be described as filesystem isolation.

Do not relax host permissions to work around a failed run in a workspace
containing private assets. Maintainer acceptance tests belong in disposable
environments; see [testing](../docs/testing.md#host-acceptance-tests).

History discovery uses pattern-based redaction, not a guarantee of anonymity.
Review the selected requests before sending them to a model. Discovery reports,
session files, model responses, and command logs are private run artifacts;
keep them outside version control and use synthetic examples in public reports.

## One-Step Installation

Windows PowerShell:

```powershell
.\integrations\claude-code\install.ps1
.\integrations\codex\install.ps1
.\integrations\cursor\install.ps1
.\integrations\github-copilot\install.ps1
```

macOS/Linux:

```bash
./integrations/claude-code/install.sh
./integrations/codex/install.sh
./integrations/cursor/install.sh
./integrations/github-copilot/install.sh
```

By default, the installer:

1. Creates a dedicated Python virtual environment at `~/.skilladam/runtime`.
2. Installs `skilladam[backend]` from the current repository.
3. Stages the adapter for the selected platform.
4. Pins the MCP server to that environment's `python -m skilladam.product_mcp`
   (Codex uses the dedicated `skilladam.codex_product_mcp`).
5. Registers the plugin, Skill, or MCP server through the platform's official
   CLI. For GitHub Copilot, it also registers a user-level MCP server through
   VS Code's official `code --add-mcp`, so new workspaces do not need a copy of
   `.vscode/mcp.json`.

Preview installation without writing files or registering a plugin:

<!-- plugin-install-preview -->
```bash
python integrations/install.py codex --dry-run
```

Available options:

```text
--dry-run                 Show planned actions without writing files
--stage-only              Stage the adapter without calling the host CLI
--skip-engine-install     Reuse SkillAdam installed in the current Python
--install-root PATH       Override the default ~/.skilladam install directory
--cursor-home PATH        Override the default ~/.cursor configuration directory
--skip-vscode-registration
                          Install only the Copilot CLI MCP server and Skill
```

Update your checkout, then rerun the installer. It installs the current local
checkout; it does not fetch repository updates. Claude Code updates its
installed plugin; Codex reuses the local marketplace with the same name;
Cursor refreshes its local plugin and MCP server. Copilot migrates the legacy SkillAdam CLI plugin,
registers the CLI and VS Code user-level MCP servers again, and refreshes
the personal workflow Skill. Custom model-routing environment variables in
existing Copilot MCP configuration are preserved. Key, token, secret, and
password values are redacted from logs. Open VS Code windows need
`Developer: Reload Window` to load the new tools.

### Verify Installation and Resume a Run

The Cursor installer writes `~/.cursor/plugins/local/skilladam` and
`~/.cursor/skills/skilladam-optimize`, then merges the SkillAdam server into
`~/.cursor/mcp.json` while preserving other servers. Restart any open Cursor
Agent session, then check:

```bash
cursor-agent mcp list
cursor-agent mcp list-tools skilladam
```

You should see a ready `skilladam` server with tools such as
`skilladam_start` and `skilladam_status`. Do not expect Cursor to expose
`skilladam_discover_history`. Installation checks for the other hosts:

```bash
claude plugin list --json
codex plugin list --json --available
copilot mcp list
copilot skill list
```

Run only the commands for the host you installed. After an interruption,
ask the host to resume using the original persistent `output_dir` instead
of creating a new run. Checkpoints preserve proposals, exact hunk selections,
gate records, and final held-out results. Closing or restarting the platform
does not confirm pending changes.

## Optimization Loop and Plugin Defaults

The plugin reuses the core mechanisms, but these small-task defaults are
not the paper reproduction settings for the seven benchmarks. The default
run has at most 3 iterations. Passing the gate describes validation on the
current tasks; it does not guarantee success or improvement on every task.

By default, the host submits 8 frozen tasks, with 3 reserved for held-out
validation. Each iteration samples up to 3 of the remaining tasks as a
mini-batch using seed 42. Each iteration follows this sequence:

1. Roll out the current Skill on the mini-batch and save ordered trajectories,
   final outputs, and case-level feedback.
2. Send the current Skill, trajectory feedback, Momentum memory, and current
   edit budget to the patch generator.
3. After review, assemble only the selected candidate and roll it out again
   on the same task IDs.
4. Have the Acceptance Gate compare current and candidate results on that
   same batch.
5. After the gate, use the original `MomentumTracker` tool workflow to update
   issues, attempts, and resolve/reopen states. Then update Adaptive Edit
   Budget from the variance of per-case deltas.
6. Evaluate the reserved held-out tasks for the final report only after a
   stopping condition is reached. Those results do not enter subsequent
   patch generation, gates, or Momentum.

The default edit budget is `base=4`, `minimum=1`, `beta=0.9`, and
`v_max=0.1`. The CLI and MCP expose overrides through
`--batch-size` / `batch_size` and `--edit-budget-*` / `edit_budget_*`,
respectively. `optimization_session.json` stores the full resumable state.
`iterations/iter_NNN/training_feedback.json` and `iteration.json` record
per-iteration audit evidence; `final_validation.json` stores the terminal
held-out results.

A completed MCP response includes compact `iterations` and
`final_validation` summaries. `baseline_validation` /
`candidate_validation` explicitly have scope `optimization_minibatch`.
Only `final_validation` with `scope=heldout_terminal` represents the final
quality report. While a background job is running, the server permits
`skilladam_status` reads; the workflow operation lock rejects all synchronous
writes.

## Patch Review

The default is auto-accept-all. Once a proposal is ready, the adapter
immediately submits every hunk ID unless the user has explicitly asked to
confirm changes before applying them or to select individual changes.
Ordinary requests to optimize, improve, fix, or modify a Skill do not opt
into review.

When the user explicitly opts into review:

- Show every hunk and wait for a selection before applying changes.
- Apply only the hunks the user explicitly accepts.
- Only an explicitly submitted empty selection rejects all changes in the
  iteration. Closing or cancelling the form is not a rejection.
- No reply, a timeout, a disconnect, or a restart leaves the session in
  `awaiting_review`.

Codex provides two native interaction paths, both using stable hunk IDs from
the shared protocol. In the TUI, `skilladam_review_pending` opens
`elicitation/create` forms with one Accept/Reject field per hunk, in batches
of at most 3. Hosts that support MCP Apps use `skilladam_render_review` to
load `ui://skilladam/hunk-review/v1.html`, select hunks in the embedded diff
view, stage the exact hunk IDs, and then resume validation from the
checkpoint. Both paths initially select every hunk, but create a
`PatchSelection` only after the user submits. If the host does not render
the component or support elicitation, the tool returns all stable hunk IDs
and the complete diff. The agent must show these in chat and wait for an
explicit response. A host fallback or `decline` must not be described as a
user rejection.

The Codex TUI currently renders the MCP form's `message` and JSON Schema
`description` as host-controlled plain text. The plugin therefore provides
the complete diff, structured selection, and no duplicate display, but cannot
set colors for added or removed lines. The MCP App provides the colored,
responsive review interface with per-hunk status.

VS Code Copilot Chat uses the same `skilladam_render_review` MCP App. Each
hunk shows its full colored diff and can be accepted or rejected individually,
with select-all and reject-all controls. Submission only stages the exact
hunk IDs; the conversation then resumes validation from the checkpoint.
Copilot CLI and older VS Code versions without MCP Apps use the complete
`fallback.hunks` in the response for text-based selection. A missing UI is
not a rejection.

Any nonempty selection first produces the actual selected candidate, which
is then validated against the same frozen EvaluationPlan. The source Skill
is updated only if the gate accepts it.

## Distribution

| Platform | Repository format | Installation / distribution |
| --- | --- | --- |
| Claude Code | `.claude-plugin/marketplace.json` + plugin | `claude plugin marketplace add OWNER/REPO`; public plugins can be submitted to the Anthropic marketplace |
| Codex | `.agents/plugins/marketplace.json` + `.codex-plugin/plugin.json` | `codex plugin marketplace add OWNER/REPO`, followed by `codex plugin add skilladam@MARKETPLACE` |
| Cursor | `.cursor-plugin/plugin.json` | Load locally into `~/.cursor/plugins/local`; submit public releases to Cursor Marketplace |
| GitHub Copilot | Shared stdio MCP + `SKILL.md` + VS Code MCP App | The installer uses `copilot mcp add`, `copilot skill add`, and `code --add-mcp`; VS Code embeds structured hunk review, with no separate VSIX |

Official references:

- [Claude Code plugins](https://code.claude.com/docs/en/discover-plugins)
- [Codex plugins](https://developers.openai.com/codex/plugins/)
- [Codex App Server](https://developers.openai.com/codex/app-server/)
- [Cursor plugins](https://cursor.com/docs/plugins)
- [GitHub Copilot plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference)
- [VS Code MCP developer guide](https://code.visualstudio.com/api/extension-guides/ai/mcp)
- [VS Code MCP server management](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
