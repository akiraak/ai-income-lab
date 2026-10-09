# 変換（GA など 6 本）を検知器の経路にも通す口（2026-10-09 利用者決定「A で進めて」）

派生元: TODO「進化的探索を他のモデル・経路に広げる」の子「検知器に『変換済みの列を受け取る』口を足すか決める」。
利用者の問い（2026-09-16）: **遺伝的アルゴリズムは既存のモデル全てに当てはめることはできる？** → (a) モデル 9 本には当てはまる ／ ⚠ 検知器 23 本には届かない（[evolutionary-search.md §8](../../specs/experiments/evolutionary-search.md)）。
2026-10-09 の比較（A 汎用の口 ／ B 足さない ／ C 合成の検知器 1 本）で、利用者の決定 **「検証の幅が広がる汎用的な方」＝ A**。

## 0. 目的・背景

- 変換（`transform`。F4-1 記号回帰〔GA〕・F4-3 tsfresh・F4-4 多項式・F5-1 PCA・F5-3 行列プロファイル・F5-4 ウェーブレットの **6 本**）は、いまは **選別 × モデルの経路**（`fold_buy_pct` の形式 (A)(B) の枝）にしか挟めない
- 検知器（rules.md 15 章。買い% を自分で作って返す手法）の枝は `prep.apply` より**前**にあり、生の表（`tr`・`te`・`feats`）を受け取る ＝ 変換の列が届かない
- 問い: **変換の列は検知器にも効くか**（まずは GA × 順方向の門 `fwd`）。⚠ GA 自身は 18 行 0 勝（[§1・§8・§9](../../specs/experiments/evolutionary-search.md)）だが、rules.md 14-10「可能性が低いから回さない」は理由にならない
- ⚠ **幅の上限を先に書く**: 口を足しても **列を自分で選ぶ検知器には届かない**。届くのは**表の全列を読む検知器**だけで、いまは `H1 先10日ゲート（全列・学習）`（`fwd`）の 1 本

| 検知器 | 読む列 | 口から届くか |
| --- | --- | --- |
| `fwd`（H1 先 10 日ゲート） | `feats` の全部（`y_fwd_` 接頭辞を除く） | ✅ 届く |
| `scale`・`pair`（D1〜D4・C1〜C3・入口 ／ 出口の窓） | `own_trend{窓}_` の 6 列固定 | ⚠ 届かない |
| `tsc`・`seqmodel`・`exomodel`（時系列分類器・PatchTST・外生系列モデル） | 観測期間の**配列**（`seq.window_columns`） | ⚠ 届かない |
| `stop`（損切りをモデルにする） | 入口 ＝ `own` 接頭辞・損切り ＝ `own_trend20_` の 2 列 | ⚠ 届かない |

⚠ ここまで広げるには検知器ごとの直し ＝ **別の処置・別の検証**（このプランには入れない）。

## 1. 対応方針

この図の主張: 口は `fold_buy_pct` の検知器の枝の頭に 1 か所。変換の fit は fold の訓練分割の内側で、出力の列を表に**足して**（置き換えない）検知器に渡す。

```mermaid
flowchart LR
  TR["訓練分割 tr<br/>（生の表・y・日付）"] --> SC["標準化<br/>（訓練で fit）"]
  SC --> TF["prep.apply<br/>（config の transform。<br/>無ければ素通り）"]
  TF --> ADD["変換の列を<br/>tr・te に足す<br/>（gp_00… など）"]
  ADD --> DET["検知器<br/>fn(tr+, te+, feats+, ctx)"]
  DET --> BUY["買い%（手法名 ＝<br/>変換名 ＋ 検知器名）"]
  TE["評価分割 te"] --> SC
  style TF fill:#fde68a
  style ADD fill:#fde68a
```

| # | 決めごと | 中身 |
| ---: | --- | --- |
| 1 | 場所 | `cli/run.py` の `fold_buy_pct`、`if detectors:` の枝の頭。⚠ **config に `transform` が無ければ 1 ビットも変えない**（既存の 21〜23 章と同じ形） |
| 2 | 変換の fit | 訓練分割で `StandardScaler` → `prep.apply(exp, Xtr, Xte, ctx, ytr, ts_tr)`（形式 (A) の枝とまったく同じ呼び方。y と日付は訓練のものだけ） |
| 3 | 列の足し方 | **足す**（置き換えない）。変換が返した列（GA なら `gp_00…`・PCA なら `pc_…`）を `tr`・`te` に同じ名前で足し、`feats + 新しい列` を検知器に渡す。⚠ 既存の列名と重なれば止める。⚠ 置き換えない理由 ＝ 検知器は自分の列を接頭辞で選ぶ（`own_trend…`）ので、元の列が無いと動かない |
| 4 | 標準化の二重 | 検知器は中で自分の列を標準化する。変換の列は既に z 化されている（GA は `conv` で訓練の平均・分散で割る）ので無害 |
| 5 | 手法名（識別項目） | `prep.label(exp, 検知器名)` ＝ **「変換名 ＋ 検知器名」**（GA × LightGBM の §8 と同じ形。先頭の ID で F4 ／ F5 の系統に入る）。`fitness` を替えた行は `〔適合度・…〕` も付く（23 章のまま） |
| 6 | 係数の記録 | `fitted_doc["_transform"]` に変換の係数（式の木・列）。⚠ 次の実行では読み込まない（3 章 B） |
| 6b | 鍵（⚠ 実装で見つけた） | `catalog.canonical` は ID だけに寄せるので、`F4-1 … ＋ H1 …` が既存の GA × Ridge の行（鍵 `F4-1`・Ridge・own・2018）と 1 行にまとまって数え落とす → **「ID ＋ 検知器名」を鍵に残す**（`_DETECTOR_PART`。「＋ 全部使う（基準）」は今までどおり ID だけ ＝ 既存の行は割れない）。予測モデル名は `f4-1-h1-gate10`（`names._ID_DETECTOR`） |
| 6c | 変換の入力（⚠ 1 回目の実行で踏んだ） | 本物の表の `feats` には `y_fwd_10` が残っている（`cli/run.py` は `META_COLUMNS` だけを外す）→ 変換の入力は `contracts.is_meta` で外す。テストの表は `feature_columns` で外していたので見えなかった ＝ テストは `cli/run.py` と同じ作り方の `feats` で |
| 7 | 本番の経路 | `cli/predict.py` も `fold_buy_pct` を通る ＝ `transform` ＋ `detectors` を持つ実験なら本番でも同じ列が足される。⚠ いまの本番の 4 本（`trade_own_ridge_a`・`trade_ownex_lgbm_a`・`trade_ownseq_ridge_a`・`sel_small4_1995`）に `transform` は無い ＝ 指紋は動かない |
| 8 | 規約 | rules.md に **24 章「変換を検知器に通す口」**（短く: 場所・足す・届く範囲・数え方）。15-1 に「変換の列が足されて来ることがある」を 1 行 |
| 9 | 数え方 | 閾値売買の行 ＝ θ 1 水準 1 検証（13-9）。**Phase 2 ＝ GA × `fwd` × θ 3 ＝ n_trials ＋3**。leak 対照 1 本（数えない）。Phase 3 の水準は回す前に §3 に書く |

## 2. Phase と Step

| Phase | 誰 | 中身 | 済み |
| --- | --- | --- | --- |
| 0 | Sx360 の Claude | プランと TODO（このファイル） | ✅ 2026-10-09 |
| 1 | titan の Claude | 口の実装（`cli/run.py`）・テスト（§4）・rules.md 24 章 ／ 15-1 の 1 行 | ✅ 2026-10-09（`prep.augment`・門の検知器の枝も同じ口・鍵 `canonical`・綴り `names`。テスト 7 本 ＝ §4） |
| 2 | titan の Claude | **GA × `fwd`** を回す: config `trade_own_fwd10_gp_ridge_a.toml` ＝ `features_from = "trade_own_fwd10_ridge_a"`（`own` 層 ＋ `label_scales = [10]` の既存の表。表は作り直さない ＝ 13-8）・`transform = "F4-1 記号回帰（遺伝的プログラミング）"`・`detectors = ["H1 先10日ゲート（全列・学習）"]`・`model = "Ridge"`・θ 50 ／ 55 ／ 60・種 0。`cli.queue` で本番 1 ＋ leak 1 → 記録 `docs/specs/experiments/transform-into-detectors.md`（§0 事前固定 → §1 結果 → 検算 n_trials ＋3）→ `cli.report --catalog` で検証結果一覧を吐き直す | ✅ 2026-10-09: 3 行とも落とす（上乗せ −408 ／ −328 ／ −1,342bp。対の差 \|t\| < 1.6）・n_trials 798 → 801。⚠ 1 回目の実行は口の穴（`y_fwd_10` が変換の入力に残る）で leak と同じ値 → 直して回し直し（記録 §1-0） |
| 3 | ⚠ 利用者の了承 → titan | 残り 5 変換 × `fwd` × θ 3（＋15）。⚠ **水準と費用を §3 に書いて了承を取ってから回す**（tsfresh・行列プロファイルは「手間 大」の実績を見る） | 了承 ✅ 2026-10-09「進めて」→ §3 を書いて回した → ✅ **15 行とも落とす**（n_trials 801 → 816。線形の 2 本は `fwd` のみと買い% が同一・非線形の 3 本は対の差 \|t\| < 2。記録 §2-1〜2-5） |
| 4 | 利用者 → Sx360 | 「デプロイ」（`cli/run.py` が本番の予測の経路なので）→ TODO の子を DONE へ・プランを archive へ | ✅ 2026-10-09 14:15 PDT（17:15 ET）: 利用者が `./run-deploy.sh` ＝ 関門 ✅（執行器 202 passed ／ 管理画面 266 passed ／ 指紋 22 passed）→ `prod` cafd762 → **2aea697**・13500t `done 2aea697`（管理画面のコンテナ起こし直し）。⚠ 分類器が Claude の実行を止めたので、関門の `--dry-run` だけ Claude（Sx360）が先に流し、本番は利用者が流した → TODO の子を DONE へ・このプランを archive へ |

## 3. Phase 3 の水準（⚠ 2026-10-09 利用者「進めて」。この節は回す前に書いた）

Phase 2 の結果を見た**後に**水準を足すことになるので、⚠ **Phase 2 の結果で Phase 3 の水準を選ばない**（6 本全部か、回さないか。間を取らない）＝ **5 本全部**。変換の水準は元の実行の事前固定をそのまま使う（1 つも動かさない）。検知器・学習器・θ・fold・表の期間は Phase 2 と同じ。

| 変換 | config | 表（`features_from`） | 足す列 | 通したままの列（足さない） | 費用【実測】（元の実行・本番 1 本） |
| --- | --- | --- | ---: | --- | ---: |
| F5-1 PCA（成分 16） | `trade_own_fwd10_pca_ridge_a` | `trade_own_fwd10_ridge_a`（own 35） | 16 | — | 数秒〜数十秒（`pca_1995`） |
| F4-4 多項式（2 次・交互作用） | `trade_own_fwd10_poly_ridge_a` | 〃 | 630（2 乗 35 ＋ 交互作用 595） | 1 次 35 | 数十秒（`trade_own_poly_a`） |
| F5-4 ウェーブレット（db4・レベル 3） | `trade_ownseq_fwd10_wave_ridge_a` | `trade_ownseq_fwd10_ridge_a`（own 35 ＋ seq 60） | 係数（元の実行で測る） | own 35 | 数十秒（`trade_ownseq_wave_a`） |
| F5-3 行列プロファイル（m 10） | `trade_ownseq_fwd10_mp_ridge_a` | 〃 | 5 | own 35 | 112 秒（`trade_ownseq_mp_a`） |
| F4-3 tsfresh（783 列） | `trade_ownseq_fwd10_tsf_ridge_a` | 〃 | ≤ 783（全 NaN・定数を落として） | own 35 | ⚠ **3,634 秒・メモリ 24GB**（`trade_ownseq_tsf_a`。titan は 47GB） |

- 数: **5 本 × θ 3 ＝ ＋15（n_trials 801 → 816）**・leak 対照 5 本（数えない）。queue `transform_detector3`（順は手間の小さい順・予算 14,400 秒）
- ⚠ **元の実行と違う点 2 つ（口の規則から来るもの。回す前に書く）**: ① F4-3 ／ F5-3 ／ F5-4 は元の実行で「窓の生の 60 列を落とした」が、口は**足す**規則なので検知器は生の 60 列も読んだまま ＝ 同じ情報が 2 度渡る ／ ② F4-3 は元の実行で F1-5 検定+FDR に絞らせたが、検知器の口は選別を挟めないので `fwd` が全列（≤ 783 ＋ 95）を読む
- ⚠ **実装で足した規則**: 変換が入力をそのまま通した列（名前が入力と同じ ＝ F4-3 ／ F4-4 ／ F5-3 ／ F5-4 の `own` 35）は足さない（検知器が元の列を持っている）。入力に無い名前が表の列と重なれば止める（rules.md 24-1 の 3）
- 対の相手: own 表の 2 本 ＝ `2026-09-27T14-21-10_trade_own_fwd10_ridge_a` ／ seq 表の 3 本 ＝ `2026-09-27T14-22-51_trade_ownseq_fwd10_ridge_a`（回し直さない）
- 予想【推測】: 15 行とも「採る」にならない（`fwd` × Ridge は 3 表とも落とす・5 変換は選別 × モデルの経路で全部落とした）。列数が大きい F4-3 ／ F4-4 は Ridge の過学習で対の差が負に寄る。leak は 5 本とも跳ねる（`fwd` のみの対照と同じ値）
- ✅ **回した結果（2026-10-09）**: 15 行とも落とす・n_trials 801 → 816・leak 5 本とも跳ねた。⚠ **PCA・ウェーブレットは `fwd` のみと買い% が 1 ビットも同じ**（線形の変換 × Ridge ＝ 空間が変わらない。[記録 §2-3](../../specs/experiments/transform-into-detectors.md)）。予想「列数の大きい変換は負に寄る」は外れ（tsfresh は 3 θ とも正だが 1 fold 由来・誤差の中）。費用【実測】: tsfresh 2,522 秒（元の実行 3,634 秒）・ほかは 10〜110 秒・queue 全体で約 90 分

## 4. テスト方針

| # | テスト | 固定すること |
| ---: | --- | --- |
| 1 | 指紋（既存）`tests/test_trading_run.py::test_default_path_fingerprint_is_unchanged`・`tests/test_predict.py` | `transform` の無い config は選別 × モデルの経路が 1 ビットも変わらない |
| 2 | 指紋（足す） | `transform` の無い検知器の config（`fwd`）の買い% が実装の前後で同じ（前の値を写して固定） |
| 3 | 口が開く | モックの検知器が受け取る `feats` に変換の列が入り、`tr`・`te` にその列がある・元の列も残っている・手法名が「変換名 ＋ 検知器名」 |
| 4 | 先読み（14-11 規約 3 と同型） | 評価分割を差し替えても `fitted["_transform"]` の式が変わらない（検知器の経路で） |
| 5 | leak 対照 | `LEAK_` の列は変換の入力に入る → 上乗せが跳ねる（既存の `test_leak_makes_the_edge_jump…` の形） |
| 6 | 名前の衝突 | 変換の列名が既存の `feats` と重なれば止まる |
| 7 | 学習の対象は変換に渡らない（⚠ 2026-10-09 に足した） | `cli/run.py` と同じ作り方の `feats`（`y_fwd_10` を含む）で、`_transform` の式に `y_fwd_` が出ない・検知器の `columns` は今までどおり |

## 5. 影響範囲

- 変わる: `experiments/feature-discovery/cli/run.py`（`fold_buy_pct` の検知器の枝）・`tests/`・`rules.md`・`config/experiment/` に 1 本・記録 1 本・`ledger.md`（吐き直し）・`ledger_rows`
- 変わらない: 検知器 7 ファイル・`prep.apply`・`cli/predict.py`・本番 4 本の config・既存の実行と検証結果一覧の行
- ⚠ 本番: `cli/run.py` は 13500t の毎日の予測が通る ＝ Phase 4 でデプロイ（指紋テストが通ってから・15:00〜16:15 ET の外）

## 6. 作業量・費用【推測】

| Phase | 時間 | 費用 |
| --- | ---: | ---: |
| 1 | 半日（titan） | 0 |
| 2 | 回すのは 1〜2 分（GA 1 fold 0.3〜0.9 秒・`fwd` Ridge は数十秒【§6・forward10 の実測から】）＋ 記録 1 時間 | 0 |
| 3 | 変換により数分〜数時間（tsfresh・行列プロファイルは別）。了承の後に書く | 0 |
| 4 | デプロイ 20 分 | 0 |
