#!/usr/bin/env bash
# シミュレーションを回す（live-trading.md §0-7）。仮データと仮の時計で、本物と同じ執行器を通しで動かす。
#   操作は別の端末から: python simctl.py speed 60 ／ pause ／ resume ／ step ／ stop ／ status
#   ./simrun.sh sim1                       # 続きから（無ければ最初から・×60）
#   ./simrun.sh sim1 --fresh --speed max   # 最初から最速で（回帰テスト）
# ⚠ 先に機械をシミュレーションモードへ:  $PY simctl.py mode sim sim1  （戻すのは mode real）
# ⚠ 実売買とは排他。記録は sim/<名前>/ の下だけ（全行 sim: true ／ mock: true）。tastytrade の挙動の【実測】にはならない。
set -uo pipefail
cd "$(dirname "$0")"
PY="../tastytrade-api-sample/.venv/bin/python"; [ -x "$PY" ] || PY=python3
"$PY" simrun.py "$@"; RC=$?
echo "=== 検査 ==="
"$PY" simctl.py check || RC=1
exit "$RC"
