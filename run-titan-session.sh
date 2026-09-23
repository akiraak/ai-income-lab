#!/usr/bin/env bash
set -euo pipefail

# titan の tmux（既定 `ail`）に入る。無ければ作って、その中で Claude Code を起こす。
#
#   ./run-titan-session.sh                  # ssh titan → tmux new -A -s ail（無ければ claude を起こす）
#   ./run-titan-session.sh --pull           # 新しく作るときだけ、先に git pull --ff-only
#   ./run-titan-session.sh --session work   # tmux のセッション名を変える
#   ./run-titan-session.sh --host titan-lan # 繋ぎ先を変える（LAN から）
#   ./run-titan-session.sh --shell          # 新しく作るときに claude を起こさない（シェルだけ）
#
# ⚠ **走らせるのは Sx360（端末）**。titan で叩いたら ssh せずにその場の tmux に入る。
# ⚠ **作業（コード・計算・コミット）は titan の Claude で**。ただし 13500t と g3plus-ops の操作は Sx360 の Claude
#   （titan から 13500t へは届かない・`~/g3plus-ops` は Sx360 にだけある。docs/plans/three-machines.md の K1）。
# ⚠ 抜けるのは tmux の detach（Ctrl+B → D）。ssh が切れても titan の Claude と計算は続く。
# ⚠ 鍵はエージェントに載せておく（パスフレーズなしの鍵にしない。K8）。載っていなければ止まって手順を出す。

TAG="[run-titan-session]"
HOST="titan"
SESSION="ail"
REPO='~/ai-income-lab'   # titan 側の道（向こうのシェルで展開する）
PULL=0
START_CLAUDE=1

usage() {
  sed -n '3,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while (( $# )); do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --host=*) HOST="${1#*=}"; shift ;;
    --session) SESSION="${2:-}"; shift 2 ;;
    --session=*) SESSION="${1#*=}"; shift ;;
    --pull) PULL=1; shift ;;
    --shell) START_CLAUDE=0; shift ;;
    -h|--help) usage 0 ;;
    *) echo "$TAG 知らない引数: $1" >&2; usage 2 ;;
  esac
done

[ -n "$HOST" ] || { echo "$TAG --host が空" >&2; exit 2; }
# ⚠ セッション名は向こうのシェルに渡る。英数字と - _ だけにする
[[ "$SESSION" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "$TAG セッション名が不正: '$SESSION'" >&2; exit 2; }

if [ -n "${TMUX:-}" ]; then
  echo "$TAG ⚠ いま tmux の中にいる（入れ子になる）。tmux の外の端末で叩く" >&2
  exit 2
fi

# ---- 新しく作るときに tmux の中で走らせるもの（既にあれば tmux が無視する）
inner="cd $REPO"
if (( PULL )); then
  # ⚠ ここは \"bash -lc '…'\" の内側に入るので、引用符と括弧を使わない
  inner+=" && { git pull --ff-only || echo ⚠ git pull が通らなかった、作業ツリーを見る; }"
fi
if (( START_CLAUDE )); then
  # ⚠ claude を抜けてもシェルを残す（窓ごと消えてセッションが終わらないように）
  inner+="; claude; exec bash -l"
else
  inner+="; exec bash -l"
fi
tmux_cmd="tmux new-session -A -s $SESSION -c $REPO \"bash -lc '$inner'\""

# ---- titan の上で叩いたら ssh しない
if [ "$(hostname -s 2>/dev/null || hostname)" = "$HOST" ]; then
  echo "$TAG ここが $HOST なので、その場の tmux（$SESSION）に入る"
  exec bash -lc "$tmux_cmd"
fi

command -v ssh >/dev/null 2>&1 || { echo "$TAG ssh が無い" >&2; exit 2; }

# ---- 鍵で通るか（パスフレーズを聞かせない。BatchMode）
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" true 2>/dev/null; then
  cat >&2 <<EOF
$TAG ⚠ $HOST に鍵で入れなかった（エージェントに鍵が無い・$HOST が落ちている・tailnet に居ない、のどれか）
  1. 鍵がエージェントにあるか:   ssh-add -l
     無ければ載せる:            ssh-add ~/.ssh/titan-ed25519
     （WSL の再起動のたびに要るなら、docs/plans/three-machines.md の「鍵を持ち続ける」を入れる）
  2. $HOST が起きているか:        tailscale status   （LAN なら --host titan-lan）
EOF
  exit 1
fi

echo "$TAG $HOST の tmux（$SESSION）に入る。抜けるのは Ctrl+B → D"
exec ssh -t "$HOST" "$tmux_cmd"
