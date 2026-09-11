# TODO

- [ ] DeepLearning と進化的探索（遺伝的アルゴリズム）を使った検証をかなり増やす。進化的探索の実行を継続して回せる仕組みを入れる
  利用者の指示（2026-09-10）: **DeepLearningとGANを使った検証をかなり増やす。GANの実行を継続して回せる仕組みを入れる**
  ⚠ 旧題は「GAN」だったが、利用者の意図は遺伝的アルゴリズム（モデル集団を対決させ、勝者の特徴で次世代を作る進化的探索）だったので 2026-09-10 に改題。ML 用語の GAN（データ増強）は 2026-09-09 に検証済みで「落とす」（[gpu-models.md §4](docs/specs/experiments/gpu-models.md)）
  土台: `runs/` に GPU 系の実行が既にある（2026-09-09 の gpu_mlp_2018・gpu_ridge_gan_2018・gpu_lgbm_gan_2018）。GAN（データ増強）系は最良でも純利 −4.74 / −7.35bp で基準線を超えていない
  ⚠ 手法を増やすほど n_trials が増えて DSR は下がる（[rules.md §11-4](docs/specs/experiments/feature-discovery/rules.md)。数え落とすと必ず甘くなる）。継続実行の仕組みは n_trials の自動集計・台帳への反映とセットで作る
  ⚠ 進化的探索の選抜も「標本から学ぶ変換」として訓練分割の内側で行う（rules §3 B）。fitted は `runs/` に残すが次の実行では読み込まない
  ⚠ 継続実行も 1 実行 1 ディレクトリ（rules §10）。seed・config・入力の指紋を毎回残す
  GPU は 3090 Ti（メモリは全部使ってよい。デバイスは `AIL_TORCH_DEVICE`）
  - [x] 時系列から上昇下降トレンドを学習する最新 AI 技術の調査 [plan](docs/plans/archive/ts-trend-ai-survey.md)
    利用者の指示（2026-09-10）: **時系列のデータから上昇下降のトレンドを学習するような最新のAI技術がないか調べて**
    ✅ 2026-09-10 完了。成果物: [ts-trend-ai-survey.md](docs/specs/experiments/ts-trend-ai-survey.md)。5 系統に整理し、候補リスト（§7）を作成。金融の一次評価 2 本が「汎用基盤モデルは対ランダムウォークの利得が小さくまばら」で §9 と同じ形
  - [ ] DL / 進化的探索の手法を増やして検証を回す（着手時にプランを作る）
    依存: 「時系列から上昇下降トレンドを学習する最新 AI 技術の調査」
    依存: 「検証の仕方を実際の取引に近づける（閾値つき売買・買い専用・銘柄別 bp）」（✅ 2026-09-10 完了。新手法の検証は閾値売買方式＝対 B&H 上乗せで測る。[threshold-trading.md](docs/specs/experiments/threshold-trading.md)）
    候補は [ts-trend-ai-survey.md §7](docs/specs/experiments/ts-trend-ai-survey.md): 優先 1 進化的ファクター探索の基盤 / 2 時系列分類器（MiniRocket ＋ Hydra ＋ QUANT）/ 3 系列モデル 1 本（PatchTST 系）
  - [ ] ChatGPT からの GAN 案の実装（条件付き GAN による株価シナリオ予測） [plan](docs/plans/cgan-scenario-forecast.md)
    利用者の指示（2026-09-10）: **ChatGPTからのGAN案の実装**（指示書が長いので全文はプランに収載）
    ⚠ ここの「GAN」は ML 用語どおりの**条件付き GAN（WGAN-GP）を予測器として使う**案。親タスクの「GAN ＝ 進化的探索」とも、2026-09-09 に落とした**データ増強** GAN（[gpu-models.md §4](docs/specs/experiments/gpu-models.md)）とも別物（プラン §0-1 に整理）
    中身: 直前 60 営業日を条件に次の 5 営業日の日次対数リターンを 1,000 本生成し、上昇確率・予測区間・下落リスクを推定。主指標は 5 日累積リターン分布の CRPS。履歴ベース再標本化・軽量モデル・既存モデルの 3 種と同条件で比較
    ⚠ 採用・収益性を前提にしない。ベースラインに負けても、検証を完了し結果を明示すればタスクとしては完了（プラン §9）
    関連: 「DL / 進化的探索の手法を増やして検証を回す」
    - [ ] Phase 0: 設計決定（統合位置・対象銘柄・既存 `gan.py` の再利用可否・設定の分離）
    - [ ] Phase 1: データと特徴量（予測時点で利用可能な値だけ・scaler は学習区間内 fit）＋ リーク防止テスト
    - [ ] Phase 2: 条件付き WGAN-GP の実装（学習・checkpoint 選択・モード崩壊の検出）
    - [ ] Phase 3: 時間順分割・walk-forward と比較対象 3 種。最終テストの前に採用基準を記録
    - [ ] Phase 4: 評価指標（CRPS・Brier・被覆率）と予測出力（JSON・分位点・校正図・予測区間図）
    - [ ] Phase 5: 再現性の確認と評価レポート（比較表・期間・seed 別結果・採用判断）
  - [ ] 進化的探索の実行を継続して回せる仕組み（キュー or ループ、失敗時の再開、台帳の自動更新）
    関連: [rules.md](docs/specs/experiments/feature-discovery/rules.md) ／ [ledger.md](docs/specs/experiments/feature-discovery/ledger.md)

- [ ] 検証の単位（実行 → 手法 → 試行 → fold → セル）を図を使って分かりやすく解説する specs のページを作成する
  利用者の指示（2026-09-10）: **実行 → 手法 → 試行 → fold → セルを図などを使い分かりやすく解説するspecsのページを作成する**
  名称の正本: [rules.md](docs/specs/experiments/feature-discovery/rules.md)（10・11・13 章）と `ail/catalog.py`（`KEY`・`is_trial`・`_collapse`）。ページはそれらの解説であり、規約の正本は rules.md のまま動かさない
  盛り込む関係（2026-09-10 の確認結果）: 1 実行に複数の手法（TOML の `selectors` ＋ 自動で付く基準線 2 本）／ 同じ鍵の手法が複数の実行にあれば 1 試行にまとめ「再現」列で幅を出す ／ 鍵のどれかが違えば同じ手法名でも別試行 ／ leak 実行は別の表
  13 章で鍵に足された 検証方式・形式・閾値 の 3 列と、試行の下の量（銘柄別 bp・ポートフォリオ日次純利）も同じページで扱う
  関連: [ledger.md](docs/specs/experiments/feature-discovery/ledger.md) ／ [plan](docs/plans/archive/trading-validation.md)

- [ ] データの取得
  - [ ] 既存にないデータを考える
    ⚠ **終了しないタスク**（完了にしない・`DONE.md` に移さない）。既存の層に無いデータを考え続けるための常設タスク
    思いついたデータ源は、この下に子タスクとして足し、採る・採らないの判断と根拠（規約・遅延・銘柄を区別できるか）を残す
    関連: [daily-data-sources.md](docs/specs/experiments/daily-data-sources.md)

- [ ] tastytrade で、実際の API 取引のサンプルプログラムを動かす [plan](docs/plans/tastytrade-api-sample.md)
  - 対象は [docs/specs/service-trust-assessment.md](docs/specs/service-trust-assessment.md) の判定「高」で、[docs/specs/trading-fee-comparison.md](docs/specs/trading-fee-comparison.md) §4 で株 $0・API プレミアム $0、常駐プロセス不要の tastytrade 1 社。moomoo・IBKR は 2026-09-04 に対象から外した（再開条件はプラン §1-2）
  - 動かす範囲: 認証 → 口座照会 → 現在値 → 指値・取消 → 約定・反対売買 → ストリーミング、の 6 手順を cert（sandbox）環境で
  - **2026-09-05: 方針は (c)（sandbox ＋ 本口座 ＋ 入金 ＋ 本番で 1 株）に決定**。CLAUDE.md にこの 1 手法だけの例外として追記済み。⚠ 口座開設・入金・本番発注を実行するのは利用者（Claude は手順とコードまで）
  - ✅ 2026-09-05: sandbox（$100,000）と本口座の資格情報が揃い、`.env` に設定済み。**待ちは市場時間だけ**
  - [x] Phase 0: 方針の決定と cert 環境の入口の確認（OAuth パネルは実装済みと確認。公式 SDK は archived で OpenAPI 直叩きに決定。入金なしの quote token と米国内承認日数は未解決のまま残す）
  - [x] Phase 1: 環境と記録形式（venv・JSONL・`.env` と `.gitignore`・cert / prod の取り違え防止・モックによる自己検査）
  - [x] Phase 2: 認証（OAuth2。cert・prod とも交換できた。`expires_in` 900 / JWT 954 秒）
  - [x] Phase 3: REST の 4 手順（2026-09-08 に手順 5 の約定・建玉・反対売買まで本物の cert で通した）
  - [x] Phase 4: ストリーミングとレート制限（口座ストリーマ・DXLink・429 ＋ 認証寿命 900 秒の実測まで済み）
  - [x] **9/8（火）の市場時間に回した**（[記録 §0-4](docs/specs/experiments/tastytrade-api-sample.md)）— **6 観点のうち 5 つが ✅**。残るは A（営業日を 2 日跨ぐ交換）だけ
    - [x] `--step cleanup` → `--step 4` / `--step 5`: ✅ **通った**（14:52〜14:53 ET。`final_Cancelled` と `buy_Filled/sell_Filled`、SPY 1 株を $766.47 で建てて解消）。⚠ **その 25 分前は `Session offline` で拒否された**（同時刻の `market-time` は `Open`、余力 $200,000）。一時的な状態がある
    - [x] `--step 3` で気配の遅延: ✅ **サーバの時計で −0.12 秒**（12 回の中央値。ばらつき 0.24 秒）。⚠ こちらの時計では −1.14〜+0.65 秒と 1.8 秒揺れる（**WSL2 の時計**）。`sample.py` が `delay_corrected_s` を記録し、判定 C はそちらを優先するようにした。DXLink 229 イベント
    - [x] `--step 6 --seconds 60`: ✅ ack 123 ms、60 秒で 5 メッセージ（Order 通知 2）
    - [x] `--step rate`: ✅ 60 回/分・30 連射とも 429 なし、中央値 133.9 ms
    - [x] `--step 1 --verify-expiry`: ✅ **920 秒待って 401** → **900 秒で失効**。⚠ 「954 秒」は `exp − iat` の読み違いだった（`iat` は grant 作成時刻の固定値。2 本のトークンで同じ値）
    - [x] 9/5 に取った refresh token がそのまま使えるか / cert の 24 時間リセット: ✅ **どちらも残った**（同じ口座・残高は $100,000 にリセット）
    - [x] ⚠ `Session offline` は**一時的**と確認（25 分後に成功）。自動売買では「注文の中身が悪い」と読まず時間をおいて再送する設計が要る
  - [x] Phase 5: 記録と判定（2026-09-08。[記録](docs/specs/experiments/tastytrade-api-sample.md) の 6 観点・訂正候補 15 件・未実測 4 件、overview §4/§6 と CLAUDE.md への反映）
    - ⚠ 観点 A だけ ⏳。g3plus の管理画面が sandbox に繋いで監視を回しているので、**翌営業日に自動で ✅ になる**。9/9 に `/judge` を見る
  - [ ] **着金の確認（$1,000 / SoFi → tastytrade、2026-09-05 送金指示）**
    - 着いたら `sample.py --step probe` をもう 1 回回し、着金前（[記録 §0](docs/specs/experiments/tastytrade-api-sample.md)）との差分を取る
    - 見るもの: `cash-balance` が 0.0 → 1000.0 になるか、`pending-cash` が消えるか、`cash-available-to-withdraw` がいつ立つか（＝ ACH の保留期間の実測）、`available-trading-funds` が 0.0 のままか
    - ⚠ 着金前の状態はもう測れない。⚠ 9/7 は Labor Day のため、着金は 9/8（火）以降の見込み
  - [ ] Phase 6（方針 (c)）: 本番口座で 1 株（入金と発注は利用者が行う）
    - ⚠ 2026-09-05 の dry-run で **着金前でも 1 株は通る**ことが分かっている（買付余力 1000.0 が効き、`available-trading-funds` 0.0 は効かない）。着金を待つ必要は無い

- [ ] moomoo・IBKR の実検証（tastytrade と同じ 6 手順・6 観点で横並びにする）
  - 背景: 2026-09-04 に 3 社 → tastytrade 1 社へ絞ったときの**再開条件**（[plan §1-2](docs/plans/tastytrade-api-sample.md)）に当たる。債券・外国株・FX まで同じ口座で試したいなら IBKR、PFOF なしの執行を試したいなら moomoo
  - 使い回せるもの: `experiments/tastytrade-api-sample/` の記録形式は `venue` 列を持ち、`ttclient.py` の関数名は会場に依存しない（`authenticate` / `list_accounts` / `get_quote` / `dry_run_order` / `submit_order` / `cancel_order`）。**同じ名前で別モジュールを書けば `sample.py` の 6 手順はそのまま動く**
  - ⚠ **方針の例外は tastytrade 1 社にしか掛かっていない**（[CLAUDE.md](CLAUDE.md) の 2026-09-05 追記）。口座開設・入金を伴うなら Phase 0 で改めて決める
  - ⚠ **moomoo は Web 規約が robot 禁止（R1）**（[trading-api-availability.md](docs/specs/trading-api-availability.md) 付録）。API 経由の自動売買が許されるかを、着手前に規約の一次情報で確認する。ここが黒なら moomoo は打ち切り
  - ⚠ **費用が同じでない**: IBKR は株 40 往復/月 ＋ API プレミアムで **$84.50 ＋ 残高 $500**【推測】、moomoo は $0（プロモ中）、tastytrade は $0（[trading-fee-comparison.md](docs/specs/trading-fee-comparison.md) §4）。IBKR は「払ってでも広い品揃えを取るか」の判断になる
  - [ ] Phase 0: 方針と入口の確認
    - 口座開設・入金をどこまで許すか（tastytrade と同じ (a)/(b)/(c) の選択）。⚠ IBKR は最低残高の条件があるので、開設だけで済むかを先に確認
    - 各社の一次情報: 発注 API の版と仕様書の所在、公式 SDK の保守状況（最終リリース日）、sandbox / paper 口座の作り方、認証方式と資格情報の作り方
  - [ ] Phase 1: IBKR — **最大の不確実性は「常駐プロセスが WSL2 で動くか」**
    - Gateway / TWS が **Linux ヘッドレスで起動するか**（GUI ログインを迂回できるか）。⚠ ここが動かなければ観点 B は ❌ で、無人運転の前提が崩れる
    - REST（Client Portal 系）と TWS API のどちらを使うか、認証の更新に人手が要るか（tastytrade は refresh token で無人だった）
    - paper 口座は本口座から作る必要があるか
  - [ ] Phase 2: moomoo — **OpenD 常駐と 2 要素**
    - OpenD が Linux ヘッドレスで動くか、SMS ＋ デバイスロックが無人運転を止めないか
    - 模擬取引が OpenD 経由でどこまで再現できるか（tastytrade の cert 相当）
  - [ ] Phase 3: 6 手順を同じ記録形式で回す（認証 → 口座照会 → 現在値 → 指値・取消 → 約定・反対売買 → ストリーミング）
    - ⚠ tastytrade で分かった落とし穴を各社でも確かめる: 認証トークンの実寿命、websocket の認証ヘッダの形、**時間外に発注・約定できるか**、レート制限、エラー本体の残し方
  - [ ] Phase 4: 3 社の横並び判定
    - 観点 A〜F（認証の寿命・常駐・現在値・往復・レート制限・SDK）を 1 表にし、**費用込みで「無人で 1 営業日回る」会場**を選ぶ
    - 品揃え（債券・外国株・FX・先物）と信用判定（IBKR は「中」）を並べ、tastytrade を置き換える理由があるかを判断する

