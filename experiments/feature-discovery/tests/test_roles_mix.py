"""役割つきの合わせる口（rules.md 25-2）と、`simulate` ／ `simulate_topk` の最短保有・毎日読む出口（25-2 の 3）。

⚠ 落としたいのは 5 つ。
  1. `min_hold_days`・`stop_exit_pct` を省けば `simulate` ／ `simulate_topk` は 1 ビットも変わらない
  2. 最短保有の中は売り係（出口%）を読まない ／ 損切り係（毎日読む出口%）は中でも読む ／ d 日目から売り係を読む
  3. `combine = "roles"` の検査（買い係・売り係が要る ／ role は roles のときだけ ／ 外の実験は使えない ／ d は 1 以上の整数）
  4. 買い% ＝ 買い係・出口% ＝ 100 − 売り係の買い%・毎日読む出口 ＝ 損切り係 ＝ `opts` に置かれ、既存の手法の行は変わらない
  5. トレーダーの形（20 章）にも最短保有が届く（d を大きくすると取引が減る）
  6. 25-4「損切り係が言っている日は買わない」（`stop_blocks_buy`）: 省けば 1 ビットも変わらない ／ 言っている日は建てない ／
     `stop_exit_pct` 無しでは受けない ／ `mix_specs` の検査 ／ 役割つきの行で保有 1 日の取引が減り、ほかの行は変わらない
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import names, registry, runs
from ail.validation import simulate as sim
from cli.run import MIX_ROLES, evaluate_trading, mix_specs, trader_name

BUY, EXIT, STOP = "テスト用 買い係", "テスト用 売り係", "テスト用 損切り係"
R1 = "R1 買い先10日・売り先5日・損切り"      # ⚠ names.toml の [mix] にある綴り（roles-b10-e5）
R1B = "R1b 買い先10日・売り先5日・損切り（買いも止める）"   # 25-4（roles-b10-e5-block）
TAG = "5本・$600"


@registry.register("detector", BUY)
def _buy(tr, te, feats, ctx):
    """前日のリターンが正なら 70・負なら 30（学ばない。配線のためだけ）。"""
    return np.where(te["own_ret_1"].to_numpy() > 0, 70.0, 30.0), {"columns": ["own_ret_1"]}


@registry.register("detector", EXIT)
def _exit(tr, te, feats, ctx):
    """`noise` が正なら 30・負なら 70 ＝ 出口%（100 − 買い%）は 70 ／ 30。"""
    return np.where(te["noise"].to_numpy() > 0, 30.0, 70.0), {"columns": ["noise"]}


@registry.register("detector", STOP)
def _stop(tr, te, feats, ctx):
    """入口 100・出口は `noise` < −2 のとき 100（損切り係の形 ＝ stop.py の X1 と同じ契約）。"""
    n = len(te)
    return np.full(n, 100.0), np.where(te["noise"].to_numpy() < -2.0, 100.0, 0.0), {"columns": []}


def _panel(n_days=600, n_sym=6, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        y = rng.normal(0.0002, 0.01, n_days)
        rows.append(pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y, "close": 20.0 * (i + 1) * np.exp(np.cumsum(y)),
                                  "own_ret_1": np.r_[0.0, y[:-1]], "noise": rng.normal(0, 1, n_days)}))
    return pd.concat(rows, ignore_index=True)


def _feats(p):
    return [c for c in p.columns if c not in ("symbol", "ts", "y", "close")]


def _mix(min_hold=10, members=None, combine=MIX_ROLES, name=R1, block=None):
    m = {"name": name, "combine": combine,
         "members": members or [{"method": BUY, "role": "buy"}, {"method": EXIT, "role": "exit"}, {"method": STOP, "role": "stop"}]}
    if min_hold is not None:
        m["min_hold_days"] = min_hold
    if block is not None:
        m["stop_blocks_buy"] = block
    return [m]


def _exp(mix=None, trader=None):
    t = {"style": "threshold", "thresholds": [50], "form": "shared"}
    if mix is not None:
        t["mix"] = mix
    if trader is not None:
        t["trader"] = trader
    return {"name": "host", "trading": t, "validation": {"split": "walk_forward_dates", "folds": 3, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "detectors": [BUY, EXIT, STOP], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_roles_mix", {}, seed=0)


# --- 1・2. simulate ／ simulate_topk -------------------------------------------------

def test_defaults_are_bit_identical():
    rng = np.random.default_rng(0)
    b, e, y = rng.uniform(0, 100, 300), rng.uniform(0, 100, 300), rng.normal(0, 0.01, 300)
    a = sim.simulate(b, y, 50.0, exit_pct=e)
    c = sim.simulate(b, y, 50.0, exit_pct=e, min_hold_days=None, stop_exit_pct=None)
    np.testing.assert_array_equal(a["net_bp"], c["net_bp"])
    assert a["trades"] == c["trades"] and a["hold_days"] == c["hold_days"]


def test_min_hold_silences_exit_and_stop_is_read_daily():
    """足 0 で買う（買い% 100）。売り係は毎日 100。d ＝ 5 なら足 5 で売る（保有 5 日）。損切り係が足 2 で 100 なら足 2 で売る。"""
    n = 12
    b = np.r_[100.0, np.zeros(n - 1)]
    e = np.full(n, 100.0)
    y = np.zeros(n)
    plain = sim.simulate(b, y, 50.0, exit_pct=e)
    assert plain["hold_days"] == [1]                                   # 既定: 翌足で売る
    held = sim.simulate(b, y, 50.0, exit_pct=e, min_hold_days=5)
    assert held["hold_days"] == [5] and held["trades"] == 1            # 5 本は売り係を読まない ＝ 足 5 で売る
    stop = np.zeros(n)
    stop[2] = 100.0
    cut = sim.simulate(b, y, 50.0, exit_pct=e, min_hold_days=5, stop_exit_pct=stop)
    assert cut["hold_days"] == [2]                                     # 損切り係は最短保有の中でも読む
    with pytest.raises(ValueError):
        sim.simulate(b, y, 50.0, exit_pct=e, min_hold_days=0)
    with pytest.raises(ValueError):
        sim.simulate(b, y, 50.0, exit_pct=e, stop_exit_pct=stop[:-1])


def test_topk_min_hold_and_stop():
    """2 銘柄 × 8 日。S0 を 1 日目に買い（買い% 100）、売り係は毎日 100。d ＝ 3 なら 4 日目に売る。損切り係が 2 日目に立てば 2 日目に売る。"""
    days = pd.date_range("2020-01-01", periods=8, freq="D")
    rows = [(d, s) for d in days for s in ("S0", "S1")]
    dates = np.array([r[0] for r in rows])
    syms = np.array([r[1] for r in rows])
    b = np.array([100.0 if (s == "S0" and d == days[0]) else 0.0 for d, s in rows])
    e = np.full(len(rows), 100.0)
    y = np.zeros(len(rows))
    kw = dict(threshold=50.0, k=2, cost_bp=5.0, exit_pct=e)
    plain = sim.simulate_topk(b, y, dates, syms, **kw)
    held = sim.simulate_topk(b, y, dates, syms, **kw, min_hold_days=3)
    stop = np.array([100.0 if (s == "S0" and d == days[1]) else 0.0 for d, s in rows])
    cut = sim.simulate_topk(b, y, dates, syms, **kw, min_hold_days=3, stop_exit_pct=stop)
    same = sim.simulate_topk(b, y, dates, syms, **kw, min_hold_days=None, stop_exit_pct=None)
    assert int(plain["pos"].sum()) == 1 and int(held["pos"].sum()) == 3 and int(cut["pos"].sum()) == 1
    np.testing.assert_array_equal(plain["pos"], same["pos"])
    assert plain["trades"] == held["trades"] == cut["trades"] == 1


# --- 3. mix_specs の検査 ----------------------------------------------------------------

def test_roles_spec_is_checked():
    s = mix_specs({"mix": _mix()})[0]
    assert s["combine"] == MIX_ROLES and s["min_hold_days"] == 10 and [m["role"] for m in s["members"]] == ["buy", "exit", "stop"]
    assert mix_specs({"mix": _mix(min_hold=None)})[0]["min_hold_days"] is None
    with pytest.raises(SystemExit, match="買い係"):
        mix_specs({"mix": _mix(members=[{"method": EXIT, "role": "exit"}, {"method": STOP, "role": "stop"}])})
    with pytest.raises(SystemExit, match="buy ／ exit ／ stop"):
        mix_specs({"mix": _mix(members=[{"method": BUY, "role": "buy"}, {"method": EXIT, "role": "sell"}])})
    with pytest.raises(SystemExit, match="この実行の手法だけ"):
        mix_specs({"mix": _mix(members=[{"method": BUY, "role": "buy"}, {"experiment": "ext", "method": EXIT, "role": "exit"}])})
    with pytest.raises(SystemExit, match="min_hold_days"):
        mix_specs({"mix": _mix(min_hold=0)})
    with pytest.raises(SystemExit, match="roles のときだけ"):
        mix_specs({"mix": _mix(combine="mean", min_hold=None)})
    with pytest.raises(SystemExit, match="mean か roles"):
        mix_specs({"mix": [{"name": R1, "combine": "majority", "members": [{"method": BUY}, {"method": EXIT}]}]})


# --- 4・5. 配線 -------------------------------------------------------------------------

def test_roles_row_uses_buy_from_buy_exit_from_exit_and_does_not_change_other_rows(run):
    p = _panel()
    a, *_ = evaluate_trading(p, _feats(p), _exp(), run)
    b, per_sym, _summary, _daily, _extra = evaluate_trading(p, _feats(p), _exp(_mix()), run)
    plain = b[b["手法"].isin(set(a["手法"]))].reset_index(drop=True)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), plain, check_exact=True, check_dtype=False)
    assert set(b["手法"]) - set(a["手法"]) == {R1}
    rows = b[b["手法"] == R1]
    assert len(rows) == 3 and rows["取引回数"].sum() > 0
    assert names.method_slug(R1) == "roles-b10-e5"


def test_longer_min_hold_trades_less_in_both_rows(run):
    """同じ係で d ＝ 1 と d ＝ 10 を並べる: 取引の回数は d ＝ 10 のほうが少ない（13 章の行・トレーダーの形の行とも）。"""
    p = _panel()
    tr = [{"tag": TAG, "symbols": ["S0", "S1", "S2", "S3", "S4"], "budget_usd": 600, "methods": [R1]}]
    short, *_ = evaluate_trading(p, _feats(p), _exp(_mix(min_hold=1), tr), run)
    long_, *_ = evaluate_trading(p, _feats(p), _exp(_mix(min_hold=10), tr), run)
    for name in (R1, trader_name(R1, TAG)):
        ts_, tl_ = short[short["手法"] == name]["取引回数"].sum(), long_[long_["手法"] == name]["取引回数"].sum()
        assert tl_ < ts_, (name, ts_, tl_)
    assert names.method_slug(trader_name(R1, TAG)) == "roles-b10-e5-tr5-600"


def test_stop_member_must_return_exit(run):
    p = _panel()
    bad = _mix(members=[{"method": BUY, "role": "buy"}, {"method": EXIT, "role": "exit"}, {"method": BUY, "role": "stop"}])
    with pytest.raises(SystemExit):
        mix_specs({"mix": bad})          # ⚠ 同じメンバーが 2 度 ＝ 先に止まる
    bad2 = [{"name": R1, "combine": MIX_ROLES, "min_hold_days": 3,
             "members": [{"method": BUY, "role": "buy"}, {"method": EXIT, "role": "stop"}, {"method": STOP, "role": "exit"}]}]
    with pytest.raises(SystemExit, match="出口% を返さない"):
        evaluate_trading(p, _feats(p), _exp(bad2), run)


# --- 6. 25-4 損切り係が言っている日は買わない --------------------------------------------

def test_stop_blocks_buy_default_is_bit_identical_and_blocks_entry():
    """買い% 毎日 100・売り係 0。損切り係が足 0〜3 で 100 なら、止める口があると足 4 で初めて建てる（無ければ足 0 で建て、足 1 で損切り係が売り、足 2 でまた建てる往復）。"""
    n = 10
    b = np.full(n, 100.0)
    e = np.zeros(n)
    y = np.zeros(n)
    stop = np.r_[100.0, 100.0, 100.0, 100.0, np.zeros(n - 4)]
    plain = sim.simulate(b, y, 50.0, exit_pct=e, stop_exit_pct=stop)
    same = sim.simulate(b, y, 50.0, exit_pct=e, stop_exit_pct=stop, stop_blocks_buy=False)
    np.testing.assert_array_equal(plain["pos"], same["pos"])
    assert plain["entry_idx"][:2] == [0, 2] and plain["hold_days"][:2] == [1, 1]     # 往復（建て → 翌足に損切り係で売り → その翌足にまた建てる ＝ 25-3 の適用例の動き）
    blocked = sim.simulate(b, y, 50.0, exit_pct=e, stop_exit_pct=stop, stop_blocks_buy=True)
    assert blocked["entry_idx"] == [4] and blocked["trades"] == 1
    with pytest.raises(ValueError, match="stop_blocks_buy"):
        sim.simulate(b, y, 50.0, exit_pct=e, stop_blocks_buy=True)


def test_topk_stop_blocks_buy():
    days = pd.date_range("2020-01-01", periods=6, freq="D")
    rows = [(d, s) for d in days for s in ("S0", "S1")]
    dates = np.array([r[0] for r in rows])
    syms = np.array([r[1] for r in rows])
    b = np.full(len(rows), 100.0)
    e = np.zeros(len(rows))
    y = np.zeros(len(rows))
    stop = np.array([100.0 if (s == "S0" and d < days[2]) else 0.0 for d, s in rows])   # S0 だけ 2 日目まで損切り係が言う
    kw = dict(threshold=50.0, k=2, cost_bp=5.0, exit_pct=e, stop_exit_pct=stop)
    plain = sim.simulate_topk(b, y, dates, syms, **kw)
    same = sim.simulate_topk(b, y, dates, syms, **kw, stop_blocks_buy=False)
    blocked = sim.simulate_topk(b, y, dates, syms, **kw, stop_blocks_buy=True)
    np.testing.assert_array_equal(plain["pos"], same["pos"])
    s0 = syms == "S0"
    assert plain["trades"] == 3 and blocked["trades"] == 2                      # S0 の往復（0 日目に建て 1 日目に売り 1 日目にまた建てる）が消える
    assert int(blocked["pos"][s0][:2].sum()) == 0 and int(blocked["pos"][s0][2:].sum()) == 4
    np.testing.assert_array_equal(plain["pos"][~s0], blocked["pos"][~s0])       # S1 は変わらない
    with pytest.raises(ValueError, match="stop_blocks_buy"):
        sim.simulate_topk(b, y, dates, syms, threshold=50.0, k=2, exit_pct=e, stop_blocks_buy=True)


def test_stop_blocks_buy_spec_is_checked():
    assert mix_specs({"mix": _mix()})[0]["stop_blocks_buy"] is False
    assert mix_specs({"mix": _mix(block=True, name=R1B)})[0]["stop_blocks_buy"] is True
    with pytest.raises(SystemExit, match="true ／ false"):
        mix_specs({"mix": _mix(block="yes")})
    with pytest.raises(SystemExit, match="損切り係"):
        mix_specs({"mix": _mix(block=True, members=[{"method": BUY, "role": "buy"}, {"method": EXIT, "role": "exit"}])})
    with pytest.raises(SystemExit, match="roles のときだけ"):
        mix_specs({"mix": [{"name": R1, "combine": "mean", "stop_blocks_buy": True, "members": [{"method": BUY}, {"method": EXIT}]}]})


def test_stop_blocks_buy_row_reduces_one_day_holds_and_leaves_other_rows(run):
    """同じ係で R1（止めない）と R1b（止める）を 1 つの実行に並べる: 単体の行と R1 の行は R1 だけの実行と同じ・R1b は保有 1 日の取引が減る。"""
    p = _panel()
    tr = [{"tag": TAG, "symbols": ["S0", "S1", "S2", "S3", "S4"], "budget_usd": 600, "methods": [R1, R1B]}]
    only, *_ = evaluate_trading(p, _feats(p), _exp(_mix(min_hold=3), tr), run)
    both, per_sym, _summary, _daily, extra = evaluate_trading(p, _feats(p), _exp(_mix(min_hold=3) + _mix(min_hold=3, block=True, name=R1B), tr), run)
    plain = both[both["手法"].isin(set(only["手法"]))].reset_index(drop=True)
    pd.testing.assert_frame_equal(only.reset_index(drop=True), plain, check_exact=True, check_dtype=False)
    assert set(both["手法"]) - set(only["手法"]) == {R1B, trader_name(R1B, TAG)}
    t_plain = both[both["手法"] == R1]["取引回数"].sum()
    t_block = both[both["手法"] == R1B]["取引回数"].sum()
    assert 0 < t_block < t_plain, (t_plain, t_block)
    assert names.method_slug(R1B) == "roles-b10-e5-block"
    assert names.method_slug(trader_name(R1B, TAG)) == "roles-b10-e5-block-tr5-600"
