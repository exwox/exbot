#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$script_dir/run_python.sh" \
  -m unittest discover -s tests -p 'test_*.py' -v
