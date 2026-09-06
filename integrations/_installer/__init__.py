"""Shared installer API."""

from .common import InstallError, InstallOptions
from .hosts import install_host_adapter, require_host
from .staging import ensure_runtime, stage_adapter

__all__ = [
    "InstallError",
    "InstallOptions",
    "ensure_runtime",
    "install_host_adapter",
    "require_host",
    "stage_adapter",
]
