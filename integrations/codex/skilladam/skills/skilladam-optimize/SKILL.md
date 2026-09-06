---
name: skilladam-optimize
description: Optimize an existing SKILL.md from a usage-intent description with SkillAdam. Use when a user asks to test, improve, patch, review, validate, or iteratively optimize an Agent Skill, with optional selective hunk review.
---

# SkillAdam Optimize

Use the `skilladam_*` MCP tools when available. Otherwise invoke the equivalent
`skilladam-product` commands and parse their JSON output.

## Build the task manifest

For a new run, research and construct the task signal in the current host
session before calling SkillAdam. Do not start a nested agent or external task
generator solely for this step.

1. Read the complete target `SKILL.md` and the user's usage intent.
2. Local history discovery is supported on Codex, Claude Code, and GitHub
   Copilot only. On Cursor, skip this entire step, including the CLI fallback:
   neither interface supports Cursor history. Continue with the task sources
   below; do not invent `local_history` provenance or a discovery report.
   On a supported platform:
   Call `skilladam_discover_history` exactly once with `skill_path`, `intent`,
   and the durable `output_dir`. It automatically scans the current host's known
   local history formats, keeps only real user messages, redacts likely secrets
   and personal data, deduplicates them, and writes `history_discovery.json`.
   If MCP is unavailable, run `skilladam-product discover-history` with the same
   values and `--platform`. Review each returned candidate for actual relevance
   and evaluation feasibility. Turn a useful real request into a self-contained
   evaluation case by minimally normalizing it and, when necessary, embedding a
   small synthetic artifact that preserves the requested work. Record
   `metadata.history_transform` describing that normalization; never claim the
   synthetic artifact came from the user. Use source `local_history` and the
   candidate's exact returned `source_ref`; never invent a history reference.
   A completed search with zero accepted candidates is valid and must not be
   relabeled as a history-derived task.
3. Reuse concrete tasks the user supplied. Extract tasks from the currently
   visible conversation only when they represent actual Skill usage; use source
   `conversation`, redact secrets and personal data, and keep a precise turn
   reference.
4. When a recognized public benchmark would materially improve coverage, use the
   host's normal web/research tools to select relevant cases. Record the direct
   source URL and benchmark/license details in metadata. Do not copy a benchmark
   label onto a generated task or invent provenance when no suitable source was
   found.
5. Fill the remaining coverage from the Skill itself. Produce exactly
   `task_count` distinct tasks, covering at least two concise lowercase capability
   slugs and at least one `boundary`, `adversarial`, or `regression` case. Prefer
   realistic end-to-end requests over shallow paraphrases or trivia about the
   Skill text.
6. Give every task a stable `task_id`, self-contained `prompt`, `capability`,
   `difficulty`, `source`, `source_ref`, and explicit `evaluation`. Allowed
   sources are `local_history`, `conversation`, `user_provided`,
   `public_benchmark`, and `skill_generated`, in that priority order when the
   source yields a genuinely useful task. A public benchmark `source_ref` must
   be its direct HTTP(S) URL.
7. Use `programmatic` or `reference` evaluation only when the task has one
   uniquely correct, deterministic answer, such as a number, fixed string,
   explicit JSON structure, or exact set of fields. An open-ended task that
   allows multiple semantically equivalent correct outputs must use
   `rubric_judge` with explicit weighted observable dimensions; never score its
   complete natural-language output with `exact` or `normalized_exact`. This
   includes generated messages, reviews, rewrites, summaries, recommendations,
   analyses, and similar natural-language generation. Use `hybrid` only for two
   or more complementary checks. Every evaluation, including each hybrid component,
   must include concise `failure_feedback` that states the actionable correction
   or evidence the optimizer should use when that evaluation fails. Never omit an
   evaluation or ask SkillAdam to infer one. The MCP boundary validates and freezes
   the complete manifest; correct a rejected manifest rather than changing the
   requested count or evaluation goal.

Pass one object shaped as
`{"schema_version":"1","tasks":[...]}` in `task_manifest`. Task text must not
depend on files, tools, credentials, or context unavailable to the rollout model.

## Start or resume

1. Obtain the source `SKILL.md`, a durable workspace-local output directory,
   and the user's usage intent. Use a path such as `run/skilladam-optimize` under
   the current workspace; never use a host temporary or session-state directory.
2. For a new run, call `skilladam_start` with `skill_path`, `intent`,
   `output_dir`, and the completed `task_manifest`. For an existing run, the same
   tool may resume from `output_dir` alone. Omit provider fields so SkillAdam reuses the
   platform's authenticated CLI. Pass provider
   configuration only for an explicit fixture or OpenAI-compatible override. Use
   `skilladam_start_for_review` instead only when the user explicitly requested
   confirmation or hunk selection before apply. In default mode, the durable job
   automatically iterates through the existing stopping policy. The synchronous
   `skilladam_prepare*` tools are compatibility fallbacks when async tools are
   unavailable.
3. Poll `skilladam_status` with the same `output_dir` and `compact: true` while
   `job.state` is `running`; do not return a final
   answer merely because the durable job is still running. Do not act on a
   workflow or proposal exposed by an older server while the job is still
   `running`, and never edit the source Skill. Once the terminal status returns the
   workflow view, treat the task suite and evaluation plan as frozen. If the job
   fails, report the exact error; do not shrink `task_count` or weaken evaluation
   settings as a workaround.

## Review a proposal

1. Read `proposal.hunks` from the structured result. Preserve every `hunk_id`.
2. Determine whether the user explicitly requested hunk review or confirmation
   before changes are applied. A general request to optimize, improve, patch, or
   modify the Skill does not opt in to review.
3. By default, `skilladam_start` auto-accepts every proposed hunk before the job
   completes. Do not issue a second selection, omit or reject hunks, pause for
   confirmation, or override the gate based on your own assessment.
4. Only after an explicit review opt-in, use `skilladam_start_for_review`,
   present every hunk's target, header, and operations in original order, and
   wait at `awaiting_review`. Prefer a native
   structured selection UI that can map decisions back to exact hunk IDs;
   otherwise ask the user for accepted IDs, accept all, or reject all.
5. In review mode, never treat silence, timeout, disconnect, resume, or an
   unrelated positive comment as acceptance. An empty selection rejects all.
6. Submit exactly the selected IDs with a new, stable idempotency key. Do not use
   review results from a different proposal or infer a partial selection.

## Validate and iterate

1. Trust only `candidate_validation` whose digest matches the selected candidate.
   Never reuse validation for the full proposal after a partial selection. Each
   iteration's current Skill rollout and selected candidate validation must keep
   the exact ordered mini-batch IDs recorded in the session.
2. Report the gate decision and stop reason concisely. The service writes the
   source Skill only when the gate accepts the selected candidate. After the gate,
   the service updates EIT/Momentum from ordered trajectory feedback, the patch,
   candidate feedback, and the decision, then updates the adaptive edit budget.
3. A default `skilladam_start` job advances `ready` and `validating` states until
   the workflow reaches `completed`. If an interrupted worker leaves resumable
   state, call `skilladam_start` again and continue polling. Use
   `skilladam_continue` only as a synchronous compatibility fallback.
4. If the source changed during review, preserve the external edit and report the
   conflict; do not overwrite it.
5. Treat `final_validation.json` as held-out terminal evidence only. Never feed it
   into patch generation, hunk selection, an iteration gate, or Momentum.
6. At completion, use the compact status response's `iterations` entries for the
   per-iteration summary. Report final quality only from `final_validation` when
   its `scope` is `heldout_terminal`; never relabel `baseline_validation` or
   `candidate_validation` (both optimization mini-batch evidence) as final or
   held-out quality. If final validation is unavailable, report that exact state
   and do not substitute another score.

Never edit, replace, or patch the source Skill with host file tools or shell
commands during an optimization session. This remains forbidden when a proposal
looks weak, the gate rejects it, or the iteration limit is reached. Only
SkillAdam may write a selected candidate after frozen validation passes the gate;
otherwise leave the source unchanged and report the session result.

The host owns task research and manifest construction only. Do not reimplement
manifest validation, evaluation execution, patch application, gate logic,
Momentum, edit budgets, checkpoints, or stopping policy in the host adapter.

## Codex structured review

When the user explicitly opted in to hunk review:

1. After `skilladam_start_for_review` reaches `awaiting_review`, use exactly one
   native review path. On Codex App or another MCP Apps-capable surface, call
   `skilladam_render_review` with the same `output_dir`. The component shows the
   complete ordered hunks, defaults each decision to accept, and submits exact
   stable hunk IDs through the existing `skilladam_submit_selection` tool. Stop
   the turn with the component available so the user can make and submit the
   selection; do not submit a second selection from the model.
2. In Codex TUI, call `skilladam_review_pending` with the same `output_dir` and a
   new idempotency key. Do not use `request_user_input`: the Codex-specific MCP
   tool owns the native structured interaction and maps decisions to exact
   stable hunk IDs. It shows every complete unified diff and presents
   accept/reject choices in ordered batches of at most three. Its default choice
   is accept, but no choice is submitted until the user submits every batch.
3. A `decline` or `cancel` TUI result only means native review was not submitted.
   Leave the session at `awaiting_review`; do not call
   `skilladam_submit_selection`, infer a choice, or tell the user that any hunk
   was rejected. Only a persisted `PatchSelection` can establish accepted or
   rejected hunks.
4. If Codex App does not render the component, use every item in
   `skilladam_render_review.fallback.hunks`; if the TUI result sets
   `fallback_required: true`, use every item in its `fallback.hunks`. Present
   them in original order, including the stable hunk ID and complete `diff`.
   Ask the user to accept all, reject all, or name the accepted hunk IDs, then
   wait for an explicit reply before calling `skilladam_submit_selection`.
5. If the selected native review tool is unavailable or returns an error without
   structured fallback data, use the proposal already returned by
   `skilladam_start_for_review` to perform the same ordered text review. Never
   silently convert an unsupported client response into a rejection.
