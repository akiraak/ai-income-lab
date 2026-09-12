# DSR（デフレーテッド SR）の解説ページを作る

作成日: 2026-09-12。TODO の「疑問に思ったことを登録し解決していく」→「DSRの解説」。

## 1. 目的・背景

DSR は本プロジェクトの検証で**あちこちに顔を出すのに、定義の説明がどこにも無い**。

| どこに出てくるか | 何が書いてあるか |
| --- | --- |
| [rules.md 11 章 規約 3](../../specs/experiments/feature-discovery/rules.md) | 「デフレーテッド SR とパージ CV を通していない良い数字は報告しない」 |
| [rules.md 13-7](../../specs/experiments/feature-discovery/rules.md) | 入力（SR・n_obs・n_trials・歪度・尖度）をどこから取るか |
| [dashboard.md §10-3](../../specs/dashboard.md) | ✅ の条件は **DSR > 0.95** |
| [ledger.md](../../specs/experiments/feature-discovery/ledger.md) | `n_trials` は台帳の 139 |
| 各実行の `runs/*/checks.json` | `dsr: {SR, SR0, DSR, n_trials, n_obs, 歪度, 尖度}` |

⚠ **「この数字が何を意味するか」だけが無い。** 読み手は次で必ずつまずく。

1. DSR 0.28 と 0.62 は何が違うのか（確率なのか、比なのか）
2. ⚠ **SR が下がったのに DSR が上がる**ことがある（1995 表の橋渡し対で実際に起きた）
3. ⚠ **DSR は「基準線を超えたか」の検定ではない**（`checks.json` の注記にはあるが、理由が無い）
4. 試行を数え落とすとなぜ甘くなるのか、どれくらい甘くなるのか

先例は [units.md](../../specs/experiments/feature-discovery/units.md)（単位の図解）。同じ形 ＝
**規約の正本には手を入れず、読み方だけを図と実測で説明するページ**を作る。

## 2. 対応方針

`docs/specs/experiments/feature-discovery/dsr.md` を新設する。

| # | 決めごと | ⚠ 理由 |
| ---: | --- | --- |
| 1 | ⚠ **解説であって規約ではない**（units.md §0 と同じ宣言を置く） | 定義を 2 か所に置くと必ず食い違う。正本は rules.md と `ail/validation/stats.py` |
| 2 | ⚠ **数字はすべて既存の `runs/*/checks.json` の実測から引く** | 新しい試行を 1 つも増やさない（増やすと n_trials が動く） |
| 3 | 感度・逆算は**プロジェクトの実装 `stats.deflated_sharpe` をそのまま呼んで**出す | 別実装で計算すると解説と実装が食い違う |
| 4 | ⚠ **2 つの経路（毎日往復 / 閾値売買）で入力の定義が違う**ことを表で示す | 同じ `DSR` の鍵で中身が違う。比べると間違える |
| 5 | 図は Mermaid で 4〜5 枚（1 図 1 主張・12 ノード以内） | CLAUDE.md のドキュメント規約 |

## 3. 影響範囲

| ファイル | 変更 |
| --- | --- |
| `docs/specs/experiments/feature-discovery/dsr.md` | **新規** |
| `docs/specs/experiments/feature-discovery/rules.md` | 付録に 1 行（units.md と並べて dsr.md を指す）だけ。⚠ **規約は 1 文字も変えない** |
| `TODO.md` / `DONE.md` | 子タスクの移動 |

⚠ **コードは変更しない。** 実装の書き直し（例: 毎日往復の経路が歪度・尖度の既定値 0/3 を使っていること）に
気づいても、⚠ **本タスクでは直さず、事実として書くだけにする**（旧行は再計算しない ＝ rules.md 14 章冒頭）。

## 4. テスト方針

コード変更が無いのでテストは無い。代わりに次を確認する。

1. ⚠ **引用した DSR がすべて `checks.json` の値と一致すること**（実装を呼んで再現し、記録と突き合わせる）
2. 感度表・逆算が `stats.deflated_sharpe` の出力そのものであること
3. Mermaid が 12 ノード以内・図の直前に主張が 1 行あること
4. リンク切れが無いこと

## 5. Step

- Step 1: 実装（`stats.py` / `checks.py`）と `checks.json` 42 実行から材料を集める
- Step 2: `dsr.md` を書く（位置づけ → 式 → 入力 → 読み方 → 効き方の実測 → 答えないもの → 誤読集）
- Step 3: rules.md 付録に 1 行足す ／ TODO → DONE ／ プランを archive へ
