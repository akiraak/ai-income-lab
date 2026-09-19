# 実売買 — トレーダー 3 人に予算を割り振り、tastytrade の本口座で売買して執行の差を記録する

プラン: [docs/plans/live-trading-three-models.md](../../plans/live-trading-three-models.md)。コード: `experiments/live-trading/`。
⚠ **2026-08-27 の方針の 2 つ目の例外**（CLAUDE.md）。判定するのは**執行の差と無人運転**であって「儲かったか」ではない。

## 0. 決めごと（事前固定）

⚠ **2026-09-17 の利用者決定: 実際に動かすトレーダーの属性はまだ設定しない。実売買のテストは先に行う**（プラン §0-1）。
この節のうち **§0-1 の「実際に動かす 3 人」だけが未設定**で、他は固定済み。

### 0-1. トレーダーの定義と、実際に動かす 3 人（⚠ 未設定）

| 属性 | 型 | 置き場 |
| --- | --- | --- |
| 名前 | 文字列（記録と画面の鍵。変えない） | `config/traders/<名前>.toml` の `name` |
| 予算 | $（執行器が超える買いを拒む） | `budget_usd` |
| 銘柄集合 | 銘柄の配列、または feature-discovery の `universe` 名 | `symbols` ／ `universe` |
| 予測モデル | 1 本以上。`kind = fixed`（固定の合図）／ `file`（CSV の合図）／ `experiment`（実験名。`predict.jsonl` を読む） | `[[models]]` |
| 合成規則 | `asis`（1 本のとき）／ `mean` ／ `majority`（奇数本）／ `unanimous` | `combine` |
| 閾値 θ | 50 以上（rules.md 13-3） | `threshold` |
| 株数の決め方 | `shares`（整数株）／ `notional`（金額指定 `Notional Market`。§0-3 で通ったときだけ） | `sizing` |
| 試験用 | `fixed` ／ `file` を持つトレーダーは `test = true` が要る。記録に `test: true` が付き判定に混ぜない | `test` |

| トレーダー | モデル | θ | 予算 | 銘柄集合 | 状態 |
| --- | --- | --- | --- | --- | --- |
| T1 ／ T2 ／ T3 | **未設定** | — | — | — | ⚠ 候補はプラン §2-2（T1 own_ridge θ=50 ／ T2 ownex_lgbm θ=55 ／ T3 ownseq_ridge θ=50、$300 × 3）。決めたらここに書き、利用者の確認を取ってから Phase 1 へ |
| `test_a`（試験用） | `file`（`config/signals/test_a.csv`。日付ごとに 買い ／ 出口 を手で書く） | 50 | $30 | `T`（us63 で株価が最も低い。$25.4【実測 2026-09-18 00:00 ET の気配】） | ✅ 固定。本番の最小額 1 発注に使う |

#### トレーダーのお金と持ち株（2026-09-18。利用者の決定）

⚠ **目的は「予算を上手く増やす」シミュレーション**。口座は 1 つだが、お金と持ち株はトレーダーごとに分けて扱う（口座は合算しか見せないので、執行器が按分して持つ）。

| 決めごと | 中身 | 実装 |
| --- | --- | --- |
| 持ち株 | トレーダーごと（1 人 1 ファイル。銘柄・株数・取得単価・建てた日） | `state/<env>/<名前>.json`（`state.py`） |
| 売り | ⚠ **自分の持ち株だけ売れる。他のトレーダーが買った株は売れない**。同じ回で A の売りと B の買いが同じ銘柄なら、口座に出さず内部で移す（A が自分の株を B に渡す） | `plan.py` の `decide`・`size_intents`・`aggregate` |
| 持ち金 | ⚠ **固定の予算枠**。空き ＝ 予算 − 建玉の取得原価 − 受渡し待ちの売却代金。⚠ **実現損益と手数料は空きに入れない**（得をしても損をしても、次の空きは予算まで戻る。増えた分は使わずに残る） | `plan.py` の `size_intents` |
| 自分の予算枠が足りない | 注文を出す前に見送る。事象 `over_budget` ／ `too_small` として記録する（⚠ エラーではなく「見送り」） | 同上 |
| 口座全体の歯止め | ⚠ **置かない**（発注の前に口座の使える現金と比べない）。⚠ 全員が損をすると空きの合計が口座の現金を超えうる（予算 $300 × 3 ・停止条件 20% なら最悪 $180 で、予備 $100 を超える【推測】） | — |
| 口座に断られた買い | ⚠ **エラーとして記録する**。`orders.jsonl` の状態が `error`（dry-run ／ 発注で拒否）か `Rejected`（受付後に拒否）で、ブローカーのエラーの種類・コード・文面を残す。資金不足のような 4xx は再送しない（再送は `Session offline` と 5xx だけ）。約定が無いので持ち株と損益は変わらない。管理画面の「問題」に数える | `execute.py` の `run_one`・`is_transient` |
| 断られたときの巻き込み | ⚠ 同じ銘柄の買いは全員ぶんを 1 注文にまとめるので、断られると乗っていた全員が一緒に失敗する（誰の何株ぶんかは `parts` に残る） | `plan.py` の `aggregate` |
| 1 日の売買回数 | トレーダーの判断（CLAUDE.md の 2 つ目の例外。2026-09-18）。⚠ **いまの執行器は 1 日 1 回の窓（§0-2）のまま** | — |

- ⚠ 資金不足のときに tastytrade が返すエラーコードはまだ見ていない【未確認】。「資金不足」の分類を別に作るのは、実際に 1 度起きてコードを確かめてから
- ⚠ 「予算を増やせたか」はトレーダーの目標であって、判定には使わない（CLAUDE.md の例外: 損益で手法を採らない）

### 0-2. 予算の上限・執行の窓・停止条件・判定の閾値

| 項目 | 値 | 執行器での実装 |
| --- | --- | --- |
| 全トレーダーの予算の合計の上限 | **$1,000**（口座の残高【実測 2026-09-16】） | `run_day.py --max-total-budget`（既定 1000。超えると起動しない） |
| 1 日の買いの合計の上限 | **$1,000** | `--max-day-usd`（既定 1000） |
| 1 人の予算 | `budget_usd`。空き ＝ 予算 − 建玉の取得原価 − 受渡し待ちの売却代金（暦日 T+1・保守側） | `plan.size_intents`（超える買いは `over_budget` ／ `too_small` の事象として記録し見送る） |
| 執行の窓 | **15:45〜16:05 ET**（⚠ **NYSE の営業日**。2026-09-18 から休場日を暦で見る）。合図は 15:50 の気配、注文は成行 | `run_day.window_refusal`（`in_window`）。外では発注せず、理由を `events.jsonl` の `out_of_window.reason` に残す（`--ignore-window` はテスト用）。暦は `tastytrade-api-sample/market_calendar.py`（管理画面と同じもの。【公表値】NYSE・取得 2026-09-18） |
| 半日立会の日 | ⚠ **発注しない（未決のあいだの既定）**。13:00 ET 引けなので、窓 15:45〜16:05 は引けの後になる。20 営業日の実験にかかるのは **2026-11-27（金）と 2026-12-24（木）** | `window_refusal` が「半日立会」で拒否する。⚠ **窓を 12:45〜13:05 に動かすかは利用者の裁定**（TODO の D13） |
| 未約定の取消 | 窓の終わり（発注から 600 秒） | `--cancel-after` |
| 再送 | `Session offline` と 5xx は 60 秒おき 3 回まで。再送の前に `external-identifier` で重複を探す。4xx と認証の 401 は再送しない | `execute.is_transient` |
| 停止 | `HALT`（管理画面の停止ボタンと同じファイル）／ 含み損が予算の 20%（`ledger.jsonl` の `drawdown_pct_of_budget`。⚠ 自動で投げ売りしない。発注をやめて持ち越す）／ 説明できない差が 3 日続く（利用者が止める） | `HALT` は執行器が毎回見る |
| 判定の閾値（20 営業日） | プラン §2-4 の表（執行価格の差 中央値 ≤ 5bp ／ 紙上 − 実物 ≤ 5bp/日 ／ 人手 0 回 ／ 執行できた日 95% 以上） | Phase 6 |

### 0-3. 本番の dry-run — 端株・小数株・成行・MOC 相当（⚠ 利用者が流す。未実施）

`experiments/tastytrade-api-sample/sample.py --step dryrun2 --allow-prod-dry-run` が 6 通りを dry-run だけで通し、記録（`out/tastytrade-prod-*.jsonl` の step 10）に残す。⚠ **何もルーティングしない。**

| 鍵 | 注文 | 知りたいこと | 結果 |
| --- | --- | --- | --- |
| `notional_market_5usd` | `Notional Market` $5.00 | 金額指定の端株が API から出せるか（【記憶・未確認】） | ⏳ |
| `limit_fractional_0.01` | 指値 0.01 株 | 小数の `quantity` が通るか | ⏳ |
| `market_fractional_0.01` | 成行 0.01 株 | 同上（成行） | ⏳ |
| `market_1` | 成行 1 株 | 基準 | ⏳ |
| `market_on_close_1` | `Market On Close` 1 株 | MOC 相当の種別があるか（無ければエラー文に使える種別の一覧が出る見込み） | ⏳ |
| `limit_1_low` | 指値 1 株（気配の 8 割） | 対照（2026-09-05 に通っている） | ⏳ |

結果で決まるもの: `test_a` と実際の 3 人の `sizing`（端株が通れば `notional` で $5 刻み、通らなければ `shares` で銘柄を絞る。プラン §2-3）。

### 0-4. cert の dry-run【実測 2026-09-18 00:00 ET】— 配線は最後まで通った

`run_day.py --traders test_a --date 2026-09-18 --mode dry-run --ignore-window` を市場時間外に流した。

| 段 | 結果 |
| --- | --- |
| 認証（cert）| 1 回目・2 回目は nginx の **502**（深夜の cert）。3 回目で通った → 5xx は 30 秒待って 1 回取り直す規則を足した（401 は再試行しない） |
| 口座・残高・建玉 | cert の $100,000 を読めた |
| 気配 | ⚠ cert は配信しないので**本番の資格情報で読む**。T bid 25.40 / ask 25.45 / last 25.44（`updated-at` 00:00:01 ET） |
| 合図 → 計画 | `test_a.csv` の 9/18 の行（買い 100）→ 未保有 → 買い → $30 ÷ 1 銘柄 ÷ $25.42 → **1 株** |
| dry-run（cert） | 1 回目 **504** `timeout_error`・2 回目 **502** → 3 回目で API に届き、**422 `tif_no_after_hours_opening_market_orders`（市場が閉まっていると建玉を作る成行は出せない）** |

⚠ 市場時間外の拒否は想定どおり（2026-09-08 の sample と同じ）。**市場時間の sandbox リハーサル（Phase 2 の 6）が残る。** 記録は `experiments/live-trading/out/2026-09-18/`（口座番号・トークンは出ていないことを grep で確認）。

### 0-5. 実売買のテストの手順書（Phase 5-1。⚠ 利用者が行う）

⚠ **Claude はここまで。鍵を入れて起動するのは利用者。** 時刻は ET（EDT のいまは JST −13 時間。15:55 ET ＝ 翌 04:55 JST）。

**前日まで**

1. `sample.py --step dryrun2 --allow-prod-dry-run` を流し、§0-3 の表を埋める（Claude が記録から写す）
2. `experiments/live-trading/config/signals/test_a.csv` の日付を、**買う日**（1 行目 `buy=100`）と**手仕舞う日**（翌営業日 `exit=100`）に書き換える
3. `cd experiments/live-trading && ../feature-discovery/.venv/bin/python -m pytest -q tests && ./mockrun.sh` が通ることを見る
4. 管理画面（`dashboard/`）で停止ボタンが押せる状態にしておく（`HALT` は `experiments/tastytrade-api-sample/out/HALT`）

**買う日（15:45〜16:04 ET）**

```bash
cd experiments/live-trading
PY=../tastytrade-api-sample/.venv/bin/python
# (a) 本番の dry-run（何もルーティングしない）。注文 1 件が dry-run で通り、buying-power-effect が $25 前後であること
$PY run_day.py --traders test_a --env prod --mode dry-run --allow-prod-dry-run
# (b) 本番の発注（実弾）。鍵 2 つを両方付ける。記録は out/<日付>/orders.jsonl
TT_ALLOW_PROD_ORDERS=1 $PY run_day.py --traders test_a --env prod --mode submit --i-know-this-is-real-money
```

**手仕舞う日（15:45〜16:04 ET）**: 同じ 2 行。`test_a.csv` のその日の行が `exit=100` なので売りが出る。

**チェックリスト（買う日・手仕舞う日それぞれ）**

- [ ] `orders.jsonl` の `final_status` が `Filled`・`fills[].price` と `quote_at_signal.mid` の差（bp）
- [ ] `positions.jsonl` の `after` に T 1 株（買う日）／ 無し（手仕舞う日）
- [ ] `state/prod/test_a.json` の `holdings`・`realized_usd` が口座と一致
- [ ] `events.jsonl` に `retry` ／ `halted` ／ `over_budget` が無いか（あれば理由を §1 に書く）
- [ ] `grep -r "<口座番号の下 4 桁>" out/` が空（秘密が出ていない）
- [ ] 何かおかしければ管理画面の停止ボタン（`HALT` ＋ 全取消）。執行器は次の起動で `HALT` を見て発注しない

**⚠ 先に指摘するもの**: 現金口座なので、手仕舞った代金は T+1 まで再投資に使わない（執行器も `unsettled` として差し引く）／ 同日の往復は作らない（PDT）／ wash sale は 1 銘柄 1 往復なら起きない。

## 1. 記録（日次）

Phase 5-1・Phase 6 で埋める。1 日 1 行 × トレーダー。数字は全部【実測】。

| 日付 | トレーダー | 注文 | 約定 | 差 1（気配 → 約定 bp） | 事象 | 備考 |
| --- | --- | --- | --- | --- | --- | --- |
| （まだ無い） | | | | | | |

## 2. 判定

20 営業日の後に §0-2 の閾値で書く（Phase 6）。⚠ **損益で手法を採らない。**
