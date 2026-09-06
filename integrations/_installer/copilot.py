"""GitHub Copilot CLI and VS Code installation."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from .common import INTEGRATIONS_ROOT, InstallError


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
JsonRunner = Callable[[list[str]], Any]

_SECRET_ENV_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
)


def install_copilot(
    target: Path,
    python: Path,
    *,
    dry_run: bool,
    vscode_command: str | None,
    command_runner: CommandRunner,
    json_runner: JsonRunner,
) -> None:
    """Install Copilot CLI skills/MCP and optionally register VS Code user MCP."""
    skill = target / "skills" / "skilladam-optimize" / "SKILL.md"
    server = _server_definition(target, python, dry_run=dry_run)
    environment = _environment(server)
    environment.update(_existing_environment())
    environment["SKILLADAM_PLATFORM"] = "github-copilot"
    server["env"] = environment

    add_mcp = ["copilot", "mcp", "add", "skilladam"]
    for key, value in sorted(environment.items()):
        add_mcp.extend(["--env", f"{key}={value}"])
    add_mcp.extend(["--", str(python), "-m", "skilladam.product_mcp"])
    shown_add_mcp = _redacted_environment_command(add_mcp)

    vscode_add_mcp: list[str] | None = None
    shown_vscode_add_mcp: list[str] | None = None
    if vscode_command is not None:
        vscode_payload = {"name": "skilladam", **server}
        vscode_add_mcp = [
            vscode_command,
            "--add-mcp",
            json.dumps(
                vscode_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ]
        shown_vscode_add_mcp = [
            vscode_command,
            "--add-mcp",
            json.dumps(
                _redacted_server_definition(vscode_payload),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ]

    if dry_run:
        command_runner(
            add_mcp,
            dry_run=True,
            display_override=shown_add_mcp,
        )
        command_runner(
            ["copilot", "skill", "add", str(skill)],
            dry_run=True,
        )
        if vscode_add_mcp is not None:
            command_runner(
                vscode_add_mcp,
                dry_run=True,
                display_override=shown_vscode_add_mcp,
            )
        return

    _remove_legacy_plugin(command_runner, json_runner)
    existing_mcp = command_runner(
        ["copilot", "mcp", "get", "skilladam"],
        check=False,
    )
    if existing_mcp.returncode == 0:
        command_runner(["copilot", "mcp", "remove", "skilladam"])

    skills = json_runner(["copilot", "skill", "list", "--json"])
    if any(
        item.get("name") == "skilladam-optimize"
        and item.get("source") != "plugin"
        for item in skills
    ):
        command_runner(
            ["copilot", "skill", "remove", "skilladam-optimize"]
        )

    command_runner(add_mcp, display_override=shown_add_mcp)
    command_runner(["copilot", "skill", "add", str(skill)])
    if vscode_add_mcp is not None:
        command_runner(
            vscode_add_mcp,
            display_override=shown_vscode_add_mcp,
        )
        print(
            "VS Code Copilot user MCP registered; reload any open VS Code windows."
        )


def _remove_legacy_plugin(
    command_runner: CommandRunner,
    json_runner: JsonRunner,
) -> None:
    plugins = command_runner(
        ["copilot", "plugin", "list"],
        check=False,
    )
    if not any(
        line.strip().lower().startswith(("• skilladam ", "* skilladam "))
        for line in plugins.stdout.splitlines()
    ):
        return

    uninstall = command_runner(
        ["copilot", "plugin", "uninstall", "skilladam"],
        check=False,
    )
    if uninstall.returncode == 0:
        return

    active_servers = command_runner(
        ["copilot", "mcp", "list"],
        check=False,
    )
    active_skills = json_runner(["copilot", "skill", "list", "--json"])
    plugin_is_active = _has_plugin_server(
        active_servers.stdout,
        "skilladam",
    ) or any(
        item.get("name") == "skilladam-optimize"
        and item.get("source") == "plugin"
        for item in active_skills
    )
    if plugin_is_active:
        raise InstallError(
            "An older Copilot skilladam plugin is still active and could not be uninstalled. "
            "Close the Copilot/VS Code processes using it and try again."
        )
    print(
        "Ignoring stale Copilot plugin registration: "
        "its MCP and skills are no longer active."
    )


def _has_plugin_server(output: str, name: str) -> bool:
    section = ""
    expected = name.strip().lower() + " "
    for line in output.splitlines():
        value = line.strip().lower()
        if value.endswith("servers:"):
            section = value
        elif section == "plugin servers:" and value.startswith(expected):
            return True
    return False


def _server_definition(
    target: Path,
    python: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    config = target / ".vscode" / "mcp.json"
    if dry_run and not config.is_file():
        config = (
            INTEGRATIONS_ROOT
            / "github-copilot"
            / "skilladam"
            / ".vscode"
            / "mcp.json"
        )
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"Cannot read VS Code MCP configuration: {config}") from exc
    servers = payload.get("servers")
    if not isinstance(servers, dict):
        raise InstallError(f"VS Code MCP configuration is missing servers: {config}")
    server = servers.get("skilladam")
    if not isinstance(server, dict):
        raise InstallError(f"VS Code MCP configuration is missing skilladam: {config}")

    result = deepcopy(server)
    result["command"] = str(python)
    result["args"] = ["-m", "skilladam.product_mcp"]
    environment = _environment(result)
    environment["SKILLADAM_PLATFORM"] = "github-copilot"
    result["env"] = environment
    return result


def _environment(server: dict[str, Any]) -> dict[str, str]:
    environment = server.get("env", {})
    if not isinstance(environment, dict):
        raise InstallError("SkillAdam Copilot MCP env must be an object.")
    result: dict[str, str] = {}
    for key, value in environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise InstallError("SkillAdam Copilot MCP env must contain only strings.")
        result[key] = value
    return result


def _existing_environment() -> dict[str, str]:
    copilot_home = os.environ.get("COPILOT_HOME", "").strip()
    root = (
        Path(copilot_home).expanduser()
        if copilot_home
        else Path.home() / ".copilot"
    )
    config = root / "mcp-config.json"
    if not config.is_file():
        return {}
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"Copilot MCP configuration is not valid JSON: {config}") from exc
    if not isinstance(payload, dict):
        raise InstallError(f"Copilot MCP configuration must be a JSON object: {config}")
    servers = payload.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise InstallError(
            f"Copilot MCP mcpServers must be an object: {config}"
        )
    server = servers.get("skilladam")
    if not isinstance(server, dict):
        return {}
    return _environment(server)


def _is_secret_environment_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in _SECRET_ENV_MARKERS)


def _redacted_environment_command(command: list[str]) -> list[str]:
    shown = list(command)
    for index, value in enumerate(shown[:-1]):
        if value != "--env":
            continue
        key, separator, _ = shown[index + 1].partition("=")
        if separator and _is_secret_environment_key(key):
            shown[index + 1] = f"{key}=<redacted>"
    return shown


def _redacted_server_definition(server: dict[str, Any]) -> dict[str, Any]:
    shown = deepcopy(server)
    environment = shown.get("env")
    if isinstance(environment, dict):
        for key in tuple(environment):
            if isinstance(key, str) and _is_secret_environment_key(key):
                environment[key] = "<redacted>"
    return shown
