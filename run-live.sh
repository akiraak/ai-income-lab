#!/usr/bin/env bash
# 実売買の 1 日ぶんを流す。⚠ 2026-11-02 から予測は前の晩（docs/plans/nightly-predict.md）:
#   夜  ./run-live.sh --predict-only                         # 引けの後: 日足（公式の終値）＋ 外部系列 → 直下の本物の人全員の予測 → out/<今日>/predict.done.jsonl（済みの印）
#   15:50 ./run-live.sh --traders @roster --wait …           # 前の営業日の「済み」の印を確かめ、その予測で執行器（日足の更新・予測はしない）。印が無い ／ 欠けていれば rc 13（発注しない）
#   ./run-live.sh --traders T1 --same-day --mode plan …      # 今までの形（その場で予測して同じ日に読む）。⚠ plan ／ dry-run だけ（submit は拒む）
#   ./run-live.sh --prepare                                   # 朝に 1 度: 日足 ＋ 外部系列の更新 → 紙上の対照 daily.csv（発注しない）
#   ./run-live.sh --traders T1,T2,T3                           # 既定は --mode dry-run・接続先は .env の TT_ENV（無ければ cert）
#   ./run-live.sh --traders T1,T2,T3 --wait                    # 15:50:00 ET まで待ってから流す（窓の中で使う形）
#   ./run-live.sh --traders @roster --wait …                   # 名簿（管理画面の /roster。roster.json）の人を動かす（2026-10-07）
#
# ⚠ **起動してすぐ 2 つを見る**（どちらも本体は run_day.py 側にあり、ここは 95 秒むだにしないための早見）:
#    シミュレーションモードなら rc=5 ／ 別の執行器・運転手が動いていれば rc=6 ／「本番の機械ではない」印があって submit ＋ --env prod なら rc=7。
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

TRADERS=""; MODE="dry-run"; DATE=""; WAIT=0; PREPARE=0; EXOG=0; SKIP_UPDATE=0; PREDICT_ONLY=0; SAME_DAY=0; PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --traders) TRADERS="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --date) DATE="$2"; shift 2 ;;
    --wait) WAIT=1; shift ;;
    --prepare) PREPARE=1; shift ;;
    --with-exog) EXOG=1; shift ;;
    --skip-update) SKIP_UPDATE=1; shift ;;
    --predict-only) PREDICT_ONLY=1; shift ;;
    --same-day) SAME_DAY=1; shift ;;
    --) shift; PASS=("$@"); break ;;
    *) echo "使い方: $0 [--prepare] [--traders A,B] [--mode plan|dry-run|submit] [--date YYYY-MM-DD] [--wait] [--with-exog] [--skip-update] [--predict-only] [--same-day] [-- run_day.py の引数]" >&2; exit 64 ;;
  esac
done
T0=$(date +%s)
LAST_SAY=""
say() { LAST_SAY="$*"; echo "[$(TZ=America/New_York date +%H:%M:%S) ET ＋$(( $(date +%s) - T0 ))秒] $*"; }

# ---- 毎日の起動の記録（docs/plans/archive/launch-record-auto.md）: 終わるとき（どの rc でも）に out/launches.jsonl へ 1 行。
# ⚠ 書けなくても rc は変えない（DB の無い置き場では警告 1 行）。⚠ out/<日付>/ には書かない（見張り「今日の起動が無い」を崩す）
LAUNCH_START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
STAGE="start"; STEPS=""; NOTE=""; TMP=""
on_exit() {
  local rc=$? kind=trade mode="$MODE"
  [ "$PREPARE" = 1 ] && { kind=prepare; mode=prepare; }
  [ "$PREDICT_ONLY" = 1 ] && { kind=predict; mode=predict; }
  [ "$rc" != 0 ] && [ -z "$NOTE" ] && NOTE="$LAST_SAY"
  "$PY_LT" "$LT/launchlog.py" add --kind "$kind" --start "$LAUNCH_START" --rc "$rc" --mode "$mode" --stage "$STAGE" \
      --steps "$STEPS" --note "$NOTE" > /dev/null 2>&1 || echo "⚠ 起動の記録を書けなかった（売買には影響しない）" >&2
  [ -n "$TMP" ] && rm -rf "$TMP"
  exit "$rc"
}
trap on_exit EXIT

NIGHTLY="$LT/nightly.py"
# ---- 予測を並列で流し、そろったものを 1 本の predict-<時刻>.jsonl にして「済み」の印を書く（2026-11-01 nightly-predict.md §8-2 の 1）
# 引数: $1 ＝ 「実験\t手法」の行 ／ $2 ＝ asof ／ $3 ＝ 置き場（out/<asof>）。PRED に道を入れる。rc 0 ＝ 全部そろった ／ 12 ＝ 1 本でも失敗（そろった分だけ印に書く）
run_predict() {
  local models="$1" asof="$2" out="$3" n=0 fail=0 pids=() keys=()
  [ -n "$TMP" ] || TMP=$(mktemp -d)
  export AIL_DATA_DIR="${AIL_LIVE_DATA_DIR:-$FD/data-live}"
  : > "$TMP/ok.tsv"; : > "$TMP/failed.tsv"
  while IFS=$'\t' read -r EXP METHOD; do
    [ -n "$EXP" ] || continue
    n=$((n+1)); keys+=("$EXP"$'\t'"$METHOD")
    ( cd "$FD" && if [ -n "$METHOD" ]; then "$PY_FD" -m cli.predict --experiment "$EXP" --method "$METHOD" --asof "$asof" --out "$TMP/p$n.jsonl" --meta-out "$TMP/m$n.json"
      else "$PY_FD" -m cli.predict --experiment "$EXP" --asof "$asof" --out "$TMP/p$n.jsonl" --meta-out "$TMP/m$n.json"; fi ) &
    pids+=($!)
  done <<< "$models"
  local i
  for ((i=0; i<${#pids[@]}; i++)); do
    if wait "${pids[$i]}"; then echo "${keys[$i]}" >> "$TMP/ok.tsv"; else echo "${keys[$i]}" >> "$TMP/failed.tsv"; fail=1; rm -f "$TMP/p$((i+1)).jsonl" "$TMP/m$((i+1)).json"; fi
  done
  PRED=""
  if [ -s "$TMP/ok.tsv" ]; then
    PRED="$out/predict-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
    cat "$TMP"/p*.jsonl | "$PY_LT" "$LIVEFS" append "$PRED" || { say "⚠ 予測を記録に入れられない"; return 12; }
    for m in "$TMP"/m*.json; do [ -e "$m" ] && { tr -d '\n' < "$m"; echo; }; done | "$PY_LT" "$LIVEFS" append "$out/predict.meta.jsonl" || say "⚠ 予測の付記を記録に入れられない（売買には影響しない）"
    say "予測 $(cat "$TMP"/p*.jsonl | grep -c .) 行 → ${PRED#$ROOT/}（DB）"
  fi
  if [ "${4:-}" = "mark" ] && [ -s "$TMP/ok.tsv" ]; then
    "$PY_LT" "$NIGHTLY" done --out-dir "${out%/*}" --date "$asof" --file "$PRED" --ok "$TMP/ok.tsv" --failed "$TMP/failed.tsv" > /dev/null \
      && say "済みの印 → ${out#$ROOT/}/predict.done.jsonl（そろった $(grep -c . "$TMP/ok.tsv") 本 ／ 失敗 $(grep -c . "$TMP/failed.tsv") 本）" \
      || { say "⚠ 済みの印を書けない"; return 12; }
  fi
  [ "$fail" = 0 ] || { say "⚠ 予測が $(grep -c . "$TMP/failed.tsv") 本失敗した: $(tr '\n' ' ' < "$TMP/failed.tsv")"; return 12; }
  return 0
}

if [ "$PREPARE" = 1 ]; then
  say "朝の準備: 日足 ＋ 外部系列（発注しない）"
  STAGE=update; TMP=$(mktemp -d)
  "$FD/live_update.sh" 2>&1 | tee "$TMP/update.log"; RC=${PIPESTATUS[0]}
  STEPS="update=$RC"
  # rc ≠ 0 の理由 ＝ 取得元の例外か「どこまで入ったか」の ⚠ の最後の 1 行（起動の記録に残す）
  [ "$RC" = 0 ] || NOTE=$(grep -E 'Error|⚠' "$TMP/update.log" | tail -1)
  # 外部系列の取得の失敗は止めずに続ける（live_update.sh・2026-11-03）＝ rc 0 でも理由を起動の記録に残す
  [ "$RC" != 0 ] || NOTE=$(grep -E '^⚠ 取得に失敗した外部系列' "$TMP/update.log" | tail -1)
  # 紙上の対照（Phase 3）: 前の営業日までの公式終値が入ったので daily.csv を作り直す（読むだけ・何度流しても同じ）
  if "$PY_LT" "$LIVEFS" ls "${LT_OUT_DIR:-$LT/out}" 2>/dev/null | grep -q -E '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'; then
    say "紙上の対照 → daily.csv"; STAGE=paper
    (cd "$LT" && "$PY_LT" paper.py --bars-dir "${AIL_LIVE_DATA_DIR:-$FD/data-live}/adjusted/d"); R=$?
    STEPS="$STEPS,paper=$R"; [ "$R" = 0 ] || say "⚠ paper.py が失敗した（売買には影響しない）"
    # 物差し A〜E（docs/plans/archive/daily-record-auto.md）: 記録と daily.csv から yardstick.csv を作り直す（読むだけ・何度流しても同じ）
    say "物差し A〜E → yardstick.csv"; STAGE=yardstick
    (cd "$LT" && "$PY_LT" yardstick.py); R=$?
    STEPS="$STEPS,yardstick=$R"; [ "$R" = 0 ] || say "⚠ yardstick.py が失敗した（売買には影響しない）"
    # 手数料の実額（docs/plans/actual-fees.md）: 口座の取引履歴を取り足し、注文の見積りと突き合わせる（読むだけの照会・発注しない・売買履歴は書かない）
    say "手数料の実額 → fees.jsonl ／ fees.csv"; STAGE=fees
    (cd "$LT" && "$PY_LT" fees.py fetch --match); R=$?
    STEPS="$STEPS,fees=$R"; [ "$R" = 0 ] || say "⚠ fees.py が失敗した（売買には影響しない）"
  fi
  # 夜の予測の回し直し（2026-11-01 nightly-predict.md §8-2 の 5）: 前の営業日の「済み」の印が無ければ、ここで asof ＝ 前の営業日で流す（時間はある）
  TODAY_P=$(TZ=America/New_York date +%F)
  if [ -z "${AIL_NO_PREDICT_RETRY:-}" ] && ! "$PY_LT" "$NIGHTLY" find --out-dir "${LT_OUT_DIR:-$LT/out}" --date "$TODAY_P" --traders "$( "$PY_LT" "$NIGHTLY" real-traders )" > /dev/null 2>&1; then
    PREV=$("$PY_LT" "$NIGHTLY" prev --date "$TODAY_P") && {
      say "⚠ 前の営業日 $PREV の夜の予測がそろっていない → ここで回し直す"; STAGE=predict_retry
      run_predict "$("$PY_LT" "$NIGHTLY" models --all-real)" "$PREV" "${LT_OUT_DIR:-$LT/out}/$PREV" mark; R=$?
      STEPS="$STEPS,predict_retry=$R"; [ "$R" = 0 ] || { NOTE="夜の予測の回し直しが失敗（rc=$R）"; RC=12; }
    }
  fi
  exit $RC
fi

# ---- 夜の予測（2026-11-01 nightly-predict.md）: 日足（公式の終値）＋ 外部系列 → 直下の本物の人全員の予測 → 済みの印。⚠ 発注しない
if [ "$PREDICT_ONLY" = 1 ]; then
  TODAY=$(TZ=America/New_York date +%F); DATE=${DATE:-$TODAY}; STAGE=predict_only
  "$PY_LT" "$NIGHTLY" is-trading-day --date "$DATE" || { say "$DATE は営業日ではない ＝ 何もしない"; exit 0; }
  if [ "$DATE" = "$TODAY" ] && ! "$PY_LT" "$NIGHTLY" after-close --date "$DATE"; then
    say "拒否: 引けの 15 分後より前（途中の足で予測しない）"; exit 14
  fi
  TMP=$(mktemp -d)
  if [ "$SKIP_UPDATE" = 0 ] && [ "$DATE" = "$TODAY" ]; then
    say "日足 ＋ 外部系列の更新（公式の終値）"; STAGE=update
    "$FD/live_update.sh"; RC=$?; STEPS="update=$RC"
    [ "$RC" = 0 ] || [ "$RC" = 2 ] || { say "⚠ 日足の更新に失敗（rc=$RC）。予測しない"; exit 10; }
    # 終値の控え（docs/plans/closes-snapshot-night.md・2026-11-09）: 公式の終値を取った直後に closes.json へ足す
    # ＝ 権利落ち日の朝に前日の終値だけが書き換わっても、朝の paper.py が ② で拒める。⚠ 失敗しても予測は止めない（rc は変えない）
    say "終値の控え → closes.json"; STAGE=closes
    (cd "$LT" && "$PY_LT" paper.py --snapshot-only --bars-dir "${AIL_LIVE_DATA_DIR:-$FD/data-live}/adjusted/d"); R=$?
    STEPS="$STEPS,closes=$R"; [ "$R" = 0 ] || say "⚠ 終値の控えに失敗（rc=$R。予測は続ける・朝の準備がいつもどおり控える）"
  fi
  STAGE=predict
  MODELS=$("$PY_LT" "$NIGHTLY" models --all-real) || { say "⚠ トレーダーの設定が読めない"; exit 11; }
  say "夜の予測（asof $DATE・$(grep -c . <<< "$MODELS") 本）"
  run_predict "$MODELS" "$DATE" "${LT_OUT_DIR:-$LT/out}/$DATE" mark; RC=$?
  STEPS="${STEPS:+$STEPS,}predict=$RC"
  exit $RC
fi
[ -n "$TRADERS" ] || { echo "--traders が要る" >&2; exit 64; }
TODAY=$(TZ=America/New_York date +%F)
DATE=${DATE:-$TODAY}

# ---- シミュレーションと二重起動を**先に**見る（⚠ 排他の本体は run_day.py。ここは「95 秒むだにしない」ための早見）
# ⚠ **ここで run.lock を持ち続けない**（持つと後で起こす run_day.py が自分で取れなくなる）＝ 取れるかだけ見てすぐ放す。
# ⚠ **確かめられなくても止めない**（この早見の不具合で実売買を止めない。本物の関門は run_day.py の側）。
STAGE=precheck
PROD_SUBMIT=0
if [ "$MODE" = "submit" ]; then for a in "${PASS[@]}"; do [ "$a" = "--env" ] && PROD_SUBMIT=pending; [ "$PROD_SUBMIT" = pending ] && [ "$a" = "prod" ] && PROD_SUBMIT=1; done; fi
[ "$PROD_SUBMIT" = 1 ] || PROD_SUBMIT=0
PRECHECK=$(cd "$LT" && AIL_PROD_SUBMIT="$PROD_SUBMIT" "$PY_LT" - <<'PY' 2>/dev/null
import json, os, sys
try:
    import mode as modes
    m = modes.read_mode()
    if m.is_sim:
        print(json.dumps({"stop": "sim", "name": m.name, "since": m.since}, ensure_ascii=False)); sys.exit(0)
    mark = modes.read_not_production()
    if mark is not None and os.environ.get("AIL_PROD_SUBMIT") == "1":
        print(json.dumps({"stop": "not_production", "mark": mark.describe()}, ensure_ascii=False)); sys.exit(0)
    lock = modes.RunLock()
    if lock.acquire(m.mode, "run-live.sh の早見"):
        lock.release()                      # ⚠ すぐ放す（run_day.py が取り直す）
        print(json.dumps({"stop": None}, ensure_ascii=False))
    else:
        print(json.dumps({"stop": "lock", "holder": lock.holder()}, ensure_ascii=False))
except Exception as exc:                    # ⚠ 早見が壊れても実売買を止めない
    print(json.dumps({"stop": None, "warn": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
PY
) || PRECHECK=""
case "$PRECHECK" in
  *'"stop": "sim"'*)
    say "拒否: この機械はシミュレーションモード"
    echo "  $PRECHECK" >&2
    echo "  ⚠ 本物の執行器は起動しない（run_day.py も rc=5 で拒む）。戻すのは:" >&2
    echo "     cd experiments/live-trading && ../tastytrade-api-sample/.venv/bin/python simctl.py mode real" >&2
    exit 5 ;;
  *'"stop": "not_production"'*)
    say "拒否: この機械は本番の機械ではない（NOT_PRODUCTION の印。live-trading.md §0-14）"
    echo "  $PRECHECK" >&2
    echo "  ⚠ 本番の発注は 13500t の cron が出す。run_day.py も rc=7 で拒む。外すのは notprod.py clear（2 台で同時に発注しないことを確かめてから）" >&2
    exit 7 ;;
  *'"stop": "lock"'*)
    say "拒否: 別の執行器 ／ 運転手が動いている（run.lock）"
    echo "  $PRECHECK" >&2
    echo "  ⚠ 終わるのを待つか、止めてから起こす。" >&2
    exit 6 ;;
esac
case "$PRECHECK" in *'"warn"'*) say "⚠ 早見ができなかった（そのまま進む。関門は run_day.py の側）: $PRECHECK" ;; esac

if [ "$WAIT" = 1 ]; then
  STAGE=wait
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
  [ "$SAME_DAY" = 0 ] && PREP=${AIL_PREP_SECONDS:-15}      # ⚠ 夜の予測を読むだけ（2026-11-01）
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

TRADERS_DIR=""; for ((i=0; i<${#PASS[@]}; i++)); do [ "${PASS[$i]}" = "--traders-dir" ] && TRADERS_DIR="${PASS[$((i+1))]}"; done
OUT="${LT_OUT_DIR:-$LT/out}/$DATE"; TMP=$(mktemp -d)      # TMP は on_exit が消す
NEED=$("$PY_LT" "$NIGHTLY" models --traders "$TRADERS" ${TRADERS_DIR:+--traders-dir "$TRADERS_DIR"}) || { say "⚠ トレーダーの設定 ／ 名簿が読めない"; exit 11; }
if [ "$SAME_DAY" = 0 ] && [ -z "$NEED" ]; then
  say "experiment のモデルを使う人がいない ＝ 予測は読まない（試験用の合図だけ）"; PRED="$OUT/predict.jsonl"
elif [ "$SAME_DAY" = 0 ]; then
  # ---- 前の営業日の夜の予測を読む（2026-11-01 nightly-predict.md §8-2 の 2）。⚠ 印が無い ／ 欠けていれば発注しない（その場で予測して埋めない）
  STAGE=find
  FOUND=$("$PY_LT" "$NIGHTLY" find --out-dir "${LT_OUT_DIR:-$LT/out}" --date "$DATE" --traders "$TRADERS" ${TRADERS_DIR:+--traders-dir "$TRADERS_DIR"})
  RC=$?
  if [ "$RC" != 0 ]; then
    say "⚠ 夜の予測が使えない: $FOUND。執行器は起こさない（発注しない）"; NOTE="$FOUND"; exit 13
  fi
  PRED=$("$PY_LT" -c 'import json,sys; print(json.loads(sys.argv[1])["predict"])' "$FOUND")
  SIGDATE=$("$PY_LT" -c 'import json,sys; print(json.loads(sys.argv[1])["signal_date"])' "$FOUND")
  say "夜の予測（asof $SIGDATE）を読む → ${PRED#$ROOT/}"
  PASS+=(--signal-date "$SIGDATE")
else
  [ "$MODE" = "submit" ] && { say "拒否: --same-day（その場で予測して同じ日に読む今までの形）は plan ／ dry-run だけ"; exit 64; }
  # 1. 日足（今日の途中の足 ＝ 終値の代役が末尾に入る）
  if [ "$SKIP_UPDATE" = 0 ] && [ "$DATE" = "$TODAY" ]; then
    say "日足の更新"; STAGE=update
    if [ "$EXOG" = 1 ]; then "$FD/live_update.sh"; else "$FD/live_update.sh" --no-exog; fi
    RC=$?; STEPS="update=$RC"
    [ "$RC" = 0 ] || [ "$RC" = 2 ] || { say "⚠ 日足の更新に失敗（rc=$RC）。執行器は起こさない"; exit 10; }
  else
    say "日足の更新は飛ばす（--skip-update か、過去の日付）"
  fi
  # 2. 今日の買い%（名簿 ／ 指定の人のモデル）
  STAGE=predict
  MODELS="$NEED"
  PRED="$OUT/predict.jsonl"
  if [ -n "$MODELS" ]; then
    say "今日の買い%（asof $DATE）"
    run_predict "$MODELS" "$DATE" "$OUT" || { say "⚠ 予測が失敗した。執行器は起こさない"; exit 12; }
  fi
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
say "執行器（--mode $MODE）"; STAGE=executor
cd "$LT" && "$PY_LT" run_day.py --traders "$TRADERS" --date "$DATE" --mode "$MODE" --predict "$PRED" "${PASS[@]}"
RC=$?
STEPS="${STEPS:+$STEPS,}executor=$RC"
say "終了 rc=$RC"
exit $RC
