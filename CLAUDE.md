# ai-income-lab

## プロジェクトの目的

AI を使って収入を稼ぐ方法を体系化し、**机上で検証する**ためのプロジェクト。

**2026-08-27 に方針を変更した。実際に資金・機材を動かす実行（購入・口座開設・出品・登録・リリース）は行わない。**
一次情報の調査と試算にもとづいて「その手法が成立するか / 自分の条件で採れるか」を判定するところまでを成果物とする。

**2026-09-05 に tastytrade の API 検証だけ例外を設けた（[プラン §3](docs/plans/archive/tastytrade-api-sample.md) の選択肢 (c)）。**
この 1 手法に限り、sandbox ユーザーの作成・本口座の開設・入金・本番での 1 株の発注まで行う。ただし、

- **口座開設・入金・本番発注を実行するのは利用者**。Claude は手順とコードを示すところまでで、実行しない
- 実測するのは **API の挙動**（認証の寿命・遅延・レート制限・往復時間・約定価格と気配の差）。
  収入・費用の【実測】を取りにいくものではない
- 他の手法には広げない。同じことをしたくなったら、その手法ごとにここへ追記する

**2026-09-17 に 2 つ目の例外を設けた（[プラン](docs/plans/live-trading-three-models.md)。利用者の指示）。**
tastytrade の本口座で、**トレーダー（Trader）3 人に予算を割り振って売買し、執行の差を記録する**。トレーダーは擬人化した売買判断の単位で、予算・銘柄集合・**1 本以上の予測モデル**・合成規則・閾値を持つ（プラン §2-1）。
**2026-09-18: 1 日に何回売買するかはトレーダーの判断とする（利用者の指示）。** モデルはそれぞれのルールで 1 日に何回でも更新してよく、その提案を受けて売買するかどうかをトレーダーが決める（例: モデル A が買いを提案しても、今日すでに買っていれば買わない）。ただし、

- **本番の鍵を入れて執行器を起動するのは利用者**。Claude はコードと手順まで（鍵を `.env` に書かない・本番で起動しない）
- 判定するのは **執行の差（合図時の気配・約定・終値）と無人運転の成立**。「儲かったか」で手法を採らない（数週間では統計的に判定できない）
- 鍵の 3 段・`HALT`・停止ボタンは API 検証のものをそのまま使う。取消の鍵で発注は開かない
- 予算の上限は事前に書き（`docs/specs/experiments/live-trading.md` §0）、執行器がそれを超える買いを拒む（⚠ 1 日に何回売買しても同じ）

## 進め方

- 収益化の手法を洗い出し、一次情報（公表単価・稼働率・規約・法令・税制）を当たって成立条件を詰める
- 各手法について、初期コスト・投下時間・想定収入を【推測】として試算し、根拠となる出典を残す
- 判断は「実行して収入が出たか」ではなく「**一次情報と試算が成立条件を満たすか**」で行う。満たさないものは理由を残して打ち切る
- **2026-09-12: 机上の検証（`experiments/feature-discovery/` の実行）は、コストが低いので可能性が低くても積極的に回す。空白は積極的に埋める。**
  - 「可能性が低いから回さない」は理由にならない。回さないのは「測れない」（[rules.md 14-2](docs/specs/experiments/feature-discovery/rules.md)）か規約に反するときだけ
  - 理由と代償の数字は [rules.md 14-10](docs/specs/experiments/feature-discovery/rules.md)。⚠ **試行を増やしても採否は厳しくならない**（判定式に DSR は入らない）
  - ⚠ **緩めないもの**: 回すと決めるのは結果を見る前 ／ 回したものは全部 `n_trials` に数える ／ leak 対照 ／ 1 実行 1 ディレクトリ
- 収入・費用の【実測】は今後取得しない。過去に取得済みの【実測】（`docs/specs/experiments/i7-dataset.md` のパイロット等）はそのまま残す
  - 例外: **API の挙動**（認証の寿命・遅延・レート制限・往復時間）は 2026-09-05 から【実測】を取る（tastytrade の検証。上の例外を参照）
  - 例外: **実売買の執行の差とトレーダー別の損益**は 2026-09-17 の 2 つ目の例外で【実測】を取る。⚠ **損益で手法を採らない**（上の例外を参照）

## Claude への依頼方針

- アイデアを出すときは、必要な作業量・初期コスト・収益化までの想定期間もあわせて示す
- **金銭・契約が発生する行動は提案と試算までとし、実行しない**。手順を示すのは可、代行や実行は不可
- 数値（収入、費用、期間）を書くときは、**【実測】/【公表値】/【推測】** のどれかを必ず明示する。根拠のない金額を断定しない
  - 【公表値】は出典（URL・取得日）を併記する。出典が取れないものは【推測】として扱う
- 法規制・各サービスの利用規約（AI 生成物の扱い、アフィリエイト規約など）に触れる施策は、その旨を先に指摘する

## ドキュメントの書き方

- 説明が構造・流れ・位置関係を含むときは、文章だけで済ませず **図を使う**（Mermaid で書く）
  - vibeboard が mermaid@11 でレンダリングするので、` ```mermaid ` フェンスで書けばそのまま閲覧できる
  - プランや仕様書では、各層・各フェーズに最低 1 枚は図を置く
- 図の原則
  - **1 図 1 主張**。その図で何を言いたいのかを図の直前に 1 行書く
  - **一覧や属性は表**、図は関係・流れ・位置を示すときだけ使う
  - ノードは 12 個以内。超えたら図を分割する
  - 本文と図が食い違ったら本文を正として図を直す

## 現状

ドキュメント中心。アプリ実装は `dashboard/`（売買システムの管理画面。2026-09-05）が最初の 1 つ。

- `TODO.md` / `DONE.md` — タスク管理
- `docs/plans/` — 作業プラン（完了したものは `docs/plans/archive/` へ）
- `docs/specs/` — 成果物となる仕様・体系
- `docs/specs/experiments/` — 検証タスクごとの調査結果・試算・判定の記録（1 手法 1 ファイル）
- `experiments/` — 調査用コード（1 手法 1 ディレクトリ）。2026-08-27 の方針変更以降は新規追加の予定なし（例外: `experiments/live-trading/`。2026-09-17 の 2 つ目の例外の執行器）
- `dashboard/` — **売買システムの管理画面**（実運用の監視 ＋ 開発時の検証）。仕様は `docs/specs/dashboard.md`
- `vibeboard/` — 開発管理画面（vendor 済み）。`docs/` と `TODO.md` を見るためのもので、`dashboard/` とは別物

## 管理画面 (dashboard)

```bash
cd dashboard
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                                   # AIL_AUTH_MODE=local（既定は loopback）
./run.sh                                               # http://127.0.0.1:3012
# または プロジェクト直下から（⚠ **ポートを掴んでいるプロセスを止めてから起動する**）
../run-server.sh                                       # --port N / --no-kill / --demo
.venv/bin/python -m pytest -q tests                    # 面の判定・JWT・記録と判定・秘密が応答に出ないこと
```

- `experiments/tastytrade-api-sample/` の `ttclient.py` / `record.py` を import し、記録（`out/*.jsonl`）をそのまま読む。資格情報もサンプルの `.env` を読む
- 画面（2026-09-18 に作り直した。デザイン 3「数字とグラフが主役」・ナビは左ペイン）: **概要（`/`。監視の帯・大きな数字・損益の推移・執行の差・トレーダーの段）**・全体の詳細（`/overall`。日次・注文の履歴・口座と接続）・トレーダーの詳細（`/traders/<name>`）・記録と差分（`/records`）・6 観点の自動判定（`/judge`。観点 A まで）・操作（`/ops`。**停止 ／ 解除と履歴だけ**）。`/live` は `/` へ転送。**操作はローカル面だけ**。⚠ **検証（`/experiments`）・データ（`/data`）・手動の注文（dry-run ／ 発注 ／ 取消 ／ 後片付け）・開発（`/dev`）は 2026-09-18 に経路ごと消した** ＝ ⚠ **管理画面に発注の経路は無い**（発注は執行器と CLI だけ。`ops.py` のクライアントは取消の鍵しか開けない）。残した部品: `app/experiments.py`・`app/inventory.py`（vibeboard のタブが import）・`devtools` の `MockServer`・`run_step`（デモ）。⚠ **紙上の損益・差 3 は、執行器の `out/daily.csv`（`paper.py`）があれば本物・無ければ仮データ**（印つき。仮は `/api/live` に出さない）。休場日は NYSE の暦（`experiments/tastytrade-api-sample/nyse_calendar.py`。執行器の窓と共有。⚠ **年に 1 度、次の年を足す**。載っていない年だけ「仮」に戻る）。図は `app/charts.py`（サーバで組む SVG）。仕様は `docs/specs/dashboard.md` §13・§15
- **シミュレーションモード**（2026-09-19）: 機械のモード（`experiments/live-trading/MODE`）が sim の間、管理画面は `sim/<名前>/` だけを読み、全ページの最上部に青緑の帯・`<title>` に `[SIM]`・数字に「仮」の印・`/api/*` に `mode`（`app/simmode.py`。リクエストごとに読む）。⚠ **表示だけ**（切り替え・速さ・停止は CLI の `simctl.py`）。⚠ **1 つの画面に本物とシミュレーションを混ぜない**（モードと木が食い違えば数字を出さない）。⚠ 公開面は `MODE` を読まない。仕様は `docs/specs/dashboard.md` §13-6・§15-11
- **検証の部品**（`app/experiments.py`。画面は vibeboard の検証タブ。管理画面の `/experiments` は 2026-09-18 に消した）は `experiments/feature-discovery/runs/` を**読むだけ**（`AIL_RUNS_DIR`）。**スコアは最良手法（基準線を除く）の純利 bp** で、fold の符号・上乗せ t・実効標本数・デフレーテッド SR を横に並べる。⚠ **検査は実験側が `checks.json` に書いたものを読むだけ**（管理画面に pandas / scipy を入れない）。仕様は `docs/specs/dashboard.md` §10
- **鍵なしでも動く（デモ）**: 資格情報が無いか `AIL_DEMO=1` なら、起動時にモックサーバを立てて全画面にモックのデータを出す（帯に「デモ」）。データは `data/demo/` に分ける。仕様 §6-2。実売買の画面は執行器のモックの記録（`dashboard/demo/live/`）を読む（`AIL_LIVE_DIR` を指定したときはそれ）
- 面は `AIL_AUTH_MODE`: `loopback`（既定）/ `local`（＋ LAN）/ `cloudflare`（公開面。Access の JWT を全リクエストで検証。**監視と停止だけ**）
- **停止ボタン** ＝ 記録ディレクトリに `HALT` を書き、働いている注文を全部取り消す。`sample.py` も `HALT` があると発注系の手順を拒否する
- 本番の鍵はサンプルと同じ 3 段（dry-run `TT_ALLOW_PROD_DRY_RUN=1` / 取消 `allow_prod_cancel` / 発注 `TT_ALLOW_PROD_ORDERS=1` ＋ 確認文）。**取消の鍵で発注は開かない**。⚠ **管理画面が使うのは取消の鍵（停止ボタン）だけ**（2026-09-18。dry-run と発注の鍵は執行器と `sample.py` のもの）
- 秘密（client secret・トークン・口座番号）はブラウザに送らない。全応答が `Redactor` を通る
- **i マークのヘルプ**（2026-09-18）: 見出しの横に `{{ info("語") }}`。⚠ **文面の正本は `dashboard/glossary.toml`**（vibeboard の用語タブと同じ 1 本。説明を templates や Python に書かない）。`<details>` ＋ `app.js`（CSP の内）。⚠ **`<p>`・`.scroll-x`・左ペイン・停止ボタンの横には置かない**。語を消す・改名すると `tests/test_help.py` が落ちる。仕様と手順は `docs/specs/dashboard.md` §15-10
- g3plus に載せる契約は `docs/specs/dashboard.md` §7。デプロイ設定・公開ホスト名・Access は **g3plus-ops（private）側にだけ書く**
- 起動は `dashboard/run.sh`、または**プロジェクト直下の `run-server.sh`**（⚠ **既にポートを掴んでいるプロセスを止めてから起動する**）。⚠ **プロセスは名前ではなくポートから引く**（`pgrep -f` のパターンは自分自身のコマンドラインにも当たるため）。⚠ **vibeboard（3010）は触らない**
- vibeboard との棲み分け: vibeboard はこのリポジトリの文書とタスクを見る**開発用**、dashboard は tastytrade の口座と記録を見る**運用用**。ポートも別（3010 / 3012）

## 実験コード

### experiments/i7-dataset（I7 案 B: 日本語評価セットの生成）

```bash
ollama pull qwen3:8b                                   # 生成モデル（Apache 2.0、自前ホスト）
python3 experiments/i7-dataset/generate.py --n 10      # 生成 → out/pilot.jsonl
python3 experiments/i7-dataset/validate.py             # スキーマ・重複・実在名の検査
```

- 本文は Apache 2.0 / MIT モデルか DeepSeek / Mistral API で生成する。**Claude・OpenAI・Gemini で本文を書かない**（権利処理の結論。`experiments/i7-dataset/README.md`）
- `out/` は git 管理外。生成物は AWS Data Exchange 等の認証付き経路でのみ配布する
- I7 の検証は 2026-08-26 に打ち切り。パイプラインは他の合成データ実験に再利用できる。Ollama サーバは停止中（使うときは `nohup ollama serve &`）

### experiments/tastytrade-api-sample（tastytrade の API 取引サンプル）

```bash
cd experiments/tastytrade-api-sample
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./selftest.sh                                          # 資格情報なしでモックに 6 手順を通す
.venv/bin/python sample.py --step all                  # cert（sandbox）で 6 手順 → out/*.jsonl
```

- 既定は sandbox（cert）。**本番では発注系（手順 4・5）を拒否する**。実弾は `TT_ALLOW_PROD_ORDERS=1` と `--i-know-this-is-real-money` の両方が要る。取消だけの鍵 `allow_prod_cancel` は管理画面の停止ボタン用（発注は開かない）
- 資格情報（`.env`）と実行記録（`out/`）は git 管理外。記録はトークン・口座番号をマスクしてから書く。接続先を差し替えた実行（selftest）は `mock: true` が付く
- `out/HALT` があると発注系の手順（4・5・5limit・6）を拒否する（管理画面の停止ボタンが置く。`TT_HALT_FILE` / `TT_OUT_DIR` で場所を変えられる）
- sandbox は相場データを配信しない（`/market-data` が全経路 502）ので、気配は本番の資格情報で読む
- 記録と判定は `docs/specs/experiments/tastytrade-api-sample.md`。**2026-09-08 の市場時間に 6 観点のうち 5 つが ✅**（残るは営業日を 2 日跨ぐ交換だけで、g3plus の管理画面が監視を回して自動で埋める）
- ⚠ **2026-09-08 に踏んだ落とし穴 3 つ**（自動売買を書くときに効く）
  - **cert は市場時間内でも `Session offline` で注文を拒否することがある**（同時刻の `market-time` は `Open`）。25 分後には通った。拒否を「注文の中身が悪い」と読まず、時間をおいて再送する
  - **`/accounts/{n}/orders/live` は「その日の注文」**で、Filled / Cancelled / Rejected も混ざる。働いている注文は `Received / Routed / In Flight / Live / Contingent` で絞る
  - **気配の遅延は開発機（WSL2）の時計では測れない**（±1 秒揺れて負にもなる）。同じ応答の `Date` で補正した `delay_corrected_s` を使う

### experiments/feature-discovery の `cli.scenario`（条件付き GAN のシナリオ予測。2026-09-19 に「落とす」）

```bash
cd experiments/feature-discovery
.venv/bin/python -m cli.scenario run --config cgan_spy            # 5 fold × 種 3 を学習 → 評価 → 判定（GPU で約 15 分【実測】）
.venv/bin/python -m cli.scenario_report --run runs/<実行> --out ../../docs/specs/experiments/assets   # 表・再現の確認・図（SVG）
.venv/bin/python -m cli.scenario predict --run runs/<実行> --latest   # 保存した重みから予測（JSON）
```

- ⚠ **`cli.run`・台帳とは別の物差し**（過去 60 日を条件に次の 5 日の分布を 1,000 本生成 → CRPS）。実行は `runs/<時刻>_scn_<名前>/` に残すが `summary.csv` を書かないので、台帳・検証タブには出ない。**台帳の n_trials は動かさず、試した構成は記録の §0-3 に数える**
- ハイパラ・分割・採用基準は `config/scenario/cgan_spy.toml` に事前固定（テストが写しを持つ）。⚠ **結果を見てから動かして回し直さない**（テストの 5 塊は既に 1 度見た）。変えるなら新しい構成として先に登録する
- 記録と判定は `docs/specs/experiments/cgan-scenario.md`（cGAN 0.01210 ／ 履歴ベース 0.01195 ／ 軽量モデル 0.01146。CRPS は低いほどよい）

### experiments/feature-discovery の上位 K（保有できる銘柄数に上限を置く。2026-09-19 に机上で回した）

```bash
cd experiments/feature-discovery
.venv/bin/python -m cli.queue --config topk          # 本番 3 ＋ leak 3（2 分【実測】）。⚠ 回し直すと同じ鍵の実行が増えるだけ（`topk_vol` は基準線を足した 2 回目）
```

- config の `[trading] top_k = [...]`（＋ `top_k_budgets_usd`）があるときだけ、既存の行の後に上位 K の行（`手法〔上位3・端数〕` ／ `〔上位3・整数株A〕` ／ `〔上位3・整数株B⚠〕`）と基準線 `基準 乱択上位〔…〕` を足す。⚠ **`top_k` の無い config は経路が 1 行も変わらない**。規約は rules.md 17 章、記録は `docs/specs/experiments/topk-holdings.md`
- 結果: 採る 0 ／ 45。⚠ **数字は良く出たが順位の情報ではなかった**: モデルを使わず直近 60 日の値動きの大きさで並べる基準線 `基準 ボラ上位〔…〕`（rules.md 17-7。表に `close` があれば自動で出る）が、T1・T3 の端数 10 対のうち 9 対でモデルの順位を上回った ＝ 上乗せの正体は荒い銘柄への集中 × 生存バイアス。T2 は 96% の日で全銘柄の買い% が同点（銘柄を選んでいない）。⚠ **この結果で実売買の K を選ばない**。⚠ **銘柄をまたいで並べる規則を試すときは、モデルを使わない並べ方の基準線を最初から置く**
- ⚠ **規模 B（$10,000）の行は実際の取引で使えない可能性がある**（手法名の ⚠）

### experiments/live-trading（実売買の執行器。2026-09-17 の 2 つ目の例外）

```bash
cd experiments/live-trading
../feature-discovery/.venv/bin/python -m pytest -q tests      # 合成規則・状態機械・予算・鍵・HALT・再送
./mockrun.sh                                                  # モックで 20 営業日（記録に mock: true）
../tastytrade-api-sample/.venv/bin/python run_day.py --traders test_a --mode dry-run --ignore-window   # cert で dry-run
```

- トレーダーは `config/traders/<名前>.toml`（予算・銘柄集合・モデルの一覧・合成規則・θ・`sizing`）。モデルの `kind` は `fixed` / `file`（試験用。`test = true` が要る）/ `experiment`（`predict.jsonl`。Phase 1 の後）
- **実際に動かす 3 人は 2026-09-19 に確定**（利用者決定）: `T1` `trade_own_ridge_a` θ=50 ／ `T2` `trade_ownex_lgbm_a` θ=55 ／ `T3` `trade_ownseq_ridge_a` の T3 QUANT θ=50。予算は 2 つの規模を並べて持つ（2026-09-19 の利用者決定）＝ **規模 A $1,000**（$300 × 3 ＋ 予備 $100。口座の残高 ＝ 実際に使える。執行器の上限の既定）／ **規模 B $10,000**（$3,000 × 3 ＋ 予備 $1,000。⚠ **規模 B（$10,000）は実際の取引で使えない可能性がある** ＝ 数字を出すときは必ずそう添える。執行器では `--max-total-budget 10000 --max-day-usd 10000` を明示したときだけ。追加入金は利用者の判断）・合成 `asis`・種 0。⚠ **銘柄集合と `sizing` は未設定**（本番の `dryrun2` で端株が通るかで決まる。`live-trading.md` §0-1）。⚠ **ここから先にモデル・θ・合成規則を替えるのは新しい試行**。`config/traders/T1〜T3.toml` はまだ無い。試験用の `test_a`（固定の合図・1 銘柄・最小額）で配線と本番の 1 発注を先に通す順は変わらない
- 記録は `out/<日付>/*.jsonl`（`Masker` 経由・git 管理外）、状態は `state/<env>/<名前>.json`。`--mode submit` 以外は状態を書かない
- 本番の鍵は `ttclient.Client` の 3 段そのまま。発注は `TT_ALLOW_PROD_ORDERS=1` ＋ `--i-know-this-is-real-money`。⚠ **鍵を入れて起動するのは利用者**。`HALT` は管理画面の停止ボタンと同じファイル
- **シミュレーション（仮データと仮の時計で執行器を通しで動かす）は 2026-09-19 に実装した**（決めごと・使い方・筋書き・見つかったことは `live-trading.md` §0-7。プランは `docs/plans/archive/live-trading-sim-clock.md`）

  ```bash
  ./run-sim.sh --fresh --speed max       # ⚠ **ふだんはこれ 1 本**（プロジェクト直下。依存 → 日足の確認 → モード → 管理画面 → 運転手 → 検査）。sim2 ／ --paused ／ --fetch titan
  ./run-sim.sh ctl speed 60              # 別の端末から: ctl の後ろは simctl.py へ（status ／ pause ／ resume ／ step ／ stop）。reconcile も同じ
  # 中身を 1 つずつ叩くなら:
  cd experiments/live-trading; PY=../tastytrade-api-sample/.venv/bin/python
  $PY simctl.py mode sim sim1            # 機械をシミュレーションモードへ（何も動いていないとき）。戻すのは mode real
  ./simrun.sh sim1 --fresh --speed max   # 64 営業日を最速で（約 8 秒【実測】）。sim2 ＝ 筋書き（故障の注入）つき
  $PY simctl.py speed 60 ／ pause ／ resume ／ step ／ stop ／ status   # 別の端末から
  ```

  - ⚠ **実売買とシミュレーションは排他にし、必ず分かるようにする**（利用者決定）＝ 機械全体のモードを 1 つ（`MODE`。無ければ `real`）・`run.lock`・シミュレーションモードでは本物の `run_day` が鍵があっても起動を拒否（rc=5。本物の `events.jsonl` に `refused_mode_sim`）・仮の時計（`run_day.py --sim-clock`）は MODE が sim ＆ 接続先がループバックのモック ＆ prod でない ＆ 本番の鍵なし、のときだけ・記録は `sim/<名前>/` に分け全行 `sim: true`・トレーダー名は `sim_` 始まり・管理画面は全ページの帯と `[SIM]`
  - ⚠ **`run-sim.sh` は資格情報のある機械（titan）では本物の `MODE` に触らない** ＝ `MODE`・`run.lock`・記録を作業用の置き場（`~/.cache/ai-income-lab-sim`。`AIL_SIM_SCRATCH`）に向け、管理画面はデモで 3014 に起こす（⚠ 3012 は触らない）。資格情報の無い機械（Sx360）では機械のモードを sim に切り替え、管理画面は 3012（既に動いていればそのまま使う）
  - ⚠ **操作は CLI（`simctl.py`）だけ・管理画面は表示だけ**（POST の経路を増やさない。停止ボタンは sim のとき `sim/<名前>/HALT` だけを書く）
  - ⚠ **テストは `LT_MODE_DIR` で `MODE`・`run.lock`・`sim/` を tmp に向ける**（本物の `run.lock` を一瞬でも取ると、同じ時刻の本物の執行器が拒否される）。⚠ **titan で試すときも `LT_MODE_DIR` を scratch に向ける**（本物の `MODE` を sim にしない）
  - ⚠ **シミュレーションを回す機械は Sx360**（利用者決定）: Sx360 には tastytrade の `.env` を置かない（資格情報が無いので実売買が物理的に起きない）。titan は実売買と日足の取得で、ふだんシミュレーションを回さない。日足は titan から Sx360 へ写す
  - ⚠ **本番と同じ形（`kind = "experiment"`）は `sim3`（`sim_T1〜T3`・金額指定）／ `sim4`（`sim_S1〜S3`・整数株 5 本）**（2026-09-20）: 先に `simpredict.py make sim3`（titan・研究用の `data/` を読むだけ・192 本で約 16 分【実測】）。作り置きは `sim-predict/`（git 管理外。`sim3` と `sim4` で共通）で、欠けていれば運転手は起動を拒否する。Sx360 へは `sim-predict/` を写す（LightGBM 不要）。記録は `live-trading.md` §0-7 (k)
  - ⚠ **回して見つかった執行器の穴 3 つ**（§0-7 (j)）: ✅ 違うトレーダーの買いを合算して按分すると整数株の人に端数の持ち分ができる → **口座への注文はトレーダーごとに別々に出す**（2026-09-19 利用者決定「成績を正確に知りたい」。合算しない・按分しない・⚠ **内部移転もしない** ＝ 同日の利用者決定「トレーダーの実際の実績が検証できない」。A の売りと B の買いが重なる日も両方を口座に出し、売りが先。注文ごとに金額の内訳 `amounts` を残し、手数料をその人の台帳に入れる。⚠ 手数料は dry-run の見積り）／ ✅ 含み損 20% は**執行器は警告だけ・止めるのは人**（利用者決定。`drawdown_warning`）／ ✅ 発注の後に落ちた次の日に口座と台帳の食い違いを検知しない → **帳尻を合わせる 3 段**（利用者決定。`live-trading.md` §0-8）: 発注の前に控え（`state/<env>/journal.jsonl`）を書き、起動時に未完を照会してその人の台帳に戻す ／ 口座 − 台帳の合計を突き合わせ、多いぶんは台帳の外として記録・**少ない銘柄だけその日は売買しない** ／ 人が `reconcile.py` で合わせる（ネットワークなし）。⚠ **差を推測で誰かに割り振らない**。⚠ 台帳の保存は 1 注文ごと
- **今日の買い% と 1 日の流し方**（2026-09-20。`live-trading.md` §0-9・§0-10。段取りは `docs/plans/live-trading-go-live-0922.md`）

  ```bash
  ./run-live.sh --prepare                                  # 朝: 日足 ＋ 外部系列の更新（約 2 分）→ 紙上の対照 out/daily.csv。発注しない
  ./run-live.sh --traders T1,T2,T3 --date 2026-09-18 --mode plan -- --ignore-window    # 過去の日で通す（発注しない）
  ./run-live.sh --traders T1,T2,T3 -- --env prod --allow-prod-dry-run                  # 本番の dry-run（何もルーティングしない）
  # ⚠ 本番の発注は利用者だけ: TT_ALLOW_PROD_ORDERS=1 ./run-live.sh --traders T1,T2,T3 --mode submit --wait -- --env prod --i-know-this-is-real-money
  ```

  - `run-live.sh` ＝ 日足の更新（足だけ 52 秒）→ `cli.predict` × 3（並列 36 秒）→ `out/<日付>/predict.jsonl` → 台帳の控え（`state-backup/`。submit の回だけ）→ `run_day.py`。「--」の後ろはそのまま執行器へ。⚠ **本番の鍵は書いていない**。予測が 1 本でも失敗したら執行器を起こさない
  - `experiments/feature-discovery/cli/predict.py`: ⚠ **モデル・較正のコードは足していない**（表 ＝ `cli.build.assemble`・買い% ＝ `cli.run.fold_buy_pct` ＝ 既存のコードの切り出し。⚠ この 2 つを触るときは既定経路の指紋テスト `tests/test_trading_run.py` と `tests/test_predict.py` を流す）。訓練 ＝ ラベルが `asof` より前に確定している行・検証 ＝ `asof` の行。⚠ `runs/` を作らない（試行ではない）
  - ⚠ **実売買の日足は `experiments/feature-discovery/data-live/`**（`live_update.sh`。`AIL_DATA_DIR` で `store.DATA` が差し替わる）。⚠ **研究用の `data/` に `cli.fetch --dataset daily` を流さない** ＝ 配信側が過去の足をさかのぼって変えていて（小数 2 桁・権利落ち前日の終値が未調整。41 銘柄に 2% 超【実測 2026-09-19】）、表が壊れる。`data-live/` は研究用の写しを種にして後ろだけ継ぐ。外部系列は `exog_live`（⚠ 年が変わったら金利の `years` を足す）
  - ⚠ **発注は 2 段**（利用者決定 D6）: ① 全部の注文を順に 控え → dry-run → 発注 ／ ② 1 本ずつ約定を確かめて台帳へ。1 本ずつ約定を待つ直列の形（`--serial` で戻せる）では初日の約 120 本が窓に収まらない
  - 3 人の設定は `config/traders/candidates/{notional,shares}/`（⚠ まだ `config/traders/` に無い ＝ 起動できない）。`dryrun2`（9 行。$4.76 ／ $6.25 の端株を含む）の結果で片方を写す。端株が通らなければ T・PFE・NKE・VZ・BAC の 5 本（利用者決定 D3）
  - `test_a` の往復は `test_signal.py buy|exit` で合図を書き換えて同じ日に 2 回起動する（利用者決定 D1 ＝ 案 B）
  - 紙上の対照は `paper.py` → `out/daily.csv`（読むだけの後処理。管理画面はあれば本物・無ければ仮データ）
  - 毎日の自動起動の雛形は `experiments/live-trading/systemd/`（⚠ 入れるのも鍵を置くのも利用者。水曜から ＝ D4）
- 決めごと・手順書・記録は `docs/specs/experiments/live-trading.md`

## Git 運用ルール

- **作業ブランチは作らず、常に `main` 上で直接作業・コミットする**（個人プロジェクトのため、レビュー用のブランチ分岐は不要）
- Claude は「デフォルトブランチでは先にブランチを切る」という既定の挙動を持つが、**このプロジェクトではそれを行わない**
- コミット・push はユーザーから依頼されたときだけ行う

<!-- vibeboard:begin -->
## 開発管理画面 (vibeboard)

ローカル開発時のタスク・プラン管理は [vibeboard](https://github.com/akiraak/vibeboard) で行う。
プロジェクト直下に degit で vendor してある（`./vibeboard/`）。

```bash
# 親プロジェクト直下から
node vibeboard/dist/cli.js --root .
```

`http://localhost:3010` でプロジェクト直下の `docs/plans/`・`docs/specs/`・`TODO.md`・`DONE.md`・`CLAUDE.md`・`README.md` を閲覧・編集できる。

- `Files` タブでプロジェクト内のファイル（`TODO.md` / `DONE.md` / `CLAUDE.md` / `README.md` を含む）をプレビュー表示・編集できる。`TODO.md` はツリー表示つき
  - 編集は楽観ロック（mtime チェック）付き。外部で先に更新されていた場合は保存時に 409 を返し、リロード / 手元維持 / 強制上書き を選べる
  - `fs.watch` + 2 秒ポーリングで外部変更を検知し、SSE でクライアントへ即時反映する
- `Tasks` タブで `TODO.md` のタスクを、このプロジェクトで動いている Claude Code のセッションへ渡して実行できる（実行 / プラン作成 / 説明 / 削除）。
  プラン作成は `docs/plans/` のプランファイルと `TODO.md` へのリンク・子タスクだけを作らせる（実装はしない）。
  ボタンの上の **「追加の指示（任意）」** に書いた文面は、実行 / プラン作成 / 説明の文面の末尾に足して送る（空欄なら今までどおり。Ctrl+Enter で実行）。
  左ペインの上の「プロジェクト全体」に **commit & push** があり、タスクとは無関係に作業ツリーの変更をまとめてコミットして push させる（メッセージと `TODO.md` / `DONE.md` の整理はセッションが行う）。
  **タスク追加**（「プロジェクト全体」）と**子タスク追加**（タスク詳細）は投函せず、vibeboard がバックグラウンドの
  Claude Code（`claude -p`。許すツールは `TODO.md` の Edit だけ）を起こして `TODO.md` に足させる。1 行目が
  タスクの文面（そのまま入る）、2 行目以降はメモ。成功判定は「`TODO.md` に文面が増えたか」の事後検査で、
  モデル・制限時間は `vibeboard.config.json` の `taskAdd`（`model` / `timeoutSec`。既定は CLI の既定モデル・120 秒）。
  送り先は `claude agents` の一覧から選ぶ。セッションは起動時の hook（`vibeboard init` が `.claude/settings.json` に書く）で
  自分の受信口を vibeboard に登録し、vibeboard がそこへ文面を投函する。登録が無くても Linux なら `claude agents` の pid から
  受信口（`$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`）を引いて投函する。hook が使えない環境では
  `node vibeboard/dist/cli.js listen --name <画面の名前>` を回す
- ローカル開発専用（本番管理画面とは独立）
- ポート変更は `--port` または `VIBEBOARD_PORT` 環境変数で指定可能
- 本体の更新は `node vibeboard/dist/cli.js update --restart`（再 degit → `npm install` → `init` → 同じ root の vibeboard の起動し直し、を 1 コマンドで）

## タスク管理ルール

- タスクは `TODO.md` で管理する
- **タスクの行は、親項目も含めて必ず `- [ ] 文面` の形で書く。** vibeboard はチェックボックス
  （`[ ]` 未着手 / `[x]` 完了 / `[~]` 進行中 / `[-]` 中止）のある行だけをタスクとして拾う。
  `- 文面` のようにチェックボックスの無い行はタスクではなく直前のタスクのメモ扱いになり、ツリーにも
  Tasks タブにも出ない（親に付け忘れると、子タスクだけが親を失って並ぶ）
- **`TODO.md` に書くのはタスクだけ。** メモや決定事項を残すときは、関係するタスクの
  下に字下げして付ける（タスクに関連付ける）。タスクに属さないメモの節（「決まったこと」「備考」など）は
  作らない。プロジェクトとしての決定は `CLAUDE.md` へ、済んだ経緯は `DONE.md` へ書く
- 字下げが親子。vibeboard はこれをツリーとして表示する

  ```markdown
  - [ ] 親タスク [plan](docs/plans/foo.md)
    - [x] Step 1: 済んだ子タスク
    - [~] Step 2: 進行中の子タスク
      - 決定: この子タスクに付くメモ（チェックボックスなし）
  ```
- タスク同士の関係は、そのタスクの下に字下げした **`依存:` / `派生元:` / `関連:`** の行で書く。
  相手のタスクは `「文面」` で（例: `依存: 「スキーマに tags 列を追加」`）、プランや仕様は
  Markdown リンクで（例: `関連: [spec](docs/specs/api.md)`）示す。vibeboard のツリーで両方向に辿れる
- タスクの**期日**（いつやるか）は、そのタスクの下に字下げした **`期日:`** の行で書く（例: `期日: 2026-09-21` /
  `期日: 2026-09-21 06:35` / `期日: 2026-09-21 12:45〜13:05`）。時刻だけの行（`期日: 06:35`）は、親をさかのぼって
  最初に見つかる期日の日付を借りる。vibeboard の Tasks タブの「タイムライン」が、期日のあるタスクを日ごと・時刻の順に並べる
- タスクが完了したら `TODO.md` から該当項目を削除し、`DONE.md` に移動する
- `DONE.md` には完了日を `YYYY-MM-DD` 形式で付けて記録する
- 新しいタスクが発生したら `TODO.md` の適切なセクションに追加する
- タスクの実施前に `TODO.md` を確認し、優先度の高いものから着手する
- コミット時に `TODO.md` を確認し、実装した機能に対応するタスクがあれば `DONE.md` に移動する

## 作業着手ルール

作業（実装・調査いずれも）を始めるときは、コードに手を入れる前に以下を行う。

1. **プランファイルを作成する**: `docs/plans/<task-name>.md` に実装プラン or 調査プランを作成する
   - 目的・背景、対応方針、影響範囲、テスト方針を最低限記載する
   - 複数 Phase / Step に分かれる場合はファイル内でも Phase / Step を明示する
2. **`TODO.md` に該当項目があるか確認する**
   - 無ければ適切なセクションに追加する
   - 既存項目があれば、その項目に作成したプランファイルへのリンクを追記する（例: `[plan](docs/plans/<task-name>.md)`）
3. **複数 Phase / Step がある場合は `TODO.md` に子タスクとして追加する**
   - 親項目の下にインデントしたチェックボックスで Phase / Step を列挙する
   - Phase / Step が完了するごとにチェックを入れ、全完了で親項目を `DONE.md` に移す
4. **作業完了時の後片付け**
   - 親タスクを `DONE.md` に移動する
   - 対応するプランファイルは `docs/plans/archive/` に移動する
<!-- vibeboard:end -->

## vibeboard のこのプロジェクト固有の運用

⚠ **この節はマーカーの外に置く**（`vibeboard update` / `init` はマーカー間を置換するので、中に書くと消える。2026-09-10 に一度消えた）。

- **titan で動かした vibeboard は tailnet から `http://titan-income-vibeboard`** で見る（Tailscale Services。titan の Windows 側 `tailscale serve --service=svc:titan-income-vibeboard --http=80 http://127.0.0.1:3010`、再起動をまたいで残る。Funnel なし。2026-09-10）。`ssh titan` で入って `./run-vibeboard.sh` を叩けば開く。⚠ **dashboard（3012）は serve に出さない**（serve 経由は全部ループバックに見え、発注の面が無認証で開く。外から見るなら `ssh -L 3013:127.0.0.1:3012 titan`）。⚠ **serve の外向きポートを 3010 にしない**（mirrored では Windows 側の listener が WSL の bind を塞ぎ、vibeboard が起動できなくなる）。経緯と切り分けは `docs/plans/archive/vibeboard-remote-view.md`
- **検証・データのタブ**（2026-09-10）: `vibeboard.config.json` の customTabs。中身は `dashboard/vibetab.py`（127.0.0.1:3015、標準ライブラリのみ。vibeboard の sidecar が `python3` で自動起動）が `experiments/feature-discovery/` の `runs/` と在庫を読んで出す。vibeboard 本体が `/ext/<name>` で中継する（upstream 改造）ので、`http://titan-income-vibeboard` 越しでもタブが動く。⚠ **customTabs の baseUrl に dashboard（3012）を指定しない**（中継後はループバック発に見え、ローカル面が開く）。プランは `docs/plans/archive/vibeboard-experiments-tabs.md`
- **ハードのタブ**（2026-09-18）: customTabs の 4 本目（`hardware`）。中身は同じ `dashboard/vibetab.py` の `/hardware`（読み手 `dashboard/hwstat.py`・画面 `dashboard/hwview.py`。標準ライブラリのみ）が `nvidia-smi` と `/proc` を読み、GPU・CPU・メモリ・ディスクの「いまの状態」と「この 1 時間」を出す。⚠ **vibeboard 本体は改造していない**（このプロジェクト専用。`dashboard/app/` の外なので g3plus にも載らない）。⚠ **値を読むのは sidecar の見張り 1 本**（5 秒おき。`AIL_HW_INTERVAL_S`）で、画面は `api/snapshot`・`api/history` を自前で取りに来る。履歴はメモリ上の 1 時間だけ（sidecar を入れ直すと消える）。⚠ **WSL2 ではプロセス別の GPU メモリ・CPU 温度は読めない**。⚠ **読むだけ。このサーバに「操作」を足さない**（tailnet の閲覧者にも見える）。仕様は `docs/specs/dashboard.md` §14、プランは `docs/plans/archive/vibeboard-hardware-tab.md`
- **トレーダーのタブ**（2026-09-20）: customTabs の 5 本目（`traders`）。**トレーダー 1 人 1 ページ**で、その人が**使うモデル**（正式な名前も出す）と**モデルの特性**をやさしい言葉で出す（大見出し 5 つ ＝ 使うモデル ／ モデルの特性 ／ 何を見て、どう点を出すか ／ この人の決まり〔点のものさし〕／ 気をつけること）。中身は同じ `dashboard/vibetab.py` の `/traders`（画面 `dashboard/traderview.py`・標準ライブラリのみ）。⚠ **だれが居るかは実売買の設定から引く**（`config/traders/` の試験用でない人。無ければ `candidates/notional/` ＝「未確定」）。⚠ **人数を数えない・見くらべるページを作らない・ほかの人やモデルとくらべる文を書かない**（利用者の指示「トレーダーは常に変わるし 1 人になるときもあれば 10 人になるときもある」。テストの `COMPARING` が弾く）。⚠ **言葉の正本は `dashboard/traders.toml`**（特性 ＝ `[[model]]`・呼び名 ＝ `[nicks]`。説明を Python に書かない）。⚠ **特性には必ず「どこまで確かか」の印**（`basis` ＝ 作りから ／ 試し運転で見えた ／ 見立て。⚠ 確かめていない理由を言い切らない ＝ 最初の版は比較の文を作ろうとして誤りを 1 つ書いた）。⚠ **やさしい言葉で書く**（本文で 買い%・θ・特徴量・Ridge・fit・`sizing` などを使わない ＝ `FORBIDDEN`）。⚠ **呼び名は意味の無い名前**（`T1` ＝ アキ ／ `T2` ＝ アリス ／ `T3` ＝ カエデ。鍵は変えない・呼び名は変えない・使い回さない）。⚠ **白地に固定**（「黒背景はやめる」）。⚠ **数字は設定から写すだけ・売買結果は出さない・`out/`・`state/`・`.env` を読まない**。⚠ 新しいモデルを使う人を足したら `[[model]]` を 1 つ書く（書くまで「説明はまだ」と出る）。仕様は `docs/specs/dashboard.md` §16、プランは `docs/plans/vibeboard-traders-tab.md`
- **サイドバーの検索**（2026-09-18）: Tasks・Plans・Specs・Files の左ペイン上端の箱で絞り込む。文書のタブはサーバがパスと本文を探し（`GET /api/search/:category?q=`。空白区切りは AND・大文字小文字は区別しない・1MB 超と二進は本文を見ない）、Tasks は手元の木を文面・メモ・親の文面で絞る。結果は平らな一覧で、Escape で消すとツリーに戻る。✅ **akiraak/vibeboard 本体へ反映済み**（vendor と本体は一致）。プランは `docs/plans/archive/vibeboard-search.md`
- **Tasks のタイムライン**（2026-09-20）: Tasks タブの左ペインを `ツリー` ｜ `タイムライン` で切り替える。時間軸に置くのは**予定だけ**（`TODO.md` の `期日:` の行。`DONE.md` の完了日は置かない ＝ 利用者の裁定）。日の見出しを押すと右ペインにその日の一覧（`#tasks/@day/<日付>`）。⚠ **済んだタスクは既定では出さない**（ツリーと同じ。全部済んだ日は見出しごと出ない。`済みも出す` で薄く出る）。⚠ **時刻は `TODO.md` に書いたままの土地の時刻（いまは PDT）で、vibeboard は時差を計算しない**（「いま」「過ぎた」は見ているブラウザの時計と比べる ＝ 別の時間帯から見るとずれる）。⚠ 日・時刻を文面の頭に書いても読まない（読むのは `期日:` の行だけ。文面の頭の時刻が期日と同じなら、タイムラインでは二重に出さない）。✅ **2026-09-20 に akiraak/vibeboard 本体へ反映済み**（`ca8134c`・済みを既定で出さない直しは `7f0184f`。vendor と本体は一致しており `vibeboard update` を流してよい）。プランは `docs/plans/vibeboard-tasks-timeline.md`
- **タスク追加 / 子タスク追加**（2026-09-11）: バックグラウンドの `claude -p` に TODO.md を編集させる（`vibeboard/src/claudeJob.ts`。詳細はマーカー内の Tasks の項）。✅ **2026-09-11 に akiraak/vibeboard 本体へ反映済み**（Ctrl+クリック修正も同時に反映。vendor と本体は一致しており `vibeboard update` を流してよい）。プランは `docs/plans/archive/vibeboard-task-add.md`
