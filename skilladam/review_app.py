"""MCP App resources for selective SkillAdam patch review."""

from __future__ import annotations

from importlib.resources import files
from typing import Any


REVIEW_APP_URI = "ui://skilladam/hunk-review/v1.html"
REVIEW_APP_MIME_TYPE = "text/html;profile=mcp-app"


def review_app_resource() -> dict[str, Any]:
    """Return UI resource declarations discoverable by MCP hosts."""

    return {
        "uri": REVIEW_APP_URI,
        "name": "skilladam-hunk-review",
        "title": "SkillAdam Patch Review",
        "description": "Review and submit exact SkillAdam patch hunks.",
        "mimeType": REVIEW_APP_MIME_TYPE,
    }


def read_review_app_resource() -> dict[str, Any]:
    """Read self-contained MCP App HTML without external network dependencies."""

    html = (
        files("skilladam.ui")
        .joinpath("codex_review.html")
        .read_text(encoding="utf-8")
    )
    return {
        "contents": [
            {
                "uri": REVIEW_APP_URI,
                "mimeType": REVIEW_APP_MIME_TYPE,
                "text": html,
                "_meta": {
                    "ui": {
                        "prefersBorder": True,
                        "csp": {
                            "connectDomains": [],
                            "resourceDomains": [],
                            "frameDomains": [],
                            "baseUriDomains": [],
                        },
                    }
                },
            }
        ]
    }


__all__ = [
    "REVIEW_APP_MIME_TYPE",
    "REVIEW_APP_URI",
    "read_review_app_resource",
    "review_app_resource",
]
