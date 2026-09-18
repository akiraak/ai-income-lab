"""入口と出口を別の窓で持つ配線（rules.md 16 章。[プラン](../../../docs/plans/archive/downtrend-entry-timing.md)）。

⚠ **ここで落としたいのは 5 つ。**
  1. ⚠ **退化**（16-1 の 4・16-2）: 出口% を省く / 100 − 入口% を渡す / 対称な窓にする の 3 通りが、
     ⚠ **既存の 1 出力の契約と 1 つも違わないこと**。違ったら既存 217 試行が動いてしまう
  2. ⚠ **非対称性は窓の違いからしか生まれないこと**（16-2）— 較正はラベルを反転すると係数が反転するだけ
  3. 状態が読む側を決めること（16-1 の 2。⚠ **未保有は入口% だけ・保有中は出口% だけ**）
  4. ⚠ **毎日反転してコストだけ払う構成が実在すること**（16-7 の 1。取引回数で見分けられること）
  5. config の 12 本が registry で全部引けること（16-4 の事前固定。⚠ **打ち間違いをここで落とす**）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import config, registry, runs
from ail.detectors import pair, scale
from ail.features import trend
from ail.models import calibrate
from ail.validation import checks, simulate as sim
from cli.run import evaluate_trading
import ail.bootstrap  # noqa: F401

# ⚠ **本物の窓（20/60/200）は合成パネルでは回らない**ので、形だけ同じ短い窓で通す。
# ⚠ **パネルの作り方は test_downtrend と同じものを使う**（表の列の形が食い違うと検査にならない）
from tests.test_downtrend import WINDOWS, _exp, _feats, _panel


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_pairs", {}, seed=0)


def _split(p, q=0.7):
    cut = pd.to_datetime(p["ts"]).quantile(q)
    return p[p["ts"] < cut], p[p["ts"] >= cut]


def _ctx():
    return {"seed": 0, "model": registry.resolve("model", "Ridge"), "k": 4}


# --- 1. 退化 — 既存の契約と 1 つも違わない（rules.md 16-1 の 4・16-2）------

def test_omitting_exit_is_the_same_as_passing_its_complement():
    """⚠ **出口% を省く ＝ 100 − 入口%。** 既存の行はこの枝で再現する。"""
    rng = np.random.default_rng(0)
    n = 500
    buy = rng.uniform(0, 100, n)
    y = rng.normal(0.0002, 0.01, n)
    for th in (50.0, 55.0, 60.0):
        a = sim.simulate(buy, y, th, 5.0)
        b = sim.simulate(buy, y, th, 5.0, exit_pct=100.0 - buy)
        assert np.array_equal(a["pos"], b["pos"])
        assert np.allclose(a["net_bp"], b["net_bp"])
        assert (a["trades"], a["cost_bp_total"]) == (b["trades"], b["cost_bp_total"])


def test_symmetric_windows_degenerate_to_the_single_output_contract():
    """⚠ **同じ窓なら出口% は厳密に 100 − 入口%**（16-2）。⚠ **だから対称構成は回さない。**"""
    p = _panel()
    tr, te = _split(p)
    for w in WINDOWS:
        entry, exit_pct, doc = pair.pair_gate(w, w, tr, te, _feats(p), _ctx())
        single, _ = scale.scale_gate(w, tr, te, _feats(p), _ctx())
        assert np.allclose(entry, single)                  # 入口% は単独のゲートそのもの
        assert np.allclose(exit_pct, 100.0 - entry)        # ⚠ 退化（新しい情報は 1 つも無い）
        assert doc["入口の窓"] == doc["出口の窓"] == w


def test_symmetric_pair_reproduces_the_single_window_run_row_by_row(run):
    """⚠ **実行ごと通しても退化すること。** 1 行でも動いたら既存 217 試行が動く配線である。"""
    w = WINDOWS[-1]
    one, two = f"TEST 単独 {w}", f"TEST 対称 {w}/{w}"
    for name, fn in ((one, lambda tr, te, f, c, _w=w: scale.scale_gate(_w, tr, te, f, c)),
                     (two, lambda tr, te, f, c, _w=w: pair.pair_gate(_w, _w, tr, te, f, c))):
        if name not in registry.available("detector"):
            registry.register("detector", name)(fn)
    p = _panel()
    res, per_sym, _summary, _daily, _extra = evaluate_trading(p, _feats(p), _exp([one, two]), run)
    a = res[res["手法"] == one].set_index(["fold", "閾値"])["純利bp"]
    b = res[res["手法"] == two].set_index(["fold", "閾値"])["純利bp"]
    assert len(a) > 0 and np.allclose(a.to_numpy(), b.reindex(a.index).to_numpy())
    sa = per_sym[per_sym["手法"] == one].set_index(["fold", "閾値", "銘柄"])
    sb = per_sym[per_sym["手法"] == two].set_index(["fold", "閾値", "銘柄"])
    for col in ("純利bp", "取引回数", "保有日率", "見送り日数"):
        assert np.allclose(sa[col].to_numpy(), sb[col].reindex(sa.index).to_numpy()), col


# --- 2. ⚠ 非対称性は窓の違いからしか生まれない（rules.md 16-2）-----------

def test_calibrating_the_flipped_label_only_flips_the_coefficients():
    """⚠ **16-2 の根拠。** P(下げ) を独立に較正しても 100 − P(上げ) そのものになる。"""
    rng = np.random.default_rng(1)
    pred = rng.normal(0, 1, 800)
    y = 0.3 * pred + rng.normal(0, 1, 800)              # 情報のある予測（傾きが学べる）
    up = calibrate.fit_from_predictions(pred, y, "holdout")
    down = calibrate.fit_from_predictions(pred, -y, "holdout")   # ⚠ ラベルだけ反転
    assert up.a == pytest.approx(-down.a, rel=1e-3)
    assert up.b == pytest.approx(-down.b, rel=1e-3)
    assert np.allclose(down.buy_pct(pred), 100.0 - up.buy_pct(pred), atol=1e-6)


def test_asymmetric_pair_uses_both_windows_and_is_not_a_complement():
    """⚠ **違う窓なら出口% は 100 − 入口% ではない**（ここが 2 本にした意味）。"""
    p = _panel()
    tr, te = _split(p)
    w_in, w_out = WINDOWS[0], WINDOWS[-1]
    entry, exit_pct, doc = pair.pair_gate(w_in, w_out, tr, te, _feats(p), _ctx())
    assert len(entry) == len(exit_pct) == len(te)
    assert 0.0 <= float(np.nanmin(entry)) and float(np.nanmax(entry)) <= 100.0
    assert 0.0 <= float(np.nanmin(exit_pct)) and float(np.nanmax(exit_pct)) <= 100.0
    assert not np.allclose(exit_pct, 100.0 - entry)
    # ⚠ **両方の窓の列を使っている**（片方だけなら leak 対照が片側で跳ねない。16-7 の 4）
    assert any(c.startswith(f"{trend.PREFIX}trend{w_in}_") for c in doc["columns"])
    assert any(c.startswith(f"{trend.PREFIX}trend{w_out}_") for c in doc["columns"])
    assert doc["入口"]["scale"] == w_in and doc["出口"]["scale"] == w_out


def test_classic_pair_enters_on_the_short_window_and_exits_on_the_long_one():
    """C 系は ⚠ **SMA W_in の上で入り、SMA W_out の下で出る**（学習しない）。"""
    p = _panel()
    tr, te = _split(p)
    w_in, w_out = WINDOWS[0], WINDOWS[-1]
    entry, exit_pct, doc = pair.pair_classic(w_in, w_out, tr, te, _feats(p), {"seed": 0})
    assert set(np.unique(entry)) <= {0.0, 100.0} and set(np.unique(exit_pct)) <= {0.0, 100.0}
    assert np.array_equal(entry > 50, te[f"{trend.PREFIX}trend{w_in}_dist"].to_numpy() > 0)
    assert np.array_equal(exit_pct > 50, te[f"{trend.PREFIX}trend{w_out}_dist"].to_numpy() <= 0)
    assert doc["columns"] == []                          # 学習に使う列が無い ＝ 「本数」は 0


# --- 3. 状態が読む側を決める（rules.md 16-1 の 2）------------------------

def test_the_state_decides_which_output_is_read():
    """⚠ **未保有の日は入口% だけ・保有中の日は出口% だけ。** 優先規則は要らない。"""
    n = 50
    y = np.full(n, 0.001)
    ones, zeros = np.full(n, 100.0), np.zeros(n)
    # 入口が立ち続け、出口は立たない → 初日に建てて fold 末尾まで持つ
    r = sim.simulate(ones, y, 50.0, 5.0, exit_pct=zeros)
    assert r["trades"] == 1 and r["pos"].sum() == n
    # 入口が立たない → 出口が立ち続けても何も起きない（⚠ 買い専用・13-4 の 2）
    r = sim.simulate(zeros, y, 50.0, 5.0, exit_pct=ones)
    assert r["trades"] == 0 and r["pos"].sum() == 0 and r["cost_bp_total"] == 0.0


def test_both_outputs_high_churns_every_day_and_only_pays_cost():
    """⚠ **16-7 の 1 の失敗モードが実在すること。** 取引回数と保有日率で見分ける。"""
    n = 40
    y = np.zeros(n)                                      # 相場が動かない ＝ 出るのはコストだけ
    ones = np.full(n, 100.0)
    r = sim.simulate(ones, y, 50.0, 5.0, exit_pct=ones)
    assert np.array_equal(r["pos"], np.tile([1, 0], n // 2))   # 1 日ごとに反転
    assert r["hold_ratio"] == pytest.approx(0.5)
    assert r["trades"] == n // 2
    assert r["net_bp"].sum() < 0.0 and r["cost_bp_total"] == pytest.approx(2.5 * n)


# --- 4. 実行ごと通す ／ leak 対照（rules.md 16-7 の 4）-------------------

def _pair_names(w_in, w_out):
    names = [f"TEST 対 D{w_in}/{w_out}", f"TEST 対 C{w_in}/{w_out}"]
    fns = [lambda tr, te, f, c, _i=w_in, _o=w_out: pair.pair_gate(_i, _o, tr, te, f, c),
           lambda tr, te, f, c, _i=w_in, _o=w_out: pair.pair_classic(_i, _o, tr, te, f, c)]
    for name, fn in zip(names, fns):
        if name not in registry.available("detector"):
            registry.register("detector", name)(fn)
    return names


def test_evaluate_trading_runs_two_output_detectors(run):
    """⚠ **出力 2 本の検知器が既存の配線でそのまま回る**（シミュレータから先は同じ物差し）。"""
    p = _panel()
    names = _pair_names(WINDOWS[0], WINDOWS[-1])
    res, per_sym, summary, _daily, extra = evaluate_trading(p, _feats(p), _exp(names), run)
    for n in names:
        assert n in set(res["手法"]) and n in set(summary.index)
        rows = res[res["手法"] == n]
        assert (rows["保有日率"].between(0.0, 1.0)).all()
        assert set(rows["閾値"]) == {50.0, 55.0, 60.0}   # ⚠ 3 水準とも残す（13-3 の 3）
    {"hold", "rand", "rev", "holds"} <= set(extra)   # ⚠ 2026-09-17 に rev / holds が増えた（13-4 の 6・14-3）"rand"}                # 乱択ゲートの診断も付く（14-6 b）


def test_leak_makes_the_edge_jump_for_pairs(run):
    """⚠ **2 つの窓の `LEAK_fwd_` を混ぜて跳ねること。** 跳ねなければ配線が壊れている。"""
    p = _panel(leak=True)
    names = _pair_names(WINDOWS[0], WINDOWS[-1])
    exp = _exp(names)
    res, per_sym, summary, daily, extra = evaluate_trading(p, _feats(p), exp, run)
    doc = checks.compute_trading(res, summary, per_sym, daily, exp,
                                 n_trials=253, leak=True, extra=extra)
    for th, e in doc["by_threshold"].items():
        assert e["edge_vs_bh"]["mean_bp"] > 50.0, th


# --- 5. 事前固定した 12 本（rules.md 16-4・16-6）-------------------------

def test_the_six_pairs_are_fixed_and_have_no_symmetric_entry():
    """⚠ **構成は 6 つ。** 対称な窓は置かない（16-2 で退化する）。逆向き 3 つは対照。"""
    assert len(pair.PAIRS) == 6
    assert all(a != b for a, b in pair.PAIRS)
    assert len(set(pair.PAIRS)) == 6
    # ⚠ **仮説 3 つ（入口が短期）と対照 3 つ（逆向き）が対になっている**
    assert {(b, a) for a, b in pair.PAIRS} == set(pair.PAIRS)


def test_every_detector_in_the_config_resolves_and_names_carry_the_construction():
    """⚠ **config の 12 本が registry で引けること**（打ち間違いを実行前に落とす。16-4）。

    ⚠ **名前に入口と出口の窓が入っていること**も見る（16-6 の 1。入れ忘れると台帳で行が
    まとまり、⚠ **差が「再現の幅」に化ける**）。
    """
    exp = config.resolve_experiment("trend_pairs_1995")
    names = exp["detectors"]
    assert len(names) == 12
    registry.resolve_all("detector", names)              # 1 本でも欠ければここで止まる
    for (w_in, w_out) in pair.PAIRS:
        for kind in ("D", "C"):
            name = pair.name_of(kind, w_in, w_out)
            assert name in names
            assert f"({w_in})" in name and f"({w_out})" in name
    assert exp["features_from"] == "trend_scales_1995"   # ⚠ 表は作り直さない（13-8）
    assert exp["trading"]["thresholds"] == [50, 55, 60]
    assert exp["trading"]["form"] == "shared"
    assert exp["model"] == "Ridge"                       # ⚠ 入口と出口で変えない（16-3 の 6）
