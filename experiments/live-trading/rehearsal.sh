#!/usr/bin/env bash
# sandbox（cert）のリハーサル（プラン Phase 2 の 6）: 指定の日付の 15:45 ET まで待ち、執行器を 1 回流す。
#   setsid nohup ./rehearsal.sh 2026-09-18 > out/rehearsal-2026-09-18.log 2>&1 &
# ⚠ cert だけ。実弾は動かない。本番の資格情報は気配の読み取りにだけ使う。
set -u
cd "$(dirname "$0")"
DAY=${1:?日付 YYYY-MM-DD}
PY=../tastytrade-api-sample/.venv/bin/python
TARGET=$(TZ=America/New_York date -d "$DAY 15:45:30" +%s)
echo "[$(date -u +%FT%TZ)] リハーサル $DAY: 15:45:30 ET（$(date -d @$TARGET)）まで待つ pid=$$"
while :; do
  NOW=$(date +%s)
  [ "$NOW" -ge "$TARGET" ] && break
  sleep $(( TARGET - NOW > 300 ? 300 : TARGET - NOW ))
done
echo "[$(date -u +%FT%TZ)] 起動"
$PY run_day.py --traders test_a --env cert --date "$DAY" --mode submit
RC=$?
echo "[$(date -u +%FT%TZ)] 終了 rc=$RC 記録: out/$DAY/"
