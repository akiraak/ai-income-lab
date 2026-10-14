# 実売買の日次の記録を自動で出す

利用者の質問（2026-10-13）: **「記録はなんで毎回手動で行わないといけない？」** → 「プランを書いて」

## 1. 目的・背景

いまは営業日ごとに Sx360 の Claude が 13500t の DB（`live.sqlite`）を読み、[live-trading.md §1](../specs/experiments/live-trading.md) の日次の表と「新しい 3 人の物差し」（A〜E）の表へ手で写している。
手で写しているのは**決まりではなく、仕組みを作っていないだけ**。経緯は 3 つ:

1. 最初の頃は毎日のように事故があり（9/22 の起動遅れ・10/5 の上限超え・10/6 の `live.env` の引用符）、数字より「何が起きたか」を書く必要があった
2. 【実測】の数字の正本は仕様書と決めてある（CLAUDE.md「説明の正本の分け方」）
3. 13500t の DB を読めるのは Sx360 からだけ（titan から届かない）

ところが、記録の中身のほとんどは DB から機械的に出せる。人（Claude）の判断が要るのは「事故の原因」「1 日の運」といった読み方だけ。

| 記録の中身 | いま | 自動にできるか |
| --- | --- | --- |
| 注文・約定の本数・差 1 | 管理画面が計算済み（`dashboard/app/live.py` の `_diff1_bp`・`history`） | ✅ 出ている。写しているだけ |
| A 差 1（中央値 ／ 最大） | 同上 | ✅ 同上 |
| B 持ち続けるとの差 | Claude が毎回手で計算（気配・売買履歴・初日の約定） | ✅ 定義が決まっている（[trader-loop.md Phase 2](trader-loop.md)）。⚠ 基準の株数の決め方だけ文にする（K3） |
| C 机上との一致 | Claude が合図と注文を読んで手で判定 | ✅ 合図の行に `position_before`（合図の前の持ち高）があるので、規則（rules.md 13-4）で出る判断と実物の判断（注文の向き ／ `hold` ／ `skip`）を直接突き合わせられる（⚠ 実装で変えた: 最初の案は `paper.py` に口を足す形だった ＝ `paper.py` は触らない） |
| D 無人運転 | Claude が `trade.log`・`reconcile` を読む | ✅ ほぼ（起動・終了・食い違いは `events` の `start` ／ `end` ／ `positions_outside_ledger` ／ `position_short` ／ `journal_unresolved`）。⚠ 「人手の回数」は記録から分かるもの（`HALT`・指示・`reconcile.py` の操作の履歴）だけ数える |
| E 差 3（そろえた紙上） | `paper.py` が `out/daily.csv` に書く（朝の `--prepare`）・管理画面も読む | ✅ 出ている。写しているだけ |
| cron の `end rc=` | `check.sh` が 16:20 ET に判定（`checks.log`） | ✅ 判定済み。⚠ 管理画面はホストのログを読めない（`events` から同じことを出す） |
| 事故の原因・読み方 | Claude | ❌ 人の判断。ここだけ手で書く |

いまの流れ（手で写している箇所が太線）:

```mermaid
flowchart LR
  EX["執行器<br/>15:50 ET"] --> DB[("live.sqlite<br/>13500t")]
  PA["paper.py<br/>翌朝 --prepare"] --> DB
  DB --> DASH["管理画面<br/>差 1・差 3 は出ている"]
  DB ==>|"Sx360 の Claude が<br/>毎日読んで手で計算"| SPEC["live-trading.md §1<br/>日次の表 ＋ A〜E"]
```

## 2. 対応方針

**`paper.py` と同じ形の「読むだけの後処理」を 1 本足し、管理画面に出す。仕様書には異常の日と判定の写しだけを書く。**

直した後の流れ（1 図 1 主張: 人の手が「読み方」だけに減る）:

```mermaid
flowchart LR
  EX["執行器<br/>（変えない）"] --> DB[("live.sqlite")]
  PA["paper.py ＋ yardstick.py<br/>翌朝 --prepare"] --> DB
  DB --> DASH["管理画面<br/>A〜E と異常の一覧"]
  DASH -.->|"異常の日・判定の日だけ"| SPEC["live-trading.md §1<br/>読み方を書く"]
```

- 新しい部品 `experiments/live-trading/yardstick.py`（物差し）: 入力は執行器の記録（`signals` ／ `orders` ／ `ledger` ／ `events` ／ `quotes`）と `out/daily.csv`。出力は `out/yardstick.csv`（1 日 1 行 × トレーダー）。⚠ **読むだけ**（ネットワークを使わない・発注しない・売買履歴を書かない）。毎回ぜんぶ作り直す（何度流しても同じ）＝ `paper.py` と同じ約束
- 起こす場所は朝の `run-live.sh --prepare`（`paper.py` の直後）。E は公式終値が要るので、どのみち翌朝にならないとそろわない ＝ 同じ時刻にまとめる。A・D の当日分は、管理画面がいまも記録から直接出している
- 管理画面は `yardstick.csv` を読むだけ（`daily_rows` と同じ形）。トレーダーの詳細に A〜E の表、全体の詳細に「異常の一覧」（`end` が 0 以外・`journal_unresolved`・`positions_outside_ledger`・`position_short`・`order_unknown`・`blocked_symbol`・C が合わない日・A が線 5bp を越えた日）
- ⚠ **執行器（`run_day.py`・`execute.py`・`plan.py`）は変えない**（新しい 3 人の記録の途中。いまの方針「記録の間は執行器を変えない」を守る）
- ⚠ **損益で人を採らない**（B は見るだけ。表に注記を固定で置く）

### 利用者に決めてほしいこと（⚠ 実装の前に）

✅ **2026-10-13 利用者決定「全部おススメで進めて」＝ K1〜K4 とも推す案**（K1 は CLAUDE.md「説明の正本の分け方」の例外に、K2 は `live-trading.md` §1 の頭に書いた）

| # | 論点 | 推す案 | 理由 |
| --- | --- | --- | --- |
| K1 | 日次の数字の正本 | **DB と管理画面**（`yardstick.csv`）。仕様書は「異常の日の読み方」と「判定の日の写し」だけ | 同じ数字を 2 か所に書かない（CLAUDE.md の「同じしくみの説明を 2 度書かない」と同じ考え）。⚠ CLAUDE.md の「数字は仕様書」の例外として書き足す |
| K2 | 普段の日の §1 の行 | **書かない**。週に 1 度（金曜の引け後）、Claude が A〜E の表を写して 1 行の所見を付ける | 毎日の手作業をなくす。週 1 回でも 20 営業日の判定には足りる |
| K3 | B の基準（持ち続ける）の株数 | **その人の 1 日目の 15:50 ET の中値で `plan.size_intents` と同じ丸め**（予算 ÷ 5 本 → 整数株）。何も買わなかった人（T4 の 1 日目）も同じ計算 | 10/8・10/9 の手計算は「T1・T6 の 1 日目と同じ株数」で、T4 も同じ株数を使った。式にすると同じ値になる見込み【推測】。違えば 10/9 の手計算と並べて報告する |
| K4 | 「人手」の数え方 | **記録から分かるものだけ**（`HALT` の置き外し・名簿 ／ 指示の操作・`reconcile.py` の書き込み・`liquidate.py`）。投入の手順（名簿の開始）は数えない | 記録に残らない人手（ssh で何かした）は機械では数えられない。数えられないものは「数えていない」と表に書く |

## 3. Phase と Step

```mermaid
flowchart LR
  P0["Phase 0<br/>決めごと K1〜K4<br/>（利用者）"] --> P1["Phase 1<br/>yardstick.py<br/>＋ テスト"]
  P1 --> P2["Phase 2<br/>管理画面に出す"]
  P2 --> P3["Phase 3<br/>デプロイ<br/>（利用者）"]
  P3 --> P4["Phase 4<br/>手書きと突き合わせ<br/>→ 手書きをやめる"]
```

### Phase 0: 決めごと（⚠ 利用者）✅ 2026-10-13

- K1〜K4（§2 の表）。決まるまで実装しない
- 決まったら CLAUDE.md の「説明の正本の分け方」に 1 行足す（日次の数字は `yardstick.csv`）

### Phase 1: `yardstick.py`（titan ／ Sx360。⚠ 本番は触らない）✅ 2026-10-13 Sx360

✅ `experiments/live-trading/yardstick.py` ＋ `tests/test_yardstick.py` 10 本（10/7〜10/9 を記録の値から合成で作り直し、手書きの A・C・D と B の「その人の損益」〔T1 −$17.92 ／ T4 +$0.01 ／ T6 −$17.84〕を再現）・`run-live.sh --prepare` の `paper.py` の後。⚠ 13500t の記録を Sx360 へまとめて写すのは自動の安全判定に止められた（本番の記録の持ち出し）＝ 本物との突き合わせは Phase 4（デプロイの後に 13500t の上で）

⚠ 見つかったこと: **B は手書きと少しずれる**（T1 −$0.14 ／ 手書き −$0.10・T4 +$17.79 ／ +$17.83・T6 −$0.06 ／ −$0.02）。基準の取得単価が K3 ＝ 1 日目の中値で、手書きは T1 の約定だった ＝ 1 日目の差 1（T +2bp・BAC +2bp ≈ $0.04）のぶん。K3 どおりなので式を変えない

| Step | 中身 |
| --- | --- |
| 1-1 | A: `orders` から差 1（中央値 ／ 最大・本数）。管理画面の `_diff1_bp` と同じ式（共有できるなら共有する。⚠ 管理画面から執行器側へ import の向きを作らない ＝ 式を執行器側に置いて管理画面が import する形） |
| 1-2 | B: 売買履歴の実現 ＋ 含み（15:51 ET の中値）− 手数料（1 日目の買いを含む）と、基準（K3）の時価の差。USD と bp of 予算 |
| 1-3 | C: 合図（買い% ／ 出口%）・θ・`position_before` から規則で出る判断と、実物の判断（`orders` ＋ `hold` ／ `skip`）を突き合わせ、合わない銘柄を列に残す。見送り（`too_small` 等）は不一致に数えない・手じまいと一時停止の日は見ない |
| 1-4 | D: 営業日（NYSE の暦）ごとに `start` ／ `end` の有無と結果・食い違いの事象・人手（K4） |
| 1-5 | E: `daily.csv` の `diff3a_bp` ／ `diff3a_cum_bp` を写す（計算しない） |
| 1-6 | `run-live.sh --prepare` の `paper.py` の直後に起こす（失敗しても準備は失敗にしない ＝ `paper.py` と同じ扱い。⚠ `--prepare` の外の経路〔発注の回〕は触らない） |

### Phase 2: 管理画面に出す ✅ 2026-10-13 Sx360

✅ `dashboard/app/live.py`（`yardstick_rows`・`_ys_view`・`anomalies`。まとめは `yardstick.summarize` を道で読んで呼ぶだけ）・`trader.html`・`overall.html`・`glossary.toml` 2 語・`dashboard.md` §13-13・`tests/test_live.py` 2 本。`./run-tests.sh` ✅（管理画面 268 本）

| Step | 中身 |
| --- | --- |
| 2-1 | `live.py` に `yardstick_rows`（`daily_rows` と同じ形で読むだけ。無ければ空・仮データは出さない） |
| 2-2 | トレーダーの詳細に A〜E の表（全期間 ＝ その面の決まり）。B の横に「⚠ 損益で人を採らない」を固定で置く。言葉は `glossary.toml` に（i マーク） |
| 2-3 | 全体の詳細に「異常の一覧」（直近 20 営業日 ＝ その面の決まり）。異常 0 の日は出さない |
| 2-4 | ⏭ やめた: シミュレーション・デモには `daily.csv` も無い（`paper.py` を流していない・デモの記録は `quotes.jsonl` を除いてある ＝ B が出ない）。紙上の対照と同じ扱いにそろえ、`yardstick.csv` が無ければ節を出さない。シミュレーションで見たいときは `python3 yardstick.py --out-dir sim/<名前>/out` を手で流す |

### Phase 3: デプロイ（⚠ 利用者の「デプロイ」）

- `run-live.sh`・`dashboard/` は本番の経路 ＝ `prod` を進めると 13500t が 15 分以内に取り込む。⚠ 15:00〜16:15 ET（12:00〜13:15 PDT）は `run-deploy.sh` が拒む
- 次の朝 06:00 PDT（09:00 ET）の `--prepare` で初めて `yardstick.csv` ができる

### Phase 4: 手書きと突き合わせて、手書きをやめる（Sx360 の Claude）

- 最初の 1 回は、手で書いた 10/8・10/9（と、それまでに書いた日）の A〜E と `yardstick.csv` を並べる。違えば**どちらが正しいかを確かめてから**直す（手計算の誤りもありうる）
- 一致したら（K2 の推す案なら）普段の日の §1 の行をやめ、週 1 回の写しと異常の日の読み方だけにする
- 後片付け: TODO の親を DONE へ・このプランを `archive/` へ

## 4. 影響範囲

| 場所 | 変わるもの | 本番への影響 |
| --- | --- | --- |
| `experiments/live-trading/yardstick.py` | 新規 | 朝の準備の後に動く。読むだけ |
| `run-live.sh` | `--prepare` の最後に 1 行 | 発注の回は変えない |
| `dashboard/app/live.py`・templates・`glossary.toml` | 表示を足す（POST は増やさない） | 管理画面のコンテナの起こし直し |
| `docs/specs/experiments/live-trading.md` §1 | 書き方を変える（K2） | なし |
| `CLAUDE.md` | 正本の分け方に 1 行（K1） | なし |
| ⚠ 変えないもの | `run_day.py`・`execute.py`・`plan.py`・`ledger.py`・g3plus-ops（`run.sh`・`check.sh`） | — |

## 5. テスト方針

- `experiments/live-trading/tests/test_yardstick.py`: 合成の記録で A〜E を 1 つずつ（買い ／ 売り ／ hold ／ skip ／ 手じまい ／ 一時停止 ／ 起動しなかった日 ／ `end` が 1 の日 ／ C が合わない日）
- **10/8・10/9 の手計算の値を写したもの**を固定する（T1 の B −$0.10 ／ T4 +$17.83 ／ T6 −$0.02・C 0 日・D 2 ／ 2）。⚠ 13500t の記録はテストに入れない（口座の数字をリポジトリに入れない）＝ 必要な行だけを合成で作り直す
- `paper.py` は触らない（C は合図の `position_before` から判定する）
- 黄金の集計値（`--full`）が変わらないこと（執行器は触らないので変わらないはず。変わったら止まって原因を確かめる）
- 管理画面: `yardstick.csv` が無いときに落ちない・仮データを `/api/live` に出さない・秘密が応答に出ない（既存のテストに足す）
- 関門はいつもどおり `./run-tests.sh`

## 6. 作業量と期間【推測】

- Phase 1: 半日〜1 日（B と C の突き合わせがいちばん重い）
- Phase 2: 半日
- Phase 3〜4: デプロイの翌朝に 1 回の突き合わせ（30 分ほど）
- 合計 1〜2 日。費用は 0（計算だけ・新しいサービスを使わない）

## 7. やらないこと

- 外への通知を増やす（いまの `check.sh` の healthchecks.io の口のまま）
- 「読み方」を自動で書く（事故の原因は人が書く）
- 執行器を変える（新しい 3 人の記録の途中）
