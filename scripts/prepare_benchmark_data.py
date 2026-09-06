#!/usr/bin/env python3
"""One-command entry point for audited external benchmark data."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skilladam.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["download-data", *sys.argv[1:]]))
