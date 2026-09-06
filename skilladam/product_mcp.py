"""Minimal-dependency MCP stdio adapter for SkillAdam product workflows."""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

from skilladam.product.commands import (
    apply_default_review_policy,
    continue_product,
    prepare_product,
    product_status,
    submit_product_selection,
)
from skilladam.product.cli_model import DEFAULT_PLATFORM_CLI_TIMEOUT_SECONDS
from skilladam.product.jobs import (
    ProductOperationBusy,
    ensure_product_job_idle,
    product_job_status,
    product_operation_lock,
    start_product_job,
)
from skilladam.product.history_adapters import HISTORY_PLATFORMS
from skilladam.product.history_discovery import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MAX_CANDIDATES,
    discover_history_tasks,
    validate_history_discovery,
)
from skilladam.product.project import ProductProjectStore
from skilladam.product.runtime import (
    build_runtime_config,
    default_runtime_config,
)
from skilladam.product.task_manifest import task_manifest_schema
from skilladam.product.session_store import SessionStore
from skilladam.product_review_mcp import (
    RENDER_REVIEW_TOOL,
    STAGE_SELECTION_TOOL,
    render_review,
    render_review_tool_definition,
    stage_review_selection,
    stage_selection_tool_definition,
)
from skilladam.review_app import (
    REVIEW_APP_URI,
    read_review_app_resource,
    review_app_resource,
)


SERVER_INFO = {"name": "skilladam", "version": "0.1.0"}
_PROGRESS_INTERVAL_SECONDS = 15.0
DEFAULT_PLUGIN_TASK_COUNT = 8
DEFAULT_PLUGIN_VALIDATION_SIZE = 3
_SYNC_ONLY_PLATFORMS = {"claude-code"}
_ASYNC_ONLY_TOOLS = {
    "skilladam_start",
    "skilladam_start_for_review",
    "skilladam_continue",
}
_MCP_APP_PLATFORMS = {"github-copilot"}


def handle_request(request: Mapping[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None and method != "notifications/initialized":
        return None
    try:
        if method == "initialize":
            params = _mapping(request.get("params", {}), "params")
            capabilities: dict[str, Any] = {"tools": {"listChanged": False}}
            if _supports_review_app(_configured_platform()):
                capabilities["resources"] = {
                    "subscribe": False,
                    "listChanged": False,
                }
            result = {
                "protocolVersion": params.get(
                    "protocolVersion",
                    "2025-06-18",
                ),
                "capabilities": capabilities,
                "serverInfo": SERVER_INFO,
            }
        elif method == "notifications/initialized":
            return None
        elif method == "ping":
            result = {}
        elif method == "resources/list":
            _require_review_app(_configured_platform())
            result = {"resources": [review_app_resource()]}
        elif method == "resources/read":
            _require_review_app(_configured_platform())
            params = _mapping(request.get("params", {}), "params")
            if params.get("uri") != REVIEW_APP_URI:
                raise ValueError(f"unknown resource URI: {params.get('uri')!r}")
            result = read_review_app_resource()
        elif method == "tools/list":
            platform = _configured_platform()
            tools = _tool_definitions(platform)
            if _supports_review_app(platform):
                tools.extend(
                    (
                        render_review_tool_definition(),
                        stage_selection_tool_definition(),
                    )
                )
            result = {"tools": tools}
        elif method == "tools/call":
            params = _mapping(request.get("params", {}), "params")
            name = str(params.get("name", ""))
            arguments = _mapping(params.get("arguments", {}), "arguments")
            platform = _configured_platform()
            if _supports_review_app(platform) and name == RENDER_REVIEW_TOOL:
                result = render_review(arguments)
            elif _supports_review_app(platform) and name == STAGE_SELECTION_TOOL:
                result = stage_review_selection(arguments)
            else:
                result = _call_tool(name, arguments)
        else:
            return _error(request_id, -32601, f"method not found: {method}")
    except (OSError, RuntimeError, ValueError) as exc:
        if method == "tools/call":
            result = {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
            if isinstance(exc, ProductOperationBusy):
                result["structuredContent"] = {"error": exc.to_dict()}
        else:
            return _error(request_id, -32602, str(exc))
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(input_stream: TextIO, output_stream: TextIO) -> None:
    write_lock = threading.Lock()
    for line in input_stream:
        if not line.strip():
            continue
        request: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, Mapping):
                raise ValueError("request must be a JSON object")
            stop_progress = threading.Event()
            progress_thread = _start_progress_heartbeat(
                request,
                output_stream,
                write_lock,
                stop_progress,
            )
            try:
                response = handle_request(request)
            finally:
                stop_progress.set()
                if progress_thread is not None:
                    progress_thread.join(timeout=1)
        except (json.JSONDecodeError, ValueError) as exc:
            response = _error(None, -32700, str(exc))
        except Exception as exc:  # noqa: BLE001
            # Keep the stdio server alive on unexpected exceptions so the host can distinguish
            # a failed call from a lost connection. Write the traceback to stderr,
            # return a protocol error, and continue serving requests.
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            response = _error(
                request.get("id") if isinstance(request, Mapping) else None,
                -32603,
                f"internal error: {type(exc).__name__}: {exc}",
            )
        if response is not None:
            _write_message(output_stream, write_lock, response)


def _start_progress_heartbeat(
    request: Mapping[str, Any],
    output_stream: TextIO,
    write_lock: threading.Lock,
    stop: threading.Event,
) -> threading.Thread | None:
    if request.get("method") != "tools/call":
        return None
    params = request.get("params")
    if not isinstance(params, Mapping):
        return None
    metadata = params.get("_meta")
    if not isinstance(metadata, Mapping):
        return None
    token = metadata.get("progressToken")
    if isinstance(token, bool) or not isinstance(token, (str, int)):
        return None

    def _heartbeat() -> None:
        progress = 0
        while not stop.is_set():
            _write_message(
                output_stream,
                write_lock,
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {
                        "progressToken": token,
                        "progress": progress,
                        "message": "SkillAdam optimization is running",
                    },
                },
            )
            progress += 1
            if stop.wait(_PROGRESS_INTERVAL_SECONDS):
                break

    thread = threading.Thread(
        target=_heartbeat,
        name="skilladam-mcp-progress",
        daemon=True,
    )
    thread.start()
    return thread


def _write_message(
    output_stream: TextIO,
    write_lock: threading.Lock,
    payload: Mapping[str, Any],
) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with write_lock:
        output_stream.write(encoded + "\n")
        output_stream.flush()


def _call_tool(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    _ensure_tool_available(name, _configured_platform())
    output_dir = Path(_required_text(arguments, "output_dir"))
    if name == "skilladam_discover_history":
        platform = _history_platform(arguments)
        report = discover_history_tasks(
            skill_path=Path(_required_text(arguments, "skill_path")),
            intent=_required_text(arguments, "intent"),
            output_dir=output_dir,
            platform=platform,
            lookback_days=_int(
                arguments.get("lookback_days", DEFAULT_LOOKBACK_DAYS),
                "lookback_days",
            ),
            max_candidates=_int(
                arguments.get("max_candidates", DEFAULT_MAX_CANDIDATES),
                "max_candidates",
            ),
        )
        return _tool_result(report)
    prepare_tools = {
        "skilladam_start",
        "skilladam_start_for_review",
        "skilladam_prepare",
        "skilladam_prepare_for_review",
    }
    if name in prepare_tools:
        arguments = _with_plugin_task_defaults(arguments)
        if "review_mode" in arguments:
            raise ValueError(
                "review_mode is not accepted; use "
                "skilladam_start_for_review for explicit review"
            )
        _validate_initial_prepare_arguments(output_dir, arguments)
        review_mode = name in {
            "skilladam_start_for_review",
            "skilladam_prepare_for_review",
        }
        if name.startswith("skilladam_start"):
            payload = start_product_job(
                output_dir,
                operation=(
                    "prepare_for_review" if review_mode else "prepare"
                ),
                arguments=arguments,
                review_mode=review_mode,
            )
            return _tool_result(payload)
        compact = (
            _bool(arguments.get("compact", False), "compact")
            if name == "skilladam_prepare"
            else False
        )
        with product_operation_lock(output_dir):
            ensure_product_job_idle(output_dir)
            view = _prepare_view(
                output_dir,
                arguments,
                review_mode=review_mode,
            )
    elif name == "skilladam_status":
        compact = _bool(arguments.get("compact", False), "compact")
        payload = _status_payload(output_dir, compact=compact)
        if payload is not None:
            return _tool_result(payload)
        view = product_status(output_dir)
    elif name == "skilladam_submit_selection":
        with product_operation_lock(output_dir):
            ensure_product_job_idle(output_dir)
            view = submit_product_selection(
                output_dir=output_dir,
                accepted_hunk_ids=_text_sequence(
                    arguments.get("accepted_hunk_ids"),
                    "accepted_hunk_ids",
                ),
                idempotency_key=_required_text(arguments, "idempotency_key"),
                actor=str(arguments.get("actor", "user")),
                reason=str(arguments.get("reason", "")),
            )
    elif name == "skilladam_continue":
        with product_operation_lock(output_dir):
            ensure_product_job_idle(output_dir)
            view = continue_product(output_dir)
    else:
        raise ValueError(f"unknown tool {name!r}")
    payload = _workflow_payload(output_dir, view.to_dict())
    if name in {"skilladam_prepare", "skilladam_status"} and compact:
        payload = _compact_workflow(payload)
        return _tool_result(payload)
    return _tool_result(payload)


def _prepare_view(
    output_dir: Path,
    arguments: Mapping[str, Any],
    *,
    review_mode: bool,
    run_to_completion: bool = False,
):
    provider = _optional_text(arguments.get("provider"))
    platform = _optional_text(arguments.get("platform"))
    runtime = default_runtime_config()
    if provider is not None or platform is not None:
        runtime = build_runtime_config(
            provider=provider or "platform-cli",
            fixture=_optional_path(arguments.get("fixture")),
            model=_optional_text(arguments.get("model")),
            api_key_env=str(arguments.get("api_key_env", "OPENAI_API_KEY")),
            base_url_env=str(arguments.get("base_url_env", "OPENAI_BASE_URL")),
            wire_api=str(arguments.get("wire_api", "completions")),
            reasoning_effort=_optional_text(arguments.get("reasoning_effort")),
            platform=platform,
            executable=_optional_text(arguments.get("executable")),
            timeout_seconds=_int(
                arguments.get(
                    "timeout_seconds",
                    DEFAULT_PLATFORM_CLI_TIMEOUT_SECONDS,
                ),
                "timeout_seconds",
            ),
        )
    if ProductProjectStore(output_dir).exists:
        current = product_status(output_dir)
        if current.session.state == "validating":
            view = continue_product(output_dir)
        else:
            view = prepare_product(output_dir=output_dir, runtime=runtime)
    else:
        view = prepare_product(
            output_dir=output_dir,
            skill_path=_optional_path(arguments.get("skill_path")),
            intent=_optional_text(arguments.get("intent")),
            runtime=runtime,
            task_manifest=_mapping(
                arguments.get("task_manifest"),
                "task_manifest",
            ),
            task_count=_int(
                arguments.get("task_count", DEFAULT_PLUGIN_TASK_COUNT),
                "task_count",
            ),
            validation_size=_optional_int(arguments.get("validation_size")),
            batch_size=(
                None
                if arguments.get("batch_size") is None
                else _int(arguments["batch_size"], "batch_size")
            ),
            max_iterations=_int(
                arguments.get("max_iterations", 3),
                "max_iterations",
            ),
            min_iterations=_int(
                arguments.get("min_iterations", 0),
                "min_iterations",
            ),
            max_consecutive_failures=_int(
                arguments.get("max_consecutive_failures", 3),
                "max_consecutive_failures",
            ),
            patch_attempts=_int(
                arguments.get("patch_attempts", 3),
                "patch_attempts",
            ),
            edit_budget_base=_int(
                arguments.get("edit_budget_base", 4),
                "edit_budget_base",
            ),
            edit_budget_minimum=_int(
                arguments.get("edit_budget_minimum", 1),
                "edit_budget_minimum",
            ),
            edit_budget_beta=_number(
                arguments.get("edit_budget_beta", 0.9),
                "edit_budget_beta",
            ),
            edit_budget_v_max=_number(
                arguments.get("edit_budget_v_max", 0.1),
                "edit_budget_v_max",
            ),
        )
    view = apply_default_review_policy(
        output_dir=output_dir,
        view=view,
        review_mode=review_mode,
    )
    while (
        run_to_completion
        and not review_mode
        and view.session.state in {"ready", "validating"}
    ):
        if view.session.state == "validating":
            view = continue_product(output_dir)
        else:
            view = prepare_product(output_dir=output_dir, runtime=runtime)
        view = apply_default_review_policy(
            output_dir=output_dir,
            view=view,
            review_mode=False,
        )
    return view


def _status_payload(
    output_dir: Path,
    *,
    compact: bool = False,
) -> dict[str, Any] | None:
    try:
        job = product_job_status(output_dir)
    except ValueError:
        return None
    if job["state"] == "succeeded" and ProductProjectStore(output_dir).exists:
        return None
    payload: dict[str, Any] = {"job": job}
    # The worker briefly persists awaiting_review before auto-accepting in the same job.
    # Do not expose that proposal while running: the host could submit or edit prematurely.
    if (
        job["state"] != "running"
        and ProductProjectStore(output_dir).exists
    ):
        workflow = _workflow_payload(
            output_dir,
            product_status(output_dir).to_dict(),
        )
        payload["workflow"] = (
            _compact_workflow(workflow) if compact else workflow
        )
    return payload


def _workflow_payload(
    output_dir: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Add evidence scope and terminal held-out summaries without changing the workflow."""

    enriched = dict(payload)
    scopes: dict[str, str] = {}
    if payload.get("baseline_validation") is not None:
        scopes["baseline_validation"] = "optimization_minibatch"
    if payload.get("candidate_validation") is not None:
        scopes["candidate_validation"] = "optimization_minibatch"
    session = _mapping(payload.get("session"), "session")
    if (
        session.get("state") == "awaiting_review"
        and _supports_review_app(_configured_platform())
    ):
        enriched["review_presentation"] = {
            "mode": "mcp_app",
            "tool": RENDER_REVIEW_TOOL,
            "host_question_form_allowed": False,
            "instruction": (
                "Call skilladam_render_review immediately; do not use "
                "askQuestions, ask_user, multiSelect, or Quick Pick."
            ),
        }
    if session.get("state") == "completed":
        enriched["final_validation"] = _final_validation_summary(output_dir)
        scopes["final_validation"] = "heldout_terminal"
    enriched["validation_scopes"] = scopes
    return enriched


def _final_validation_summary(output_dir: Path) -> dict[str, Any]:
    store = SessionStore(output_dir, filename="final_validation.json")
    artifact = str(store.path.resolve())
    if not store.exists:
        return {
            "scope": "heldout_terminal",
            "available": False,
            "artifact": artifact,
            "error": "completed session has no final_validation.json",
        }
    payload = _mapping(store.load(), "final_validation")
    if payload.get("kind") != "heldout_final_validation":
        raise ValueError("final_validation kind is invalid")
    task_ids = payload.get("task_ids")
    if not isinstance(task_ids, list) or any(
        not isinstance(task_id, str) for task_id in task_ids
    ):
        raise ValueError("final_validation.task_ids must be an array of strings")
    validation = _mapping(payload.get("validation"), "final_validation.validation")
    summary = _compact_validation(validation, scope="heldout_terminal")
    assert summary is not None
    return {
        "available": True,
        "kind": "heldout_final_validation",
        "task_ids": list(task_ids),
        "artifact": artifact,
        **summary,
    }


def _tool_result(
    payload: Mapping[str, Any],
    *,
    text: str | None = None,
) -> dict[str, Any]:
    serialized = text or json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "content": [{"type": "text", "text": serialized}],
        "structuredContent": dict(payload),
        "isError": False,
    }


def _compact_workflow(payload: Mapping[str, Any]) -> dict[str, Any]:
    session = _mapping(payload.get("session"), "session")
    records = payload.get("records", ())
    if not isinstance(records, (list, tuple)):
        raise ValueError("records must be an array")

    compact = {
        "schema_version": "1",
        "session": {
            "session_id": session.get("session_id", ""),
            "state": session.get("state", ""),
            "iteration": session.get("iteration", 0),
            "stop_reason": session.get("stop_reason", ""),
        },
        "record_count": len(records),
        "accepted_count": sum(
            1
            for record in records
            if isinstance(record, Mapping) and record.get("accepted") is True
        ),
        "proposal": _compact_proposal(payload.get("proposal")),
        "iterations": [
            _compact_iteration(record)
            for record in records
            if isinstance(record, Mapping)
        ],
        "baseline_validation": _compact_validation(
            payload.get("baseline_validation"),
            scope="optimization_minibatch",
        ),
        "candidate_validation": _compact_validation(
            payload.get("candidate_validation"),
            scope="optimization_minibatch",
        ),
        "gate": _compact_gate(payload.get("gate")),
        "metadata": dict(_mapping(payload.get("metadata", {}), "metadata")),
        "validation_scopes": dict(
            _mapping(payload.get("validation_scopes", {}), "validation_scopes")
        ),
    }
    if payload.get("final_validation") is not None:
        compact["final_validation"] = dict(
            _mapping(payload["final_validation"], "final_validation")
        )
    if payload.get("review_presentation") is not None:
        compact["review_presentation"] = dict(
            _mapping(payload["review_presentation"], "review_presentation")
        )
    artifacts = session.get("artifacts")
    if isinstance(artifacts, Mapping):
        compact["artifacts"] = dict(artifacts)
    return compact


def _compact_iteration(record: Mapping[str, Any]) -> dict[str, Any]:
    selection = record.get("selection")
    accepted_hunks: list[Any] = []
    rejected_hunks: list[Any] = []
    actor = ""
    if isinstance(selection, Mapping):
        raw_accepted = selection.get("accepted_hunk_ids", [])
        raw_rejected = selection.get("rejected_hunk_ids", [])
        if isinstance(raw_accepted, list):
            accepted_hunks = raw_accepted
        if isinstance(raw_rejected, list):
            rejected_hunks = raw_rejected
        actor = str(selection.get("actor", ""))
    training_ids = record.get("training_case_ids")
    validation_ids = record.get("validation_case_ids")
    return {
        "iteration": record.get("iteration", 0),
        "accepted": record.get("accepted") is True,
        "reason": str(record.get("reason", "")),
        "gate": _compact_gate(record.get("gate")),
        "selection": {
            "actor": actor,
            "accepted_hunk_count": len(accepted_hunks),
            "rejected_hunk_count": len(rejected_hunks),
        },
        "same_ordered_minibatch": (
            isinstance(training_ids, list)
            and isinstance(validation_ids, list)
            and training_ids == validation_ids
        ),
        "momentum_updated": record.get("momentum_update") is not None,
        "edit_budget": record.get("edit_budget"),
    }


def _compact_proposal(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    proposal = _mapping(value, "proposal")
    hunks = proposal.get("hunks", ())
    if not isinstance(hunks, (list, tuple)):
        raise ValueError("proposal.hunks must be an array")
    return {
        "proposal_id": proposal.get("proposal_id", ""),
        "base_digest": proposal.get("base_digest", ""),
        "hunk_count": len(hunks),
    }


def _compact_validation(
    value: Any,
    *,
    scope: str | None = None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    validation = _mapping(value, "validation")
    result = {
        "candidate_digest": validation.get("candidate_digest", ""),
        "evaluation_plan_digest": validation.get(
            "evaluation_plan_digest",
            "",
        ),
        "primary": validation.get("primary"),
        "metrics": dict(_mapping(validation.get("metrics", {}), "metrics")),
    }
    if scope is not None:
        result["scope"] = scope
    return result


def _compact_gate(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    gate = _mapping(value, "gate")
    return {
        "accepted": gate.get("accepted"),
        "reason": gate.get("reason", ""),
        "baseline_validation_digest": gate.get(
            "baseline_validation_digest",
            "",
        ),
        "candidate_validation_digest": gate.get(
            "candidate_validation_digest",
            "",
        ),
    }


def _tool_definitions(platform: str | None = None) -> list[dict[str, Any]]:
    configured_platform = (
        _configured_platform() if platform is None else platform.strip().lower()
    )
    path_property = {"type": "string"}
    prepare_schema = {
        "type": "object",
        "properties": {
            "output_dir": path_property,
            "skill_path": path_property,
            "intent": {"type": "string"},
            "provider": {
                "type": "string",
                "enum": ["fixture", "openai-compatible", "platform-cli"],
            },
            "fixture": path_property,
            "model": {"type": "string"},
            "api_key_env": {"type": "string"},
            "base_url_env": {"type": "string"},
            "wire_api": {
                "type": "string",
                "enum": ["completions", "responses"],
            },
            "reasoning_effort": {"type": "string"},
            "platform": {
                "type": "string",
                "enum": [
                    "claude-code",
                    "codex",
                    "cursor",
                    "github-copilot",
                ],
            },
            "executable": path_property,
            "timeout_seconds": {
                "type": "integer",
                "minimum": 10,
                "maximum": 1800,
            },
            "task_manifest": task_manifest_schema(),
            "task_count": {"type": "integer", "minimum": 3},
            "validation_size": {"type": "integer", "minimum": 1},
            "batch_size": {"type": "integer", "minimum": 1},
            "max_iterations": {"type": "integer", "minimum": 1},
            "min_iterations": {"type": "integer", "minimum": 0},
            "max_consecutive_failures": {"type": "integer", "minimum": 1},
            "patch_attempts": {"type": "integer", "minimum": 1},
            "edit_budget_base": {"type": "integer", "minimum": 1},
            "edit_budget_minimum": {"type": "integer", "minimum": 1},
            "edit_budget_beta": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "edit_budget_v_max": {
                "type": "number",
                "exclusiveMinimum": 0,
            },
            "compact": {
                "type": "boolean",
                "description": (
                    "The default synchronous workflow returns only state and gate summaries; full artifacts remain on disk."
                ),
            },
        },
        "required": ["output_dir"],
        "additionalProperties": False,
    }
    tools = [
        {
            "name": "skilladam_discover_history",
            "description": (
                "Before the first optimization, search the current host's local history for real user input only, "
                "redact and deduplicate it, and rank it by relevance to the target skill; write the report to output_dir. "
                "The host must review candidates before constructing task_manifest."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "output_dir": path_property,
                    "skill_path": path_property,
                    "intent": {"type": "string"},
                    "platform": {
                        "type": "string",
                        "enum": sorted(HISTORY_PLATFORMS),
                        "description": (
                            "Provide only for generic MCP debugging; installed plugins automatically use the current host."
                        ),
                    },
                    "lookback_days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3650,
                        "default": DEFAULT_LOOKBACK_DAYS,
                    },
                    "max_candidates": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "default": DEFAULT_MAX_CANDIDATES,
                    },
                },
                "required": ["output_dir", "skill_path", "intent"],
                "additionalProperties": False,
            },
        },
        {
            "name": "skilladam_start",
            "description": (
                "Start or resume accept-all optimization without blocking; run to completion using the configured stopping rules. "
                "New tasks must first call skilladam_discover_history, then provide "
                "skill_path, intent, output_dir, and a host-generated task_manifest; "
                "resuming needs only output_dir. Return the job immediately, then poll skilladam_status with compact=true."
            ),
            "inputSchema": prepare_schema,
        },
        {
            "name": "skilladam_start_for_review",
            "description": (
                "Start without blocking only when the user explicitly requests review before application; pause at "
                "awaiting_review. New tasks must provide skill_path, intent, "
                "output_dir, and task_manifest, then poll with compact=true using "
                "skilladam_status."
            ),
            "inputSchema": prepare_schema,
        },
        {
            "name": "skilladam_prepare",
            "description": (
                (
                    "Default synchronous entry for Claude Code: prepare the next iteration and accept all by default. Use "
                    "compact to reduce response size; repeat until completed."
                )
                if configured_platform == "claude-code"
                else (
                    "Synchronously prepare the next iteration and accept all by default; only for compatible "
                    "hosts without MCP call time limits. Prefer skilladam_start for normal requests; use compact to reduce response size."
                )
            ),
            "inputSchema": prepare_schema,
        },
        {
            "name": "skilladam_status",
            "description": (
                "Read job and workflow state; hosts should pass compact=true. Terminal responses return only "
                "iteration, gate, and held-out final_validation summaries; full evidence remains on disk."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "output_dir": path_property,
                    "compact": {"type": "boolean"},
                },
                "required": ["output_dir"],
                "additionalProperties": False,
            },
        },
        {
            "name": "skilladam_submit_selection",
            "description": "Submit selected hunks and validate only the resulting candidate.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "output_dir": path_property,
                    "accepted_hunk_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "idempotency_key": {"type": "string"},
                    "actor": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "output_dir",
                    "accepted_hunk_ids",
                    "idempotency_key",
                ],
                "additionalProperties": False,
            },
        },
        _path_tool(
            "skilladam_continue",
            "Resume interrupted validation from the persisted checkpoint.",
        ),
    ]
    review_description = (
        "Only when the user explicitly requests review or hunk selection before application, prepare the next iteration and pause at"
        " awaiting_review; do not use this tool for ordinary optimization requests. "
    )
    if configured_platform == "github-copilot":
        review_description += (
            "After entering awaiting_review, immediately call skilladam_render_review; "
            "do not use VS Code askQuestions, ask_user, multiSelect, or Quick Pick."
        )
    tools.insert(
        1,
        {
            "name": "skilladam_prepare_for_review",
            "description": review_description,
            "inputSchema": prepare_schema,
        },
    )
    return [
        tool
        for tool in tools
        if _tool_is_available(str(tool["name"]), configured_platform)
    ]


def _configured_platform() -> str:
    return os.environ.get("SKILLADAM_PLATFORM", "").strip().lower()


def _supports_review_app(platform: str) -> bool:
    return platform in _MCP_APP_PLATFORMS


def _require_review_app(platform: str) -> None:
    if not _supports_review_app(platform):
        host = platform or "this host"
        raise ValueError(f"MCP App review is not available for {host}")


def _tool_is_available(name: str, platform: str) -> bool:
    if (
        name == "skilladam_discover_history"
        and platform
        and platform not in HISTORY_PLATFORMS
    ):
        return False
    return not (
        platform in _SYNC_ONLY_PLATFORMS and name in _ASYNC_ONLY_TOOLS
    )


def _ensure_tool_available(name: str, platform: str) -> None:
    if not _tool_is_available(name, platform):
        raise ValueError(
            f"tool {name!r} is not available for {platform}; use "
            "skilladam_prepare with compact=true"
        )


def _validate_initial_prepare_arguments(
    output_dir: Path,
    arguments: Mapping[str, Any],
) -> None:
    if ProductProjectStore(output_dir).exists:
        return
    skill_path = Path(_required_text(arguments, "skill_path"))
    intent = _required_text(arguments, "intent")
    task_manifest = _mapping(arguments.get("task_manifest"), "task_manifest")
    platform = _optional_text(arguments.get("platform")) or _configured_platform()
    if (
        platform in HISTORY_PLATFORMS
        and _optional_text(arguments.get("provider")) != "fixture"
    ):
        validate_history_discovery(
            output_dir=output_dir,
            skill_path=skill_path,
            intent=intent,
            platform=platform,
            task_manifest=task_manifest,
        )


def _history_platform(arguments: Mapping[str, Any]) -> str:
    configured = _configured_platform()
    requested = _optional_text(arguments.get("platform"))
    if configured and requested and requested != configured:
        raise ValueError(
            "history discovery platform does not match configured host"
        )
    platform = requested or configured
    if platform not in HISTORY_PLATFORMS:
        raise ValueError(
            "platform is required and must be one of "
            f"{sorted(HISTORY_PLATFORMS)}"
        )
    return platform


def _with_plugin_task_defaults(
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Provide at least three validation tasks for plugin bootstrap; keep explicit parameters."""

    normalized = dict(arguments)
    task_count = _int(
        normalized.get("task_count", DEFAULT_PLUGIN_TASK_COUNT),
        "task_count",
    )
    normalized.setdefault("task_count", task_count)
    if "validation_size" not in normalized and task_count >= 4:
        normalized["validation_size"] = DEFAULT_PLUGIN_VALIDATION_SIZE
    return normalized


def _path_tool(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {"output_dir": {"type": "string"}},
            "required": ["output_dir"],
            "additionalProperties": False,
        },
    }


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _required_text(arguments: Mapping[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _optional_path(value: Any) -> Path | None:
    text = _optional_text(value)
    return Path(text) if text is not None else None


def _optional_int(value: Any) -> int | None:
    return None if value is None else _int(value, "validation_size")


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _text_sequence(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"{field} must be an array of strings")
    return tuple(value)


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _configure_stdio(*streams: TextIO) -> None:
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict")


def entrypoint() -> None:
    _configure_stdio(sys.stdin, sys.stdout)
    serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    entrypoint()
