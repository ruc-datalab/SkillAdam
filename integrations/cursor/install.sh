#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
installer=$(CDPATH= cd -- "$script_dir/.." && pwd)/install.py
python_bin=${SKILLADAM_INSTALL_PYTHON:-python3}

exec "$python_bin" "$installer" cursor "$@"
