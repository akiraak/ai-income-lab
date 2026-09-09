# TODO

- [ ] 予想モデルに使うデータを広く収集する [plan](docs/plans/daily-data-sources.md)
  利用者の指示（2026-09-08）: **予想モデルに使うデータを広く収集する**
  利用者の指示（2026-09-08・追加）: **為替のデータを入れる。それ以外で日ごとのデータで入れられるものを調査する。⚠ 天気予報など全く関連がなさそうなものでもいい**
  ⚠ **方向は (a) 情報の種類を増やす に決まった**（下の 2 択のうち）
  ⚠ **無関係に見える系列は「偽薬（プラセボ）」として明示的に置く**（[plan §1-1](docs/plans/daily-data-sources.md)）。⚠ **足すだけだと多重検定の温床だが、基準線として使えば検証が強くなる**。⚠ **本命が偽薬を超えられないなら、本命も偶然の範囲**
  ⚠ **一番危ないのは発表の遅れ**。⚠ **「その日のデータ」が「その日に手に入る」とは限らない**ので、外部系列は**既定で 1 日ずらす**（[plan §2-4](docs/plans/daily-data-sources.md)）
  - [x] Phase 1: ⚠ **規約の確認**（2026-09-08。[記録](docs/specs/experiments/daily-data-sources.md) §2）
    ⚠ **FRED の規約は「データマイニング・スクレイピング・抽出をするな」。正規の経路は無料 API**。⚠ **2026-09-01 に採用した `fredgraph.csv` 直叩きは正規ではない**ので実測を途中で止めた
    ⚠ **Open-Meteo と SILSO は CC BY-NC（非商用のみ）。** ⚠ **天気は NOAA（米政府の公有）に替えれば判断が要らない**
  - [x] Phase 2: 無登録で取れるものの実測（2026-09-08。[記録](docs/specs/experiments/daily-data-sources.md) §3）
  - [x] Phase 3: ⚠ **取得して層に置いた**（2026-09-08。`python3 -m cli.fetch --exog exog_daily`。[記録 §3-4](docs/specs/experiments/daily-data-sources.md)）
    ⚠ **4 経路・25 系列**: ECB 為替 7 本（7,088 行・1999〜）／米財務省イールド 7 本（2,171 行・2018〜）／⚠ **NOAA 気象 9 本（偽薬）**／⚠ **USGS 地震 2 本（偽薬）**
    ⚠ **地震だけ「行が無い日」＝「0 件の日」**（71 日）。⚠ **特徴量にするときは 0 で埋める。前方埋めは禁止**
    ⏳ 残り（判断待ちの外）: 連邦準備 H.10 の URL 確定 ／ 地磁気の長期 ／ 黒点の公有経路 ／ GDELT の規約
  - [x] Phase 3-2: ⚠ **相関の低い銘柄を 23 本足した**（2026-09-09。[記録 §7](docs/specs/experiments/daily-data-sources.md)）
    ⚠ **銘柄数を増やしても標本はほとんど増えない**: 会社株の相関 0.341 で、⚠ **実効系列数の上限は 8.35 本。48 銘柄で既に 77%**。⚠ **10 倍にしても t は 1.13 倍**
    ⚠ **狙いが当たったのは 23 本中 12 本だけ。** 国際株・小型株・REIT は実質が株（EFA 0.843・IJR 0.839）。⚠ **HYG は債券 ETF なのに 0.776**
    ⚠ **23 本ぜんぶより 12 本に絞るほうが実効系列数が多い**（7.81 → 9.06。t の伸び 1.17）
    ⚠ **線引きは資産クラスで引いた**（測った相関で引くと分割の外で選ぶことになる）。⚠ **取ったものは raw から消していない**
  - [ ] **⚠ 利用者の判断待ち 2 件**（[記録 §4](docs/specs/experiments/daily-data-sources.md)）
    ⚠ **(1) FRED の無料 API キーを取るか**（読み取り専用。売買口座の登録とは別物）
    ⚠ **(2) 本プロジェクトを「非商用」と言えるか**（目的が「収入を稼ぐ方法の体系化」なので言い切れない）
    ⚠ **どちらも No でも進む**: 為替・気象・地震・イールドは判断の外側で使える
  - [ ] Phase 4: ⚠ **`ex_` 層と偽薬の枠**（1 日ずらす ／ `n_trials` に数える）
  - [ ] Phase 5: 検証に載せて、⚠ **本命が偽薬を超えるかを見る**
  派生元: 「特徴量を見つけ出す手法を、1 分足の実データで検証する」（2026-09-08 に打ち切り）。
  ⚠ **その [§9-2](docs/specs/experiments/feature-discovery.md) の結論が「次に変えるなら推定器ではなく入力」**。
  ⚠ **価格だけからは方向が出なかった**ので、⚠ **同じ価格データで手法を替えるのをやめて、入れる情報を増やす側に回る**
  ⚠ **着手前にプランで決めること（どちらの「広く」かで作業がまるごと変わる）**
    - (a) ⚠ **情報の種類を増やす**（決算・イベント・マクロ・センチメント・板 など）。⚠ **E8 の失敗の型で最多は X12「方向を当てていない」29 件**で、価格だけの限界はここに出ている
    - (b) ⚠ **銘柄と期間を増やす**（いまは 63 銘柄・1 分足 6 週間・日足 32 年）。⚠ **生存バイアスが直せる**（63 銘柄は 2026-09-08 時点の時価総額上位で、上場廃止した銘柄が 1 つも入っていない。[rules.md 12 章 限界 1](docs/specs/experiments/feature-discovery/rules.md)）
  ⚠ **規約と法令を先に当たる**（CLAUDE.md の依頼方針）。⚠ **収集は「取れるか」より「取ってよいか」で落ちる**
    - ⚠ **Stooq は robots.txt で全自動アクセスを拒否していて 2026-09-01 に「採らない」で確定済み**（[market-data-availability.md §3-2](docs/specs/market-data-availability.md)）。⚠ **同じ確認を新しい取得元ごとに行う**
    - ⚠ **再配布の可否は取得の可否と別**。⚠ **`data/` は git 管理外だが、それは規約を満たす理由にならない**
  ⚠ **やり直さなくてよい調査**（2026-09-01 に済み。[market-data-availability.md](docs/specs/market-data-availability.md) 96 件）
    - ⚠ **無償かつ無登録で tick が取れるのは暗号資産と予測市場だけ**。⚠ **米国株は無償でも必ずアカウント登録を要求する**
    - ⚠ **株価指数は FRED から無登録・日次で取れる**（`NASDAQCOM` 14,498 行 1971〜。⚠ **指数であって個別銘柄ではない**）
    - ⚠ **tastytrade / dxFeed の上限は実測済み**: 1 購読 約 8,000 本・`toTime` は無視される・1 分足は約 6 週間で頭打ち（[data-routes.md](docs/specs/experiments/e8-signal-methods/data-routes.md)）
  ⚠ **置き場と作法は [rules.md](docs/specs/experiments/feature-discovery/rules.md) が正本**。⚠ **新しい取得元も同じ層に載せる**（`raw/<取得元>/` は書き換えない ／ 加工は新しい層 ／ 取得のたびに検査して manifest を書く）
  ⚠ **資金は動かさない**（2026-08-27 の方針）。⚠ **有償のデータは試算までで、購読しない**
  ⚠ **数字は【実測】/【公表値】/【推測】を明示する**（【公表値】は URL と取得日を併記）
  関連: [market-data-availability.md](docs/specs/market-data-availability.md) ／ [feature-discovery.md §9](docs/specs/experiments/feature-discovery.md) ／ [rules.md](docs/specs/experiments/feature-discovery/rules.md) ／ [e8-signal-methods.md](docs/specs/experiments/e8-signal-methods.md)

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
