# live-trading — トレーダー 3 人の実売買の執行器

プラン [docs/plans/live-trading-three-models.md](../../docs/plans/live-trading-three-models.md)・記録と決めごと [docs/specs/experiments/live-trading.md](../../docs/specs/experiments/live-trading.md)。
⚠ **2026-08-27 の方針の 2 つ目の例外**（CLAUDE.md）。⚠ **本番の鍵を入れて起動するのは利用者**。

```bash
cd experiments/live-trading
../feature-discovery/.venv/bin/python -m pytest -q tests      # 合成規則・状態機械・予算・鍵・HALT・再送・按分
./mockrun.sh                                                  # モックで 20 営業日（2 人。差 1・内部移転・秘密の不在を検査）
KEEP_DIR=/tmp/lt ./mockrun.sh                                 # 同じ ＋ 記録を /tmp/lt に残す（管理画面の /live を AIL_LIVE_DIR=/tmp/lt で見る用）
../tastytrade-api-sample/.venv/bin/python run_day.py --traders test_a --mode plan                # 合図と計画だけ
../tastytrade-api-sample/.venv/bin/python run_day.py --traders test_a --mode dry-run --ignore-window   # cert で dry-run まで
```

| ファイル | 役割 |
| --- | --- |
| `trader.py` | トレーダーの定義（`config/traders/<名前>.toml`）と合成規則（そのまま／平均／多数決／全員一致） |
| `signals.py` | 各モデルの買い% ／ 出口% を集めてトレーダーごとに合成。モデルの `kind` は `fixed` / `file`（試験用）/ `experiment`（`predict.jsonl`。Phase 1 の後） |
| `state.py` | トレーダー × 銘柄の 0 ／ 1・持ち分・取得単価・受渡し待ち（`state/<env>/<名前>.json`） |
| `plan.py` | 状態機械 → 株数 → **予算の上限** → 銘柄ごとに全員ぶんを合算（A の売り × B の買いは内部移転） |
| `execute.py` | dry-run → 発注 → 約定確認 → 取消。`Session offline` は再送。鍵は `ttclient.Client` の 3 段そのまま |
| `ledger.py` | トレーダー別の損益 1 日 1 行（⚠ 損益で手法を採らない） |
| `run_day.py` | 1 営業日の窓（15:45〜16:05 ET）を 1 回通す。記録は `out/<日付>/*.jsonl`（`Masker` 経由） |
| `mockrun.sh` | モックで 20 営業日。`KEEP_DIR` を指定したときだけ、終了時に `config/`・`state/cert/`・`out/` をそこへ写す（指定しなければ消す） |

記録（`out/<日付>/`）: `signals.jsonl`（トレーダー × 銘柄）・`quotes.jsonl`・`orders.jsonl`（注文・dry-run・応答・遷移・約定・合図時の気配・誰の何株ぶんか）・`transfers.jsonl`・`positions.jsonl`・`balances.jsonl`・`ledger.jsonl`・`events.jsonl`（見送り・予算超え・拒否・再送）。
試験用トレーダー（`test = true`）の行には `test: true` が付く。モックの行には `mock: true` が付く。

安全: `HALT`（管理画面の停止ボタンと同じファイル。`TT_HALT_FILE`）／ 予算の上限（1 人ずつ・合計 `--max-total-budget`・1 日 `--max-day-usd`）／ 窓の外では発注しない ／ 本番の発注は `TT_ALLOW_PROD_ORDERS=1` ＋ `--i-know-this-is-real-money`（取消・dry-run の鍵では開かない）／ `--mode submit` 以外は状態を書かない。
