"""Codex SkillAdam MCP adapter with native hunk selection forms."""

from __future__ import annotations

import json
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

from skilladam.review_app import (
    REVIEW_APP_URI,
    read_review_app_resource,
    review_app_resource,
)
from skilladam.product.commands import (
    product_status,
    submit_product_selection,
)
from skilladam.product_mcp import (
    _configure_stdio,
    _error,
    _mapping,
    _required_text,
    _tool_definitions,
    _tool_result,
    _write_message,
    handle_request as handle_common_request,
)
from skilladam.product_review_mcp import (
    RENDER_REVIEW_TOOL as CODEX_RENDER_REVIEW_TOOL,
    STAGE_SELECTION_TOOL as CODEX_STAGE_SELECTION_TOOL,
    hunk_unified_diff as _hunk_unified_diff,
    render_review as _render_review,
    render_review_tool_definition as _render_review_tool_definition,
    stage_review_selection as _stage_review_selection,
    stage_selection_tool_definition as _stage_selection_tool_definition,
)


CODEX_REVIEW_TOOL = "skilladam_review_pending"
_REVIEW_BATCH_SIZE = 3
_CLIENT_UNSUPPORTED_META_KEY = "openai/client_unsupported"
_Elicit = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class _ElicitationBroker:
    """Forward responses between the stdio reader and blocked MCP tool threads."""

    def __init__(self, output_stream: TextIO, write_lock: threading.Lock) -> None:
        self._output_stream = output_stream
        self._write_lock = write_lock
        self._condition = threading.Condition()
        self._next_id = 1
        self._pending: dict[str, dict[str, Any]] = {}
        self._closed = False

    def request(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        with self._condition:
            if self._closed:
                raise RuntimeError("MCP client closed during hunk review")
            request_id = f"skilladam-elicit-{self._next_id}"
            self._next_id += 1
            pending = {"event": threading.Event(), "response": None}
            self._pending[request_id] = pending
            self._condition.notify_all()
        _write_message(
            self._output_stream,
            self._write_lock,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "elicitation/create",
                "params": dict(params),
            },
        )
        pending["event"].wait()
        response = pending["response"]
        if not isinstance(response, Mapping):
            raise RuntimeError("MCP client closed during hunk review")
        if "error" in response:
            error = response.get("error")
            if isinstance(error, Mapping):
                message = error.get("message", "elicitation failed")
            else:
                message = "elicitation failed"
            raise RuntimeError(str(message))
        return _mapping(response.get("result", {}), "elicitation result")

    def resolve(self, message: Mapping[str, Any]) -> bool:
        request_id = message.get("id")
        if not isinstance(request_id, str) or not request_id.startswith(
            "skilladam-elicit-"
        ):
            return False
        with self._condition:
            # StringIO protocol tests can read a response before its tool thread registers it.
            self._condition.wait_for(
                lambda: request_id in self._pending or self._closed,
                timeout=1,
            )
            pending = self._pending.pop(request_id, None)
        if pending is None:
            return False
        pending["response"] = dict(message)
        pending["event"].set()
        return True

    def close(self) -> None:
        with self._condition:
            self._closed = True
            pending = tuple(self._pending.values())
            self._pending.clear()
            self._condition.notify_all()
        for item in pending:
            item["event"].set()


def handle_request(
    request: Mapping[str, Any],
    *,
    elicit: _Elicit | None = None,
) -> dict[str, Any] | None:
    """Handle Codex extensions; delegate shared tools to the base MCP adapter."""

    request_id = request.get("id")
    method = request.get("method")
    try:
        if method == "initialize":
            common = handle_common_request(request)
            if common is None:
                raise RuntimeError("common MCP initialize returned no response")
            result = dict(_mapping(common.get("result", {}), "initialize result"))
            capabilities = dict(
                _mapping(result.get("capabilities", {}), "capabilities")
            )
            capabilities["resources"] = {
                "subscribe": False,
                "listChanged": False,
            }
            result["capabilities"] = capabilities
        elif method == "resources/list":
            result = {"resources": [review_app_resource()]}
        elif method == "resources/read":
            params = _mapping(request.get("params", {}), "params")
            if params.get("uri") != REVIEW_APP_URI:
                raise ValueError(f"unknown resource URI: {params.get('uri')!r}")
            result = read_review_app_resource()
        elif method == "tools/list":
            result = {
                "tools": _tool_definitions("codex")
                + [
                    _review_tool_definition(),
                    _render_review_tool_definition(),
                    _stage_selection_tool_definition(),
                ]
            }
        elif method == "tools/call":
            params = _mapping(request.get("params", {}), "params")
            name = str(params.get("name", ""))
            arguments = _mapping(params.get("arguments", {}), "arguments")
            if name == CODEX_RENDER_REVIEW_TOOL:
                result = _render_review(arguments)
            elif name == CODEX_STAGE_SELECTION_TOOL:
                result = _stage_review_selection(arguments)
            elif name != CODEX_REVIEW_TOOL:
                return handle_common_request(request)
            else:
                if elicit is None:
                    raise RuntimeError(
                        "Codex MCP elicitation is unavailable; keep the session "
                        "at awaiting_review and use skilladam_render_review on "
                        "MCP Apps hosts or explicit text hunk selection"
                    )
                result = _review_pending(arguments, elicit=elicit)
        else:
            return handle_common_request(request)
    except (OSError, RuntimeError, ValueError) as exc:
        if method == "tools/call":
            result = {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
        else:
            return _error(request_id, -32602, str(exc))
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(input_stream: TextIO, output_stream: TextIO) -> None:
    """Run the Codex stdio service with server-to-client elicitation responses."""

    write_lock = threading.Lock()
    broker = _ElicitationBroker(output_stream, write_lock)
    interactive_threads: list[threading.Thread] = []
    try:
        for line in input_stream:
            if not line.strip():
                continue
            request: Any = None
            try:
                request = json.loads(line)
                if not isinstance(request, Mapping):
                    raise ValueError("request must be a JSON object")
                if request.get("method") is None and broker.resolve(request):
                    continue
                if _is_review_call(request):
                    thread = threading.Thread(
                        target=_handle_interactive_request,
                        args=(request, broker, output_stream, write_lock),
                        name="skilladam-codex-review",
                        daemon=True,
                    )
                    interactive_threads.append(thread)
                    thread.start()
                    continue
                response = handle_request(request)
            except (json.JSONDecodeError, ValueError) as exc:
                response = _error(None, -32700, str(exc))
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                response = _error(
                    request.get("id") if isinstance(request, Mapping) else None,
                    -32603,
                    f"internal error: {type(exc).__name__}: {exc}",
                )
            if response is not None:
                _write_message(output_stream, write_lock, response)
        for thread in interactive_threads:
            thread.join(timeout=2)
    finally:
        broker.close()


def _handle_interactive_request(
    request: Mapping[str, Any],
    broker: _ElicitationBroker,
    output_stream: TextIO,
    write_lock: threading.Lock,
) -> None:
    try:
        response = handle_request(request, elicit=broker.request)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        response = _error(
            request.get("id"),
            -32603,
            f"internal error: {type(exc).__name__}: {exc}",
        )
    if response is not None:
        _write_message(output_stream, write_lock, response)


def _is_review_call(request: Mapping[str, Any]) -> bool:
    if request.get("method") != "tools/call":
        return False
    params = request.get("params")
    return isinstance(params, Mapping) and params.get("name") == CODEX_REVIEW_TOOL


def _review_pending(
    arguments: Mapping[str, Any],
    *,
    elicit: _Elicit,
) -> dict[str, Any]:
    output_dir = Path(_required_text(arguments, "output_dir"))
    idempotency_key = _required_text(arguments, "idempotency_key")
    view = product_status(output_dir)
    if view.session.state != "awaiting_review" or view.proposal is None:
        raise ValueError("session must be awaiting_review with a patch proposal")

    proposal = view.proposal
    accepted_ids: list[str] = []
    hunks = proposal.hunks
    batches = tuple(
        hunks[index : index + _REVIEW_BATCH_SIZE]
        for index in range(0, len(hunks), _REVIEW_BATCH_SIZE)
    )
    for batch_index, batch in enumerate(batches, start=1):
        properties = {
            hunk.hunk_id: {
                "type": "string",
                "title": _hunk_review_title(hunk, total=len(hunks)),
                "description": _hunk_unified_diff(hunk),
                "enum": ["accept", "reject"],
                "default": "accept",
            }
            for hunk in batch
        }
        elicited = elicit(
            {
                "mode": "form",
                "message": (
                    "Review SkillAdam patch hunks "
                    f"({batch_index}/{len(batches)}). "
                    "Choose accept or reject for every hunk below."
                ),
                "requestedSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                },
            }
        )
        action = elicited.get("action")
        if action != "accept":
            return _review_not_submitted(
                view=view,
                proposal=proposal,
                action=action,
                client_unsupported=_client_unsupported(elicited),
            )
        content = _mapping(elicited.get("content", {}), "elicitation content")
        for hunk in batch:
            decision = content.get(hunk.hunk_id)
            if decision not in {"accept", "reject"}:
                raise ValueError(
                    f"elicitation omitted a valid decision for {hunk.hunk_id}"
                )
            if decision == "accept":
                accepted_ids.append(hunk.hunk_id)

    result = submit_product_selection(
        output_dir=output_dir,
        accepted_hunk_ids=accepted_ids,
        idempotency_key=idempotency_key,
        actor=str(arguments.get("actor", "user")),
        reason=str(
            arguments.get(
                "reason",
                "selected through Codex MCP elicitation",
            )
        ),
    )
    payload = result.to_dict()
    payload["review"] = {
        "status": "submitted",
        "proposal_id": proposal.proposal_id,
        "accepted_hunk_ids": accepted_ids,
        "rejected_hunk_ids": [
            hunk.hunk_id for hunk in hunks if hunk.hunk_id not in accepted_ids
        ],
    }
    return _tool_result(payload, text=result.to_json())


def _client_unsupported(elicited: Mapping[str, Any]) -> bool:
    metadata = elicited.get("_meta")
    return (
        isinstance(metadata, Mapping)
        and metadata.get(_CLIENT_UNSUPPORTED_META_KEY) is True
    )


def _review_not_submitted(
    *,
    view: Any,
    proposal: Any,
    action: Any,
    client_unsupported: bool,
) -> dict[str, Any]:
    status = (
        "unavailable"
        if client_unsupported
        else action if action in {"decline", "cancel"} else "cancel"
    )
    payload: dict[str, Any] = {
        "review_status": status,
        "review_submitted": False,
        "fallback_required": client_unsupported,
        "proposal_id": proposal.proposal_id,
        "session": view.session.to_dict(),
        "message": (
            "Native review is unavailable on this Codex surface. No "
            "PatchSelection was recorded; do not describe any hunk as "
            "user-accepted or user-rejected. Present fallback.hunks in chat "
            "and wait for an explicit text selection before submitting."
            if client_unsupported
            else "Native review was not submitted. No PatchSelection was "
            "recorded; do not describe any hunk as user-accepted or "
            "user-rejected. The session remains awaiting_review and the "
            "source Skill is unchanged."
        ),
    }
    if client_unsupported:
        payload["fallback"] = {
            "mode": "explicit_text_hunk_selection",
            "proposal_id": proposal.proposal_id,
            "instruction": (
                "Show every hunk below in original order, then wait for the "
                "user to explicitly accept all, reject all, or name accepted "
                "hunk IDs. Do not submit a selection before that reply."
            ),
            "hunks": [
                {
                    "hunk_id": hunk.hunk_id,
                    "order": hunk.order,
                    "target_path": hunk.target_path,
                    "diff": _hunk_unified_diff(hunk),
                }
                for hunk in proposal.hunks
            ],
        }
    return _tool_result(payload)


def _hunk_review_title(hunk: Any, *, total: int) -> str:
    return f"Hunk {hunk.order + 1}/{total} - {hunk.target_path} [{hunk.hunk_id}]"


def _review_tool_definition() -> dict[str, Any]:
    return {
        "name": CODEX_REVIEW_TOOL,
        "description": (
            "Only after the user explicitly requests hunk review, show each "
            "pending diff hunk in a native Codex form when supported, collect "
            "accept/reject decisions, then submit and validate that exact "
            "selection. If the client cannot render elicitation, return every "
            "complete diff hunk for explicit text review instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_dir": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "actor": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["output_dir", "idempotency_key"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }


def entrypoint() -> None:
    _configure_stdio(sys.stdin, sys.stdout)
    serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    entrypoint()
