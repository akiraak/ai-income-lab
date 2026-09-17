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


def apply(exp: dict, Xtr: pd.DataFrame, Xte: pd.DataFrame, ctx: dict):
    """(Xtr, Xte, 係数) を返す。⚠ **変換が無ければ入力をそのまま返す**（同じオブジェクト）。"""
    name = exp.get("transform")
    if not name:
        return Xtr, Xte, None
    return registry.resolve("transform", name)(Xtr, Xte, ctx)


def label(exp: dict, method: str) -> str:
    """手法名に変換を混ぜる。⚠ **変換名を先頭に置く。**

    ⚠ **台帳は手法名の先頭から `F5-1` を ID として読む**（`ail/catalog.py` の `_ID`）。
    ⚠ **混ぜないと「全部使う（基準）」の行に化けて、その試行が数えられない**（rules.md 11 章 規約 4）。
    """
    name = exp.get("transform")
    return f"{name} ＋ {method}" if name else method
