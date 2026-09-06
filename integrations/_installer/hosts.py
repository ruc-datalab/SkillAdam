"""Installation wrappers for platform CLIs."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .common import (
    InstallError,
    MARKETPLACE_NAME,
    PLUGIN_ID,
    require_command,
    run_command,
    run_json,
    same_path,
    write_json,
)
from .copilot import install_copilot


HOST_COMMANDS = {
    "claude-code": "claude",
    "codex": "codex",
    "cursor": "cursor",
    "github-copilot": "copilot",
}

def _vscode_cli(*, dry_run: bool) -> str:
    configured = os.environ.get("SKILLADAM_VSCODE_COMMAND", "").strip()
    if configured:
        resolved = shutil.which(configured)
        if resolved is not None:
            return resolved
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve())
        if not dry_run:
            raise InstallError(
                "VS Code CLI set by SKILLADAM_VSCODE_COMMAND does not exist: "
                f"{configured}"
            )

    for command in ("code", "code-insiders"):
        resolved = shutil.which(command)
        if resolved is not None:
            return resolved

    if os.name == "nt":
        roots = [
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        ]
        relative_paths = (
            Path("Programs/Microsoft VS Code/bin/code.cmd"),
            Path("Programs/Microsoft VS Code Insiders/bin/code-insiders.cmd"),
            Path("Microsoft VS Code/bin/code.cmd"),
            Path("Microsoft VS Code Insiders/bin/code-insiders.cmd"),
        )
        for root in roots:
            if not root:
                continue
            for relative in relative_paths:
                candidate = Path(root) / relative
                if candidate.is_file():
                    return str(candidate.resolve())

    if dry_run:
        return "code"
    raise InstallError(
        "VS Code CLI was not found. In VS Code, run Shell Command: Install "
        "'code' command in PATH, or set SKILLADAM_VSCODE_COMMAND."
    )


def require_host(
    platform: str,
    *,
    dry_run: bool,
    install_vscode: bool = True,
) -> None:
    if platform == "cursor":
        if dry_run:
            return
        commands = ("cursor-agent", "cursor")
        if any(shutil.which(command) for command in commands):
            return
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            cursor_agent = Path(local_app_data) / "cursor-agent"
            if any(
                (cursor_agent / filename).is_file()
                for filename in ("cursor-agent.exe", "cursor-agent.cmd")
            ):
                return
        raise InstallError(
            "Cursor Agent was not found. Install Cursor CLI and make cursor-agent "
            "or cursor available in PATH."
        )
    require_command(HOST_COMMANDS[platform], dry_run=dry_run)
    if platform == "github-copilot" and install_vscode:
        _vscode_cli(dry_run=dry_run)


def _install_claude(target: Path, *, dry_run: bool) -> None:
    marketplace_root = target.parents[1]
    if dry_run:
        run_command(
            ["claude", "plugin", "marketplace", "add", str(marketplace_root)],
            dry_run=True,
        )
        run_command(
            ["claude", "plugin", "install", PLUGIN_ID, "--scope", "user"],
            dry_run=True,
        )
        return

    marketplaces = run_json(
        ["claude", "plugin", "marketplace", "list", "--json"]
    )
    exists = any(
        item.get("name") == MARKETPLACE_NAME for item in marketplaces
    )
    if exists:
        run_command(
            [
                "claude",
                "plugin",
                "marketplace",
                "update",
                MARKETPLACE_NAME,
            ]
        )
    else:
        run_command(
            ["claude", "plugin", "marketplace", "add", str(marketplace_root)]
        )

    plugins = run_json(["claude", "plugin", "list", "--json"])
    installed = any(item.get("id") == PLUGIN_ID for item in plugins)
    if installed:
        # Claude caches local plugins by version. Uninstall and reinstall
        # to deploy the current staged adapter even when its version is unchanged.
        run_command(
            ["claude", "plugin", "uninstall", PLUGIN_ID, "--scope", "user"]
        )
    run_command(
        ["claude", "plugin", "install", PLUGIN_ID, "--scope", "user"]
    )


def _install_codex(target: Path, *, dry_run: bool) -> None:
    marketplace_root = target.parents[1]
    if dry_run:
        run_command(
            ["codex", "plugin", "marketplace", "add", str(marketplace_root)],
            dry_run=True,
        )
        run_command(
            ["codex", "plugin", "add", PLUGIN_ID],
            dry_run=True,
        )
        return

    payload = run_json(["codex", "plugin", "marketplace", "list", "--json"])
    marketplaces = payload.get("marketplaces", [])
    existing = next(
        (
            item
            for item in marketplaces
            if item.get("name") == MARKETPLACE_NAME
        ),
        None,
    )
    if existing is None:
        run_command(
            [
                "codex",
                "plugin",
                "marketplace",
                "add",
                str(marketplace_root),
            ]
        )
    elif not same_path(str(existing.get("root", "")), marketplace_root):
        raise InstallError(
            f"A Codex marketplace with this name already uses a different path: {existing.get('root')}"
        )

    plugins = run_json(["codex", "plugin", "list", "--json", "--available"])
    installed = any(
        item.get("pluginId") == PLUGIN_ID
        for item in plugins.get("installed", [])
    )
    if installed:
        # Codex also caches local plugins by version; reinstall to refresh
        # staged MCP configuration and skill content.
        run_command(["codex", "plugin", "remove", PLUGIN_ID])
    run_command(["codex", "plugin", "add", PLUGIN_ID])


def _install_cursor(target: Path, python: Path, *, dry_run: bool) -> None:
    cursor_home = target.parents[2]
    source_config = target / ".cursor" / "mcp.json"
    global_config = cursor_home / "mcp.json"
    source_skill = target / "skills" / "skilladam-optimize"
    global_skill = cursor_home / "skills" / "skilladam-optimize"

    if dry_run:
        print(f"Will merge Cursor MCP configuration: {source_config} -> {global_config}")
        print(f"Will install Cursor skill: {source_skill} -> {global_skill}")
        return

    source_payload = json.loads(source_config.read_text(encoding="utf-8"))
    source_servers = source_payload.get("mcpServers")
    if not isinstance(source_servers, dict):
        raise InstallError(f"Unrecognized Cursor MCP configuration: {source_config}")

    if global_config.is_file():
        try:
            payload = json.loads(global_config.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InstallError(
                f"Cursor global MCP configuration is not valid JSON: {global_config}"
            ) from exc
        if not isinstance(payload, dict):
            raise InstallError(
                f"Cursor global MCP configuration must be a JSON object: {global_config}"
            )
    else:
        payload = {}

    servers = payload.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise InstallError(
            f"Cursor global MCP mcpServers must be an object: {global_config}"
        )
    servers["skilladam"] = source_servers["skilladam"]
    write_json(global_config, payload)
    shutil.copytree(source_skill, global_skill, dirs_exist_ok=True)
    print(f"Cursor local plugin directory: {target}")


def install_host_adapter(
    platform: str,
    target: Path,
    python: Path,
    *,
    dry_run: bool,
    install_vscode: bool = True,
) -> None:
    if platform == "claude-code":
        _install_claude(target, dry_run=dry_run)
    elif platform == "codex":
        _install_codex(target, dry_run=dry_run)
    elif platform == "cursor":
        _install_cursor(target, python, dry_run=dry_run)
    else:
        vscode_command = (
            _vscode_cli(dry_run=dry_run) if install_vscode else None
        )
        install_copilot(
            target,
            python,
            dry_run=dry_run,
            vscode_command=vscode_command,
            command_runner=run_command,
            json_runner=run_json,
        )
