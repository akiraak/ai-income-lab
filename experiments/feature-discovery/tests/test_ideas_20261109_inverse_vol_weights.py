"""レジストリ `20261109-inverse-vol-weights`: 同じ合図のまま、持つ量を直近の値動きの逆数で配る（`[trading] weights = "inverse_vol"`）。

⚠ 見るもの: 無い config は "equal" ＝ 既存の経路 ／ 値動きは足 t−1 までの終値だけ ／ 倍率は保有銘柄の中で平均 1（総量は等加重と同じ）／
iv が全部同じなら等加重の `portfolio_daily` と同じ値 ／ 費用の掛け方 ／ 名前と識別項目。
"""

from __future__ import annotations

import os
import tomllib

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import catalog, names
from ail.validation import simulate as sim
from cli import run as runmod

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def _s(vals, dates):
    return pd.Series(np.asarray(vals, dtype=float), index=dates)


# ---------- ① オプションの無い config は既存の経路

def test_weights_default_is_equal_and_unknown_is_refused():
    assert runmod.weights_of({}) == "equal"
    assert runmod.weights_of({"weights": "equal"}) == "equal"
    assert runmod.weights_of({"weights": "inverse_vol"}) == "inverse_vol"
    with pytest.raises(SystemExit):
        runmod.weights_of({"weights": "vol"})


def test_style_is_unchanged_so_plain_rows_keep_their_key():
    """`weights` は検証方式を変えない ＝ 同じ実行の素の行は既存の行と同じ識別項目（増えるのは〔量を逆ボラで〕の 3 行だけ）。"""
    with open(os.path.join(ROOT, "config", "experiment", "dev_inverse_vol_weights.toml"), "rb") as f:
        cfg = tomllib.load(f)
    with open(os.path.join(ROOT, "config", "experiment", "trade_own_ridge_a.toml"), "rb") as f:
        base = tomllib.load(f)
    assert catalog.threshold_style(cfg) == catalog.threshold_style(base) == "閾値売買"
    assert cfg["trading"]["weights"] == "inverse_vol"
    assert [float(x) for x in cfg["trading"]["thresholds"]] == [50.0, 55.0, 60.0]


def test_equal_inverse_vol_reproduces_portfolio_daily():
    """iv が全銘柄で同じなら、倍率は保有銘柄で全部 1 ＝ 等加重の `portfolio_daily` と同じ値（費用も含めて）。"""
    rng = np.random.default_rng(0)
    d = pd.date_range("2020-01-01", periods=30, freq="B")
    nets, grosses, poss, ivs = {}, {}, {}, {}
    for s in "ABC":
        b = rng.uniform(30, 80, len(d))
        y = rng.normal(0, 0.01, len(d))
        r = sim.simulate(b, y, 55.0, 5.0)
        nets[s], grosses[s] = _s(r["net_bp"], d), _s(r["gross_bp"], d)
        poss[s], ivs[s] = _s(r["pos"], d), _s(np.full(len(d), 50.0), d)
    net, gross = sim.portfolio_inverse_vol(nets, grosses, poss, ivs)
    assert np.allclose(net.values, sim.portfolio_daily(nets).values)
    assert np.allclose(gross.values, sim.portfolio_daily(grosses).values)


# ---------- ② 新しい処置の中身

def test_inverse_vol_uses_only_closes_up_to_previous_day():
    n = 90
    rng = np.random.default_rng(1)
    ts = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    c = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    p = pd.DataFrame({"symbol": "A", "ts": ts, "close": c}).iloc[::-1]   # 並びが逆でも行で返す
    v = runmod.inverse_vol(p).sort_index()
    r = pd.Series(np.log(c)).diff()
    want = r.rolling(60, min_periods=20).std().shift(1)
    assert np.allclose(v.values, want.values, equal_nan=True)
    # 足 t の終値を大きく動かしても、足 t の値は変わらない（足 t+1 から変わる）
    c2 = c.copy(); c2[70] *= 1.5
    v2 = runmod.inverse_vol(pd.DataFrame({"symbol": "A", "ts": ts, "close": c2})).values
    assert np.allclose(v2[:71], v.values[:71], equal_nan=True) and not np.isclose(v2[71], v.values[71])


def test_multiplier_is_inverse_vol_normalized_within_held_and_costs():
    d = pd.date_range("2020-01-01", periods=3, freq="B")
    # A と B を日 0〜1 に持ち、日 2 に手仕舞う。C は持たない（iv は大きいが配らない）
    pos = {"A": [1, 1, 0], "B": [1, 1, 0], "C": [0, 0, 0]}
    y = 0.01
    gross = {s: [p * y * 1e4 for p in v] for s, v in pos.items()}
    cost = {"A": [2.5, 0, 2.5], "B": [2.5, 0, 2.5], "C": [0, 0, 0]}
    net = {s: [g - c for g, c in zip(gross[s], cost[s])] for s in pos}
    iv = {"A": [1.0, 1.0, 1.0], "B": [3.0, 3.0, 3.0], "C": [10.0, 10.0, 10.0]}
    mk = lambda dct: {s: _s(v, d) for s, v in dct.items()}           # noqa: E731
    pn, pg = sim.portfolio_inverse_vol(mk(net), mk(gross), mk(pos), mk(iv))
    mA, mB = 2 * 1 / 4, 2 * 3 / 4                                    # 保有 2 本 × iv ／ Σiv（平均 1）
    assert np.isclose(pg.iloc[0], (mA * 100 + mB * 100) / 3)        # 総量は等加重と同じ（2 本 ／ 3 本）
    assert np.isclose(pn.iloc[0], (mA * (100 - 2.5) + mB * (100 - 2.5)) / 3)
    assert np.isclose(pn.iloc[2], -(mA * 2.5 + mB * 2.5) / 3)        # 手仕舞った日は前日の倍率
    assert np.isclose(pg.iloc[2], 0.0)


def test_missing_vol_of_held_symbol_is_filled_with_mean_of_held():
    d = pd.date_range("2020-01-01", periods=1, freq="B")
    mk = lambda v: _s([v], d)                                         # noqa: E731
    pos = {"A": mk(1), "B": mk(1)}
    g = {"A": mk(100.0), "B": mk(0.0)}
    pn, pg = sim.portfolio_inverse_vol(g, g, pos, {"A": mk(np.nan), "B": mk(np.inf)})
    assert np.isclose(pg.iloc[0], 50.0)                               # 両方欠け ＝ 等加重
    pn, pg = sim.portfolio_inverse_vol(g, g, pos, {"A": mk(np.nan), "B": mk(2.0)})
    assert np.isclose(pg.iloc[0], 50.0)                               # 欠けた A は B の iv で埋まる ＝ 等加重


# ---------- ③ 名前と識別項目

def test_name_and_key_split():
    assert runmod.ivw_name("全部使う（基準）") == "全部使う（基準）〔量を逆ボラで〕"
    assert names.method_slug("全部使う（基準）〔量を逆ボラで〕") == "all-ivw"
    base = {"鍵": "全部使う（基準）", "モデル": "Ridge", "粒度": "日足", "地平": "1 本（1 日）",
            "特徴量の層": "own", "層": "adjusted", "期間": "2018-01-31", "銘柄": "63", "集合": "daily",
            "検証方式": "閾値売買", "形式": "共通", "較正": "std", "閾値": "50", "純利bp": 1.0, "fold": None, "出所": "runs", "実行": "r"}
    ivw = {**base, "鍵": "全部使う（基準）〔量を逆ボラで〕"}
    plain, new = names.trial_name(base), names.trial_name(ivw)
    assert new == plain.replace(".all.", ".all-ivw.", 1) and new.endswith("@50")
    assert len(catalog._collapse([base, ivw])) == 2                  # 素の行とまとまらない
