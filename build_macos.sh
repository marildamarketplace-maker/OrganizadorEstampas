#!/bin/bash
set -e
cd "$(dirname "$0")"
# Atalho compatível: o Python identifica o SO e escolhe o build nativo.
if [ -f .venv/Scripts/python.exe ]; then
  exec .venv/Scripts/python.exe build_app.py
elif [ -x .venv/bin/python ]; then
  exec .venv/bin/python build_app.py
elif command -v python3 >/dev/null 2>&1; then
  exec python3 build_app.py
elif command -v python >/dev/null 2>&1; then
  exec python build_app.py
elif command -v py >/dev/null 2>&1; then
  exec py -3 build_app.py
else
  echo "ERRO: Python 3 não encontrado." >&2
  exit 1
fi
