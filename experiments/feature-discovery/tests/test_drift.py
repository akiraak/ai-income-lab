"""⚠ **上乗せの分解そのものを検査する**（cli/drift.py）。

⚠ **この分解を間違えると「当てた」と「基準線が落ちただけ」を取り違える。**
⚠ **符号を取り違えても数字はそれらしく出る**ので、恒等式と端の場合で縛る。
"""

from __future__ import annotations

import pandas as pd
import pytest

from cli import drift


def _per_symbol(rows):
    """per_symbol.csv の形（手法 × 閾値 × fold × 銘柄）を作る。"""
    out = []
    for name, sym, fold, net, gross, hold in rows:
        out.append({"手法": name, "閾値": 55.0, "fold": fold, "銘柄": sym,
                    "純利bp": net, "粗利bp": gross, "保有日率": hold})
    return pd.DataFrame(out)


def test_expectation_is_zero_when_the_method_never_rests():
    """⚠ **休まなければ落とす損益が無い。** 保有日率 1.0 で期待値はちょうど 0。"""
    assert drift.random_gate_expectation(1.0, -57308.0) == 0.0
    assert drift.random_gate_expectation(1.0, 16832.0) == 0.0


def test_expectation_is_positive_only_when_the_benchmark_falls():
    """⚠ **下がり続ける銘柄では、でたらめに休むだけで上乗せが正になる。**

    ⚠ **上がる銘柄では逆に負**（休むと取り損ねる）。⚠ **符号を取り違えると読みが反転する。**
    """
    assert drift.random_gate_expectation(0.5, -1000.0) == pytest.approx(500.0)
    assert drift.random_gate_expectation(0.5, 1000.0) == pytest.approx(-500.0)
    assert drift.random_gate_expectation(0.97, -57308.0) == pytest.approx(1719.24, abs=0.01)


def test_decompose_is_an_identity():
    """⚠ **上乗せ ＝ 落ちた分 ＋ 当てた分。** 恒等式なので、どんな入力でも成り立つ。"""
    ps = _per_symbol([
        ("D2", "UNG", 1, -52201.0, -52100.0, 0.97),
        (drift.DRIFT, "UNG", 1, -57308.0, -57303.0, 1.0),
        ("D2", "AAPL", 1, 900.0, 950.0, 0.80),
        (drift.DRIFT, "AAPL", 1, 800.0, 805.0, 1.0),
    ])
    j = drift.decompose(ps, "D2", 55.0)
    assert (j["上乗せ"] - (j["落ちた分"] + j["当てた分"])).abs().max() == pytest.approx(0.0)


def test_a_falling_benchmark_shows_up_as_the_fallen_part():
    """⚠ **段 1 で踏んだ形**: 壊滅する銘柄をほぼ持ちっぱなしでも上乗せが出る。

    ⚠ **その多くが「落ちた分」に振り分けられ、残りだけが「当てた分」になる。**
    """
    ps = _per_symbol([
        ("D2", "UNG", 1, -52201.0, -52100.0, 0.97),
        (drift.DRIFT, "UNG", 1, -57308.0, -57303.0, 1.0),
    ])
    r = drift.decompose(ps, "D2", 55.0).iloc[0]
    assert r["上乗せ"] == pytest.approx(5107.0)
    assert r["落ちた分"] == pytest.approx(1719.09, abs=0.1)   # ⚠ でたらめでも出る分
    assert r["当てた分"] == pytest.approx(3387.91, abs=0.1)   # ⚠ それを超えた分


def test_a_rising_benchmark_makes_resting_cost_something():
    """⚠ **上がる銘柄では「落ちた分」が負**なので、⚠ **当てた分は上乗せより大きくなる。**"""
    ps = _per_symbol([
        ("D2", "AAPL", 1, 900.0, 950.0, 0.80),
        (drift.DRIFT, "AAPL", 1, 800.0, 805.0, 1.0),
    ])
    r = drift.decompose(ps, "D2", 55.0).iloc[0]
    assert r["落ちた分"] < 0
    assert r["当てた分"] > r["上乗せ"]


def test_missing_method_is_refused_loudly():
    """⚠ **黙って空の表を返さない**（0 と読み違えるため）。"""
    ps = _per_symbol([(drift.DRIFT, "AAPL", 1, 800.0, 805.0, 1.0)])
    with pytest.raises(SystemExit):
        drift.decompose(ps, "存在しない手法", 55.0)
