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

- [ ] 下降トレンドでの売買のタイミングを他のパターンでも検証する（着手時にプランを作る）
  利用者の指示（2026-09-12）: **下降トレンドでの売買のタイミングを他のパターンも検証したい**
  派生元: 「下降トレンドの検知の検証」（✅ 2026-09-12 完了。[downtrend-detection.md](docs/specs/experiments/downtrend-detection.md)）
  背景: ⚠ **出口（下げの検知）は既に働いている** — 12 回の下げの 12 回とも山 → 谷の損失を半分前後に圧縮した。⚠ **負けの本体は入口（再進入）である**【実測 2026-09-12】: 5 fold 合計の対 B&H 上乗せは **山 → 谷 ＋17,624 ／ 谷 → 回復 −21,009 ／ それ以外 −4,867bp**（[§4](docs/specs/experiments/downtrend-detection.md)）
  ⚠ **「もっと良い下げ検知」を足しても効かない**（そこは既に働いている）。⚠ **動かす軸は退出と再進入のタイミングそのもの**
  ⚠ **いまの配線では非対称なタイミングを表現できない**（着手前に読む）
  - ⚠ **θ は買いと売りで同じ**と決まっている（[rules.md 13-3](docs/specs/experiments/feature-discovery/rules.md) 規約 1）。理由は当時「売り閾値を独立に振る仮説が無い」だった。⚠ **本タスクはその仮説を実測で持っている**ので、13-3 を開け直すなら ⚠ **変更規約の 2 問（なぜ変えたか / 変える前の結果をどう扱うか）に先に答える**
  - ⚠ **検知器は自分の建玉を知らない**（[rules.md 14-1](docs/specs/experiments/feature-discovery/rules.md) の出力の契約 ＝ 買い% 1 本。状態は状態機械が持つ）。⚠ **だから「保有中は長期で見て、未保有のときは短期で見る」型は検知器の中に書けない**
  - ⚠ **建玉を 0/1 以外にするのはシミュレータの変更**（13-4 規約 2 の買い専用・0 か 1）＝ **検証方式の変更**。⚠ **やるなら橋渡し対が要る**（13-8）
  候補【推測】: 非対称な閾値（出口は長期・入口は短期）／ 入口専用の検知器（谷からの反転を当てる。出口とは別のラベル）／ 退出後の待ち日数（日数は学習させる）／ 出口だけ使い入口は最短（出口の価値の上限を見る）／ 段階的な建玉（⚠ シミュレータの変更）
  ⚠ **数値は手で置かない**（[rules.md 14-9](docs/specs/experiments/feature-discovery/rules.md)）。事前固定するのは構造だけで、待ち日数・閾値・重みは訓練分割の内側で学習させる
  ⚠ **組み合わせを増やすと n_trials が跳ねる**（13-3 規約 1 の警告「3 × 3 = 9 は n_trials を 3 倍に膨らませるだけ」）。⚠ **試す構成の数を事前に決めて数え、全部台帳に載せる**
  ⚠ **生存バイアスがこの方向に直接効く**（[rules.md 12-1](docs/specs/experiments/feature-discovery/rules.md)）。⚠ **12 回の下げが 12 回とも回復する標本**なので、「早く戻る」規則は構造的に有利に出る。⚠ **良い数字が出たらまずここを疑う**
  物差しは変えない（対 B&H 上乗せ・θ の 3 水準・(A) 共通。[rules.md 13-7](docs/specs/experiments/feature-discovery/rules.md)）。土台は `trend` 層と検知器（[rules.md 15 章](docs/specs/experiments/feature-discovery/rules.md)）がそのまま使える
  関連: 「DL / 進化的探索の手法を増やして検証を回す」（入口の検知器は対決の参加者にもなる）
  - [ ] Phase 0: ⚠ **回す前に上限を見積もる**（[rules.md 14-2](docs/specs/experiments/feature-discovery/rules.md)）
    ⚠ **「谷を知っていたら何 bp か」を先に出す**（保存した `daily.csv` から計算できる。新しい試行にはならない）。⚠ **それが検出限界より小さければ、回す前に「測れない」と判断してよい**
    あわせて決める: どの型を試すか（上の候補から本数を固定）／ 13-3 を開けるか 14-1 を広げるか ／ ⚠ **シミュレータを変えるか**（変えるなら橋渡し対）
  - [ ] Phase 1: 規約の更新（13-3 か 14-1 のどちらを動かすかを決めて、⚠ **変更規約の 2 問に先に答えてから**書く）
  - [ ] Phase 2: 実装（入口と出口を別に持てる形。`ail/detectors/` と `ail/validation/simulate.py`。⚠ **leak 対照が跳ねることを先に確認**）
  - [ ] Phase 3: 回して記録（台帳の再生成・n_trials の確認・⚠ **既存の行が変わらないことの検算**・エピソード表の更新）

- [ ] 検証タブのスコアを比べられる形にする（fold の長さと検証方式で単位が違う。着手時にプランを作る）
  派生元: 「下降トレンドの検知の検証」（✅ 2026-09-12 完了。[downtrend-detection.md](docs/specs/experiments/downtrend-detection.md)）。1995 表の実行を足したときに気づいた
  ⚠ **問題は 2 つある。** どちらも画面のスコア（最良手法の純利 bp・降順。[dashboard.md §10-2](docs/specs/dashboard.md)）にだけ効く
  ⚠ **(1) 検証方式で単位が違う**: 毎日往復の `純利bp` は **1 日 1 銘柄あたり**（例 2.60）、閾値売買は **fold 1 本の累計**（例 4,321.47）。⚠ **規約は直接比べることを禁じている**（[rules.md 13-8](docs/specs/experiments/feature-discovery/rules.md)「別の物差しであり、数字を直接比べない」）のに、⚠ **画面は 1 本の降順リストに混ぜる** ＝ 閾値売買の実行が必ず上に来る
  ⚠ **(2) 期間で fold の長さが違う**: 1995 系は fold 1,267〜1,324 日、2018 系は 360 日。⚠ **日次に直すと順位が反転する**【実測 2026-09-12】 — 画面 1 位 5,000.0bp/fold ＝ 3.78bp/日 ／ 2 位 4,321.5 ＝ 3.41 ／ ⚠ **3 位 1,664.8 ＝ 4.62** ／ 4 位 1,597.2 ＝ 4.43。⚠ **[rules.md 14-4](docs/specs/experiments/feature-discovery/rules.md) が名指しで警告している読み違い**（「期間の効果は必ず日次換算（か同じ fold 長）で比べる」）
  ✅ **歪んでいないもの**（直さなくてよい）: 横の 5 列（層・純利・fold・上乗せ・DSR）は ⚠ **どれも単位に依存しない**（符号・符号の数・t 値・日次系列の比）。採否の判定も台帳側で対 B&H 上乗せの符号から決まる。⚠ **歪むのはスコアの数字と並び順だけ**
  ⚠ **DSR が期間で動くのは誤りではない**（1995 系 0.62 対 2018 系 0.28）。日数が 3.7 倍で推定が締まっただけで、統計としては正しい（[validation-power.md §8-2-3](docs/specs/experiments/feature-discovery/validation-power.md) の読み 3）。⚠ **「良くなった」と読まないための注記が要るかは Phase 0 で決める**
  ⚠ **直す場所は管理画面ではない**（[dashboard.md §10](docs/specs/dashboard.md) の「重い計算は実行のときに 1 度だけ・画面は読むだけ」）。⚠ **日次換算は実験側が `checks.json` に書き、画面は写す**
  ⚠ **vibeboard の検証タブも同じ**（`dashboard/vibetab.py` が `app/experiments.py` をそのまま import する）。片方だけ直さない
  - [ ] Phase 0: 決めごと（⚠ **利用者の裁定が要る**）
    ⚠ **スコアの定義は利用者が 2026-09-08 に決めたもの**なので、Claude の判断で列や並び順を変えない
    決めること: (a) 日次換算を**併記するだけ**か、**並び順も日次にする**か ／ (b) 検証方式が混ざる一覧を**分ける**か、**混ぜたまま単位を明示する**か ／ (c) DSR に「期間で動く」注記を足すか
  - [ ] Phase 1: 実験側（`ail/validation/checks.py` の `compute_trading` に fold あたりの検証日数を足す）
    ⚠ **過去の実行は `checks.json` を書き直せない**（閾値売買は `cli/report.py --recheck` の対象外。[validation-power.md §8-2-6](docs/specs/experiments/feature-discovery/validation-power.md) と同じ理由）。⚠ **当面は `dsr.n_obs ÷ fold 数`で代用できるが、DSR が無い実行では欠ける** — 欠けたら「—」にする（0 で埋めない）
  - [ ] Phase 2: 画面（`dashboard/app/experiments.py` の一覧と詳細、`dashboard/vibetab.py` の写し、テスト）
  - [ ] Phase 3: 仕様の更新（[dashboard.md §10-2](docs/specs/dashboard.md) のスコアの規約と §10-4 の限界。⚠ **§10-4 の限界 3 は粒度・地平・層・列の数しか挙げておらず、fold の長さと単位の違いに触れていない**）

- [ ] 疑問に思ったことを登録し解決していく
  ⚠ **終了しないタスク**（完了にしない・`DONE.md` に移さない・消さない）。利用者の指示（2026-09-11）: **「疑問に思ったことを登録し解決していく大タスク」。このタスク自体は消さずにずっと残るようにする**
  使い方: ⚠ **子タスクの追加は利用者が指示する**（利用者の指示 2026-09-11。Claude は疑問に答えても、指示なしにここへ子タスクを足さない）。解決した子タスクは、答えの要点（と、ドキュメントに反映した場合はそのリンク）をメモで残して `DONE.md` へ移す。親のこの行は残す

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

- [ ] vibeboard の「用語」タブを利用者の画面で確かめる（ブラウザを再読み込みするだけ）
  派生元: 「vibeboardに用語解説のページを追加」（✅ 2026-09-12 完了。[plan](docs/plans/archive/vibeboard-glossary.md) ／ 仕様: [dashboard.md §12](docs/specs/dashboard.md)）
  ✅ 2026-09-12: 「接続できません: HTTP 404」は**古い sidecar が 3015 に居座っていた**のが原因（[dashboard.md §12-3](docs/specs/dashboard.md)）。入れ直して 3 タブとも 200 を確認済み
  確認: 上部に「用語」タブが出る ／ 「すべての用語」で 87 語が 1 ページに出る ／ 語の「詳しく」を押すと Specs / Files タブへ飛ぶ

