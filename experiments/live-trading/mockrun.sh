#!/usr/bin/env bash
# モックで 20 営業日を通す（プラン Phase 2 の 5）。
#   ./mockrun.sh            # 2 人（mock_a: 整数株 / mock_b: 金額指定・平均合成）を 2026-10-01〜10-28 の 20 営業日
#   KEEP_DIR=/tmp/lt ./mockrun.sh   # 終了時に config/・state/cert/・out/ を写す（管理画面 /live を AIL_LIVE_DIR=/tmp/lt で見る用）
# ⚠ 通るのは配線だけで、tastytrade の挙動の【実測】にはならない（記録に mock: true が付く）。
set -uo pipefail
cd "$(dirname "$0")"
PY="../tastytrade-api-sample/.venv/bin/python"; [ -x "$PY" ] || PY=python3
PORT=${PORT:-8865}
WORK=$(mktemp -d)
FAIL=0
LIVEFS=../tastytrade-api-sample/livefs.py
# ⚠ 記録は DB（2026-09-21）。作業の置き場に自分の DB を作る ＝ 本物の live.sqlite に書かない
$PY "$LIVEFS" init "$WORK" demo > /dev/null
fail() { echo "  NG: $1"; FAIL=1; }
# KEEP_DIR（任意）: 管理画面が読む形（config/traders・state/<env>・out/<日付>）で記録を残す。⚠ 指定しなければ今までどおり消す
keep() {
  [ -n "${KEEP_DIR:-}" ] || return 0
  # 記録はファイルの形で書き出す（dashboard/demo/live のようにデモの正本にするとき）。管理画面はデモの起動で demo.sqlite に読み込む
  mkdir -p "$KEEP_DIR/config"
  $PY "$LIVEFS" export "$WORK/out" --to "$KEEP_DIR/out" > /dev/null && $PY "$LIVEFS" export "$WORK/state" --to "$KEEP_DIR/state/cert" > /dev/null \
    && cp -r config/traders "$KEEP_DIR/config/" && echo "記録を残した: $KEEP_DIR（ファイルの形）"
}
cleanup() { [ -n "${MOCK_PID:-}" ] && kill "$MOCK_PID" 2>/dev/null; keep; rm -rf "$WORK"; }
trap cleanup EXIT

$PY ../tastytrade-api-sample/mock_server.py --port "$PORT" --ws-port $((PORT+1)) --dxlink-port $((PORT+2)) --market-data --fill-noise 0.003 --seed 0 > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
for _ in $(seq 1 20); do
  curl -sS -m 1 -o /dev/null -H "User-Agent: mockrun/1.0" "http://127.0.0.1:$PORT/customers/me/accounts" && break
  sleep 0.3
done

: > "$WORK/env.test"
export TT_ENV_FILE="$WORK/env.test" TT_REST_BASE="http://127.0.0.1:$PORT" TT_CLIENT_SECRET="MOCK-SECRET" TT_REFRESH_TOKEN="MOCK-REFRESH"
export TT_HALT_FILE="$WORK/HALT" LT_OUT_DIR="$WORK/out" LT_STATE_DIR="$WORK/state"
# MODE ／ run.lock も作業ディレクトリに向ける（本物の run.lock を取らない ＝ 同じ時刻の本物の執行器を邪魔しない。接続先がモックなので通る）
export LT_MODE_DIR="$WORK"

DAYS=$($PY - <<'PY'
from datetime import date, timedelta
d=date(2026,10,1); out=[]
while len(out)<20:
    if d.weekday()<5: out.append(d.isoformat())
    d+=timedelta(days=1)
print(" ".join(out))
PY
)
i=0
for day in $DAYS; do
  i=$((i+1))
  # 翌営業日の気配（$100 を中心に ±1% の道筋。整数株の A と金額指定の B が同じ株数になる水準）
  q=$($PY -c "import math; print(f'{100*(1+0.01*math.sin($i/2)):.2f}')")
  curl -sS -o /dev/null -X POST -H "User-Agent: mockrun/1.0" -H "Content-Type: application/json" -d "{\"quote\": $q}" "http://127.0.0.1:$PORT/_mock/quote"
  $PY run_day.py --traders mock_a,mock_b --date "$day" --mode submit --ignore-window --retry-interval 0.1 --cancel-after 10 > "$WORK/day-$day.log" 2>&1 || fail "run_day $day ($(tail -1 "$WORK/day-$day.log"))"
  printf '  %s  %s\n' "$day" "$(grep -E '^完了' "$WORK/day-$day.log")"
done

echo "=== 検査 ==="
$PY - "$WORK/out" "$WORK/state" <<'PY' || FAIL=1
import json, os, sys
sys.path.insert(0, "../tastytrade-api-sample")
import livefs                                    # ⚠ 記録は DB（道はそのまま）
out, state = sys.argv[1], sys.argv[2]
def rows(kind):
    return [json.loads(l) for f in livefs.find(out, f"*/{kind}.jsonl") for l in livefs.read_lines(f)]
orders, transfers, ledgers = rows("orders"), rows("transfers"), rows("ledger")
bad = False
def ok(cond, msg):
    global bad
    print(f"  {'ok  ' if cond else 'NG  '} {msg}"); bad |= not cond
filled = [o for o in orders if o["final_status"] == "Filled"]
ok(len(orders) >= 10 and len(filled) == len(orders), f"注文 {len(orders)} 件が全部 Filled（{len(filled)}）")
ok(all(o.get("mock") is True and o.get("test") is True for o in orders), "全注文に mock: true と test: true")
notional = [o for o in orders if o["sizing"] == "notional" and o["side"] == "buy"]
ok(notional and all(o["shares"] == 0 and o["value_usd"] > 0 for o in notional), f"金額指定の買い {len(notional)} 件（Notional Market）")
# 差 1: 合図時の気配（mid）と約定価格の差が 0 でない
diffs = []
for o in filled:
    mid = o["quote_at_signal"]["mid"]
    for f in o["fills"]:
        diffs.append((f["price"] - mid) / mid * 1e4)
ok(diffs and any(abs(d) > 0.5 for d in diffs), f"差 1（気配 → 約定）が出る: 中央値 {sorted(diffs)[len(diffs)//2]:.1f}bp・最大 {max(diffs):.1f}bp")
# 2026-09-19: 内部移転はやめた（利用者決定「トレーダーの実際の実績が検証できない」）。A の売りと B の買いが重なる日は、両方が口座に出て売りが先
ok(len(transfers) == 0, "内部移転 0 件（transfers.jsonl を書かない）")
by_day = {}
for o in orders:
    by_day.setdefault((o["date"], o["symbol"]), []).append((o["side"], o["parts"][0]["trader"]))
both = {k: v for k, v in by_day.items() if {s for s, _ in v} == {"buy", "sell"}}
ok(both and all(v[0][0] == "sell" for v in both.values()), f"同じ日・同じ銘柄に売りと買いが別々の注文で出た日 {len(both)} 件（売りが先）")
ok(all(len(o["parts"]) == 1 for o in orders), "1 注文 1 トレーダー")
last = {l["trader"]: l for l in ledgers}
ok(all(l["cost_in_use_usd"] <= l["budget_usd"] + 1e-6 for l in ledgers), "全日・全員で原価が予算を超えない")
for name in ("mock_a", "mock_b"):
    st = json.loads(livefs.read_doc(f"{state}/{name}.json"))
    ok(st["last_date"] == "2026-10-28" and len(st["history"]) >= 2, f"{name}: 状態が最終日まで進み売買 {len(st['history'])} 行")
print(f"  台帳の最終日: " + ", ".join(f"{n}: 原価 ${l['cost_in_use_usd']} 実現 ${l['realized_usd']} 建玉 {list(l['holdings'])}" for n, l in last.items()))
sys.exit(1 if bad else 0)
PY

echo "=== 秘密が記録に出ていないか ==="
for secret in MOCK-SECRET MOCK-REFRESH 5WT00042 eyJ; do
  hits=$($PY "$LIVEFS" dump "$WORK" | grep -c -- "$secret" || true)
  [ "$hits" = "0" ] && echo "  ok   $secret は出ていない" || fail "$secret が $hits 行に出ている"
done
[ "$FAIL" = "0" ] && echo "すべて通った" || echo "NG あり"
exit "$FAIL"
