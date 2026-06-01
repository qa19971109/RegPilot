#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${REGPILOT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
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
"$VENV_DIR/bin/python" -m compileall -q src tests
PYTHONPATH=src "$VENV_DIR/bin/python" -m unittest discover -s tests -p 'test*.py'
