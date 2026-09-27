# 売買に使うトレーダーやモデルの設定などの説明を分かりやすく書く

作成日: 2026-09-26。派生元: [TODO](../../../TODO.md) の同名のタスク（利用者の指示 2026-09-26）。

## 0. 目的・背景

実売買で使うトレーダー（T1〜T3）と予測モデル、その設定（予算・銘柄集合・売買基準値・合成規則・`sizing` など）の説明が、仕様書・記録・設定ファイル・vibeboard のタブ（システム説明 ／ 予測モデル ／ トレーダー）に散らばっている。読む人が「誰が・何を見て・どう売買するのか」を分かるように、説明を分かりやすく書き直す。

**置き場は vibeboard**（2026-09-26 利用者決定「ドキュメントは vibeboard に書きます」）。どのタブ・ページに置くか（既存の「システム説明」「予測モデル」「トレーダー」のタブに足すか、新しいページにするか）は Step 2 で決める。

⚠ 文書と説明の文を書くだけ。⚠ **設定の値（モデル・θ・銘柄・予算・`sizing`）は変えない**（変えると新しい検証になる ＝ CLAUDE.md）。

## 1. 進め方

```mermaid
flowchart LR
  A[Step 1<br>既存の説明を棚卸し] --> B[Step 2<br>アウトラインを決める] --> C[Step 3 以降<br>書く（アウトラインの後に決める）]
```

### Step 1: 既存の説明を棚卸し、必要なものをまとめる

- 説明がどこに何が書いてあるかを一覧にする（場所 ／ 読み手 ／ 扱う内容 ／ 古い・重複・食い違い）
  - 設定: `experiments/live-trading/config/traders/T1〜T3.toml`・`candidates/`
  - 仕様・記録: `docs/specs/experiments/live-trading.md`（§0-1 ほか）・`docs/specs/dashboard.md` §16〜§18・`docs/plans/live-trading-trader-attributes.md`・`live-trading-three-models.md` §2
  - 画面の文の正本: `dashboard/models.toml`・`dashboard/traders.toml`・`dashboard/system.toml`・`dashboard/glossary.toml`
  - 研究側: `docs/specs/experiments/feature-discovery/rules.md` 10-2（予測モデル名）
- そこから「説明に必要なもの」（読み手が知るべき項目）を抜き出してまとめる

### Step 2: どのように説明するかのアウトラインを決める

- 読み手・vibeboard の中の置き場（どのタブ・ページか）・節の並び・図と表の割り当てを決める
- ⚠ 言葉は CLAUDE.md の「言葉」の表に従う。タブの決まり（やさしい言葉・比べる文を書かない・数字は設定から写すだけ）を守る
- アウトラインは利用者に見せて決めてから書く

## 2. 影響範囲

文書と説明の文（TOML の文面）だけ。執行器・予測の経路には触れない。

## 3. テスト方針

TOML の文面を直したときは `./run-tests.sh --fast`（`tests/test_help.py`・タブの `FORBIDDEN` ／ `COMPARING` の検査を含む）。
