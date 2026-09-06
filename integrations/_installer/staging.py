"""Local staging for the SkillAdam runtime and platform adapters."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .common import (
    INTEGRATIONS_ROOT,
    InstallError,
    InstallOptions,
    MARKETPLACE_NAME,
    run_command,
    write_json,
)


def runtime_python_path(install_root: Path) -> Path:
    runtime = install_root / "runtime"
    if os.name == "nt":
        return runtime / "Scripts" / "python.exe"
    return runtime / "bin" / "python"


def ensure_runtime(options: InstallOptions) -> Path:
    if options.skip_engine_install:
        python = Path(sys.executable).resolve()
        if options.dry_run:
            print(f"Will reuse the current Python: {python}")
            return python
        result = run_command(
            [str(python), "-c", "import skilladam.product_mcp"],
            check=False,
        )
        if result.returncode != 0:
            raise InstallError(
                "--skip-engine-install requires SkillAdam in the current Python environment."
            )
        return python

    python = runtime_python_path(options.install_root)
    runtime = python.parent.parent
    if not python.exists():
        run_command(
            [sys.executable, "-m", "venv", str(runtime)],
            dry_run=options.dry_run,
        )
    if not options.dry_run:
        _clean_build_directory(INTEGRATIONS_ROOT.parent)
    run_command(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            f"{INTEGRATIONS_ROOT.parent}[backend]",
        ],
        dry_run=options.dry_run,
    )
    if not options.dry_run:
        run_command([str(python), "-c", "import skilladam.product_mcp"])
    return python


def _clean_build_directory(project_root: Path) -> None:
    """Remove the local setuptools build cache to avoid packaging stale modules."""

    root = project_root.resolve()
    build = root / "build"
    resolved = build.resolve()
    if resolved.parent != root:
        raise InstallError(f"Refusing to clean a build directory outside the repository: {resolved}")
    if build.is_dir():
        shutil.rmtree(build)


def adapter_target(options: InstallOptions) -> Path:
    if options.platform in {"claude-code", "codex"}:
        return (
            options.install_root
            / "marketplaces"
            / options.platform
            / "plugins"
            / "skilladam"
        )
    if options.platform == "cursor":
        return options.cursor_home / "plugins" / "local" / "skilladam"
    return options.install_root / "plugins" / "github-copilot" / "skilladam"


def _rewrite_mcp(path: Path, python: Path, platform: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    servers = payload.get("mcpServers", payload.get("servers"))
    if not isinstance(servers, dict) or "skilladam" not in servers:
        raise InstallError(f"Unrecognized SkillAdam MCP configuration: {path}")
    server = servers["skilladam"]
    server["command"] = str(python)
    module = (
        "skilladam.codex_product_mcp"
        if platform == "codex"
        else "skilladam.product_mcp"
    )
    server["args"] = ["-m", module]
    environment = server.setdefault("env", {})
    if not isinstance(environment, dict):
        raise InstallError(f"SkillAdam MCP env must be an object: {path}")
    environment["SKILLADAM_PLATFORM"] = platform
    write_json(path, payload)


def _write_claude_marketplace(root: Path) -> None:
    write_json(
        root / ".claude-plugin" / "marketplace.json",
        {
            "name": MARKETPLACE_NAME,
            "owner": {"name": "SkillAdam contributors"},
            "metadata": {
                "description": "Local SkillAdam adapter marketplace",
                "version": "0.1.0",
            },
            "plugins": [
                {
                    "name": "skilladam",
                    "source": "./plugins/skilladam",
                    "description": (
                        "Optimize Skills with selective patch review."
                    ),
                    "version": "0.1.0",
                }
            ],
        },
    )


def _write_codex_marketplace(root: Path) -> None:
    write_json(
        root / ".agents" / "plugins" / "marketplace.json",
        {
            "name": MARKETPLACE_NAME,
            "interface": {"displayName": "SkillAdam Local"},
            "plugins": [
                {
                    "name": "skilladam",
                    "source": {
                        "source": "local",
                        "path": "./plugins/skilladam",
                    },
                    "policy": {
                        "installation": "AVAILABLE",
                        "authentication": "ON_INSTALL",
                    },
                    "category": "Developer Tools",
                }
            ],
        },
    )


def stage_adapter(options: InstallOptions, python: Path) -> Path:
    source = INTEGRATIONS_ROOT / options.platform / "skilladam"
    target = adapter_target(options)
    if options.dry_run:
        print(f"Will copy adapter: {source} -> {target}")
        return target
    if not source.is_dir():
        raise InstallError(f"Adapter directory does not exist: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True)

    if options.platform in {"claude-code", "codex"}:
        _rewrite_mcp(target / ".mcp.json", python, options.platform)
        marketplace_root = target.parents[1]
        if options.platform == "claude-code":
            _write_claude_marketplace(marketplace_root)
        else:
            _write_codex_marketplace(marketplace_root)
    elif options.platform == "cursor":
        _rewrite_mcp(
            target / ".cursor" / "mcp.json",
            python,
            options.platform,
        )
    else:
        _rewrite_mcp(target / ".mcp.json", python, options.platform)
        _rewrite_mcp(
            target / ".vscode" / "mcp.json",
            python,
            options.platform,
        )
        _rewrite_mcp(
            target / "mcp-config.example.json",
            python,
            options.platform,
        )
    return target
