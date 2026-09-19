#!/usr/bin/env bash
# パターンの撮影用の記録を作る（docs/plans/dashboard-required-features.md Phase 5）。
#   ./run.sh <出力先>        # 例: ./run.sh /tmp/lt3 → build.py --live-dir /tmp/lt3
# mockrun.sh と同じ手順（モックの tastytrade・$100 前後の気配・20 営業日）を 3 人（mock_a・mock_b ＋ ここの mock_c）で回す。
# ⚠ 2026-10-19 は起動しない（N7「起動しなかった日」を画面に出すため）。
# ⚠ 通るのは配線だけ。記録の数字はモックの値で【実測】ではない（記録に mock: true が付く）。
set -uo pipefail
OUT=${1:?出力先を渡す}
HERE=$(cd "$(dirname "$0")" && pwd)
LT=$(cd "$HERE/../../../../../experiments/live-trading" && pwd)
mkdir -p "$OUT/config/traders" "$OUT/config/signals" "$OUT/state/cert"
OUT=$(cd "$OUT" && pwd)
cp "$LT/config/traders/mock_a.toml" "$LT/config/traders/mock_b.toml" "$HERE/mock_c.toml" "$OUT/config/traders/"
cp "$LT/config/signals/mock_a.csv" "$LT/config/signals/mock_b1.csv" "$LT/config/signals/mock_b2.csv" "$HERE/mock_c.csv" "$OUT/config/signals/"

cd "$LT"
PY=../tastytrade-api-sample/.venv/bin/python
PORT=${PORT:-8875}
WORK=$(mktemp -d)
$PY ../tastytrade-api-sample/mock_server.py --port "$PORT" --ws-port $((PORT+1)) --dxlink-port $((PORT+2)) --market-data --fill-noise 0.003 --seed 0 > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
trap 'kill $MOCK_PID 2>/dev/null; rm -rf "$WORK"' EXIT
for _ in $(seq 1 20); do curl -sS -m 1 -o /dev/null -H "User-Agent: mockrun/1.0" "http://127.0.0.1:$PORT/customers/me/accounts" 2>/dev/null && break; sleep 0.3; done

: > "$WORK/env.test"
export TT_ENV_FILE="$WORK/env.test" TT_REST_BASE="http://127.0.0.1:$PORT" TT_CLIENT_SECRET="MOCK-SECRET" TT_REFRESH_TOKEN="MOCK-REFRESH"
export TT_HALT_FILE="$WORK/HALT" LT_OUT_DIR="$OUT/out" LT_STATE_DIR="$WORK/state" LT_TRADERS_DIR="$OUT/config/traders"
i=0
for day in $($PY -c "
from datetime import date, timedelta
d = date(2026, 10, 1); out = []
while len(out) < 20:
    if d.weekday() < 5: out.append(d.isoformat())
    d += timedelta(days=1)
print(' '.join(out))"); do
  i=$((i+1))
  q=$($PY -c "import math; print(f'{100*(1+0.01*math.sin($i/2)):.2f}')")
  curl -sS -o /dev/null -X POST -H "User-Agent: mockrun/1.0" -H "Content-Type: application/json" -d "{\"quote\": $q}" "http://127.0.0.1:$PORT/_mock/quote"
  if [ "$day" = "2026-10-19" ]; then echo "  $day  （起動しない）"; continue; fi
  $PY run_day.py --traders mock_a,mock_b,mock_c --date "$day" --mode submit --ignore-window --retry-interval 0.1 --cancel-after 10 > "$WORK/day-$day.log" 2>&1 \
    || echo "  NG $day: $(tail -1 "$WORK/day-$day.log")"
  printf '  %s  %s\n' "$day" "$(grep -E '^完了' "$WORK/day-$day.log")"
done
cp "$WORK"/state/*.json "$OUT/state/cert/"
echo "記録: $OUT"
