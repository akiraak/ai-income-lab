#!/usr/bin/env bash
set -euo pipefail

# 独自タブ 7 本の中身（dashboard/vibetab.py。127.0.0.1:3015）を vibeboard 無しで起こす。
# tmux の中で前景のまま動かし続ける（デーモンにしない・nohup しない）。
# project-manager の Terminal のタブの「サーバ」の端末が、Sx360 でも titan（ssh 越し）でもこれを叩く。
#
#   ./run-vibetab.sh                 # ポートは AIL_VIBETAB_PORT、無ければ vibetab.py の既定（3015）
#   ./run-vibetab.sh 3016            # 第 1 引数がポート
#   ./run-vibetab.sh --runs-dir … --exp-dir …   # そのほかの引数は vibetab.py にそのまま渡す
#
# ⚠ **vibetab.py はポートが塞がっていると「先に居るものに任せる」で終了コード 0 で引く**
#   （vibeboard の sidecar との競走を想定した振る舞い。ここでは変えない）。tmux の中で静かに
#   消えると困るので、起こす前に LISTEN しているプロセスを引いて、居れば 1 行出して終了コード 1 で止まる。
#   ⚠ 止めはしない（ポートを掴んでいるものには触らない）。
# ⚠ **python3 は 3.11 以上**（vibetab.py が tomllib を読む）。足りなければ版を書いて終了コード 1。

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="dashboard/vibetab.py"
cd "$HERE"

usage() {
  sed -n '4,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

[ -f "$SCRIPT" ] || { echo "[run-vibetab] 無い: $HERE/$SCRIPT" >&2; exit 1; }

# ---- ポートを決める（第 1 引数 > AIL_VIBETAB_PORT > vibetab.py の既定）
PORT=""
PASS=()
if (( $# )); then
  case "$1" in
    -h|--help) usage 0 ;;
    --*) ;;                                   # 第 1 引数が --runs-dir などなら、ポートは引数にない
    *) PORT="$1"; shift ;;
  esac
fi
PASS=("$@")

if [ -z "$PORT" ]; then PORT="${AIL_VIBETAB_PORT:-}"; fi
PORT_FROM_SCRIPT=0
if [ -z "$PORT" ]; then
  # vibetab.py の既定を写す（渡さずに起こす ＝ 既定は vibetab.py のもの。ここでは表示と塞がりの確認に使うだけ）
  PORT="$(grep -E '^DEFAULT_PORT *= *[0-9]+' "$SCRIPT" | head -1 | grep -oE '[0-9]+' || true)"
  PORT="${PORT:-3015}"
  PORT_FROM_SCRIPT=1
fi

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "[run-vibetab] ポートが不正: '$PORT'" >&2
  exit 2
fi

# ---- python3 が 3.11 以上か
command -v python3 >/dev/null 2>&1 || { echo "[run-vibetab] python3 が無い" >&2; exit 1; }
PYVER="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "[run-vibetab] python3 は 3.11 以上が要る（tomllib）。いまは $PYVER" >&2
  exit 1
fi

# ---- そのポートを LISTEN しているプロセスを引く（⚠ 名前ではなくポートから。止めはしない）
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
  printf '%s\n' $pids | sort -u | grep -E '^[0-9]+$' || true
}

BUSY="$(listeners)"
if [ -n "$BUSY" ]; then
  echo "[run-vibetab] ⚠ ポート $PORT は先に居るものが使っています（vibetab.py はこのまま起こすと何も言わず終了コード 0 で引く）:" >&2
  for pid in $BUSY; do echo "    pid=$pid $(ps -o args= -p "$pid" 2>/dev/null | cut -c1-100)" >&2; done
  exit 1
fi

echo "[run-vibetab] http://127.0.0.1:$PORT/ で起こします（python3 $PYVER・前景。止めるのは Ctrl+C）"
if (( PORT_FROM_SCRIPT )); then
  exec python3 "$SCRIPT" ${PASS[@]+"${PASS[@]}"}
else
  exec python3 "$SCRIPT" --port "$PORT" ${PASS[@]+"${PASS[@]}"}
fi
