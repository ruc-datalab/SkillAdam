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
2. Call `skilladam_discover_history` exactly once with `skill_path`, `intent`,
   and the durable `output_dir`. It automatically scans the current host's known
   local history formats, keeps only real user messages, redacts likely secrets
   and personal data, deduplicates them, and writes `history_discovery.json`.
   Review each candidate for actual relevance and evaluation feasibility. Turn
   a useful real request into a self-contained evaluation case by minimally
   normalizing it and, when necessary, embedding a small synthetic artifact that
   preserves the requested work. Record `metadata.history_transform` describing
   that normalization; never claim the synthetic artifact came from the user.
   Use source `local_history` and the candidate's exact returned `source_ref`;
   never invent a history reference. A completed search with zero accepted
   candidates is valid and must not be relabeled as a history-derived task.
3. Reuse concrete tasks the user supplied. Extract tasks from the currently
   visible conversation only when they represent actual Skill usage; use source
   `conversation`, redact secrets and personal data, and keep a precise turn
   reference.
4. When a recognized public benchmark would materially improve coverage, use the
   host's normal web/research tools to select relevant cases. Record the direct
   source URL and benchmark/license details in metadata. Do not invent provenance
   when no suitable source was found.
5. Fill the remaining coverage from the Skill itself. Produce exactly
   `task_count` distinct tasks, covering at least two concise lowercase capability
   slugs and at least one `boundary`, `adversarial`, or `regression` case. Prefer
   realistic end-to-end requests over shallow paraphrases.
6. Give every task a stable `task_id`, self-contained `prompt`, `capability`,
   `difficulty`, `source`, `source_ref`, and explicit `evaluation`. Allowed
   sources are `local_history`, `conversation`, `user_provided`,
   `public_benchmark`, and `skill_generated`, in that priority order when the
   source yields a genuinely useful task. Public benchmark references must be
   direct HTTP(S) URLs.
7. Use deterministic `programmatic` or `reference` evaluation only when the task
   has one uniquely correct answer, such as a number, fixed string, explicit JSON
   structure, or exact set of fields. Open-ended natural-language tasks that
   allow semantically equivalent answers must use a `rubric_judge` rubric with
   weighted observable dimensions; never score the complete response with
   `exact` or `normalized_exact`. This includes generated messages, reviews,
   rewrites, summaries, recommendations, and analyses. Use `hybrid` only for
   complementary components. Every
   evaluation, including each hybrid component, must include concise
   `failure_feedback` with the actionable correction or evidence to use after a
   failure. Never omit an evaluation or ask SkillAdam to infer one. Correct a
   rejected manifest instead of weakening the requested count or evaluation goal.

Pass `{"schema_version":"1","tasks":[...]}` in `task_manifest`. Task text must
not depend on files, tools, credentials, or context unavailable to the rollout
model.

## Start or resume

1. Obtain the source `SKILL.md`, a durable workspace-local output directory,
   and the user's usage intent. Use a path such as `run/skilladam-optimize` under
   the current workspace; never use a host temporary or session-state directory.
2. For a new run, call `skilladam_prepare` with the source `skill_path`, `intent`,
   durable `output_dir`, completed `task_manifest`, and `compact: true`; omit
   provider fields so SkillAdam reuses the platform's authenticated CLI. Pass provider configuration only for
   an explicit fixture or OpenAI-compatible override. Use
   `skilladam_prepare_for_review` instead only when the user explicitly requested
   confirmation or hunk selection before apply. Do not use the asynchronous
   `skilladam_start*` tools: the Claude adapter intentionally does not expose
   them because a non-interactive turn may stop polling before a worker reaches
   a terminal state.
3. In default mode, call `skilladam_prepare` again with the same `output_dir` and
   `compact: true` while the workflow state is `ready` or `validating`, until the
   existing stopping policy returns `completed`. Each call resumes the durable
   checkpoint. If a call fails, report the exact error; do not shrink
   `task_count` or weaken evaluation settings as a workaround.

## Review a proposal

1. Read `proposal.hunks` from the structured result. Preserve every `hunk_id`.
2. Determine whether the user explicitly requested hunk review or confirmation
   before changes are applied. A general request to optimize, improve, patch, or
   modify the Skill does not opt in to review.
3. By default, `skilladam_prepare` auto-accepts every proposed hunk before the
   call returns. Do not issue a second selection, omit or reject hunks, pause for
   confirmation, or override the gate based on your own assessment.
4. Only after an explicit review opt-in, use `skilladam_prepare_for_review`,
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
3. Continue synchronous `skilladam_prepare` calls through `ready` and
   `validating` until the workflow reaches `completed`; the same tool resumes
   both states, so do not call `skilladam_continue`.
4. If the source changed during review, preserve the external edit and report the
   conflict; do not overwrite it.
5. On `completed`, report final quality only from `final_validation` whose scope
   is `heldout_terminal`. `baseline_validation` and `candidate_validation` are
   optimization mini-batch evidence and must never be described as final or
   held-out. If terminal evidence is unavailable, report that exact condition;
   do not substitute another metric. Never feed final held-out evidence into
   patch generation, hunk selection, an iteration gate, or Momentum.
6. Use the compact response's `iterations` entries for per-iteration results.
   Do not request a full status response merely to reconstruct the final report.

Never edit, replace, or patch the source Skill with host file tools or shell
commands during an optimization session. This remains forbidden when a proposal
looks weak, the gate rejects it, or the iteration limit is reached. Only
SkillAdam may write a selected candidate after frozen validation passes the gate;
otherwise leave the source unchanged and report the session result.

The host owns task research and manifest construction only. Do not reimplement
manifest validation, evaluation execution, patch application, gate logic,
Momentum, edit budgets, checkpoints, or stopping policy in the host adapter.

## Claude Code structured review

When the user explicitly requested confirmation or selective hunk review:

1. After `skilladam_prepare_for_review` reaches `awaiting_review`, present every
   hunk in original order. For each hunk, show its stable `hunk_id`, target path,
   header, and complete operations inside a fenced `diff` block. Do not summarize
   or omit the diff before asking for a decision.
2. Use Claude Code's `AskUserQuestion` tool to collect an explicit accept/reject
   decision for each displayed hunk. Keep the stable `hunk_id` in each question
   and batch questions only when required by the host tool's current limit.
3. Map the answers back to exact hunk IDs and call
   `skilladam_submit_selection` once with the final accepted set. Cancellation,
   silence, timeout, or an incomplete answer leaves the session at
   `awaiting_review` and must never trigger default acceptance.
