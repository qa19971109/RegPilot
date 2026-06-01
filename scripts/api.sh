#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${REGPILOT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HOST="${REGPILOT_HOST:-0.0.0.0}"
PORT="${REGPILOT_PORT:-8766}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$PROJECT_DIR"
VENV_DIR="${REGPILOT_VENV:-}"
if [ -z "$VENV_DIR" ]; then
  if [ -x .venv-linux312/bin/python ]; then
    VENV_DIR=".venv-linux312"
  elif [ -x .venv-linux/bin/python ]; then
    VENV_DIR=".venv-linux"
  elif [ -x .venv_linux/bin/python ]; then
    VENV_DIR=".venv_linux"
  else
    VENV_DIR=".venv"
  fi
fi
if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
"$VENV_DIR/bin/python" -m pip install -e .
exec "$VENV_DIR/bin/python" -m regpilot.api --host "$HOST" --port "$PORT"
