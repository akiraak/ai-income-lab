#!/usr/bin/env bash
# 資格情報なしでサンプルの配線を確かめる。モックを立てて 6 手順 ＋ レート制限 ＋ 本番ガードを通す。
#
#   ./selftest.sh
#
# ⚠ 通るのは「コードの配線」だけで、tastytrade の挙動の【実測】にはならない。
set -uo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
PORT=${PORT:-8765}
WS_PORT=${WS_PORT:-8766}
DX_PORT=${DX_PORT:-8767}
WORK=$(mktemp -d)
FAIL=0

note() { printf '\n=== %s ===\n' "$1"; }
fail() { echo "  NG: $1"; FAIL=1; }

cleanup() {
  [ -n "${MOCK_PID:-}" ] && kill "$MOCK_PID" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

note "1. 記録とマスクのテスト・本番ガードの鍵"
$PY test_record.py || fail "test_record.py"
$PY test_guard.py || fail "test_guard.py"

note "2. モックを起動"
$PY mock_server.py --port "$PORT" --ws-port "$WS_PORT" --dxlink-port "$DX_PORT" --market-data \
  > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
for _ in $(seq 1 20); do
  curl -sS -m 1 -o /dev/null -H "User-Agent: selftest/1.0" "http://127.0.0.1:$PORT/customers/me/accounts" && break
  sleep 0.3
done
echo "  pid=$MOCK_PID"

# 手元の .env（本物の資格情報）を読ませない。テストはモックだけを相手にする
: > "$WORK/env.test"
export TT_ENV_FILE="$WORK/env.test"
export TT_REST_BASE="http://127.0.0.1:$PORT"
export TT_ACCOUNT_STREAMER="ws://127.0.0.1:$WS_PORT"
export TT_PROD_REST_BASE="http://127.0.0.1:$PORT"
export TT_CLIENT_SECRET="SELFTEST-CLIENT-SECRET"
export TT_REFRESH_TOKEN="SELFTEST-REFRESH-TOKEN"
export TT_PROD_CLIENT_SECRET="SELFTEST-PROD-SECRET"
export TT_PROD_REFRESH_TOKEN="SELFTEST-PROD-REFRESH"
# 停止フラグは手元の out/HALT ではなく作業用の場所を見る（本物の停止中でも自己検査は回せる）
export TT_HALT_FILE="$WORK/HALT"

note "3. 6 手順を通す"
OUT_BEFORE=$(ls out/*.jsonl 2>/dev/null | wc -l)
$PY sample.py --step all --seconds 8 || fail "sample.py --step all"
LATEST=$(ls -t out/*.jsonl | head -1)
[ "$(ls out/*.jsonl | wc -l)" -gt "$OUT_BEFORE" ] || fail "記録ファイルが増えていない"

note "4. 記録の中身"
$PY - "$LATEST" <<'EOF' || FAIL=1
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
by_step = {r["step"]: r for r in rows}
want = {1: "authenticated", 2: "ok", 4: "final_Cancelled", 5: "buy_Filled/sell_Filled"}
bad = False
for step, result in want.items():
    got = by_step.get(step, {}).get("result")
    print(f"  {'ok  ' if got == result else 'NG  '} step {step}: {got}")
    bad |= got != result
for step in (3, 6, 61):
    ok = by_step.get(step, {}).get("ok")
    print(f"  {'ok  ' if ok else 'NG  '} step {step}: {by_step.get(step, {}).get('result')}")
    bad |= not ok
# 接続先を差し替えた実行は mock: true が付く（判定から外すための印）
mock_rows = sum(1 for r in rows if r.get("mock") is True)
print(f"  {'ok  ' if mock_rows == len(rows) else 'NG  '} mock フラグ: {mock_rows}/{len(rows)} 行")
bad |= mock_rows != len(rows)
sys.exit(1 if bad else 0)
EOF

note "5. 秘密が記録に出ていないか"
for secret in SELFTEST-CLIENT-SECRET SELFTEST-REFRESH-TOKEN SELFTEST-PROD-SECRET SELFTEST-PROD-REFRESH 5WT00042 eyJ; do
  hits=$(grep -c -- "$secret" "$LATEST" || true)
  if [ "$hits" = "0" ]; then echo "  ok   $secret は出ていない"; else fail "$secret が $hits 行に出ている"; fi
done

note "6. 本番では発注系を拒否するか"
# sample.py は拒否したときに終了コード 1 を返すので、出力を取ってから見る（pipefail 対策）
GUARD_OUT=$($PY sample.py --env prod --step 4,5 2>&1)
case "$GUARD_OUT" in
  *拒否*) echo "  ok   --env prod で手順 4・5 を拒否した" ;;
  *) fail "本番ガードが効いていない" ;;
esac
GUARD_OUT=$($PY sample.py --env prod --step 4 --i-know-this-is-real-money 2>&1)
case "$GUARD_OUT" in
  *拒否*) echo "  ok   フラグ 1 つだけでは通らない（TT_ALLOW_PROD_ORDERS=1 も要る）" ;;
  *) fail "フラグ 1 つで本番発注に進んでしまう" ;;
esac
# dry-run を許しても本発注は開かない（鍵は別）
GUARD_OUT=$($PY sample.py --env prod --step 4 --allow-prod-dry-run 2>&1)
case "$GUARD_OUT" in
  *拒否*) echo "  ok   --allow-prod-dry-run は本発注を開かない" ;;
  *) fail "dry-run の許可で本発注まで通ってしまう" ;;
esac
GUARD_OUT=$($PY sample.py --step dryrun 2>&1)
case "$GUARD_OUT" in
  *--allow-prod-dry-run*) echo "  ok   本番 dry-run はフラグ無しでは走らない" ;;
  *) fail "本番 dry-run がフラグ無しで走ってしまう" ;;
esac

note "7. 停止フラグ（HALT）で発注系の手順が止まるか"
touch "$WORK/HALT"
HALT_OUT=$($PY sample.py --step 4 2>&1); HALT_RC=$?
case "$HALT_OUT" in
  *停止フラグ*) [ "$HALT_RC" = "3" ] && echo "  ok   HALT があると --step 4 を拒否した（exit 3）" || fail "HALT の拒否コードが 3 でない ($HALT_RC)" ;;
  *) fail "HALT があるのに手順 4 が止まらない" ;;
esac
HALT_OUT=$($PY sample.py --step cleanup 2>&1); HALT_RC=$?
[ "$HALT_RC" = "0" ] && echo "  ok   HALT 中でも cleanup（取消）は通る" || fail "HALT 中に cleanup が通らない ($HALT_RC): $HALT_OUT"
rm -f "$WORK/HALT"

note "結果"
if [ "$FAIL" = "0" ]; then echo "すべて通った"; else echo "NG あり"; fi
exit "$FAIL"
