"""`im_` 層。⚠ **この層の存在理由は 1 つ — 銘柄ごとに値が変わること。**

⚠ **変わらないなら `ex_` と同じで、[§9-4](../../../docs/specs/experiments/daily-data-sources.md) と同じ結果になる**
（市場全体の方向にしか効かず、銘柄の選択には効かない）。
⚠ **落ちなければ意味が無い検査**は [plan §5](../../../docs/plans/impact-data.md) の 4 つ。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail.features import impact


def series(start="2020-01-01", n=60, step=1.0):
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.Series(np.arange(n, dtype=float) * step, index=idx)


def bars(dates):
    ts = pd.to_datetime(dates, utc=True)
    return pd.DataFrame({"ts": ts, "close": np.arange(len(ts), dtype=float)})


CONF = {
    "name": "test",
    "channel": [
        {"name": "ins", "source": "ncei_storm", "kind": "scalar",
         "series": "SE_DAMAGE", "weights": "ins"},
        {"name": "geo", "source": "iem", "kind": "region",
         "series": "WW_RGN_{region}", "weights": "geo"},
    ],
    "weights": {
        "ins": {"AAA": 1.0, "BBB": 0.5},                       # ⚠ CCC は書かない = 曝露 0
        "geo": {"AAA": {"湾岸": 1.0}, "BBB": {"西部": 1.0}},
    },
}


def run(panel, srcs, conf=CONF, **ctx):
    """`load_series` と `config.exposure` を差し替えて層を回す（取得済みのデータに依存させない）。"""
    import ail.features.impact as m
    orig_load, orig_conf = m.load_series, m.config.exposure
    m.load_series = lambda *a, **k: (srcs, {sid: "x" for sid in srcs})
    m.config.exposure = lambda name: conf
    try:
        return m.layer(panel, ctx)
    finally:
        m.load_series, m.config.exposure = orig_load, orig_conf


def three_symbols(dates=("2020-02-20", "2020-02-21")):
    return {s: bars(list(dates)) for s in ("AAA", "BBB", "CCC")}


def all_series(n=60):
    return {"SE_DAMAGE": series(n=n), "WW_RGN_湾岸": series(n=n, step=2.0),
            "WW_RGN_西部": series(n=n, step=3.0), "WW_RGN_中西部": series(n=n, step=0.5),
            "WW_RGN_北東部": series(n=n, step=0.25)}


# --- ⚠ この層の存在理由（検査 1）---------------------------------------

def test_the_value_differs_by_symbol():
    """⚠ **全銘柄で同じ値なら `ex_` と変わらない。** ⚠ **この検査が落ちたら層ごと作り直す。**"""
    out = run(three_symbols(), all_series(), im_transforms=("lvl",),
              im_source_lag_days={"ncei_storm": 1})
    a, b, c = (out[s]["im_ins_lvl"].iloc[0] for s in ("AAA", "BBB", "CCC"))
    assert a != b and b != c
    ga, gb = (out[s]["im_geo_lvl"].iloc[0] for s in ("AAA", "BBB"))
    assert ga != gb                     # ⚠ 地域が違えば値も違う


def test_a_symbol_without_exposure_gets_zero_not_missing():
    """⚠ **曝露 0 は「反応する商売を持たない」という値である。** ⚠ 欠損にすると行ごと落ちる。"""
    out = run(three_symbols(), all_series(), im_transforms=("lvl",),
              im_source_lag_days={"ncei_storm": 1})
    assert (out["CCC"]["im_ins_lvl"] == 0.0).all()
    assert not out["CCC"].isna().any().any()


# --- ⚠ 重みを掛ける順番（一番踏みやすい罠）------------------------------

def test_the_weight_is_applied_after_the_transform():
    """⚠ **`z20` は定数倍で消える**（`z(k·x) = z(x)`）。

    ⚠ **先に重みを掛けると、曝露を持つ銘柄が全部同じ値になり、この層の意味が消える。**
    """
    out = run(three_symbols(), all_series(), im_transforms=("z20",),
              im_source_lag_days={"ncei_storm": 1})
    a = out["AAA"]["im_ins_z20"].iloc[0]
    b = out["BBB"]["im_ins_z20"].iloc[0]
    assert not np.isnan(a)
    assert b == pytest.approx(a * 0.5)      # ⚠ 重み 0.5 がそのまま効いている


# --- ずらし（`ex_` と同じ規約）------------------------------------------

@pytest.mark.parametrize("lag", [0, -1])
def test_zero_or_negative_lag_is_refused(lag):
    with pytest.raises(ValueError, match="1 以上"):
        run(three_symbols(), all_series(), im_lag_days=lag)


def test_the_slow_source_is_shifted_by_its_own_lag():
    """⚠ **NCEI は 101 日遅れて公表される**【実測】。⚠ **一律 1 日だと先読みになる。**"""
    assert impact.DEFAULT_SOURCE_LAG_DAYS["ncei_storm"] == 120
    p = {"AAA": bars(["2020-06-01"])}
    out = run(p, all_series(n=200), im_transforms=("lvl",))["AAA"]
    # 系列は 2020-01-01 を 0 とする連番。⚠ NCEI は 120 日前 = 2020-02-02 = 32.0
    assert out["im_ins_lvl"].iloc[0] == 32.0
    # ⚠ IEM は既定の 1 日ずらし → 2020-05-31 = 151 日目 × step 2.0
    assert out["im_geo_lvl"].iloc[0] == 302.0


def test_future_values_never_leak_in():
    """⚠ **系列の未来を切り落としても、過去の行は 1 つも変わらない**（rules.md 7 章）。"""
    full = all_series()
    cut = {k: v[v.index <= "2020-02-25"] for k, v in full.items()}
    p = three_symbols(["2020-02-20", "2020-02-24"])
    ctx = dict(im_transforms=("lvl", "d1"), im_source_lag_days={"ncei_storm": 1})
    a = run(p, full, **ctx)["AAA"]
    b = run(p, cut, **ctx)["AAA"]
    pd.testing.assert_frame_equal(a, b)


# --- ⚠ 偽薬（割り当ての入れ替え。検査 4）--------------------------------

def test_without_scrambling_every_symbol_keeps_its_own_weights():
    m = impact.alias_map(["AAA", "BBB", "CCC"], scramble=False, seed=0)
    assert m == {"AAA": "AAA", "BBB": "BBB", "CCC": "CCC"}


def test_scrambling_is_a_permutation_so_the_weights_are_the_same_set():
    """⚠ **偽薬は「重みの分布」を変えない。** ⚠ **変えると本命と条件が揃わない。**"""
    syms = [f"S{i:02d}" for i in range(20)]
    m = impact.alias_map(syms, scramble=True, seed=0)
    assert sorted(m.values()) == sorted(syms)      # ⚠ 並べ替えであること
    # ⚠ **1 本も自分に戻らない**（撹乱）。⚠ 残ると、そのぶん偽薬が本物に近づく
    assert all(a != b for a, b in m.items())


def test_scrambling_is_the_same_every_run():
    """⚠ **種が同じなら同じ入れ替え。** ⚠ **実行ごとに変わると比べられない。**"""
    syms = [f"S{i:02d}" for i in range(20)]
    a = impact.alias_map(syms, scramble=True, seed=0)
    b = impact.alias_map(syms, scramble=True, seed=0)
    c = impact.alias_map(syms, scramble=True, seed=1)
    assert a == b and a != c


def test_the_scrambled_layer_moves_the_values_to_other_symbols():
    """⚠ **系列も重みもそのまま、付いている銘柄だけが違う。**"""
    p = three_symbols()
    ctx = dict(im_transforms=("lvl",), im_source_lag_days={"ncei_storm": 1})
    real = run(p, all_series(), **ctx)
    fake = run(p, all_series(), im_scramble=True, im_scramble_seed=0, **ctx)
    got = {s: fake[s]["im_ins_lvl"].iloc[0] for s in p}
    want = {s: real[s]["im_ins_lvl"].iloc[0] for s in p}
    assert sorted(got.values()) == sorted(want.values())    # ⚠ 値の集合は同じ
    assert got != want                                      # ⚠ 付き先が違う


# --- 0 埋めと接頭辞 -----------------------------------------------------

def test_event_sources_are_zero_filled_by_default():
    """⚠ **災害も警報も「行が無い日 = 0 件の日」。** ⚠ **前方埋めをすると起きなかった日に前の値が入る。**"""
    assert set(impact.DEFAULT_ZERO_FILL) >= {"ncei_storm", "iem"}


def test_prefix_is_registered():
    from ail.contracts import FEATURE_PREFIXES, layer_of
    assert "im_" in FEATURE_PREFIXES
    assert layer_of("im_ins_d1") == "im_"


# --- ⚠ 本物と偽薬を同じ表に並べる（偽発見率をその実行の中で測る）-------

def test_the_placebo_can_sit_next_to_the_real_one():
    """⚠ **同じ実行に両方あると、選別手法がどちらを選んだかがそのまま偽発見率になる。**"""
    out = run(three_symbols(), all_series(), im_transforms=("lvl",),
              im_with_placebo=True, im_source_lag_days={"ncei_storm": 1})["AAA"]
    assert "im_ins_lvl" in out and "im_pb_ins_lvl" in out
    assert len(out.columns) == 4                     # 経路 2 本 × 本物 / 偽薬


def test_the_real_column_is_untouched_by_the_placebo():
    """⚠ **偽薬を並べても本物の列は 1 つも変わらない。** 変わると比べる相手が無くなる。"""
    ctx = dict(im_transforms=("lvl",), im_source_lag_days={"ncei_storm": 1})
    plain = run(three_symbols(), all_series(), **ctx)["AAA"]
    both = run(three_symbols(), all_series(), im_with_placebo=True, **ctx)["AAA"]
    pd.testing.assert_frame_equal(plain, both[list(plain.columns)])


def test_the_two_flags_cannot_be_used_together():
    """⚠ **両方立てると本物の側まで入れ替わり、比べる相手が消える。**"""
    with pytest.raises(SystemExit, match="同時に立てない"):
        run(three_symbols(), all_series(), im_with_placebo=True, im_scramble=True)


def test_the_placebo_prefix_is_read_before_the_real_one():
    """⚠ **接頭辞の順番を間違えると、偽薬の列が本命として数えられる。**"""
    from ail.contracts import layer_of
    assert layer_of("im_pb_ins_d1") == "im_pb_"
    assert layer_of("im_ins_d1") == "im_"
