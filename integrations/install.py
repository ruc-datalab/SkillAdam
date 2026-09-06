"""Install SkillAdam adapters for the four supported platforms."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

if __package__:
    from ._installer import (
        InstallError,
        InstallOptions,
        ensure_runtime,
        install_host_adapter,
        require_host,
        stage_adapter,
    )
    from ._installer.common import PLATFORMS, configure_console_encoding
else:
    from _installer import (  # type: ignore[no-redef]
        InstallError,
        InstallOptions,
        ensure_runtime,
        install_host_adapter,
        require_host,
        stage_adapter,
    )
    from _installer.common import (  # type: ignore[no-redef]
        PLATFORMS,
        configure_console_encoding,
    )


def install(options: InstallOptions) -> Path:
    if not options.stage_only:
        require_host(
            options.platform,
            dry_run=options.dry_run,
            install_vscode=not options.skip_vscode_registration,
        )

    python = ensure_runtime(options)
    target = stage_adapter(options, python)
    if options.stage_only:
        print(f"Staged adapter: {target}")
        return target

    install_host_adapter(
        options.platform,
        target,
        python,
        dry_run=options.dry_run,
        install_vscode=not options.skip_vscode_registration,
    )
    print(f"SkillAdam {options.platform} adapter installed.")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the SkillAdam engine and platform adapter."
    )
    parser.add_argument("platform", choices=PLATFORMS)
    parser.add_argument(
        "--install-root",
        type=Path,
        default=Path.home() / ".skilladam",
        help="Installation root for the runtime and local plugins.",
    )
    parser.add_argument(
        "--cursor-home",
        type=Path,
        default=Path(os.environ.get("CURSOR_HOME", Path.home() / ".cursor")),
        help="Cursor configuration directory.",
    )
    parser.add_argument(
        "--skip-engine-install",
        action="store_true",
        help="Reuse SkillAdam installed in the current Python environment.",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="Stage and configure the adapter without running host installation commands.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview file copies and commands without writing files.",
    )
    parser.add_argument(
        "--skip-vscode-registration",
        action="store_true",
        help=(
            "Install only Copilot CLI MCP and skills; skip VS Code user MCP registration."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_console_encoding()
    args = build_parser().parse_args(argv)
    options = InstallOptions(
        platform=args.platform,
        install_root=args.install_root.expanduser().resolve(),
        cursor_home=args.cursor_home.expanduser().resolve(),
        skip_engine_install=args.skip_engine_install,
        stage_only=args.stage_only,
        dry_run=args.dry_run,
        skip_vscode_registration=args.skip_vscode_registration,
    )
    try:
        install(options)
    except InstallError as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
