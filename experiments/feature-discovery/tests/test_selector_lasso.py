"""F3-1（壊れているほう）と F3-1b（直したほう）の検査（プラン `plans/lasso-fix-cs-threshold.md`）。

⚠ **主題は「黙ったフォールバックを作らない」こと。** F3-1 は α が強いと係数が全部 0 になり、
⚠ **列の並び順どおり先頭の 1 本を返していた**（2026-09-13 に判明。台帳で本数 1.0 の 11 行）。

⚠ **F3-1 の検査は「壊れたままであること」を固定する**（利用者の裁定 (d)。直すと既存 20 行が
⚠ **全部「別の手法」になる** — rules.md 14-10 規約 5）。⚠ **うっかり直したらここで落ちる。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import registry
import ail.bootstrap  # noqa: F401

OLD = "F3-1 Lasso"
NEW = "F3-1b Lasso（本数を固定）"
CTX = {"seed": 0}


def _panel(n=1200, p=20, signal=0.0, seed=0):
    """⚠ **`cli/run.py` が標準化したあとの表を模す**（rules.md 3 章 B）。`(X, y, 仕込んだ列)` を返す。

    `signal=0.0` なら y は列と無関係 ＝ ⚠ **罰則が勝って係数が全部 0 になる側**。
    ⚠ **種は 0 に固定**。⚠ **雑音の引き方しだいで `LassoCV` が 0 本にならない**ので
    （種 5 では 1 本残って F3-1 の穴が再現しなかった）、⚠ **再現する種を選んである。**

    仕込む列は ⚠ **位置で決める**（`p` を変えても存在する列を指すように）。
    """
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, p)),
                     columns=[f"own_{i:02d}" for i in range(p)])
    y = rng.normal(scale=0.01, size=n)
    planted = [X.columns[p // 3], X.columns[2 * p // 3]]
    if signal:
        y = y + signal * X[planted[0]].values + 0.5 * signal * X[planted[1]].values
    return X, y, planted


# --- F3-1（壊れているほう）を固定する -----------------------------------

def test_f3_1_still_falls_back_to_the_first_column():
    """⚠ **これは「直っていないこと」の検査である。** 裁定 (d) の約束を破ったら落ちる。"""
    X, y, _ = _panel(signal=0.0)
    got = registry.resolve("selector", OLD)(X, y, 8, CTX)
    assert got == ["own_00"], "⚠ F3-1 は 0 本→先頭列のまま残す（既存 20 行の中身）"


def test_f3_1_ignores_k():
    """⚠ **`k` を受け取りながら使っていない**のも当時のまま。"""
    X, y, _ = _panel(signal=0.0)
    fn = registry.resolve("selector", OLD)
    assert fn(X, y, 2, CTX) == fn(X, y, 16, CTX)


# --- F3-1b（直したほう）-------------------------------------------------

@pytest.mark.parametrize("k", [1, 5, 12])
def test_f3_1b_returns_exactly_k_even_with_no_signal(k):
    """⚠ **本題**: 同じ入力（信号なし）で F3-1 が 1 本に潰れるのに、F3-1b は k 本返す。"""
    X, y, _ = _panel(signal=0.0)
    got = registry.resolve("selector", NEW)(X, y, k, CTX)
    assert len(got) == k
    assert set(got) <= set(X.columns)
    assert len(set(got)) == k, "⚠ 同じ列を 2 度返さない"


def test_f3_1b_beats_f3_1_on_the_same_input():
    """⚠ **②の実行で 2 つが同じ fold に並ぶ**ので、差が出ることをここで押さえる。"""
    X, y, _ = _panel(signal=0.0)
    old = registry.resolve("selector", OLD)(X, y, 8, CTX)
    new = registry.resolve("selector", NEW)(X, y, 8, CTX)
    assert len(old) == 1 and len(new) == 8


def test_f3_1b_finds_the_planted_columns_first():
    """⚠ **α の強い側から辿る**ので、効いている列が先に入る（順番に意味がある）。"""
    X, y, planted = _panel(signal=0.05)
    got = registry.resolve("selector", NEW)(X, y, 2, CTX)
    assert set(got) == set(planted)


def test_f3_1b_is_deterministic():
    """⚠ **`lasso_path` は乱数を引かない。** `ctx["seed"]` を変えても同じ列。"""
    X, y, _ = _panel(signal=0.02)
    fn = registry.resolve("selector", NEW)
    assert fn(X, y, 6, {"seed": 0}) == fn(X, y, 6, {"seed": 99})


def test_f3_1b_never_pads_when_it_cannot_reach_k():
    """⚠ **届かないときは黙らず、届いた本数だけ返す**（台帳の「本数」が k を下回って見える）。

    ⚠ **列より大きい k を頼む**のが、経路のどこでも k に届かない状況そのものである。
    """
    X, y, _ = _panel(p=6, signal=0.05)
    got = registry.resolve("selector", NEW)(X, y, 50, CTX)
    assert 0 < len(got) <= 6
    assert set(got) <= set(X.columns)


def test_f3_1b_does_not_look_at_row_order():
    """⚠ **選別は訓練分割の内側で呼ばれる。** 行を並べ替えても同じ列（時系列の順に依存しない）。"""
    X, y, _ = _panel(signal=0.03)
    fn = registry.resolve("selector", NEW)
    idx = np.random.default_rng(0).permutation(len(X))
    shuffled = X.iloc[idx].reset_index(drop=True)
    assert set(fn(X, y, 5, CTX)) == set(fn(shuffled, y[idx], 5, CTX))
