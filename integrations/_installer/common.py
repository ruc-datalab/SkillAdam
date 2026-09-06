"""Shared installer models and command utilities."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
INTEGRATIONS_ROOT = REPO_ROOT / "integrations"
PLATFORMS = ("claude-code", "codex", "cursor", "github-copilot")
MARKETPLACE_NAME = "skilladam-local"
PLUGIN_ID = f"skilladam@{MARKETPLACE_NAME}"


class InstallError(RuntimeError):
    """An installation prerequisite or host command failed."""


@dataclass(frozen=True)
class InstallOptions:
    platform: str
    install_root: Path
    cursor_home: Path
    skip_engine_install: bool
    stage_only: bool
    dry_run: bool
    skip_vscode_registration: bool = False


def configure_console_encoding() -> None:
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def display_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def run_command(
    command: Sequence[str],
    *,
    dry_run: bool = False,
    check: bool = True,
    display_override: Sequence[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    shown_command = display_override or command
    shown = display_command(shown_command)
    print(f"+ {shown}")
    if dry_run:
        return subprocess.CompletedProcess(command, 0, "", "")
    resolved = list(command)
    executable = shutil.which(resolved[0])
    if executable is not None:
        resolved[0] = executable
    result = subprocess.run(
        resolved,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)
    if check and result.returncode != 0:
        raise InstallError(
            f"Command failed (exit code {result.returncode}): "
            f"{shown}"
        )
    return result


def run_json(command: Sequence[str]) -> Any:
    result = run_command(command)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InstallError(
            f"Host command did not return valid JSON: {display_command(command)}"
        ) from exc


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def same_path(left: str, right: Path) -> bool:
    try:
        return Path(left).resolve() == right.resolve()
    except OSError:
        return False


def require_command(command: str, *, dry_run: bool) -> None:
    if dry_run or shutil.which(command) is not None:
        return
    raise InstallError(
        f"Host command {command!r} was not found. Install the platform and add its command to PATH."
    )
