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
  ✅ **モデルの軸は 2026-09-15 に一旦閉じた**（利用者の指示: **親タスクを残しつつ一旦このタスクを終わらせて他を進める**）。子「DL / 進化的探索の手法を増やして検証を回す」は [DONE.md](DONE.md) へ移した（優先 2・3 とも「落とす」）
  ⚠ **残る優先 1（進化的ファクター探索の基盤）は下の「進化的探索の実行を継続して回せる仕組み」が器である**（[ts-trend-ai-survey.md §7](docs/specs/experiments/ts-trend-ai-survey.md) の総括）
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

- [ ] 台帳の空白を埋める（✅ **「小」4 件は 2026-09-12 ・ 「中」6 件は 2026-09-16 に完了**。⚠ **カタログの未実施は 12 → 6 件**＝ 手間「大」3 件（F4-1 記号回帰・F4-3 tsfresh・F5-3 行列プロファイル）＋ 見送り 3 件（2026-09-16 に再判断して見送り継続））
  ⚠ **残る 3 件はどれも「計算時間を先に測る」か「n_trials の数え方を先に決める」が要る**（[ledger.md §3](docs/specs/experiments/feature-discovery/ledger.md) の「次の一手」）
  利用者の決定（2026-09-12）: **検証はコストが低いので可能性が低くても積極的に行う。空白は積極的に埋める** → [rules.md 14-10](docs/specs/experiments/feature-discovery/rules.md) に規約化済み
  ⚠ **回す理由は「効くはず」ではない。** ⚠ **「未実施」を「効かなかった」と読ませないために埋める**（14-10 規約 4）
  ✅ **代償が小さいことは実測で確かめた**【実測 2026-09-12】: 「小」4 件 ＝ ＋12 試行で **n_trials 253 → 265**、⚠ **SR0 は 0.034926 → 0.035108（＋0.52%）にしか動かなかった**（[selectors-small-four.md §4](docs/specs/experiments/selectors-small-four.md)）。残り 12 件でも同じ向き
  ⚠ **緩めないもの**: 回すと決めるのは結果を見る前 ／ 回したものは門前も含めて全部数える ／ ⚠ **`--leak` 対照を毎回通す** ／ 1 実行 1 ディレクトリ
  ⚠ **落とす結果でも消さない**（[ledger-role.md](docs/specs/experiments/feature-discovery/ledger-role.md)。落とした行が分母になる）
  関連: [ledger.md §3](docs/specs/experiments/feature-discovery/ledger.md)（未実施 6 件の一覧と「次の一手」。⚠ **うち 3 件は「まだ試していない」・3 件は「試さないと決めた」**）
  ✅ **「小」4 件が残した示唆は 2026-09-12 に解決した**: ⚠ **3 手法（F1-7・F5-1・F1-4）が乱択と 1 ビットも違わなかったのは Platt の数値解が止まっていたから**（[buy-pct-width-collapse.md](docs/specs/experiments/buy-pct-width-collapse.md)）。⚠ **直したら取引が 2 桁増えて手法ごとに散った**（それでも採るは 0 件）
  ⚠ **だから 2026-09-12 以降に回す分は、直した較正（鍵の「較正」＝ std）で回る。** ⚠ **旧行と同じ鍵にはまとまらない**（⚠ **残るは手間「大」3 件**）
  ✅ **F4-4 多項式展開・F5-4 ウェーブレットは、2026-09-12 に足した `transform` の口にそのまま乗る**（[selectors-small-four.md §1](docs/specs/experiments/selectors-small-four.md)）
  ✅ **手間「中」の 6 件（F1-6・F2-1・F3-4・F3-6・F4-4・F5-4）は 2026-09-16 に完了し [DONE.md](DONE.md) へ移した**: ⚠ **18 行とも落とす**（[記録](docs/specs/experiments/ledger-blanks-six.md)）。⚠ **F4 生成型は初めての実施**。n_trials 571 → 589
  ✅ **見送り 3 件（F4-2 Featuretools ／ F5-2 オートエンコーダ ／ F2-4 GA 部分集合探索）の再判断も 2026-09-16 に完了し [DONE.md](DONE.md) へ移した**: ⚠ **3 件とも見送りのまま**（根拠は `config/catalog_notes.toml` の「次の一手」）
  ✅ **保留 78 行の処遇も 2026-09-16 に完了し [DONE.md](DONE.md) へ移した**: ⚠ **62 件を `[[closed]]` で閉じ、再測は 0 件**。⚠ **判定は 1 行も変えていない**（[validation-power.md §6-2-2](docs/specs/experiments/feature-discovery/validation-power.md)）
  - [ ] F4-3 tsfresh の総当たり（794 特徴量）— 手間 大。⚠ **計算時間を先に測る**
  - [ ] F4-1 遺伝的プログラミング・記号回帰 — 手間 大。⚠ **n_trials が数えられなくなる**ので数え方を先に決める
  - [ ] F5-3 行列プロファイル（モチーフ）— 手間 大。⚠ **先読みが入りやすい。leak 対照を必ず通す**

- [ ] 出来高を `trend` 層に足して検知器を回す（✅ **回すと決定**。着手時にプランを作る）
  派生元: 「出来高を含んだデータから機械学習でトレンドを判断できるか調査する」（✅ 2026-09-12 完了。[volume-trend-ml.md](docs/specs/experiments/volume-trend-ml.md)）
  ✅ **利用者の決定（2026-09-12）: 積極的に埋める方針**（[rules.md 14-10](docs/specs/experiments/feature-discovery/rules.md)）。調査の判定「保留（条件つきで採る）」→ **採る**
  ⚠ **回す理由は「効くはず」ではなく「価格だけの入力の軸を閉じるため」**（結果を見てから理由を決めない・14-10 規約 3）
  ✅ **安い**: 実装 半日 ／ 実行 数分〜数十分（CPU）／ **n_trials ＋12**（⚠ **調査時の 160 → 172 は古い。2026-09-13 時点では 409 → 421**）・⚠ **SR0 は 1.0071 倍にしかならない**【実測】
  ⚠ **効く見込みは薄い**: 一次情報が効くと言う条件（断面・長短・小型株・低流動性）を ⚠ **1 つも共有していない**（こちらは銘柄ごと・買い専用・時価総額上位 63 本）。⚠ **良い数字が出たらまずここを疑う**
  ⚠ **動かす軸は入力だけ**（スケール・ラベル・θ・形式・モデルは据え置き）。⚠ **対象は学習ゲート D1〜D4 だけ**（C1〜C3 は公表された価格の規則なので触らない）
  ⚠ **分割調整の向きに注意**（価格を割ったら出来高は掛ける）。⚠ **`--leak` の対照を必ず通す**（入力を増やすと配線の穴も増える）
  派生元の裁定は済んだ: 「下降トレンドでの売買のタイミング」は ✅ 2026-09-12 完了（[DONE.md](DONE.md) ／ 記録 [entry-timing.md](docs/specs/experiments/entry-timing.md)）。⚠ **裁定は「タイミングが先」で、そちらは落とすで閉じた**
  ⚠ **2026-09-13 に前提が変わった。着手前に読む**（[theta-placement.md](docs/specs/experiments/theta-placement.md)）
  ⚠ **D 系（学習ゲート）は、いまの物差しでは検定できないと分かった** — ⚠ **θ ≥ 50 では出口が構造的に立たない**（証明は [§2](docs/specs/experiments/theta-placement.md)）。⚠ **出来高を足しても保有日率 1.000 は動かない見込み**
  ⚠ **さらに、検定できる器で 1 回測ったら D 系は同じ保有日率の乱択ゲートに負けた**（[§6-1](docs/specs/experiments/theta-placement.md)）。⚠ **検知器の軸は 2026-09-13 に閉じた**
  ⚠ **だから本タスクは「対象を D 系から変える」か「軸を閉じたまま見送る」の裁定が先**（着手時に利用者に聞く）。⚠ **C1〜C3 は公表された価格の規則なので触らない**という元の方針は変わらない
  ⚠ **回すなら入力を選別 × モデルの経路に足すほうが測れる**（1 日ラベルなら出口も立つ。較正の不具合も 2026-09-12 に直った）

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
  - [ ] 検証方式を足したときに画面を更新する手順を仕様に書く（⚠ **手順が無いので列が静かに欠ける**）
    派生元: 利用者の指示（2026-09-12）「vibeboard の experiments/overview は最新情報かチェックして」で見つけた。⚠ **積んでおくだけ。いまは直さない**
    ✅ **データは最新だった**【実測 2026-09-12】: 画面は `runs/` をリクエストごとに読む（検証 36 件・先読み 18 件 ＝ ディスクと一致。⚠ **前日に足した `trend_pairs_1995` も一覧 3 位 ＋4,249.77bp で出ている**）
    ⚠ **欠けていたのは列**: 画面は `breadth.実効観測数` を読むが、⚠ **閾値売買の `checks.json` はそのキーを書かない**（書くのは `実効系列数` と `パネルの時刻`）。⚠ **本番 36 件のうち 18 件 ＝ いま主力の閾値売買が全部「—」**（旧方式 18 件だけ数字が出る）
    ⚠ **分析の文だけが残っている**: 値が空のまま「実効観測数が大きい組ほど検査に乗りやすい」と書く（`vibetab.py` の `_analysis_html`）。⚠ **根拠の数字が無い文は消すか、値を出すかのどちらか**
    ⚠ **単純に差し替えてはいけない**: **実効観測数 ≠ 実効系列数**（前者は行、後者は銘柄の本数。`trend_pairs` は実効系列数 4.192）。⚠ **どちらを出すかは定義の決めごと**なので Phase 0 の裁定に寄せる
    ⚠ **検知器の実行は種類が「プーリング」に入る**（`trend_*`・`trend_pairs` を検知器として区別しない）。⚠ **16 章で出力を 2 本にした構成も画面の軸には出ない**（手法名でしか分からない）
    書くもの: ⚠ **「新しい検証方式・新しい章を足したら、`checks.json` の新しいキーと画面の列の対応を確かめる」**を [dashboard.md §10](docs/specs/dashboard.md) の手順に足す（⚠ **画面側にフォールバックを足すのではなく、欠けたら気づく手順にする**）
    関連: [rules.md 13-8](docs/specs/experiments/feature-discovery/rules.md)（別の物差しの数字を直接比べない）／ 「Phase 0: 決めごと」

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


