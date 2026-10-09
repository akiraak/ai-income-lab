"""GA の適合度を替える（rules.md 23 章。2026-10-08）。

⚠ **既定 `pooled` は探索の出力（式と適合度）が 1 ビットも変わらない**（指紋は 2026-10-08 に、替える前のコード
〔commit 666ca38 の `ail/search/evolve.py`〕で同じ合成データから取った）。
⚠ 曜日のような「日ごとに全銘柄が同じ値」の列は、断面の 2 案で適合度 0。⚠ 3 案とも検証分割を替えても式が変わらない。
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from ail import catalog, names
from ail.search import evolve
from ail.validation import prep

N, P = 1_200, 6
PRE_CHANGE_SHA = "661646ab021e3082c7db1499030d2dc093bafb7c7ee37813a5fce9a74e49fa35"
DATES = np.repeat(pd.date_range("2020-01-01", periods=N // 6, freq="D"), 6)   # 6 銘柄 × 200 日


def _data(seed: int = 3):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(N, P)), columns=[f"c{i}" for i in range(P)])
    y = (X["c0"] * 2.0 - X["c1"] + rng.normal(scale=0.5, size=N)).to_numpy()
    return X, y


def _ctx(**kw):
    return {"seed": 0, "generations": 5, "pop": 30, "k": 4, **kw}


def _sha(doc):
    return hashlib.sha256(json.dumps([doc["式"], doc["適合度"]], ensure_ascii=False).encode()).hexdigest()


def test_default_search_is_unchanged():
    X, y = _data()
    for extra in ({}, {"ts_tr": DATES}, {"fitness": "pooled", "ts_tr": DATES}):
        doc = evolve.tf_symbolic(X, X.iloc[:10], _ctx(ytr=y, **extra))[2]
        assert _sha(doc) == PRE_CHANGE_SHA
        assert "適合度の測り方" not in doc


def test_day_constant_column_scores_zero_in_cross_section():
    rng = np.random.default_rng(0)
    y = rng.normal(size=N)
    dow = pd.Series(DATES).dt.dayofweek.to_numpy().astype(float) + y.mean() * 0   # 日ごとに全銘柄が同じ値
    signal = y + rng.normal(scale=0.5, size=N)
    for kind in ("xs_mean", "xs_ir"):
        f = evolve.fitness_of(kind, y, DATES)
        assert f(dow) == 0.0
        assert f(signal) > 0.3
    # pooled は日ごとに同じ値の列にも値が付きうる（直したい性質）。y が曜日に効く例
    y2 = y + dow
    assert evolve.fitness_of("pooled", y2)(dow) > 0.3
    assert evolve.fitness_of("xs_mean", y2, DATES)(dow) == 0.0


def test_xs_rho_matches_a_plain_per_day_spearman():
    rng = np.random.default_rng(1)
    y = rng.normal(size=N)
    v = y * 0.3 + rng.normal(size=N)
    setup = evolve._xs_setup(y, DATES)
    got = evolve.xs_rho(v, setup)
    df = pd.DataFrame({"d": DATES, "v": v, "y": y})
    want = df.groupby("d").apply(lambda g: g["v"].rank().corr(g["y"].rank()), include_groups=False).to_numpy()
    assert np.allclose(got, want)
    f = evolve.fitness_of("xs_mean", y, DATES)
    assert f(v) == pytest.approx(abs(want.mean()))
    g = evolve.fitness_of("xs_ir", y, DATES)
    assert g(v) == pytest.approx(abs(want.mean()) / want.std(ddof=1))


def test_worst4_is_the_minimum_over_four_periods():
    rng = np.random.default_rng(2)
    y = rng.normal(size=N)
    v = np.where(np.arange(N) < N // 2, y, rng.normal(size=N))     # 前半だけ効く式
    f = evolve.fitness_of("worst4", y, DATES)
    assert f(v) < 0.2 < evolve.fitness_of("pooled", y)(v)
    assert evolve.fitness_of("worst4", y, DATES)(y) == pytest.approx(1.0)


@pytest.mark.parametrize("kind", ["xs_mean", "xs_ir", "worst4"])
def test_validation_split_does_not_change_the_search(kind):
    X, y = _data()
    d1 = evolve.tf_symbolic(X, X.iloc[:50].copy(), _ctx(ytr=y, ts_tr=DATES, fitness=kind))[2]
    d2 = evolve.tf_symbolic(X, X.iloc[:50] * 7.5 + 3.0, _ctx(ytr=y, ts_tr=DATES, fitness=kind))[2]
    assert d1["式"] == d2["式"] and d1["適合度"] == d2["適合度"]
    assert d1["適合度の測り方"] == kind


def test_bad_or_missing_inputs_stop():
    X, y = _data()
    with pytest.raises(SystemExit):
        evolve.tf_symbolic(X, X.iloc[:10], _ctx(ytr=y, fitness="xs_mean"))        # 日付が無い
    with pytest.raises(SystemExit):
        evolve.fitness_of("ic", y, DATES)
    with pytest.raises(SystemExit):                                                 # 1 日 1 行 ＝ (B) 銘柄別
        evolve.fitness_of("xs_mean", y, pd.date_range("2000-01-01", periods=N))


def test_labels_keys_and_names():
    exp = {"transform": "F4-1 記号回帰（遺伝的プログラミング）"}
    assert prep.label(exp, "全部使う（基準）") == "F4-1 記号回帰（遺伝的プログラミング） ＋ 全部使う（基準）"
    lab = prep.label({**exp, "fitness": "xs_mean"}, "全部使う（基準）")
    assert lab.endswith("〔適合度・断面の平均〕")
    assert catalog.canonical(lab) == ("F4-1", "F4-1〔適合度・断面の平均〕")       # ⚠ 鍵に残す（23-2）
    assert catalog.canonical(prep.label(exp, "全部使う（基準）")) == ("F4-1", "F4-1")
    with pytest.raises(SystemExit):
        prep.label({**exp, "fitness": "nope"}, "全部使う（基準）")
    key = dict(鍵="F4-1〔適合度・4期間の最小〕", モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own",
               層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="60")
    assert names.trial_name(key) == "own.f4-1-fit-worst4.ridge.shared@60"
    assert set(evolve.FITNESS_LABELS.values()) == {f"〔適合度・{k}〕" for k in names.table()["fitness"]}
