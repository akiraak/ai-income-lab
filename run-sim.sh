#!/usr/bin/env bash
set -uo pipefail

# シミュレーション（仮データと仮の時計で、本物と同じ執行器を通しで動かす）を 1 コマンドで回す。
# 依存の用意 → 日足の確認 → モードの切り替え → 管理画面 → 運転手、をまとめて行う。
#
#   ./run-sim.sh                         # sim1 を続きから（無ければ最初から・×60）＋ 管理画面
#   ./run-sim.sh --fresh --speed max     # 最初から最速で（64 営業日が約 8 秒）
#   ./run-sim.sh sim2 --fresh            # 筋書き（故障の注入）つき
#   ./run-sim.sh --fresh --paused        # 止めた状態で始める（別の端末から ctl step ／ ctl resume）
#   ./run-sim.sh --fetch titan           # 足りない日足を titan から scp で写してから回す
#   ./run-sim.sh --no-dashboard          # 管理画面を起こさない
#
#   別の端末から（速さ・停止。⚠ 操作は CLI だけ・管理画面は表示だけ）:
#   ./run-sim.sh ctl status              # ctl の後ろは simctl.py にそのまま渡る
#   ./run-sim.sh ctl speed 60            #   speed 1 ／ 10 ／ 60 ／ 300 ／ 1440 ／ max
#   ./run-sim.sh ctl pause ／ resume ／ step ／ stop
#   ./run-sim.sh reconcile show          # 口座の建玉と台帳の差（reconcile.py にそのまま渡る）
#
# ⚠ **実売買とシミュレーションは排他**（live-trading.md §0-7）。
#   - 資格情報の無い機械（Sx360）: 機械のモード（experiments/live-trading/MODE）を sim に切り替えて回す。管理画面は 3012
#   - 資格情報のある機械（titan）: ⚠ **本物の MODE には触らない**。MODE・run.lock・記録を作業用の置き場
#     （既定 ~/.cache/ai-income-lab-sim。AIL_SIM_SCRATCH で変更）に向けて回す ＝ 本物の執行器を止めない。
#     管理画面はデモ（AIL_DEMO=1 ＝ tastytrade に繋がない）で別のポート 3014 に起こす。⚠ 3012 の管理画面は触らない
# ⚠ 接続先はこの機械の中のモックだけ。資金は動かない。tastytrade の挙動の【実測】にはならない。

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LT="$HERE/experiments/live-trading"
SAMPLE="$HERE/experiments/tastytrade-api-sample"
DASH="$HERE/dashboard"
PY="$SAMPLE/.venv/bin/python"

usage() { sed -n '3,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
say() { echo "[run-sim] $*"; }
die() { echo "[run-sim] $*" >&2; exit 1; }

# ---- 資格情報があるか（値は読まない）→ ある機械では本物の MODE に触らず、作業用の置き場で回す
has_credentials() {
  [ -f "$SAMPLE/.env" ] && grep -qE '^[[:space:]]*TT_(PROD_)?(CLIENT_SECRET|REFRESH_TOKEN)[[:space:]]*=[[:space:]]*[^[:space:]#]' "$SAMPLE/.env"
}
SCRATCH=""
if has_credentials; then
  SCRATCH="${AIL_SIM_SCRATCH:-$HOME/.cache/ai-income-lab-sim}"
  mkdir -p "$SCRATCH"
  export LT_MODE_DIR="$SCRATCH"
fi

ensure_python() {
  command -v python3 >/dev/null 2>&1 || die "python3 が無い"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "Python 3.11 以上が要る（いま $(python3 --version 2>&1)）"
  if [ ! -x "$PY" ]; then
    say "依存を入れる（$SAMPLE/.venv）"
    python3 -m venv "$SAMPLE/.venv" && "$PY" -m pip install -q -r "$SAMPLE/requirements.txt" || die "venv を作れない"
  fi
}

# ---- 別の端末からの操作: そのまま渡す
case "${1:-}" in
  -h|--help) usage 0 ;;
  ctl) shift; ensure_python; cd "$LT" && exec "$PY" simctl.py "$@" ;;
  reconcile) shift; ensure_python; cd "$LT" && exec "$PY" reconcile.py "$@" ;;
  stop) ensure_python; cd "$LT" && exec "$PY" simctl.py stop ;;
esac

NAME="sim1"
DASHBOARD=1
FETCH=""
RUN_ARGS=()
while (( $# )); do
  case "$1" in
    --no-dashboard) DASHBOARD=0; shift ;;
    --fetch) FETCH="${2:-}"; [ -n "$FETCH" ] || die "--fetch には写し元のホスト名（例 titan）が要る"; shift 2 ;;
    --fresh|--paused) RUN_ARGS+=("$1"); shift ;;
    --speed|--days) RUN_ARGS+=("$1" "${2:-}"); shift 2 ;;
    -*) echo "[run-sim] 知らない引数: $1" >&2; usage 2 ;;
    *) NAME="$1"; shift ;;
  esac
done

ensure_python
cd "$LT" || die "experiments/live-trading が無い"
[ -f "config/sim/$NAME.toml" ] || die "config/sim/$NAME.toml が無い（あるのは: $(ls config/sim | sed 's/\.toml$//' | tr '\n' ' ')）"

if [ -n "$SCRATCH" ]; then
  say "⚠ この機械には tastytrade の資格情報がある ＝ 本物の MODE には触らず、作業用の置き場で回す: $SCRATCH"
fi

# ---- 日足（git 管理外）。使う銘柄は設定のトレーダーから決まる
DATA_DIR="$HERE/experiments/feature-discovery/data/adjusted/d"
missing="$("$PY" - "$NAME" "$DATA_DIR" <<'PYCODE'
import os, sys
sys.path.insert(0, ".")
import simdata
from trader import load_traders
cfg = simdata.load_config(sys.argv[1])
symbols = sorted({s for t in load_traders(cfg.traders) for s in t.symbols})
print(" ".join(s.replace("/", "-") for s in symbols if not os.path.exists(os.path.join(sys.argv[2], f"{s.replace('/', '-')}.csv"))))   # BRK/B → BRK-B.csv
PYCODE
)" || die "設定を読めない"
if [ -n "$missing" ] && [ -n "$FETCH" ]; then
  say "日足を $FETCH から写す: $missing"
  mkdir -p "$DATA_DIR"
  for s in $missing; do
    scp -q "$FETCH:ai-income-lab/experiments/feature-discovery/data/adjusted/d/$s.csv" "$DATA_DIR/" || die "$s.csv を写せない（$FETCH に ssh できるか・置き場が ai-income-lab/ か）"
  done
  missing=""
fi
if [ -n "$missing" ]; then
  echo "[run-sim] 日足が無い: $missing" >&2
  echo "[run-sim]   日足は git 管理外。titan から写す:  ./run-sim.sh --fetch titan   （titan へ ssh できるとき）" >&2
  echo "[run-sim]   または titan 側から:  scp experiments/feature-discovery/data/adjusted/d/{$(echo "$missing" | tr ' ' ',')}.csv <この機械>:$DATA_DIR/" >&2
  exit 1
fi

# ---- モード（何も動いていないときだけ切り替わる）
if ! "$PY" simctl.py mode sim "$NAME" >/dev/null; then
  die "モードを切り替えられない（前の運転手が動いているなら ./run-sim.sh ctl stop。状態は ./run-sim.sh ctl status）"
fi

# ---- 管理画面（表示だけ）。⚠ プロセスは名前ではなくポートから引く。⚠ 既に動いているものは止めない
DASH_PID=""
cleanup() { [ -n "$DASH_PID" ] && kill "$DASH_PID" 2>/dev/null; }
trap cleanup EXIT
trap 'exit 130' INT TERM
if (( DASHBOARD )); then
  if [ -n "$SCRATCH" ]; then PORT=3014; else PORT="$(grep -E '^[[:space:]]*AIL_PORT[[:space:]]*=' "$DASH/.env" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' \t\r"'"'")"; PORT="${PORT:-3012}"; fi
  if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE ":$PORT\$"; then
    say "管理画面は既に動いている: http://127.0.0.1:$PORT （そのまま使う。モードはリクエストごとに読む）"
  else
    if [ ! -x "$DASH/.venv/bin/python" ]; then
      say "管理画面の依存を入れる（$DASH/.venv）"
      python3 -m venv "$DASH/.venv" && "$DASH/.venv/bin/python" -m pip install -q -r "$DASH/requirements.txt" || die "管理画面の venv を作れない（--no-dashboard で飛ばせる）"
    fi
    [ -f "$DASH/.env" ] || cp "$DASH/.env.example" "$DASH/.env"
    LOG="${SCRATCH:-$LT}/run-sim-dashboard.log"
    if [ -n "$SCRATCH" ]; then
      ( cd "$DASH" && AIL_DEMO=1 AIL_PORT="$PORT" AIL_MODE_DIR="$SCRATCH" AIL_DATA_DIR="$SCRATCH/dashboard-data" exec .venv/bin/python -m app.main ) > "$LOG" 2>&1 &
    else
      ( cd "$DASH" && exec .venv/bin/python -m app.main ) > "$LOG" 2>&1 &
    fi
    DASH_PID=$!
    say "管理画面を起こした: http://127.0.0.1:$PORT （ログ $LOG。このスクリプトを Ctrl+C で終えると一緒に止まる）"
  fi
fi

say "別の端末から: ./run-sim.sh ctl status ／ ctl speed 60 ／ ctl pause ／ ctl resume ／ ctl step ／ ctl stop"
"$PY" simrun.py "$NAME" "${RUN_ARGS[@]}"
RC=$?
echo "=== 検査 ==="
"$PY" simctl.py check || RC=1
"$PY" simctl.py status | tail -2

if [ -n "$DASH_PID" ] && kill -0 "$DASH_PID" 2>/dev/null; then
  say "運転手は終わった（rc=$RC）。管理画面は動かしたまま: http://127.0.0.1:$PORT — 見終わったら Ctrl+C"
  wait "$DASH_PID"
fi
exit "$RC"
