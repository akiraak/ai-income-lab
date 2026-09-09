# 特徴量の発見手法の検証

⚠ **規約は [rules.md](../../docs/specs/experiments/feature-discovery/rules.md) が正本。**
記録は [feature-discovery.md](../../docs/specs/experiments/feature-discovery.md)、プランは [analysis-structure.md](../../docs/plans/archive/analysis-structure.md)。

⚠ **「何を試して、どうだったか」は [ledger.md](../../docs/specs/experiments/feature-discovery/ledger.md)**（台帳）。
⚠ **台帳は生成物である**（`cli/report.py --catalog`）。⚠ **手法を 1 つ足して回したら、台帳を吐き直す。**

⚠ **資金は動かさない**（2026-08-27 の方針）。データ取得は既にある tastytrade の口座の**読み取りだけ**で、発注系には触れない。

## 回し方

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# 1. 取得 → data/raw/（⚠ **この venv で通る**。requirements.txt に websockets と requests を入れてある）
./.venv/bin/python -m cli.fetch --dataset daily          # 足（tastytrade）
./.venv/bin/python -m cli.fetch --exog exog_daily        # ⚠ 外部の日次系列（為替・イールド・気象・地震）

# 2. 調整 → data/adjusted/（⚠ まず --report で継ぎ目を見てから書く）
./.venv/bin/python -m cli.adjust --period d --report
./.venv/bin/python -m cli.adjust --period d

# 3. 特徴量 → data/features/<実験>/<粒度>.parquet
./.venv/bin/python -m cli.build --experiment cross_section_h1

# 4. 実験 1 本 → runs/<時刻>_<実験名>/
./.venv/bin/python -m cli.run --experiment cross_section_h1

# 5. 実行を横に並べる
./.venv/bin/python -m cli.report
./.venv/bin/python -m cli.report --diff <実行A> <実行B>

# 6. ⚠ 台帳（試した結果の一覧）を作り直す。**手で書き換えない**
./.venv/bin/python -m cli.report --catalog \
  > ../../docs/specs/experiments/feature-discovery/ledger.md

# 検査
./.venv/bin/python -m cli.check --layer adjusted --period d
./.venv/bin/python -m pytest -q tests           # 不変条件・調整の検算・先読み
```

⚠ **先読みの対照実験は毎回通す**（rules.md 7 章）。

```bash
./.venv/bin/python -m cli.build --experiment own_only_h1 --leak
./.venv/bin/python -m cli.run   --experiment own_only_h1 --leak    # ⚠ 的中率が跳ね上がらなければ配線が壊れている
```

## 構成

| 場所 | 中身 | 手法を足すとき |
| --- | --- | --- |
| `config/universe/` | 銘柄の集合・セクターの対応 | TOML を足す |
| `config/dataset/` | 粒度・期間・調整 | TOML を足す |
| `config/experiment/` | ⚠ **1 実験 1 ファイル** | ⚠ **TOML を足すだけ。コードは書かない** |
| `ail/registry.py` | ⚠ **名前で手法を引く登録表（柔軟性の核）** | 触らない |
| `ail/bootstrap.py` | ⚠ **登録を全部走らせる。** 新しいファイルを作ったら 1 行足す | 1 行足す |
| `ail/data/sources/` | 取得元 | ファイルを 1 つ足す |
| `ail/data/adjust.py` | ⚠ **目盛りの修復**（rules.md 2 章） | 触らない |
| `ail/data/transforms/` | ⚠ **推定しない変換（`derived/` に置く）／ 推定する変換（置かない）** | 担当のファイルに足す |
| `ail/features/` | `own_` `cs_` `rel_` `ll_` ＋ ⚠ **`ex_`（価格の外）** の 5 層 | 担当のファイルに関数を足す |
| `ail/selectors/` | F1〜F5 の選別手法 | ⚠ **関数に `@register` を付けるだけ** |
| `ail/models/` | 基準線・線形・木 | 同上 |
| `ail/validation/` | 分割・指標・統計 | 同上 |
| `cli/` | 入口（薄く保つ） | 触らない |
| `ail/catalog.py` ／ `cli/ledger.py` | ⚠ **台帳**（カタログ・registry・`runs/` の突き合わせ） | 触らない |
| `config/legacy.toml` | 旧配線の結果表の**読み場所**（⚠ **数字は書かない**） | 触らない |
| `config/catalog_notes.toml` | ⚠ **未実施の手法の「次の一手」**（人が書く唯一の列） | ⚠ **実装したら行を消す** |
| `data/` | ⚠ **git 管理外**。`raw/` は書き換えない | — |
| `runs/` | ⚠ **git 管理外**。1 実行 1 ディレクトリ | — |

## 手法を 1 つ足す

```python
# ail/selectors/filter.py に追記するだけ
from ail.registry import register

@register("selector", "F1-8 条件付き相互情報量")
def cmi(X, y, k, ctx):
    ...
    return columns          # 選んだ列の名前
```

`config/experiment/*.toml` の `selectors = [...]` に名前を足せば比較に入る。⚠ **配線は触らない。**
⚠ **名前の打ち間違いは走り出す前に落ちる**（`resolve_experiment` が全部解決してから走る）。

## いまある実験

| 実験 | 特徴量 | 行 | ねらい |
| --- | ---: | ---: | --- |
| `own_only_h1` | 35（`own_` のみ） | 431,759 | ⚠ **いままでの形（プーリング）。** 各行が自分の履歴しか見ない。対象は 63 銘柄 |
| `cross_section_h1` | 134（own 35 ／ cs 26 ／ rel 13 ／ ll 60） | 96,769 | ⚠ **全銘柄を使って 1 銘柄を当てる形。** ⚠ **対象は会社株 48 本**（ETF 15 本は説明変数側）／ 行が減るのは⚠ **全 63 が揃うのが 2018-06 以降**だから |

⚠ **`ll_leaders = "all"` にすると `ll_` が 60 → 248 列になる**（`config/experiment/cross_section_h1.toml`）。
⚠ **列を増やすほど多重検定になる**ので、FDR とデフレーテッド SR を対で通すこと（rules.md 11 章）。

## 旧スクリプトからの移行（2026-09-08）

`fetch.py` `features.py` `evaluate.py` `symbols.txt` は `ail/` `cli/` `config/` に移し、**削除した**。
⚠ **移す前と同じ数字が出ることを確認済み**（旧 `out/eval_summary.csv` の 13 行と 1 バイトも違わない）。

| 旧 | 新 |
| --- | --- |
| `fetch.py` | `ail/data/sources/tastytrade.py` ＋ `cli/fetch.py` |
| `features.py` | `ail/features/own.py` ＋ `ail/features/labels.py` ＋ `cli/build.py` |
| `evaluate.py` | `ail/selectors/` ＋ `ail/models/` ＋ `ail/validation/` ＋ `cli/run.py` |
| `symbols.txt` | `config/universe/us63.toml` |
| `data/*.csv` | `data/raw/tastytrade/<粒度>/`（⚠ **以後は書き換えない**） |
| `out/*.csv` | ⚠ **ルール以前の結果。** 残してあるが、⚠ **日足のぶんは無効**（rules.md 2 章の調整の誤り） |
