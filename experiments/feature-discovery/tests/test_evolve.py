"""⚠ **進化的探索で唯一新しいのは「数えない範囲」なので、そこを機械で確かめる。**

⚠ **`n_trials` に champion × θ の 3 試行しか数えない根拠は「選抜が訓練分割の内側だけを見た」こと**
（rules.md 14-5 の門と同型。プラン `plans/archive/evolutionary-search-runner.md` §2）。
⚠ **この前提が静かに壊れると、3 試行と申告しながら実際は 2,000 試行を探索したことになる。**
だから ⚠ **検証分割を替えても答えが 1 文字も変わらないこと**を固定する。

⚠ **成績のテストは書かない**（結果は落ちてよい。14-10 規約 4）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail.search import evolve

N, P = 1_000, 6                      # ⚠ holdout が切れる大きさ（MIN_TRAIN 500）


def _data(seed: int = 0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(N, P)), columns=[f"c{i}" for i in range(P)])
    y = X["c0"] * 2.0 - X["c1"] + rng.normal(scale=0.5, size=N)    # ⚠ 見つかる信号を入れておく
    return X, y.to_numpy()


def _ctx(**kw):
    return {"seed": 0, "generations": 3, "pop": 20, "k": 4, **kw}


def test_検証分割を替えても選ばれる式が変わらない():
    """⚠ **これが「訓練の内側だけで選抜した」ことの機械的な証明である。**

    ⚠ **Xte が探索に混ざっていれば、Xte を替えたときに式が変わる。**
    """
    X, y = _data()
    te1 = X.iloc[:50].copy()
    te2 = X.iloc[:50] * 7.5 + 3.0                    # ⚠ まったく別の値
    _, _, d1 = evolve.tf_symbolic(X, te1, _ctx(ytr=y))
    _, _, d2 = evolve.tf_symbolic(X, te2, _ctx(ytr=y))
    assert d1["式"] == d2["式"]
    assert d1["適合度"] == d2["適合度"]


def test_ytr_が無ければ走り出す前に落ちる():
    X, _ = _data()
    with pytest.raises(SystemExit):
        evolve.tf_symbolic(X, X.iloc[:10], _ctx())


def test_同じ種なら同じ式(   ):
    X, y = _data()
    a = evolve.tf_symbolic(X, X.iloc[:10], _ctx(ytr=y))[2]["式"]
    b = evolve.tf_symbolic(X, X.iloc[:10], _ctx(ytr=y))[2]["式"]
    assert a == b


def test_出す列は_k_本以下で_NaN_も_inf_も無い():
    X, y = _data()
    tr, te, doc = evolve.tf_symbolic(X, X.iloc[:50], _ctx(ytr=y))
    assert 0 < tr.shape[1] <= 4 and list(tr.columns) == list(te.columns)
    assert np.isfinite(tr.to_numpy()).all() and np.isfinite(te.to_numpy()).all()
    assert len(doc["式"]) == tr.shape[1]
    assert tr.index.equals(X.index) and te.index.equals(X.iloc[:50].index)


def test_champion_は相関で間引かれる():
    """⚠ **間引かないと同じ情報の式が k 本並ぶ**（GA は良い式のまわりに集まる）。"""
    X, y = _data()
    tr, _, _ = evolve.tf_symbolic(X, X.iloc[:10], _ctx(ytr=y, k=8))
    c = np.corrcoef(tr.to_numpy(), rowvar=False)
    off = c[~np.eye(len(c), dtype=bool)]
    assert np.nanmax(np.abs(off)) <= 0.9 + 1e-9


def test_打ち切りは_3_つとも効く():
    X, y = _data()
    g = evolve.tf_symbolic(X, X.iloc[:10], _ctx(ytr=y, generations=2))[2]
    assert g["世代"] <= 2 and g["止めた理由"] == "世代の上限"
    t = evolve.tf_symbolic(X, X.iloc[:10], _ctx(ytr=y, seconds=0.0))[2]
    assert t["止めた理由"] == "時間の上限"
    p = evolve.tf_symbolic(X, X.iloc[:10], _ctx(ytr=y, generations=50, patience=1))[2]
    assert p["止めた理由"] in ("改善が止まった", "世代の上限")


def test_保護つき除算は_0_割りでも壊れない():
    zero = np.zeros((5, 2))
    v = evolve._eval(("div", ("x", 0), ("x", 1)), zero)
    assert np.isfinite(v).all()


def test_定数の式は適合度_0():
    y_rank = evolve._ranks(np.arange(10, dtype=float))
    assert evolve.fitness(np.ones(10), y_rank) == 0.0
    assert evolve.fitness(np.full(10, np.nan), y_rank) == 0.0


def test_深さと大きさの上限を超えない():
    rng = np.random.default_rng(0)
    for _ in range(50):
        t = evolve._random_tree(rng, P, 6, True)
        assert evolve._depth(t) <= 6
