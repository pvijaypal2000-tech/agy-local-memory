#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY_BIN=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c "import sys, sqlite3; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
      PY_BIN="$cand"
      break
    fi
  fi
done

if [ -z "$PY_BIN" ]; then
  echo "[错误] 未检测到可用的 Python 3.10 及以上版本，请先安装 Python 3.10+ 后重试。"
  exit 1
fi

"$PY_BIN" "${SCRIPT_DIR}/install_helper.py" "$@"
