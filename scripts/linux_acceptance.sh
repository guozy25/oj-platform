#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/.." && pwd)"
cd "${project_dir}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: Linux acceptance must run on Linux." >&2
  exit 1
fi

oj_python_bin="${OJ_ACCEPTANCE_PYTHON:-python3}"
if ! command -v "${oj_python_bin}" >/dev/null 2>&1; then
  echo "ERROR: Python executable not found: ${oj_python_bin}" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required by the default judge language configuration." >&2
  exit 1
fi
if ! command -v g++ >/dev/null 2>&1; then
  echo "ERROR: g++ is required for C++14 judging." >&2
  exit 1
fi

"${oj_python_bin}" -c \
  'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'

compiler_version="$(g++ -dumpfullversion -dumpversion)"
compiler_major="${compiler_version%%.*}"
if (( compiler_major < 9 )); then
  echo "ERROR: GCC 9 or newer is required; found ${compiler_version}." >&2
  exit 1
fi

echo "Linux: $(uname -srmo)"
echo "Application Python: $("${oj_python_bin}" --version 2>&1)"
echo "Judge python3: $(python3 --version 2>&1)"
echo "Compiler: g++ ${compiler_version}"

"${oj_python_bin}" -m ruff check .
"${oj_python_bin}" -m pytest
"${oj_python_bin}" scripts/linux_smoke.py

echo "Linux acceptance completed successfully."
