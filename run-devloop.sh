#!/usr/bin/env bash
# 開発ループの 1 晩（rules.md 31 章・プラン docs/plans/daily-dev-loop.md）: 段 0 門 → 段 1 アイデア出し → 段 2 作る → 段 3 回す → 段 4 選ぶ → 日誌 → commit。
#   ./run-devloop.sh                       # 今日の晩（timer が 01:00 PDT に起こす形）
#   ./run-devloop.sh --stages 0,4          # 門と選ぶ段だけ
#   ./run-devloop.sh --dry-run             # 外の世界を呼ばない（段 4 と日誌だけ）
#   ./run-devloop.sh --no-commit           # K5 の常設の許可を使わない
# 中身は experiments/feature-discovery の `cli.devloop night`。数と置き場は config/devloop.toml・止める手は DEVLOOP_HALT（同じディレクトリ）。
# ⚠ titan でだけ回す（研究の DB と GPU が要る）。13500t・prod・執行器・管理画面 app/ は触らない。
set -uo pipefail
# ⚠ timer の環境（systemd --user）は PATH に ~/.local/bin（claude の実体）を持たない【実測 2026-10-25】
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")" && pwd)"
FD="$ROOT/experiments/feature-discovery"
PY="$FD/.venv/bin/python"
LOGDIR="$FD/devloop/logs"
mkdir -p "$LOGDIR"
if [ ! -x "$PY" ]; then echo "⚠ $PY が無い（experiments/feature-discovery の .venv を先に作る）" >&2; exit 1; fi
if [ -f "$ROOT/experiments/live-trading/MODE" ] && [ "$(cat "$ROOT/experiments/live-trading/MODE" 2>/dev/null)" != "real" ]; then
  echo "ℹ 機械のモードは $(cat "$ROOT/experiments/live-trading/MODE")（開発ループには関係しない）" >&2
fi
cd "$FD" || exit 1
echo "===== $(date '+%Y-%m-%d %H:%M %Z') run-devloop $*" >> "$LOGDIR/$(date +%F).log"
"$PY" -m cli.devloop night "$@" 2>&1 | tee -a "$LOGDIR/$(date +%F).log"
exit "${PIPESTATUS[0]}"
