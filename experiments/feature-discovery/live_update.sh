#!/usr/bin/env bash
# 実売買用の日足と外部系列を「前の営業日ぶんまで」取り直し、調整まで済ませる（プラン live-trading-go-live-0922 の S1）。
#   ./live_update.sh                 # 取得（足 → 外部系列）→ 調整 → 検査 → どこまで入ったか
#   ./live_update.sh --no-exog       # 足だけ（外部系列は朝に 1 度でよい）
#
# ⚠ **研究用の data/ は 1 バイトも書き換えない。** 置き場は data-live/（git 管理外。AIL_LIVE_DATA_DIR で変えられる）で、
#    ail/data/store.py が AIL_DATA_DIR を見て差し替わる。表（features/）は作らない ＝ cli.predict がメモリ上で組む。
# ⚠ **読み取りだけ**（DXLink の Candle と公開の系列）。発注系には触れない。.env は読むだけ。
# ⚠ **認証に失敗したら再試行しない**（連打すると IP が約 8 時間ブロックされる）。このスクリプトは 1 回で止まる ＝ 手でも連打しない。
# ⚠ **市場時間中に流すと最後の 1 本は「今日の途中の足」**になる（cli.predict が asof 以降を落とす）。
set -euo pipefail
cd "$(dirname "$0")"
PY=${AIL_LIVE_UPDATE_PY:-.venv/bin/python}   # ⚠ 差し替えはテストだけ（tests/test_live_update.py）
LIVE=${AIL_LIVE_DATA_DIR:-$PWD/data-live}
EXOG=1
for a in "$@"; do
  case "$a" in
    --no-exog) EXOG=0 ;;
    *) echo "使い方: $0 [--no-exog]" >&2; exit 64 ;;
  esac
done
T0=$(date +%s)
step() { echo; echo "== $1（$(( $(date +%s) - T0 )) 秒）"; }

export AIL_DATA_DIR="$LIVE"
if [ ! -f "$LIVE/seed.json" ]; then
  step "初回: data/ から us63 の足と外部系列を写す（種。以後 data/ は読まない）"
  $PY -m cli.live_merge --seed
fi

# ⚠ 足は丸ごと取り直したものをそのまま使わない（取り直すと過去が変わる ＝ cli/live_merge.py の注記）。
#    別の置き場（.fetch/）へ取り、種の最終日以降だけを継ぐ
step "足（tastytrade・日足・us63）→ .fetch/"
rm -rf "$LIVE/.fetch"
AIL_DATA_DIR="$LIVE/.fetch" $PY -m cli.fetch --dataset daily | tail -4
step "種の歴史 ＋ 種の最終日以降の足"
$PY -m cli.live_merge --staging "$LIVE/.fetch" | tail -6
EXOG_FAILED=""
if [ "$EXOG" = 1 ]; then
  step "外部系列（exog_live ＝ 為替・金利・気象・地震）"
  # ⚠ 取得元ごとに 1 回ずつ取り、失敗しても止まらずに次の取得元 → 調整へ進む（2026-11-03 docs/plans/exog-failure-continue.md）。
  #    ECB が 500 ／ 503 ／ 504 を返す日がある【実測 約 27 回中 6 回】。止まると調整を飛ばし、夜の予測が印を書けず、翌日の発注が rc 13 になる。
  #    外部系列は ずらし ＋ 引き継ぎ（既定 1 ＋ 7 日）まで古くても使える作り ＝ 古すぎるかは最後の live_status が決める（rc 2）。
  for SRC in $(sed -n 's/^source *= *"\([a-z0-9_]*\)".*/\1/p' config/dataset/exog_live.toml); do
    set +e
    $PY -m cli.fetch --exog exog_live --source "$SRC" 2>&1 | grep -v "^$" | tail -12
    R=${PIPESTATUS[0]}
    set -e
    if [ "$R" != 0 ]; then
      echo "  ⚠ 外部 $SRC の取得に失敗（rc=$R）。前に取った分のまま調整へ進む（古すぎるかは「どこまで入ったか」が見る）"
      EXOG_FAILED="$EXOG_FAILED $SRC"
    fi
  done
fi
step "調整（分割の継ぎ目）"
$PY -m cli.adjust --period d > "$LIVE/adjust.log" 2>&1 || { tail -20 "$LIVE/adjust.log"; exit 1; }
tail -3 "$LIVE/adjust.log"
step "検査（adjusted）"
$PY -m cli.check --layer adjusted --period d | tail -5
step "どこまで入ったか"
RC=0
$PY -m cli.live_status --dataset daily --exog exog_live || RC=$?
[ -z "$EXOG_FAILED" ] || echo "⚠ 取得に失敗した外部系列:$EXOG_FAILED（前に取った分で続けた）"
echo; echo "所要 $(( $(date +%s) - T0 )) 秒 ／ 置き場 $LIVE"
exit $RC
