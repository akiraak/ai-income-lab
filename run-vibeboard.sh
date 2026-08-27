#!/usr/bin/env bash
# vibeboard 管理画面を起動する。
# 対象ポートが既に使われている場合は、そのプロセスを終了させてから起動する。
#
#   ./run-vibeboard.sh            # 3010 で起動
#   ./run-vibeboard.sh 3020       # ポート指定
#   VIBEBOARD_PORT=3020 ./run-vibeboard.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-${VIBEBOARD_PORT:-3010}}"
CLI="$ROOT/vibeboard/dist/cli.js"

if [ ! -f "$CLI" ]; then
  echo "[run-vibeboard] $CLI がありません。先に 'cd vibeboard && npm install' を実行してください。" >&2
  exit 1
fi

# 指定ポートを LISTEN しているプロセスの PID を返す
listeners() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
  else
    ss -lptnH "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u
  fi
}

pids="$(listeners | tr '\n' ' ')"
if [ -n "${pids// /}" ]; then
  echo "[run-vibeboard] ポート $PORT を使用中のプロセスを終了します: $pids"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true

  for _ in $(seq 1 20); do   # 最大 5 秒待つ
    sleep 0.25
    [ -z "$(listeners | tr -d '[:space:]')" ] && break
  done

  pids="$(listeners | tr '\n' ' ')"
  if [ -n "${pids// /}" ]; then
    echo "[run-vibeboard] 終了しないため強制終了します: $pids"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 0.5
  fi
fi

echo "[run-vibeboard] http://localhost:$PORT で起動します"
exec node "$CLI" --root "$ROOT" --port "$PORT"
