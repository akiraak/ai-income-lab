#!/usr/bin/env bash
# 実売買の 1 日ぶんを 1 本で流す: 日足の更新 → 今日の買い%（3 モデル並列）→ 台帳の控え → 執行器。
#   ./run-live.sh --prepare                                   # 朝に 1 度: 日足 ＋ 外部系列の更新 → 紙上の対照 daily.csv（発注しない）
#   ./run-live.sh --traders T1,T2,T3                           # 既定は --mode dry-run・接続先は .env の TT_ENV（無ければ cert）
#   ./run-live.sh --traders T1,T2,T3 --wait                    # 15:50:00 ET まで待ってから流す（窓の中で使う形）
#   ./run-live.sh --traders T1,T2,T3 --date 2026-09-18 --mode plan -- --ignore-window   # 過去の日で通す（発注しない）
#   「--」より後ろはそのまま run_day.py へ渡る（--env prod ／ --allow-prod-dry-run ／ --i-know-this-is-real-money など）。
#
# ⚠ **本番の鍵はここに書かない・ここで入れない**（CLAUDE.md の 2 つ目の例外）。本番で発注するときは、利用者が
#    TT_ALLOW_PROD_ORDERS=1 を付けて起こし、「--」の後ろに --env prod --i-know-this-is-real-money を自分で書く。
# ⚠ 予測が 1 本でも失敗したら執行器を起こさない（古い予測・欠けた予測で売買しない）。
# ⚠ 研究用の data/ は触らない（置き場は experiments/feature-discovery/data-live/）。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
FD="$ROOT/experiments/feature-discovery"
LT="$ROOT/experiments/live-trading"
PY_FD="$FD/.venv/bin/python"
PY_LT="$ROOT/experiments/tastytrade-api-sample/.venv/bin/python"
# ⚠ 記録は DB（2026-09-21 から。道はそのまま・本物はリポジトリ直下の live.sqlite）。見る・足す・書き出すのは livefs.py
LIVEFS="$ROOT/experiments/tastytrade-api-sample/livefs.py"

TRADERS=""; MODE="dry-run"; DATE=""; WAIT=0; PREPARE=0; EXOG=0; SKIP_UPDATE=0; PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --traders) TRADERS="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --date) DATE="$2"; shift 2 ;;
    --wait) WAIT=1; shift ;;
    --prepare) PREPARE=1; shift ;;
    --with-exog) EXOG=1; shift ;;
    --skip-update) SKIP_UPDATE=1; shift ;;
    --) shift; PASS=("$@"); break ;;
    *) echo "使い方: $0 [--prepare] [--traders A,B] [--mode plan|dry-run|submit] [--date YYYY-MM-DD] [--wait] [--with-exog] [--skip-update] [-- run_day.py の引数]" >&2; exit 64 ;;
  esac
done
T0=$(date +%s)
say() { echo "[$(TZ=America/New_York date +%H:%M:%S) ET ＋$(( $(date +%s) - T0 ))秒] $*"; }

if [ "$PREPARE" = 1 ]; then
  say "朝の準備: 日足 ＋ 外部系列（発注しない）"
  "$FD/live_update.sh"; RC=$?
  # 紙上の対照（Phase 3）: 前の営業日までの公式終値が入ったので daily.csv を作り直す（読むだけ・何度流しても同じ）
  if "$PY_LT" "$LIVEFS" ls "${LT_OUT_DIR:-$LT/out}" 2>/dev/null | grep -q -E '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'; then
    say "紙上の対照 → daily.csv"
    (cd "$LT" && "$PY_LT" paper.py --bars-dir "${AIL_LIVE_DATA_DIR:-$FD/data-live}/adjusted/d") || say "⚠ paper.py が失敗した（売買には影響しない）"
  fi
  exit $RC
fi
[ -n "$TRADERS" ] || { echo "--traders が要る" >&2; exit 64; }
TODAY=$(TZ=America/New_York date +%F)
DATE=${DATE:-$TODAY}

if [ "$WAIT" = 1 ]; then
  TARGET=$(TZ=America/New_York date -d "$DATE 15:50:00" +%s)
  say "15:50:00 ET（$(date -d @"$TARGET")）まで待つ"
  while [ "$(date +%s)" -lt "$TARGET" ]; do
    LEFT=$(( TARGET - $(date +%s) )); sleep $(( LEFT > 60 ? 60 : LEFT ))
  done
fi

# 1. 日足（今日の途中の足 ＝ 終値の代役が末尾に入る）。外部系列は朝の --prepare で済ませておく（--with-exog で一緒に取る）
if [ "$SKIP_UPDATE" = 0 ] && [ "$DATE" = "$TODAY" ]; then
  say "日足の更新"
  if [ "$EXOG" = 1 ]; then "$FD/live_update.sh"; else "$FD/live_update.sh" --no-exog; fi
  RC=$?
  # rc=2 は「前の営業日に届いていない ／ 外部系列が古い」の知らせ。今日の足があるかは予測の側で確かめる
  [ "$RC" = 0 ] || [ "$RC" = 2 ] || { say "⚠ 日足の更新に失敗（rc=$RC）。執行器は起こさない"; exit 10; }
else
  say "日足の更新は飛ばす（--skip-update か、過去の日付）"
fi

# 2. 今日の買い%（トレーダーの設定から 実験 × 手法 を拾い、並列で流す）。置き場は執行器と同じ（LT_OUT_DIR はテスト・モック用）
OUT="${LT_OUT_DIR:-$LT/out}/$DATE"; TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
# ⚠ 予測は回ごとに別の道（行は足すだけなので、同じ道に 2 回入れると前の回の行が残り、後の回に無い銘柄で古い値を読む）
PRED="$OUT/predict.jsonl"
TRADERS_DIR=""; for ((i=0; i<${#PASS[@]}; i++)); do [ "${PASS[$i]}" = "--traders-dir" ] && TRADERS_DIR="${PASS[$((i+1))]}"; done
MODELS=$(cd "$LT" && "$PY_LT" - "$TRADERS" "${TRADERS_DIR:-$LT/config/traders}" <<'PY'
import sys
import trader
seen = []
for name in sys.argv[1].split(","):
    t = trader.load_trader(name.strip(), sys.argv[2])
    for m in t.models:
        if m.kind == "experiment" and (m.name, m.method) not in seen:
            seen.append((m.name, m.method))
for n, meth in seen:
    print(f"{n}\t{meth or ''}")
PY
) || { say "⚠ トレーダーの設定が読めない"; exit 11; }

if [ -n "$MODELS" ]; then
  say "今日の買い%（asof $DATE）"
  export AIL_DATA_DIR="${AIL_LIVE_DATA_DIR:-$FD/data-live}"
  PIDS=(); N=0
  while IFS=$'\t' read -r EXP METHOD; do
    N=$((N+1))
    ( cd "$FD" && if [ -n "$METHOD" ]; then "$PY_FD" -m cli.predict --experiment "$EXP" --method "$METHOD" --asof "$DATE" --out "$TMP/p$N.jsonl" --meta-out "$TMP/m$N.json"
      else "$PY_FD" -m cli.predict --experiment "$EXP" --asof "$DATE" --out "$TMP/p$N.jsonl" --meta-out "$TMP/m$N.json"; fi ) &
    PIDS+=($!)
  done <<< "$MODELS"
  FAIL=0; for p in "${PIDS[@]}"; do wait "$p" || FAIL=1; done
  [ "$FAIL" = 0 ] || { say "⚠ 予測が失敗した。執行器は起こさない"; exit 12; }
  PRED="$OUT/predict-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
  cat "$TMP"/p*.jsonl | "$PY_LT" "$LIVEFS" append "$PRED" || { say "⚠ 予測を記録に入れられない。執行器は起こさない"; exit 12; }
  for m in "$TMP"/m*.json; do tr -d '\n' < "$m"; echo; done | "$PY_LT" "$LIVEFS" append "$OUT/predict.meta.jsonl" || say "⚠ 予測の付記を記録に入れられない（売買には影響しない）"
  say "予測 $(cat "$TMP"/p*.jsonl | grep -c .) 行 → ${PRED#$ROOT/}（DB）"
fi

# 3. 台帳の控え（発注する回だけ。台帳を失うと「何も持っていない」つもりで買い直す ＝ live-trading.md §0-8 の限界）
#    ⚠ 2026-09-21 から売買履歴は DB。控えは今までと同じファイルの形で書き出す（state/<env>/<名前>.json・journal.jsonl …）
if [ "$MODE" = "submit" ]; then
  BK="$LT/state-backup/$(date -u +%Y%m%dT%H%M%SZ)"
  "$PY_LT" "$LIVEFS" export "${LT_STATE_DIR:-$LT/state}" --to "$BK" > /dev/null && say "売買履歴の控え → ${BK#$ROOT/}" \
    || say "⚠ 売買履歴の控えを書き出せなかった"
  ls -1d "$LT"/state-backup/* 2>/dev/null | head -n -60 | xargs -r rm -rf      # 直近 60 回ぶんだけ残す
fi

# 4. 執行器
say "執行器（--mode $MODE）"
cd "$LT" && "$PY_LT" run_day.py --traders "$TRADERS" --date "$DATE" --mode "$MODE" --predict "$PRED" "${PASS[@]}"
RC=$?
say "終了 rc=$RC"
exit $RC
