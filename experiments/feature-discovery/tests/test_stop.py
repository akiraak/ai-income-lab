"""損切りを出口だけのモデルにする検知器と、位置を知る出口の口（[記録](../../../docs/specs/experiments/stoploss-as-model.md) §0）。

⚠ **ここで落としたいのは 6 つ。**
  1. 出力の契約: L0 は 1 本（＝ 100 − 入口% の退化）・L1〜L3 は 2 本。⚠ **入口は 8 本とも同じ 1 本**（`own_trend*` を見ない）
  2. 合わせ方: 出口% ＝ max(100 − 入口%, 損切りの出口%)。規則は深い水準ほど立たない
  3. `simulate` の口: `stop_loss_pct`・`max_hold_days` を省けば 1 ビットも変わらない ／ 立てば買値から −x% を割った翌足に降りる ／ d 日で降りる
  4. `[trading.position_exits]` の無い config は `evaluate_trading` の行が 1 つも変わらず、ある config では既存の行がそのままで
     `〔買値から−N%〕`・`基準 …〔N日で降りる〕` の行が後ろに足されること（18 章の売る線と同じ形）
  5. 名前の綴り（`-stopN`・`-holdN`）
  6. leak 対照が跳ねること（入口に `LEAK_future_ret`）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import contracts, names, registry, runs
from ail.detectors import stop
from ail.features import labels
from ail.validation import checks
from ail.validation import simulate as sim
from cli import run as cli_run
from cli.run import evaluate_trading
import ail.bootstrap  # noqa: F401

W = stop.WINDOW
L1_10 = stop.name_l1(10)


def _panel(n_days=900, n_sym=4, seed=0, leak=False):
    """own 風の列 3 本 ＋ `own_trend20_dd` ／ `_vol` ＋ `y` ＋ `y_fwd_10` を持つ合成パネル（⚠ 雑音）。"""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2015-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        r = rng.normal(0.0003, 0.011, n_days)
        close = 100 * np.exp(np.cumsum(r))
        bars = pd.DataFrame({"ts": ts, "close": close})
        lc = np.log(pd.Series(close))
        rr = pd.Series(r)
        x = pd.DataFrame({"own_ret_1": np.r_[0.0, r[:-1]],
                          "own_ret_5": rr.rolling(5).sum().shift(1).to_numpy(),
                          "own_vol_20": rr.rolling(20).std().shift(1).to_numpy(),
                          f"own_trend{W}_dd": (lc - np.log(pd.Series(close).rolling(W).max())).to_numpy(),
                          f"own_trend{W}_vol": lc.diff().rolling(W).std().to_numpy()})
        y1 = labels.build_one(bars, 1, leak=leak)
        y10 = labels.build_scales(bars, [stop.LEARN_WINDOW], leak=leak)
        d = pd.concat([bars, x, y1, y10], axis=1)
        d["symbol"] = f"S{i}"
        rows.append(d)
    out = pd.concat(rows, ignore_index=True).sort_values(["ts", "symbol"])
    return out.dropna().reset_index(drop=True)


def _feats(panel):
    return contracts.feature_columns(panel)


def _ctx():
    return {"seed": 0, "model": registry.resolve("model", "Ridge"), "k": 8}


def _split(p, q=0.7):
    cut = pd.to_datetime(p["ts"]).quantile(q)
    return p[p["ts"] < cut], p[p["ts"] >= cut]


def _exp(detectors=None, position_exits=None):
    t = {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"}
    if position_exits is not None:
        t["position_exits"] = position_exits
    return {"trading": t,
            "validation": {"split": "walk_forward_dates", "folds": 3, "seed": 0},
            "k": 8, "cost_bp": 5.0,
            "horizon_min": stop.LEARN_WINDOW * 1.5 * 1440.0, "bar_minutes": 1440.0,
            "detectors": detectors or [stop.NAME_L0, L1_10], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_stop", {}, seed=0)


# --- 1. 出力の契約と、入口が 1 本であること ---------------------------------

def test_l0_returns_one_output_and_the_others_return_two():
    p = _panel()
    tr, te = _split(p)
    res0 = registry.resolve("detector", stop.NAME_L0)(tr, te, _feats(p), _ctx())
    assert len(res0) == 2                                     # 入口% と doc ＝ 呼ぶ側が 100 − 入口% を補う
    bp0, doc0 = res0
    assert len(bp0) == len(te) and 0.0 <= float(np.min(bp0)) and float(np.max(bp0)) <= 100.0
    # ⚠ 入口は own の列だけ（own_trend* と y_fwd_ は入らない）
    assert doc0["columns"] == ["own_ret_1", "own_ret_5", "own_vol_20"]
    for name in stop.ALL_NAMES[1:]:
        res = registry.resolve("detector", name)(tr, te, _feats(p), _ctx())
        assert len(res) == 3, name
        bp, ep, doc = res
        assert np.array_equal(bp, bp0), name                  # ⚠ 入口は 8 本とも同じ 1 本
        assert len(ep) == len(te) and float(np.min(ep)) >= 0.0 and float(np.max(ep)) <= 100.0
        assert np.all(ep >= 100.0 - bp - 1e-9), name          # 出口% ＝ max(100 − 入口%, …)


def test_stops_when_the_trend_columns_are_missing():
    p = _panel().drop(columns=[f"own_trend{W}_dd"])
    tr, te = _split(p)
    with pytest.raises(SystemExit, match="trend"):
        registry.resolve("detector", L1_10)(tr, te, _feats(p), _ctx())


# --- 2. 合わせ方と規則の向き ------------------------------------------------

def test_rule_exits_fire_less_at_deeper_levels_and_combine_is_max():
    p = _panel()
    tr, te = _split(p)
    bp, _ = stop.entry(tr, te, _feats(p), _ctx())
    fires = []
    for x in stop.DD_LEVELS:
        s, doc = stop.rule_drawdown(te, x)
        assert set(np.unique(s)) <= {0.0, 100.0} and doc["level_pct"] == x
        fires.append(int((s == 100.0).sum()))
        # 規則が立った日は 100、立たない日は 100 − 入口%
        ep = stop.combine(bp, s)
        assert np.array_equal(ep[s == 100.0], np.full(int((s == 100.0).sum()), 100.0))
        assert np.allclose(ep[s == 0.0], 100.0 - bp[s == 0.0])
    assert fires[0] >= fires[1] >= fires[2] and fires[0] > 0        # 深いほど立たない
    fires = [int((stop.rule_drawdown_vol(te, k)[0] == 100.0).sum()) for k in stop.VOL_LEVELS]
    assert fires[0] >= fires[1] >= fires[2] and fires[0] > 0


def test_learned_exit_is_the_complement_of_the_forward_gate():
    from ail.detectors import fwd
    p = _panel()
    tr, te = _split(p)
    ep, _ = stop.learned_exit(tr, te, _feats(p), _ctx())
    buy_out, _ = fwd.forward_gate(stop.LEARN_WINDOW, tr, te, stop.entry_columns(_feats(p)), _ctx())
    assert np.allclose(ep, 100.0 - buy_out)


# --- 3. simulate の口 ----------------------------------------------------------

def _series(seed=0, n=400):
    rng = np.random.default_rng(seed)
    return rng.uniform(30, 70, n), rng.normal(0.0002, 0.02, n)


def test_default_path_is_unchanged_by_the_new_arguments():
    b, y = _series()
    for th in (50.0, 55.0, 60.0):
        a = sim.simulate(b, y, th, 5.0)
        c = sim.simulate(b, y, th, 5.0, stop_loss_pct=None, max_hold_days=None)
        d = sim.simulate(b, y, th, 5.0, stop_loss_pct=99.9, max_hold_days=10 ** 6)   # 事実上立たない
        for k in ("net_bp", "pos"):
            assert np.array_equal(a[k], c[k]) and np.array_equal(a[k], d[k])
        assert a["trades"] == c["trades"] == d["trades"] and a["hold_days"] == c["hold_days"] == d["hold_days"]


def test_stop_loss_exits_the_bar_after_the_cumulative_loss_crosses_the_line():
    b, y = _series(1)
    x = 3.0
    r = sim.simulate(b, y, 50.0, 5.0, stop_loss_pct=x)
    line = np.log1p(-x / 100.0)
    pos = r["pos"]
    for t in range(1, len(pos)):
        if pos[t] == 1 and pos[t - 1] == 1:
            # 保有を続けた足 t ＝ その朝までの累積（opened〜t−1）は線を割っていない
            o = max(i for i in range(t) if pos[i] == 1 and (i == 0 or pos[i - 1] == 0))
            assert float(np.sum(y[o:t])) >= line, t
    # 線を割った翌足には必ず降りている ＝ 損切りなしより保有日率が下がる
    assert r["hold_ratio"] <= sim.simulate(b, y, 50.0, 5.0)["hold_ratio"]
    assert r["trades"] >= 1


def test_max_hold_days_caps_every_holding():
    b, y = _series(2)
    r = sim.simulate(b, y, 50.0, 5.0, max_hold_days=5)
    assert r["hold_days"] and max(r["hold_days"]) <= 5
    assert sum(r["hold_days"]) == int(r["pos"].sum())


@pytest.mark.parametrize("kw", [{"stop_loss_pct": 0.0}, {"stop_loss_pct": 100.0}, {"max_hold_days": 0}])
def test_bad_position_exit_values_are_refused(kw):
    b, y = _series()
    with pytest.raises(ValueError):
        sim.simulate(b, y, 50.0, 5.0, **kw)


# --- 4. evaluate_trading ---------------------------------------------------

def test_existing_rows_do_not_change_and_position_exit_rows_are_appended(run):
    p = _panel()
    base = _exp()
    res0, sym0, sum0, daily0, extra0 = evaluate_trading(p, _feats(p), base, run)
    pe = {"methods": [stop.NAME_L0], "stop_loss_pct": [5, 20], "max_hold_days": [5]}
    res1, sym1, sum1, daily1, extra1 = evaluate_trading(p, _feats(p), _exp(position_exits=pe), run)
    added_mask = res1["手法"].str.contains("〔買値から") | res1["手法"].str.contains("日で降りる〕")
    pd.testing.assert_frame_equal(res0, res1[~added_mask].reset_index(drop=True))   # ⚠ 既存の行は 1 ビットも変わらない
    added = res1[added_mask]
    assert set(added["手法"]) == {f"{stop.NAME_L0}〔買値から−5%〕", f"{stop.NAME_L0}〔買値から−20%〕",
                                 f"基準 {stop.NAME_L0}〔5日で降りる〕"}
    assert set(added["閾値"]) == {50.0, 55.0, 60.0}
    assert not any(n.startswith(L1_10) for n in added["手法"])       # methods に無い検知器には当てない
    assert (f"{stop.NAME_L0}〔買値から−5%〕", 50.0) in daily1
    # 浅い損切りほど保有日率は下がる（L0 ≥ −20% ≥ −5%）。固定日数 5 日は保有の最長が 5
    def hr(name):
        return res1[(res1["手法"] == name) & (res1["閾値"] == 50.0)]["保有日率"].mean()
    assert hr(stop.NAME_L0) >= hr(f"{stop.NAME_L0}〔買値から−20%〕") >= hr(f"{stop.NAME_L0}〔買値から−5%〕")
    holds = extra1["holds"]
    assert holds[holds["手法"] == f"基準 {stop.NAME_L0}〔5日で降りる〕"]["保有日数"].max() <= 5


@pytest.mark.parametrize("pe,msg", [
    ({"stop_loss_pct": [5]}, "methods"),
    ({"methods": [stop.NAME_L0], "stop_loss_pct": [0]}, "100 未満"),
    ({"methods": [stop.NAME_L0], "max_hold_days": [0]}, "1 日以上"),
    ({"methods": ["基準 常に上（ドリフト）"], "stop_loss_pct": [5]}, "基準線"),
])
def test_bad_position_exits_stop(pe, msg):
    with pytest.raises(SystemExit, match=msg):
        cli_run.position_exits({"position_exits": pe})


def test_no_position_exits_is_empty():
    assert cli_run.position_exits({}) == {"methods": [], "stop_loss_pct": [], "max_hold_days": []}


# --- 5. 綴り ----------------------------------------------------------------

def _row(key, model="Ridge"):
    return {"鍵": key, "モデル": model, "粒度": "日足", "地平": "1 本（1 日）", "特徴量の層": "own trend",
            "層": "adjusted", "期間": "2018-01-31", "銘柄": "63", "検証方式": "閾値売買", "形式": "共通",
            "較正": "std", "閾値": "50"}


def test_name_spellings():
    assert names.trial_name(_row(stop.NAME_L0)) == "own-trend.l0-nostop.ridge.shared@50"
    assert names.trial_name(_row(L1_10)) == "own-trend.l1-dd20-10.ridge.shared@50"
    assert names.trial_name(_row(stop.name_l2(4))) == "own-trend.l2-ddvol20-4.ridge.shared@50"
    assert names.trial_name(_row(stop.NAME_L3)) == "own-trend.l3-learn10.ridge.shared@50"
    assert names.trial_name(_row(f"{stop.NAME_L0}〔買値から−5%〕")) == "own-trend.l0-nostop-stop5.ridge.shared@50"
    assert names.trial_name(_row(f"{stop.NAME_L0}〔5日で降りる〕", "—")) == "own-trend.l0-nostop-hold5.none.shared@50"
    for n in stop.ALL_NAMES:
        assert names.method_slug(n)


# --- 6. leak 対照 ----------------------------------------------------------

def test_leak_makes_the_edge_jump(run):
    p = _panel(leak=True)
    assert labels.LEAK_COLUMN in stop.entry_columns(_feats(p))      # ⚠ 答えの列が入口に入っている
    e = _exp(detectors=[L1_10])
    res, per_sym, summary, daily, extra = evaluate_trading(p, _feats(p), e, run)
    doc = checks.compute_trading(res, summary, per_sym, daily, e, n_trials=70, leak=True, extra=extra)
    for th, x in doc["by_threshold"].items():
        assert x["edge_vs_bh"]["mean_bp"] > 50.0, th
        assert x["edge_vs_bh"]["positive"] == x["edge_vs_bh"]["folds"], th
