# TODO

- [ ] 特徴量を見つけ出す手法を、1 分足の実データで検証する [plan](docs/plans/feature-discovery.md)
  利用者の指示（2026-09-08）: **データを取得する（1 分間隔が望ましい）／ 手法を調査し予測モデルを作成する ／ 検証**
  派生元: [E8 の記録](docs/specs/experiments/e8-signal-methods.md)。⚠ **E8 は「シグナルの手法」を並べたが、その 1 段下の「特徴量の見つけ方」は空**。⚠ **E8 §9-1 の限界 1（自前のバックテストを 1 件も回していない）を埋めにいく作業でもある**
  ⚠ **E8 から引き継ぐ決定**: 自前の数字は**悪い結果はそのまま結論、良い結果は根拠「中」が上限**。⚠ **デフレーテッド SR とパージ CV を通していない良い数字は報告しない**
  ⚠ **資金は動かさない**（2026-08-27 の方針）。データ取得は既にある tastytrade の口座（読み取りのみ）と Kraken（無登録）
  - [x] Phase 1: データ経路の確定と取得（⚠ **`toTime` は無視されると実測** ／ 1 分足 63 銘柄 504,000 行 ／ 日足を取得中）
    ⚠ **1 分足は 1 購読 約 8,000 本 = 6 週間で頭打ち**（[実測](docs/specs/experiments/e8-signal-methods/data-routes.md)）。`toTime` が効かないなら**銘柄を横に増やして標本を作る**
  - [x] Phase 2: 手法のカタログ 5 系統 25 件（F1 フィルタ 7 / F2 ラッパー 4 / F3 埋め込み 6 / F4 生成 4 / F5 表現学習 4）
  - [x] Phase 3: 基準線 4 本（全部使う・乱択・常に上・直前リターン）＋ 選別手法 11 件。⚠ **配線の検査（わざと未来を混ぜる）は合格**（的中率 99.9%）
  - [~] Phase 4: 検証と判定（⚠ **1 分足は有効。日足は無効に戻した**）
    ⚠ **1 分足は ✅ そのまま**: 完全予知でもコストに届かない（[§0](docs/specs/experiments/feature-discovery.md)）＋ 全手法がコスト後で負け。⚠ **目盛りの誤りは 1 分足には 1 件も無い**
    ⚠ **日足は ❌ 無効**: 提供元の分割調整に誤りがあり、⚠ **63 本の偽リターンがラベルの σ を 0.0208 → 0.0260 に 26% 膨らませていた**（[§6-3](docs/specs/experiments/feature-discovery.md)）。＋1.61bp・t = 1.00 は測り直しが要る
    ⚠ **前提 2 は残る: プーリングのみ**（各行が自分の履歴しか見ない）。断面を使った評価はしていない
    ⚠ **デフレーテッド SR は計算していない**（全部が負けなので通す意味が無い。良い数字が出たときは必須）
    E8 への書き戻し済み: [§9-1 の限界 1](docs/specs/experiments/e8-signal-methods/verdict.md)
  - [ ] Phase 5: ⚠ **日足のやり直し**（`adjusted/` で `own_only_h1` を回し直す）
    ⚠ **土台は用意済み**: `python3 -m cli.build --experiment own_only_h1` → `python3 -m cli.run --experiment own_only_h1`
    ⚠ **旧 `evaluate.py` と同じ数字が出ることは確認済み**なので、⚠ **変わったぶんは調整の効果である**
    ⚠ **§4 は消さずに「ルール以前の結果」として残す**
  - [ ] Phase 6: ⚠ **断面での検証**（`cs_` `rel_` `ll_` を使って NVDA を当てる）
    ⚠ **土台は用意済み**: `cross_section_h1` で **134 本**（own 35 / cs 26 / rel 13 / ll 60）・96,769 行が作れる
    ⚠ **`config/experiment/cross_section_h1.toml` の `ll_leaders = "all"` で ll が 60 → 248 列**になる。コードは書かない
    ⚠ **列を増やすほど多重検定になる。良い数字が出たらデフレーテッド SR と実効標本数の割引を必ず通す**（`ail/validation/stats.py`）
  - [ ] Phase 7: ⚠ **非線形モデル**（Ridge 1 本しか試していない）／ ⚠ **tsfresh の総当たり（F4-3）**
  - [ ] Phase 8: 記録を確定して DONE へ
  ⚠ **規約は [rules.md](docs/specs/experiments/feature-discovery/rules.md) が正本。** ⚠ **数字を出す前に 9 章（検証）と 11 章（数字の扱い）を読む**
  ⚠ **数字が変わったら、まず配線を疑う**（`python3 -m cli.report --diff A B` で入力の指紋を並べる）
  関連: [e8-signal-methods.md](docs/specs/experiments/e8-signal-methods.md) ／ [market-data-availability.md](docs/specs/market-data-availability.md)

- [ ] 試した分析手法を一覧で見えるようにする（specs に台帳を置く）
  利用者の指示（2026-09-08）: **分析手法をいろいろ試すが、一覧で見えるように specs に書く**
  ⚠ **置き場は [docs/specs/experiments/feature-discovery/](docs/specs/experiments/feature-discovery/) の下**（`rules.md` と並べる）。E8 の 93 件カタログは「手法の一覧」、これは **「試した結果の一覧」**で別物
  ⚠ **1 行 = 1 回の試行**（手法 × 粒度 × 地平 × 特徴量の層 × データの層）。⚠ **同じ手法を条件違いで何度も試すので、手法ごとに 1 行にすると潰れる**
  ⚠ **二重管理にしない。** 結果の正本は `runs/<実行>/summary.csv`、実装の正本は `ail/registry.py`。⚠ **一覧はその 2 つを突き合わせて生成するものにし、手で書き写さない**（写経すると必ずずれる）
  ⚠ **「まだ試していない」も行として出す。** カタログ 25 件のうち ⚠ **実装済みは 9 件**（F1-1/2/3/5・F2-3・F3-1/2/3/5）で、⚠ **残り 16 件は「試していない」のか「試して落とした」のかが今どこにも書いていない**
  ⚠ **数字の書き方は [rules.md 11 章](docs/specs/experiments/feature-discovery/rules.md) に従う**（悪い数字は結論、良い数字は根拠「中」が上限 ／ 【実測】【公表値】【推測】の明示 ／ fold ごとの符号を必ず出す）
  - [ ] Phase 1: 台帳の列を決める（手法 ／ 系統 ／ 実装の有無 ／ データの層 ／ 粒度 ／ 地平 ／ 特徴量の層 ／ 選んだ本数 ／ 的中率 ／ IC ／ 粗利 ／ ⚠ **純利** ／ fold の符号 ／ 判定 ／ 実行 ID）
    ⚠ **判定は「採る／落とす／保留」の 3 値にし、落とした理由は E8 の失敗の型（X1〜X12）で書く**
  - [ ] Phase 2: `cli/report.py --catalog` で `runs/` と `registry` から台帳の markdown を吐く
    ⚠ **未実装の手法はカタログ（spec §2 の 25 件）と registry の差分から出す**。⚠ **カタログ側に ID を持たせないと突き合わせられない**
  - [ ] Phase 3: 既存の結果を台帳に流し込む（1 分足 §3-3 ／ 日足 §4-3）
    ⚠ **日足は無効なので「無効・要再測」として載せる**（消さない）
  - [ ] Phase 4: 一覧を読んだだけで「次に何を試すか」が決まるか確認し、決まらなければ列を足す
  依存: 「特徴量を見つけ出す手法を、1 分足の実データで検証する」の Phase 5（⚠ **流し込む結果が要る**）
  関連: [rules.md](docs/specs/experiments/feature-discovery/rules.md) ／ [feature-discovery.md](docs/specs/experiments/feature-discovery.md) ／ [e8-signal-methods.md](docs/specs/experiments/e8-signal-methods.md)

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
