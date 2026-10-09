"""標準化のあとに挟む「標本から学ぶ変換」の 1 段（rules.md 3 章 B）。

⚠ **config に `transform` が無ければ 1 行も通らない。** 既存の実行はここを素通りする
（プラン `plans/selectors-small-four.md` §1-1 の (c)）。
⚠ **この前提が崩れると、配線を足したことが静かに全実行に影響する。** テストで固定してある
（`tests/test_selectors_small4.py` の「変換が無ければ表が同一オブジェクトのまま」）。

⚠ **fit は訓練分割だけ・検証分割には transform だけ。** 規律を守る責任は変換の実体が負う
（`ail/data/transforms/fitted.py` の `FittedTransform` が fit と transform を分けている）。
"""

from __future__ import annotations

import pandas as pd

from ail import registry


def apply(exp: dict, Xtr: pd.DataFrame, Xte: pd.DataFrame, ctx: dict, ytr=None, ts_tr=None):
    """(Xtr, Xte, 係数) を返す。⚠ **変換が無ければ入力をそのまま返す**（同じオブジェクト）。

    ⚠ **`ytr` は教師つきの変換だけが読む**（F4-1 記号回帰の適合度。2026-09-16 に足した）。
    ⚠ **渡すのは訓練分割の y だけ**で、⚠ **検証分割の y はこの口に来ない**（3 章 B）。
    ⚠ **ctx は複製して渡す** — 呼び元の ctx に混ぜると、選別やモデルにも見えてしまう。
    """
    name = exp.get("transform")
    if not name:
        return Xtr, Xte, None
    if ytr is not None:
        ctx = {**ctx, "ytr": ytr}
    # ⚠ **GA の適合度の測り方**（rules.md 23 章。2026-10-08）: `fitness` は config の平の key（無ければ渡さない ＝ pooled）。
    # `ts_tr` は訓練分割の行の日付（断面・時期で測る適合度だけが読む。⚠ 検証分割の日付は来ない）
    if ts_tr is not None:
        ctx = {**ctx, "ts_tr": ts_tr}
    if exp.get("fitness", "pooled") != "pooled":
        ctx = {**ctx, "fitness": exp["fitness"]}
    return registry.resolve("transform", name)(Xtr, Xte, ctx)


def label(exp: dict, method: str) -> str:
    """手法名に変換を混ぜる。⚠ **変換名を先頭に置く。**

    ⚠ **台帳は手法名の先頭から `F5-1` を ID として読む**（`ail/catalog.py` の `_ID`）。
    ⚠ **混ぜないと「全部使う（基準）」の行に化けて、その試行が数えられない**（rules.md 11 章 規約 4）。
    """
    name = exp.get("transform")
    if not name:
        return method
    # ⚠ **GA の適合度を替えた行は〔適合度・…〕を後ろに付ける**（rules.md 23-2。既定 pooled は付けない ＝ 既存の名前のまま）
    fit = exp.get("fitness", "pooled")
    if fit == "pooled":
        return f"{name} ＋ {method}"
    from ail.search.evolve import FITNESS_LABELS
    if fit not in FITNESS_LABELS:
        raise SystemExit(f"⚠ fitness は pooled ／ {' ／ '.join(FITNESS_LABELS)}: {fit!r}（rules.md 23-1）")
    return f"{name} ＋ {method}{FITNESS_LABELS[fit]}"
