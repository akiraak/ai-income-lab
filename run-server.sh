#!/usr/bin/env bash
set -euo pipefail

# 管理画面（dashboard/）をローカルで起動する。
# 既にポートを掴んでいるプロセスがあれば、それを止めてから起動する。
#
#   ./run-server.sh                # .env の AIL_PORT（既定 3012）
#   ./run-server.sh --port 3019    # ポートを変える
#   ./run-server.sh --no-kill      # ⚠ 掴んでいるプロセスがあれば止めずに終わる
#   ./run-server.sh --demo         # 資格情報があってもモックのデータで出す
#
# ⚠ **プロセスは名前ではなくポートから引く。**
#   `pgrep -f 'python -m app.main'` のようなパターンは**自分自身のコマンドラインにも当たる**ため
#   （このスクリプトを起動したシェルごと巻き込む）。ここは `ss` / `lsof` で
#   「そのポートを LISTEN しているプロセス」だけを特定する。
#
# ⚠ **vibeboard（既定 3010）は触らない。** あちらは ./run-vibeboard.sh の担当。

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASH="$HERE/dashboard"
PORT=""
KILL=1
DEMO=""

usage() {
  sed -n '3,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while (( $# )); do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    --no-kill) KILL=0; shift ;;
    --demo) DEMO=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "[run-server] 知らない引数: $1" >&2; usage 2 ;;
  esac
done

[ -d "$DASH" ] || { echo "[run-server] dashboard/ が無い: $DASH" >&2; exit 1; }

# ---- ポートを決める（引数 > 環境変数 > dashboard/.env > 既定 3012）
env_value() {   # .env から key を読む（コメントと前後の空白を落とす）
  local key="$1" file="$DASH/.env" line
  [ -f "$file" ] || return 0
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | tail -1 || true)"
  [ -n "$line" ] || return 0
  line="${line#*=}"
  line="${line%%#*}"
  printf '%s' "$line" | tr -d ' \t\r"'"'"
}

if [ -z "$PORT" ]; then PORT="${AIL_PORT:-}"; fi
if [ -z "$PORT" ]; then PORT="$(env_value AIL_PORT)"; fi
if [ -z "$PORT" ]; then PORT=3012; fi

# ⚠ **ポートの取り違えは「関係ないプロセスを殺す」に直結する。** 数として妥当かをここで止める
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "[run-server] ポートが不正: '$PORT'" >&2
  exit 2
fi

BIND="${AIL_BIND:-$(env_value AIL_BIND)}"
BIND="${BIND:-127.0.0.1}"

# ---- そのポートを LISTEN しているプロセスの PID を返す
listeners() {
  local pids=""
  if command -v ss >/dev/null 2>&1; then
    pids="$(ss -ltnpH 2>/dev/null \
      | awk -v p=":$PORT" '$4 ~ p"$" {print $0}' \
      | grep -oE 'pid=[0-9]+' | cut -d= -f2 || true)"
  fi
  if [ -z "$pids" ] && command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  fi
  if [ -z "$pids" ] && command -v fuser >/dev/null 2>&1; then
    pids="$(fuser -n tcp "$PORT" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true)"
  fi
  printf '%s\n' $pids | sort -u | grep -E '^[0-9]+$' || true
}

port_busy() { [ -n "$(listeners)" ]; }

# ⚠ **自分自身と、その親をたどった系列は殺さない**（スクリプトごと落ちる）
self_chain() {
  local p=$$
  while [ "$p" -gt 1 ]; do
    printf '%s\n' "$p"
    p="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ' || true)"
    [[ "$p" =~ ^[0-9]+$ ]] || break
  done
}

# ---- ポートを空ける
free_port() {
  local pids protect pid cmd i
  pids="$(listeners)"
  [ -n "$pids" ] || return 0

  if (( ! KILL )); then
    echo "[run-server] ⚠ ポート $PORT は使用中（--no-kill なので何もしない）:" >&2
    for pid in $pids; do echo "    pid=$pid $(ps -o args= -p "$pid" 2>/dev/null | cut -c1-100)" >&2; done
    exit 1
  fi

  protect="$(self_chain)"
  for pid in $pids; do
    if [ "$pid" = "1" ] || printf '%s\n' "$protect" | grep -qx "$pid"; then
      echo "[run-server] ⚠ pid=$pid は自分自身（か親）なので止めない。ポートを変えて起動する" >&2
      exit 1
    fi
    cmd="$(ps -o args= -p "$pid" 2>/dev/null | cut -c1-100 || true)"
    echo "[run-server] ポート $PORT を掴んでいる pid=$pid を止める: ${cmd:-（コマンド不明）}"
    kill "$pid" 2>/dev/null || true
  done

  # ⚠ **止まるまで待つ。** 待たずに起動すると「アドレス使用中」で落ちる
  for i in $(seq 1 20); do
    port_busy || return 0
    sleep 0.25
  done

  for pid in $(listeners); do
    echo "[run-server] ⚠ pid=$pid が終了しないので KILL する" >&2
    kill -9 "$pid" 2>/dev/null || true
  done
  for i in $(seq 1 12); do
    port_busy || return 0
    sleep 0.25
  done

  echo "[run-server] ポート $PORT を空けられなかった（別のユーザのプロセスかもしれない）:" >&2
  for pid in $(listeners); do echo "    pid=$pid $(ps -o args= -p "$pid" 2>/dev/null | cut -c1-100)" >&2; done
  exit 1
}

free_port

# ---- 起動
cd "$DASH"
PY=".venv/bin/python"
[ -x "$PY" ] || {
  echo "[run-server] 先に: cd dashboard && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
}

export AIL_PORT="$PORT"
[ -n "$DEMO" ] && export AIL_DEMO=1

echo "[run-server] 管理画面を起動する → http://${BIND}:${PORT}/"
exec "$PY" -m app.main
