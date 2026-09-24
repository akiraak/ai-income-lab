# live-trading — トレーダー 3 人の実売買の執行器

プラン [docs/plans/live-trading-three-models.md](../../docs/plans/live-trading-three-models.md)・記録と決めごと [docs/specs/experiments/live-trading.md](../../docs/specs/experiments/live-trading.md)。
⚠ **2026-08-27 の方針の 2 つ目の例外**（CLAUDE.md）。⚠ **本番の鍵を入れて起動するのは利用者**。

```bash
cd experiments/live-trading
../feature-discovery/.venv/bin/python -m pytest -q tests      # 合成規則・状態機械・予算・鍵・HALT・再送・按分
./mockrun.sh                                                  # モックで 20 営業日（2 人。差 1・売りと買いが別々に出ること・秘密の不在を検査）
KEEP_DIR=/tmp/lt ./mockrun.sh                                 # 同じ ＋ 記録を /tmp/lt に残す（管理画面の /live を AIL_LIVE_DIR=/tmp/lt で見る用）
../tastytrade-api-sample/.venv/bin/python run_day.py --traders test_a --mode plan                # 合図と計画だけ
../tastytrade-api-sample/.venv/bin/python run_day.py --traders test_a --mode dry-run --ignore-window   # cert で dry-run まで
```

| ファイル | 役割 |
| --- | --- |
| `trader.py` | トレーダーの定義（`config/traders/<名前>.toml`）と合成規則（そのまま／平均／多数決／全員一致） |
| `signals.py` | 各モデルの買い% ／ 出口% を集めてトレーダーごとに合成。モデルの `kind` は `fixed` / `file`（試験用）/ `experiment`（`predict.jsonl`。Phase 1 の後） |
| `state.py` | トレーダー × 銘柄の 0 ／ 1・持ち分・取得単価・受渡し待ち（`state/<env>/<名前>.json`） |
| `plan.py` | 状態機械 → 株数 → **予算の上限** → **1 意図 1 注文・1 注文 1 トレーダー**（`to_orders`。⚠ 2026-09-19 から合算も内部移転もしない ＝ 約定価格も手数料もその人のもの。売りが先・買いが後） |
| `execute.py` | dry-run → 発注 → 約定確認 → 取消。注文ごとに金額の内訳 `amounts`（約定代金・手数料の内訳・差し引き。手数料は dry-run の見積り）。`Session offline` は再送。鍵は `ttclient.Client` の 3 段そのまま |
| `journal.py` ／ `recovery.py` ／ `reconcile.py` | **口座の建玉と台帳の帳尻**（[live-trading.md §0-8](../../docs/specs/experiments/live-trading.md)）: 発注の前に書く控え ／ 起動時に控えの未完を戻す ＋ 口座と台帳の突き合わせ（少ない銘柄だけ止める）／ 人が台帳を合わせる CLI（ネットワークを使わない） |
| `ledger.py` | トレーダー別の損益 1 日 1 行（⚠ 損益で手法を採らない） |
| `run_day.py` | 1 営業日の窓（15:45〜16:05 ET。⚠ NYSE の休場日と半日立会の日は拒否する ＝ `../tastytrade-api-sample/market_calendar.py`）を 1 回通す。記録は `out/<日付>/*.jsonl`（`Masker` 経由） |
| `mode.py` | 機械のモード（実売買 ／ シミュレーション。排他）と `run.lock`。`MODE` が無ければ real ＝ 今までどおり |
| `notprod.py` | **「本番の機械ではない」印** `NOT_PRODUCTION`（`status` ／ `set` ／ `clear`。⚠ 利用者だけ）。あると `run_day.py`・`sample.py` は**本番の発注だけ**を許可より前で拒む（rc=7）。切り替えの手順は [live-trading.md §0-14](../../docs/specs/experiments/live-trading.md) |
| `simclock.py` ／ `simdata.py` ／ `simrun.py`（`simrun.sh`）／ `simctl.py` | **シミュレーション**: 仮の時計 ／ 仮データ（日足の再生・合図）／ 運転手 ／ 操作の口（CLI だけ）。設定は `config/sim/<名前>.toml`（`sim1` 故障なし・`sim2` 筋書きつき）・トレーダーは `config/traders/sim_*.toml`。記録は `sim/<名前>/`（git 管理外・全行 `sim: true`）。使い方は [live-trading.md §0-7 (h)](../../docs/specs/experiments/live-trading.md) |
| `mockrun.sh` | モックで 20 営業日。`KEEP_DIR` を指定したときだけ、終了時に `config/`・`state/cert/`・`out/` をそこへ写す（指定しなければ消す） |

記録（`out/<日付>/`）: `signals.jsonl`（トレーダー × 銘柄）・`quotes.jsonl`・`orders.jsonl`（注文・dry-run・応答・遷移・約定・合図時の気配・誰の何株ぶんか）・`transfers.jsonl`・`positions.jsonl`・`balances.jsonl`・`ledger.jsonl`・`events.jsonl`（見送り・予算超え・拒否・再送）。
試験用トレーダー（`test = true`）の行には `test: true` が付く。モックの行には `mock: true` が付く。

安全: `NOT_PRODUCTION`（本番の機械ではない印 ＝ prod の submit を拒む）／ `HALT`（管理画面の停止ボタンと同じファイル。`TT_HALT_FILE`）／ 予算の上限（1 人ずつ・合計 `--max-total-budget`・1 日 `--max-day-usd`）／ 窓の外では発注しない ／ 本番の発注は `TT_ALLOW_PROD_ORDERS=1` ＋ `--i-know-this-is-real-money`（取消・dry-run の鍵では開かない）／ `--mode submit` 以外は状態を書かない。
