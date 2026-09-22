#!/usr/bin/env bash
# 実売買の 1 日ぶんを 1 本で流す: 日足の更新 → 今日の買い%（3 モデル並列）→ 台帳の控え → 執行器。
#   ./run-live.sh --prepare                                   # 朝に 1 度: 日足 ＋ 外部系列の更新 → 紙上の対照 daily.csv（発注しない）
#   ./run-live.sh --traders T1,T2,T3                           # 既定は --mode dry-run・接続先は .env の TT_ENV（無ければ cert）
#   ./run-live.sh --traders T1,T2,T3 --wait                    # 15:50:00 ET まで待ってから流す（窓の中で使う形）
#
# ⚠ **--mode submit ＋ 今日の日付のときは、引けに間に合わなければ起動を拒否する（rc=11）。**
#    準備（日足の更新 ＋ 予測）に約 120 秒かかるので、発注の開始が刻限（既定 15:58 ET）を過ぎる時刻に起こすと止まる。
#    ⚠ 引けは 16:00 ET で、引け後の成行は拒否される。刻限は AIL_SUBMIT_DEADLINE、準備の見積りは AIL_PREP_SECONDS。
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
  if [ "$(date +%s)" -ge "$TARGET" ]; then
    # ⚠ 過ぎていたら待たずに進む。⚠ **これはブレーキではない**（遅れて起動したときは下の「引けに間に合うか」が止める）
    say "15:50:00 ET は既に過ぎている ＝ 待たずに進む"
  else
    say "15:50:00 ET（$(date -d @"$TARGET")）まで待つ"
    while [ "$(date +%s)" -lt "$TARGET" ]; do
      LEFT=$(( TARGET - $(date +%s) )); sleep $(( LEFT > 60 ? 60 : LEFT ))
    done
  fi
fi

# ---- 引けに間に合うか（⚠ 2026-09-22 に踏んだ形。遅れて起動すると、準備の 95 秒の後に引け後の成行を投げる）
# ⚠ **止めるのは submit ＋ 今日の日付のときだけ。** plan ／ dry-run と過去の日付は素通り。
# ⚠ `--ignore-window` を「--」の後ろに書いた回も素通り（発注できる時間帯を承知で外すと決めた回なので）。
IGNORE_WINDOW=0
for a in ${PASS+"${PASS[@]}"}; do [ "$a" = "--ignore-window" ] && IGNORE_WINDOW=1; done
if [ "$MODE" = "submit" ] && [ "$DATE" = "$TODAY" ] && [ "$IGNORE_WINDOW" = 0 ]; then
  # 見積り【実測 2026-09-21・09-22】: 日足の更新 55 秒 ＋ 予測 38 秒。余裕を見て 120 秒（飛ばすなら 60 秒）
  PREP=${AIL_PREP_SECONDS:-120}
  [ "$SKIP_UPDATE" = 1 ] && PREP=${AIL_PREP_SECONDS:-60}
  # 発注を始めていてよい刻限。⚠ 引けは 16:00 ET で、引け後の成行は拒否される（tif_no_after_hours_opening_market_orders）
  DEADLINE_AT=${AIL_SUBMIT_DEADLINE:-15:58:00}
  DEADLINE=$(TZ=America/New_York date -d "$DATE $DEADLINE_AT" +%s)
  NOW=$(date +%s)
  START_AT=$(( NOW + PREP ))
  if [ "$START_AT" -gt "$DEADLINE" ]; then
    say "拒否: 引けに間に合わない"
    echo "  いま        $(TZ=America/New_York date -d "@$NOW" +%H:%M:%S) ET" >&2
    echo "  準備に      ${PREP} 秒（日足の更新 ＋ 予測）" >&2
    echo "  発注の開始  $(TZ=America/New_York date -d "@$START_AT" +%H:%M:%S) ET  ＞  刻限 ${DEADLINE_AT} ET" >&2
    echo "  ⚠ 引けは 16:00 ET。引け後の成行は拒否される（tif_no_after_hours_opening_market_orders）" >&2
    echo "  ⚠ 今日は見送り、翌営業日の 15:45 ET までに起動する。" >&2
    echo "     どうしても流すなら AIL_SUBMIT_DEADLINE=16:05:00 か、「--」の後ろに --ignore-window を足す" >&2
    exit 11
  fi
  say "引けに間に合う（発注の開始の見込み $(TZ=America/New_York date -d "@$START_AT" +%H:%M:%S) ET ／ 刻限 ${DEADLINE_AT} ET）"
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
