#!/usr/bin/env bash
set -euo pipefail

# 管理画面（titan か 13500t の dashboard）を、別の機械から ssh のトンネル越しに見る。
#
#   ./run-dashboard-tunnel.sh                    # http://127.0.0.1:3013/ → titan の 127.0.0.1:3012
#   ./run-dashboard-tunnel.sh --port 3014        # 手元のポートを変える
#   ./run-dashboard-tunnel.sh --host 13500t      # 繋ぎ先を変える（⚠ 13500t へは Sx360 からだけ届く。名前は Sx360 の ~/.ssh/config のもの）
#   ./run-dashboard-tunnel.sh --remote-port 3019 # 向こうのポートを変える
#   ./run-dashboard-tunnel.sh --no-kill          # ⚠ ポートが塞がっていたら止めずに終わる
#
# ⚠ **走らせるのは見る側の機械（Sx360 など）。titan では要らない**（titan なら http://127.0.0.1:3012/ で直接見る）。
# ⚠ **titan 側は何も変えない。** 管理画面は titan のループバックにだけ口を開けている（`AIL_BIND=127.0.0.1`）ので、
#   IP や Tailscale の名前では開かない。CLAUDE.md が指定している見方がこのトンネル。
# ⚠ **dashboard（3012）を `tailscale serve` に出さない。** serve 経由は接続元が全部ループバックに見えるため、
#   tailnet の誰でもローカル面（停止・解除）が無認証で開く。
#
# 止めるのは Ctrl+C。

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAG="[run-dashboard-tunnel]"
PORT=""            # 手元のポート
REMOTE_PORT=""     # titan 側のポート
HOST="titan"
KILL=1

usage() {
  sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while (( $# )); do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    --remote-port) REMOTE_PORT="${2:-}"; shift 2 ;;
    --remote-port=*) REMOTE_PORT="${1#*=}"; shift ;;
    --host) HOST="${2:-}"; shift 2 ;;
    --host=*) HOST="${1#*=}"; shift ;;
    --no-kill) KILL=0; shift ;;
    -h|--help) usage 0 ;;
    *) echo "$TAG 知らない引数: $1" >&2; usage 2 ;;
  esac
done

# ---- 向こうのポート（引数 > 環境変数 > dashboard/.env > 既定 3012）
env_value() {   # .env から key を読む（コメントと前後の空白を落とす）
  local key="$1" file="$HERE/dashboard/.env" line
  [ -f "$file" ] || return 0
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | tail -1 || true)"
  [ -n "$line" ] || return 0
  line="${line#*=}"
  line="${line%%#*}"
  printf '%s' "$line" | tr -d ' \t\r"'"'"
}

[ -n "$PORT" ] || PORT=3013
[ -n "$REMOTE_PORT" ] || REMOTE_PORT="${AIL_PORT:-}"
[ -n "$REMOTE_PORT" ] || REMOTE_PORT="$(env_value AIL_PORT)"
[ -n "$REMOTE_PORT" ] || REMOTE_PORT=3012

# ⚠ **ポートの取り違えは「関係ないプロセスを殺す」に直結する。** 数として妥当かをここで止める
for p in "$PORT" "$REMOTE_PORT"; do
  if ! [[ "$p" =~ ^[0-9]+$ ]] || (( p < 1 || p > 65535 )); then
    echo "$TAG ポートが不正: '$p'" >&2
    exit 2
  fi
done
[ -n "$HOST" ] || { echo "$TAG --host が空" >&2; exit 2; }

command -v ssh >/dev/null 2>&1 || { echo "$TAG ssh が無い" >&2; exit 2; }

# ---- そのポートを LISTEN しているプロセスの PID（⚠ 名前ではなくポートから引く）
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

# ⚠ **自分自身と、その親をたどった系列は殺さない**（スクリプトごと落ちる）
self_chain() {
  local p=$$
  while [ "$p" -gt 1 ]; do
    printf '%s\n' "$p"
    p="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ' || true)"
    [[ "$p" =~ ^[0-9]+$ ]] || break
  done
}

# ---- ポートを空ける（⚠ 自動で止めるのは古い ssh のトンネルだけ。手元の関係ないプロセスは殺さない）
free_port() {
  local pids protect pid cmd i
  pids="$(listeners)"
  [ -n "$pids" ] || return 0

  protect="$(self_chain)"
  for pid in $pids; do
    cmd="$(ps -o args= -p "$pid" 2>/dev/null | cut -c1-120 || true)"
    if [ "$pid" = "1" ] || printf '%s\n' "$protect" | grep -qx "$pid"; then
      echo "$TAG ⚠ pid=$pid は自分自身（か親）なので止めない。--port でポートを変える" >&2
      exit 1
    fi
    if (( ! KILL )); then
      echo "$TAG ⚠ ポート $PORT は使用中（--no-kill なので何もしない）: pid=$pid ${cmd:-（コマンド不明）}" >&2
      exit 1
    fi
    case "$cmd" in
      *ssh*) : ;;
      *) echo "$TAG ⚠ ポート $PORT を掴んでいるのは ssh のトンネルではない: pid=$pid ${cmd:-（コマンド不明）}" >&2
         echo "$TAG    自動では止めない。止めてよいなら手で kill するか、--port でポートを変える" >&2
         exit 1 ;;
    esac
    echo "$TAG 古いトンネルを止める: pid=$pid $cmd"
    kill "$pid" 2>/dev/null || true
  done

  # ⚠ **止まるまで待つ。** 待たずに張ると「アドレス使用中」で落ちる
  for i in $(seq 1 20); do
    [ -n "$(listeners)" ] || return 0
    sleep 0.25
  done
  echo "$TAG ⚠ ポート $PORT が空かない" >&2
  exit 1
}

free_port

URL="http://127.0.0.1:${PORT}/"

echo "$TAG ${HOST} の 127.0.0.1:${REMOTE_PORT} → 手元の ${PORT} に繋ぐ"

# ⚠ ssh は後ろで起こし、繋がったのを確かめてからアドレスを出す（繋がる前に URL を出して「開けない」と言わせない）
ssh -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -L "127.0.0.1:${PORT}:127.0.0.1:${REMOTE_PORT}" \
    "$HOST" &
SSH_PID=$!

cleanup() {
  kill "$SSH_PID" 2>/dev/null || true
  wait "$SSH_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ---- 繋がるのを待つ（最大 15 秒）
ready=0
for i in $(seq 1 60); do
  if ! kill -0 "$SSH_PID" 2>/dev/null; then
    echo "$TAG ⚠ ssh が終了した（${HOST} に繋がらない・鍵・ポートの衝突など。上の ssh のメッセージを読む）" >&2
    wait "$SSH_PID" 2>/dev/null || true
    exit 1
  fi
  if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then ready=1; break; fi
  sleep 0.25
done

if (( ! ready )); then
  echo "$TAG ⚠ 15 秒たってもポート ${PORT} が開かない" >&2
  exit 1
fi

# ---- 向こうの管理画面が応答するか（curl があれば）
status=""
if command -v curl >/dev/null 2>&1; then
  status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL" || true)"
fi

echo
echo "  ========================================================"
echo "    接続先:  $URL"
echo "  ========================================================"
echo
case "$status" in
  200) echo "  ✅ 管理画面が応答した（HTTP 200）" ;;
  "")  echo "  … curl が無いので確かめていない。ブラウザで開く" ;;
  000) echo "  ⚠ 応答が無い。${HOST} で管理画面が動いているか確かめる:"
       if [ "$HOST" = "titan" ]; then
         echo "       ssh ${HOST} 'cd ~/ai-income-lab && ./run-server.sh'"
       else
         echo "       ${HOST} では常駐コンテナ（g3plus-ops の ail-dashboard。docs/specs/dashboard.md §7-1）"
       fi ;;
  403) echo "  ⚠ HTTP 403（面に弾かれた）。${HOST} の dashboard/.env の AIL_AUTH_MODE を確かめる"
       echo "     （コンテナなら network_mode: host か。ブリッジ越しはループバックに見えない ＝ dashboard.md §7-1）" ;;
  *)   echo "  ⚠ HTTP $status が返った" ;;
esac
echo
echo "  面: ローカル面（監視 ＋ 記録 ＋ 判定 ＋ 停止・解除）"
echo "      ⚠ 管理画面に発注の経路は無い（発注は執行器と CLI だけ）"
echo
echo "  止めるのは Ctrl+C"
echo

wait "$SSH_PID"
