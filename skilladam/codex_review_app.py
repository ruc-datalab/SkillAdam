"""Compatibility import for the shared MCP App resources."""

from __future__ import annotations

from skilladam.review_app import (
    REVIEW_APP_MIME_TYPE,
    REVIEW_APP_URI,
    read_review_app_resource,
    review_app_resource,
)


__all__ = [
    "REVIEW_APP_MIME_TYPE",
    "REVIEW_APP_URI",
    "read_review_app_resource",
    "review_app_resource",
]
