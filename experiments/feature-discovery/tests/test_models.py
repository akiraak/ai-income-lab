"""モデルの軸（plans/archive/gpu-models.md）の検査。

⚠ **見るのは 3 つ**: (1) 同じ種で同じ予測が出る（決定性。再現の前提）、
(2) 出力の形と NaN なし、(3) GAN 増強が構造の規約を守る（合成は訓練にだけ・実データの前に足す）。
⚠ **テストは CPU で回す**（GPU の有無で数字が変わらないように）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import catalog, registry
from ail.models.holdout import tail_holdout
import ail.bootstrap  # noqa: F401


@pytest.fixture(autouse=True)
def _cpu(monkeypatch):
    monkeypatch.setenv("AIL_TORCH_DEVICE", "cpu")


def _data(n=1500, d=8, seed=7):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, d)), columns=[f"own_f{i}" for i in range(d)])
    # 非線形の信号（x0 の符号 × x1）＋ 雑音。⚠ 線形モデルには見えない形
    y = np.sign(X["own_f0"].values) * X["own_f1"].values * 0.01 \
        + rng.normal(scale=0.005, size=n)
    cut = n - 300
    return X.iloc[:cut], y[:cut], X.iloc[cut:], y[cut:]


CTX = {"seed": 0, "mlp_epochs": 15, "gan_epochs": 2, "lgbm_estimators": 50}


@pytest.mark.parametrize("name", ["LightGBM", "MLP", "Ridge+GAN増強"])
def test_決定性と形(name):
    Xtr, ytr, Xte, _ = _data()
    fn = registry.resolve("model", name)
    p1 = np.asarray(fn(Xtr, ytr, Xte, dict(CTX)))
    p2 = np.asarray(fn(Xtr, ytr, Xte, dict(CTX)))
    assert p1.shape == (len(Xte),)
    assert np.isfinite(p1).all()
    # ⚠ **同じ種・同じ入力なら 1 ビットも違わない**（rules.md 10 章。再現の前提）
    assert np.array_equal(p1, p2), f"{name} が同じ種で違う予測を出した"


def test_非線形モデルは非線形の信号を拾える():
    """⚠ **配線の検査。** 非線形の信号を LightGBM が拾えなければ、実験で「効かない」が出ても
    「モデルが学べていないだけ」と区別できない。"""
    Xtr, ytr, Xte, yte = _data(n=4000)
    p = registry.resolve("model", "LightGBM")(Xtr, ytr, Xte, dict(CTX, lgbm_estimators=200))
    ic = np.corrcoef(p, yte)[0, 1]
    assert ic > 0.3, f"LightGBM が既知の非線形信号を拾えない（IC={ic:.3f}）"
    # ⚠ 同じ信号は線形（Ridge）には原理的に見えない（対照。⚠ 有限標本の揺れがあるので差で見る）
    q = registry.resolve("model", "Ridge")(Xtr, ytr, Xte, {"seed": 0})
    assert ic > np.corrcoef(q, yte)[0, 1] + 0.15


def test_tail_holdout_の切り方():
    X = pd.DataFrame({"a": np.arange(2000.0)})
    y = np.arange(2000.0)
    (Xf, yf), holdout = tail_holdout(X, y)
    assert holdout is not None
    Xv, yv = holdout
    # ⚠ **末尾が検証・間に隙間**（訓練の最大行 < 検証の最小行 − 隙間）
    assert Xf["a"].max() < Xv["a"].min() - 1
    assert len(Xv) == 200 and yv[0] == Xv["a"].iloc[0]
    # ⚠ 小さい標本では早期打ち切りを諦める（検証を切らない）
    (Xs, ys), none = tail_holdout(X.iloc[:400], y[:400])
    assert none is None and len(Xs) == 400


def test_GAN増強は合成行を実データの前に足す(monkeypatch):
    """⚠ **後ろに足すと、基底モデルの早期打ち切りが合成行で行われてしまう**（gan.py の冒頭）。"""
    from ail.models import gan

    Xtr, ytr, Xte, _ = _data(n=800)
    seen = {}

    def fake_base(Xa, ya, Xte_, ctx):
        seen["X"], seen["y"] = Xa, ya
        return np.zeros(len(Xte_))

    monkeypatch.setitem(registry._REGISTRY["model"], "Ridge", fake_base)
    registry.resolve("model", "Ridge+GAN増強")(Xtr, ytr, Xte, dict(CTX))
    assert len(seen["X"]) == 2 * len(Xtr) and len(seen["y"]) == 2 * len(ytr)
    # ⚠ **実データは後ろ半分にそのまま残る**（合成行が前）
    tail = seen["X"].iloc[len(Xtr):].reset_index(drop=True)
    pd.testing.assert_frame_equal(tail, Xtr.reset_index(drop=True))
    assert np.array_equal(seen["y"][len(ytr):], ytr)
    assert np.isfinite(seen["y"]).all()


def test_台帳_モデルが鍵に入っている():
    assert "モデル" in catalog.KEY
    r1 = {"手法名": "F3-1 Lasso", "モデル": "Ridge", "粒度": "日足", "地平": "1 本（1 日）",
          "特徴量の層": "own", "層": "adjusted", "純利bp": -1.0, "leak": False,
          "実行": "a", "出所": "runs", "fold": None}
    r2 = dict(r1, モデル="LightGBM", 実行="b")
    for r in (r1, r2):
        r["ID"], r["鍵"] = catalog.canonical(r["手法名"])
    merged = catalog._collapse([r1, r2])
    # ⚠ **モデル違いは別の試行**（1 行に潰れて「⚠ 2 実行・幅」の偽警告にならない）
    assert len(merged) == 2
    same = catalog._collapse([r1, dict(r1, 実行="c")])
    assert len(same) == 1 and same[0]["実行数"] == 2


def test_台帳_n_trials_の数え方():
    def row(name, model):
        r = {"手法名": name, "モデル": model}
        r["ID"], r["鍵"] = catalog.canonical(name)
        return r

    assert catalog.is_trial(row("F3-1 Lasso", "Ridge"))
    assert catalog.is_trial(row("F3-1 Lasso", "LightGBM"))
    # ⚠ **モデルが処置なら「全部使う」も 1 試行**（plans/archive/gpu-models.md §3-4）
    assert catalog.is_trial(row("全部使う（基準）", "LightGBM"))
    assert not catalog.is_trial(row("全部使う（基準）", "Ridge"))
    assert not catalog.is_trial(row("乱択（基準）", "LightGBM"))
    assert not catalog.is_trial(row("基準 常に上（ドリフト）", "—"))


def test_台帳_モデルが処置の行は基準線として判定しない():
    bases = catalog.baseline_names()
    r = {"手法名": "全部使う（基準）", "モデル": "LightGBM", "粒度": "日足",
         "層": "adjusted", "純利bp": -1.0, "粗利bp": 1.0, "fold": "1/5 ＋−−−−"}
    verdict, _why = catalog.judge(r, bases)
    assert verdict == "落とす"          # X2: 粗利 > 0・純利 ≤ 0
    verdict, _why = catalog.judge(dict(r, モデル="Ridge"), bases)
    assert verdict == "基準"
    verdict, _why = catalog.judge(dict(r, 手法名="乱択（基準）"), bases)
    assert verdict == "基準"            # ⚠ 乱択はモデルが何でも基準線のまま
