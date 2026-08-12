#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_python="$project_root/.venv/bin/python"

if [[ -x "$project_python" ]]; then
  python_bin="$project_python"
else
  python_bin="${PYTHON:-python3}"
fi

cd "$project_root"
exec "$python_bin" "$@"
