"""Shared MCP App hunk review tools for the product workflow."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from skilladam.product.commands import product_status, stage_product_selection
from skilladam.product.jobs import ensure_product_job_idle, product_operation_lock
from skilladam.product.patch_review import render_patch_hunks
from skilladam.review_app import REVIEW_APP_URI


RENDER_REVIEW_TOOL = "skilladam_render_review"
STAGE_SELECTION_TOOL = "skilladam_stage_selection"


def render_review(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return the authoritative proposal snapshot for MCP App rendering."""

    output_dir = Path(_required_text(arguments, "output_dir"))
    view = product_status(output_dir)
    if view.session.state != "awaiting_review" or view.proposal is None:
        return _tool_result(
            {
                "schema_version": "1",
                "review_status": view.session.state,
                "review_submitted": True,
                "session": view.session.to_dict(),
            }
        )

    proposal = view.proposal
    hunks = [
        {
            "hunk_id": hunk.hunk_id,
            "order": hunk.order,
            "target_path": hunk.target_path,
            "diff": hunk_unified_diff(hunk),
        }
        for hunk in proposal.hunks
    ]
    payload = {
        "schema_version": "1",
        "review_status": "awaiting_user",
        "review_submitted": False,
        "session": view.session.to_dict(),
        "review_app": {
            "mode": "selective_patch_review",
            "output_dir": str(output_dir),
            "proposal_id": proposal.proposal_id,
            "default_decision": "accept",
            "submit_tool": STAGE_SELECTION_TOOL,
            "status_tool": "skilladam_status",
            "continue_tool": "skilladam_continue",
            "hunks": hunks,
        },
        "fallback": {
            "mode": "explicit_text_hunk_selection",
            "proposal_id": proposal.proposal_id,
            "hunks": hunks,
        },
    }
    return _tool_result(payload)


def stage_review_selection(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Persist MCP App choices promptly; defer validation to host resume."""

    output_dir = Path(_required_text(arguments, "output_dir"))
    selection_mode = str(arguments.get("selection_mode", "explicit"))
    if selection_mode not in {"explicit", "all", "none"}:
        raise ValueError("selection_mode must be explicit, all, or none")
    accepted_hunk_ids = (
        _explicit_hunk_ids(arguments)
        if selection_mode == "explicit"
        else None
    )
    with product_operation_lock(output_dir):
        ensure_product_job_idle(output_dir)
        if selection_mode != "explicit":
            view = product_status(output_dir)
            if view.proposal is None:
                raise ValueError("session has no patch proposal")
            known_hunk_ids = [hunk.hunk_id for hunk in view.proposal.hunks]
            if selection_mode == "all":
                accepted_hunk_ids = known_hunk_ids
            else:
                accepted_hunk_ids = []
        if accepted_hunk_ids is None:
            raise ValueError(
                "accepted_hunk_ids is required for selection_mode=explicit"
            )
        result = stage_product_selection(
            output_dir=output_dir,
            accepted_hunk_ids=accepted_hunk_ids,
            idempotency_key=_required_text(arguments, "idempotency_key"),
            actor=str(arguments.get("actor", "user")),
            reason=str(arguments.get("reason", "")),
        )
    payload = result.to_dict()
    payload["review_status"] = "submitted"
    payload["review_submitted"] = True
    payload["selection_mode"] = selection_mode
    return _tool_result(payload, text=result.to_json())


def hunk_unified_diff(hunk: Any) -> str:
    return render_patch_hunks((hunk,))


def render_review_tool_definition() -> dict[str, Any]:
    return {
        "name": RENDER_REVIEW_TOOL,
        "title": "Review SkillAdam patch",
        "description": (
            "Required review UI for an awaiting-review SkillAdam patch in "
            "VS Code Copilot Chat. Call this immediately instead of "
            "askQuestions, ask_user, multiSelect, or Quick Pick. The MCP App "
            "renders every complete hunk with line-level add/delete colors "
            "and exact stable-ID selection; the result also contains every "
            "complete hunk for text fallback."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"output_dir": {"type": "string"}},
            "required": ["output_dir"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "_meta": {
            "ui": {
                "resourceUri": REVIEW_APP_URI,
                "visibility": ["model", "app"],
            },
            "openai/outputTemplate": REVIEW_APP_URI,
            "openai/toolInvocation/invoking": "Loading patch review...",
            "openai/toolInvocation/invoked": "Patch review ready.",
        },
    }


def stage_selection_tool_definition() -> dict[str, Any]:
    return {
        "name": STAGE_SELECTION_TOOL,
        "title": "Submit SkillAdam patch selection",
        "description": (
            "Persist an MCP App hunk selection without triggering a model "
            "follow-up. selection_mode=all is exactly equivalent to accepting "
            "every authoritative proposal hunk; none rejects every hunk; "
            "both ignore accepted_hunk_ids values added by the host. explicit "
            "requires accepted_hunk_ids."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_dir": {"type": "string"},
                "accepted_hunk_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "selection_mode": {
                    "type": "string",
                    "enum": ["explicit", "all", "none"],
                    "default": "explicit",
                },
                "idempotency_key": {"type": "string"},
                "actor": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": [
                "output_dir",
                "idempotency_key",
            ],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    }


def _required_text(arguments: Mapping[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _explicit_hunk_ids(arguments: Mapping[str, Any]) -> list[str] | None:
    value = arguments.get("accepted_hunk_ids")
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("accepted_hunk_ids must be an array of strings")
    return value


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


__all__ = [
    "RENDER_REVIEW_TOOL",
    "STAGE_SELECTION_TOOL",
    "hunk_unified_diff",
    "render_review",
    "render_review_tool_definition",
    "stage_review_selection",
    "stage_selection_tool_definition",
]
