#!/usr/bin/env bash
set -uo pipefail

# このリポジトリの自動テストを 1 コマンドで回す（プラン: docs/plans/test-automation-sim.md）。
#
#   ./run-tests.sh            # 既定: 執行器 pytest → 管理画面 pytest → selftest → mockrun（約 90 秒）
#   ./run-tests.sh --fast     # pytest 2 つだけ（約 31 秒）
#   ./run-tests.sh --full     # ＋ 黄金の集計値（通し運転 64 営業日 × 2 本）
#   ./run-tests.sh --list     # 何を回すかだけ出す
#
# ⚠ **途中で落ちても最後まで走る**（どこが壊れたか一度で分かる）。1 つでも ❌ なら終了コードは 1。
# ⚠ **本物には触らない**: 機械のモード（experiments/live-trading/MODE）・run.lock・out/・state/ を読み書きしない。
#    ⚠ 動いている管理画面（3012）・vibeboard（3010）のポートも奪わない（サーバを起こさない）。
# ⚠ 接続先はこの機械の中のモックだけ。資金は動かない。tastytrade の挙動の【実測】にはならない。

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LT="$HERE/experiments/live-trading"
SAMPLE="$HERE/experiments/tastytrade-api-sample"
DASH="$HERE/dashboard"
FD="$HERE/experiments/feature-discovery"

MODE_DEFAULT=1 FAST=0 FULL=0 LIST=0
for a in "$@"; do
  case "$a" in
    --fast) FAST=1; MODE_DEFAULT=0 ;;
    --full) FULL=1 ;;
    --list) LIST=1 ;;
    -h|--help) sed -n '3,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "[run-tests] 知らない引数: $a" >&2; exit 2 ;;
  esac
done

NAMES=() RESULTS=() SECONDS_EACH=() NOTES=()
say() { echo; echo "===== $* ====="; }

# 段を 1 つ走らせる。依存（実行するもの）が無ければ ⏭ にして理由を残す ＝ ⚠ 黙って通さない
stage() {
  local name="$1" need="$2"; shift 2
  NAMES+=("$name")
  if [ ! -x "$need" ] && [ ! -f "$need" ]; then
    RESULTS+=("⏭"); SECONDS_EACH+=(0); NOTES+=("$need が無い")
    say "$name … ⏭ 飛ばす（$need が無い）"
    return
  fi
  if (( LIST )); then
    RESULTS+=("—"); SECONDS_EACH+=(0); NOTES+=("回す: $*")
    return
  fi
  say "$name"
  local t0 rc
  t0=$(date +%s)
  "$@"
  rc=$?
  SECONDS_EACH+=($(( $(date +%s) - t0 )))
  if (( rc == 0 )); then RESULTS+=("✅"); NOTES+=(""); else RESULTS+=("❌"); NOTES+=("終了コード $rc"); fi
}

# ---- 段（⚠ どれもモックだけ・サーバを起こさない）
run_executor() { ( cd "$LT" && "$FD/.venv/bin/python" -m pytest -q tests ); }
run_dashboard() { ( cd "$DASH" && .venv/bin/python -m pytest -q tests ); }
run_selftest() { ( cd "$SAMPLE" && ./selftest.sh ); }
run_mockrun() { ( cd "$LT" && ./mockrun.sh ); }
run_golden() { ( cd "$LT" && AIL_GOLDEN=1 "$FD/.venv/bin/python" -m pytest -q tests/test_sim_golden.py ); }

stage "執行器（pytest）" "$FD/.venv/bin/python" run_executor
stage "管理画面（pytest）" "$DASH/.venv/bin/python" run_dashboard
if (( ! FAST )); then
  stage "サンプルの selftest（モックに 6 手順）" "$SAMPLE/selftest.sh" run_selftest
  stage "執行器の mockrun（20 営業日）" "$LT/mockrun.sh" run_mockrun
fi
if (( FULL )); then
  stage "黄金の集計値（通し運転 64 営業日 × 2）" "$LT/tests/test_sim_golden.py" run_golden
fi

# ---- 報告
echo
echo "===== 結果 ====="
printf "%-40s %-4s %6s  %s\n" "段" "結果" "秒" "覚書"
bad=0
for i in "${!NAMES[@]}"; do
  printf "%-40s %-4s %6s  %s\n" "${NAMES[$i]}" "${RESULTS[$i]}" "${SECONDS_EACH[$i]}" "${NOTES[$i]}"
  [ "${RESULTS[$i]}" = "❌" ] && bad=1
done
(( LIST )) && { echo "（--list: 実際には回していない）"; exit 0; }
if (( bad )); then
  echo
  echo "⚠ 落ちた段がある。上の出力を見る（⚠ 黄金の集計値が落ちたときは、直す前に「なぜ変わったか」を確かめる）"
  exit 1
fi
echo "すべて通った"
