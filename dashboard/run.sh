#!/usr/bin/env bash
# 開発機で管理画面を起動する。既定は 127.0.0.1:3012（.env の AIL_BIND / AIL_PORT）
#
#   ./run.sh
#
cd "$(dirname "$0")"
PY=".venv/bin/python"
[ -x "$PY" ] || { echo "先に: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2; exit 1; }
exec "$PY" -m app.main
