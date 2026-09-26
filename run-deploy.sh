#!/usr/bin/env bash
set -uo pipefail

# 本番の目印 prod を main まで進める（プラン: docs/plans/prod-branch.md）。13500t の auto-update は prod だけを取りに行く。
#
#   ./run-deploy.sh             # 検査 → 関門（テスト）→ git push origin HEAD:prod → 13500t の反映を待つ
#   ./run-deploy.sh --dry-run   # 検査と関門だけ（push しない）
#   ./run-deploy.sh --no-wait   # push したら待たずに終わる
#
# 利用者の「デプロイ」はこれ 1 本（CLAUDE.md の Git 運用ルール）。
# ⚠ 関門はその場で流す ＝ ふだんは Sx360（2026-09-25 利用者決定。venv に LightGBM・aeon・torch〔CPU 版〕を入れた）。
#    titan でも流せる（依存がそろっている）。それ以外の機械では拒む（依存が足りず ⏭ ／ 落ちる）。
#    13500t の反映（auto-update.log の done <sha>）は 13500t に届く機械（Sx360）でだけ待つ（titan からは届かない）。
#
# ⚠ **prod を進める ＝ 15 分以内に 13500t の本番に反映**。進めるのは利用者に頼まれたときだけ。
# ⚠ 関門を飛ばす引数は無い。⏭（依存が無くて飛ばした段）も不合格 ＝ 依存のそろった機械（Sx360 ／ titan）で流す。
# ⚠ fast-forward だけ（--force しない）。15:00〜16:15 ET は拒む（見届けなしに 15:50 の回で動くのを避ける）。

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FD="$HERE/experiments/feature-discovery"
DRY=0 WAIT=1
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --no-wait) WAIT=0 ;;
    -h|--help) sed -n '3,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "[run-deploy] 知らない引数: $a" >&2; exit 2 ;;
  esac
done

die() { echo "[run-deploy] ❌ $*" >&2; echo "[run-deploy] prod は動かしていない" >&2; exit 1; }
say() { echo; echo "===== $* ====="; }
cd "$HERE" || exit 1
GATE_HOSTS="${AIL_DEPLOY_GATE_HOSTS:-Sx360 titan}"   # 関門を流してよい機械（hostname）
PROD_HOST="${AIL_DEPLOY_PROD_HOST:-13500t.lan}"  # 反映を待つ本番の機械
SSHO=(-o BatchMode=yes -o ConnectTimeout=10)

# 13500t の auto-update.log に done <sha> が出るまで待つ（15 分おきの cron ＋ 余裕。届かない機械では案内だけ）
wait_prod() {
  local sha="$1" short="${1:0:7}" log=/home/ubuntu/g3plus-ops/trade-runner/auto-update.log i seen=""
  if ! ssh "${SSHO[@]}" "$PROD_HOST" true 2>/dev/null; then
    echo "[run-deploy] $PROD_HOST に届かない ＝ 反映は待たない（15 分以内に auto-update が pull する。Sx360 から $log を見る）"; return 0
  fi
  say "7. 13500t の反映を待つ（auto-update は 15 分おき。最長 20 分）"
  local from new
  from="$(ssh "${SSHO[@]}" "$PROD_HOST" "wc -l < $log" 2>/dev/null || echo 0)"
  for ((i = 0; i < 40; i++)); do
    # ⚠ 待ち始めた後の行だけを見る（前の ERROR ／ done を拾わない）
    new="$(ssh "${SSHO[@]}" "$PROD_HOST" "tail -n +$((from + 1)) $log" 2>/dev/null)"
    [ -n "$new" ] && [ "$new" != "${seen:-}" ] && { echo "${new#"${seen:-}"}" | grep -a '^\[' | sed 's/^/  13500t: /'; seen="$new"; }
    if echo "$new" | grep -q "done $short"; then
      echo "✅ 13500t ＝ $short（管理画面も起こし直した）"; return 0
    fi
    if echo "$new" | grep -q "ERROR"; then
      echo "[run-deploy] ❌ 13500t の auto-update が ERROR（prod は進んだ。13500t の側で直す）" >&2; return 1
    fi
    (( i % 4 == 0 )) && echo "  待つ… $((i / 2)) 分"
    sleep 30
  done
  echo "[run-deploy] ⚠ 20 分で done が出ない（SKIP の理由は $PROD_HOST の $log）" >&2; return 1
}

# 本番に効く道（変わっていれば一覧で見せる）
PROD_PATHS=(experiments/live-trading experiments/tastytrade-api-sample
            experiments/feature-discovery/cli experiments/feature-discovery/ail experiments/feature-discovery/config
            experiments/feature-discovery/requirements.txt experiments/feature-discovery/requirements-nodeps.txt
            dashboard/app dashboard/requirements.txt dashboard/glossary.toml run-live.sh)
(( ${#PROD_PATHS[@]} )) || die "PROD_PATHS が空（一覧が全ファイルになる）"

case " $GATE_HOSTS " in
  *" $(hostname) "*) ;;
  *) die "関門を流してよい機械ではない（ここは $(hostname)。流すのは $GATE_HOSTS）" ;;
esac

say "1. ブランチと作業ツリー"
[ "$(git branch --show-current)" = "main" ] || die "main の上で流す（いま: $(git branch --show-current)）"
[ -z "$(git status --porcelain)" ] || die "作業ツリーが汚れている（コミットしてから）"
git fetch origin main prod --quiet 2>/dev/null || git fetch origin main --quiet || die "git fetch に失敗"
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || die "main が origin/main と違う（push ／ pull してから）"
git rev-parse -q --verify origin/prod >/dev/null || die "origin/prod が無い（最初の 1 回は手で作る ＝ プラン §2）"
echo "✅ main ＝ origin/main ＝ $(git rev-parse --short HEAD)"

say "2. 進める中身（prod $(git rev-parse --short origin/prod) → main $(git rev-parse --short HEAD)）"
git merge-base --is-ancestor origin/prod HEAD || die "origin/prod が main の祖先でない（fast-forward できない。prod を直に触った？）"
if [ "$(git rev-parse origin/prod)" = "$(git rev-parse HEAD)" ]; then
  echo "進めるものが無い（prod ＝ main）"; exit 0
fi
git log --oneline origin/prod..HEAD
CHANGED="$(git diff --name-only origin/prod HEAD -- "${PROD_PATHS[@]}")"
if [ -n "$CHANGED" ]; then
  echo; echo "⚠ 本番に効く道の変更:"; echo "$CHANGED" | sed 's/^/  /'
else
  echo; echo "本番に効く道の変更は無い（文書・研究だけ）"
fi

say "3. 時間帯"
HHMM=$((10#$(TZ=America/New_York date +%H%M)))
if [ "$HHMM" -ge 1500 ] && [ "$HHMM" -le 1615 ]; then
  die "15:00〜16:15 ET は進めない（いま $(TZ=America/New_York date +%H:%M) ET）"
fi
echo "✅ $(TZ=America/New_York date +%H:%M) ET"

say "4. 関門: ./run-tests.sh"
OUT="$(mktemp)"; trap 'rm -f "$OUT"' EXIT
"$HERE/run-tests.sh" 2>&1 | tee "$OUT"
RC=${PIPESTATUS[0]}
[ "$RC" -eq 0 ] || die "run-tests.sh が不合格（rc=$RC）"
grep -q "⏭" "$OUT" && die "run-tests.sh に ⏭（飛ばした段）がある ＝ 依存のそろった機械で流す"

say "5. 関門: 予測の経路の指紋テスト（test_predict.py・test_trading_run.py）"
[ -x "$FD/.venv/bin/python" ] || die "$FD/.venv が無い"
( cd "$FD" && .venv/bin/python -m pytest -q -rs tests/test_predict.py tests/test_trading_run.py ) 2>&1 | tee "$OUT"
RC=${PIPESTATUS[0]}
[ "$RC" -eq 0 ] || die "指紋テストが不合格（rc=$RC。LightGBM ／ aeon ／ torch が無い機械では落ちる ＝ venv を requirements どおりにする）"
grep -qE "[0-9]+ skipped" "$OUT" && die "指紋テストに skip がある ＝ 依存のそろった機械で流す"

if (( DRY )); then
  say "--dry-run: 全部通った。push はしていない"; exit 0
fi

say "6. git push origin HEAD:prod"
git push origin HEAD:prod || die "push に失敗（fast-forward でない？）"
echo "✅ prod ＝ $(git rev-parse --short HEAD)。13500t は 15 分以内に pull する（log: ~/g3plus-ops/trade-runner/auto-update.log）"
(( WAIT )) || exit 0
wait_prod "$(git rev-parse HEAD)"
