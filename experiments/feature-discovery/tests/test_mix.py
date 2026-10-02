"""合わせる口 `[[trading.mix]]`（rules.md 20-6 の 4）。

⚠ `mix`・`methods` の無い config は経路が 1 行も変わらない ／ 同じモデルを 2 本平均すると元の行と同じ ／
メンバーは自分の表で同じ切れ目の fold を学ぶ ／ 欠けた行は欠け（その銘柄を飛ばす）／ 切れ目が違えば止まる ／
出口% は「返さないメンバーは 100 − 買い%」で平均 ／ トレーダーの形は `methods` で当てる手法を絞れる。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import catalog, names, runs
from cli.run import _mix_fold, evaluate_trading, mix_specs, trader_hold_name, trader_name

ALL = "全部使う（基準）"
MIX = "M1 3本の平均"
TAG = "全銘柄・全部を買える予算"


def _panel(n_days=700, n_sym=6, seed=0, leak=False, drop: str | None = None, shift_days: int = 0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-02", periods=n_days, freq="D", tz="UTC") + pd.Timedelta(days=shift_days)
    rows = []
    for i in range(n_sym):
        y = rng.normal(0.0002, 0.01, n_days)
        d = pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y, "close": 20.0 * (i + 1) * np.exp(np.cumsum(y)),
                          "own_ret_1": np.r_[0.0, y[:-1]], "noise": rng.normal(0, 1, n_days)})
        if leak:
            d["LEAK_y"] = y
        if drop != f"S{i}":
            rows.append(d)
    return pd.concat(rows, ignore_index=True)


def _exp(mix=None, trader=None, name="host"):
    t = {"style": "threshold", "thresholds": [50], "form": "shared"}
    if mix is not None:
        t["mix"] = mix
    if trader is not None:
        t["trader"] = trader
    return {"name": name, "trading": t, "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": [ALL, "乱択（基準）"], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


def _feats(panel):
    return [c for c in panel.columns if c not in ("symbol", "ts", "y", "close")]


def _loader(panels: dict, seen: list | None = None):
    """メンバーの実験名 → (config, 表, 列)。⚠ 本物は `cli.run.load_mix_member`（config と表をディスクから読む）。"""
    def load(experiment: str, leak: bool):
        if seen is not None:
            seen.append((experiment, leak))
        p = panels[experiment]
        return _exp(name=experiment), p, _feats(p)
    return load


def _mix(members=None):
    return [{"name": MIX, "combine": "mean",
             "members": members or [{"method": ALL}, {"experiment": "ext", "method": ALL}]}]


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_mix", {}, seed=0)


def _net(res, method):
    return res[res["手法"] == method].sort_values("fold")["純利bp"].to_numpy()


def test_existing_rows_do_not_change_when_mix_is_added(run):
    """⚠ `mix` は手法を足すだけ。既存の手法の行は 1 ビットも変わらない。"""
    panel = _panel()
    a, *_ = evaluate_trading(panel, _feats(panel), _exp(), run)
    b, *_ = evaluate_trading(panel, _feats(panel), _exp(_mix()), run, mix_loader=_loader({"ext": panel}))
    plain = b[b["手法"].isin(set(a["手法"]))].reset_index(drop=True)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), plain, check_exact=True, check_dtype=False)
    assert set(b["手法"]) - set(a["手法"]) == {MIX}


def test_mean_of_the_same_model_is_that_model(run):
    """同じ表・同じ config のメンバーを外から読んで平均する ＝ 買い% が同じ ＝ 行も同じ（メンバーが同じ fold を学んだ証拠）。"""
    panel = _panel()
    res, *_ = evaluate_trading(panel, _feats(panel), _exp(_mix()), run, mix_loader=_loader({"ext": panel}))
    assert len(_net(res, MIX)) == 5
    np.testing.assert_allclose(_net(res, MIX), _net(res, ALL), rtol=0, atol=1e-9)
    assert catalog.is_trial({"検証方式": "閾値売買", "手法名": MIX})        # ⚠ 合成の行は数える


def test_a_different_member_changes_the_rows(run):
    """違う表（違う列）で学んだメンバーを混ぜると、元の行と同じにはならない（平均が効いている）。"""
    panel = _panel()
    other = panel.assign(noise=np.random.default_rng(9).normal(0, 1, len(panel)))
    res, *_ = evaluate_trading(panel, _feats(panel), _exp(_mix()), run, mix_loader=_loader({"ext": other}))
    assert not np.allclose(_net(res, MIX), _net(res, ALL))


def test_missing_rows_skip_the_symbol(run):
    """メンバーの表に無い銘柄は欠け ＝ 合成の行ではその銘柄を飛ばす（ほかの銘柄は残る）。"""
    panel = _panel()
    logs: list[str] = []
    run.log = logs.append
    res, per_sym, *_ = evaluate_trading(panel, _feats(panel), _exp(_mix()), run,
                                        mix_loader=_loader({"ext": _panel(drop="S5")}))
    syms = set(per_sym[per_sym["手法"] == MIX]["銘柄"])
    assert syms == {f"S{i}" for i in range(5)}
    assert any("欠けた行" in line for line in logs)


def test_members_must_share_the_fold_edges(run):
    panel = _panel()
    with pytest.raises(SystemExit, match="切れ目"):
        evaluate_trading(panel, _feats(panel), _exp(_mix()), run, mix_loader=_loader({"ext": _panel(shift_days=40)}))


def test_leak_flag_reaches_the_loader(run):
    panel = _panel()
    seen: list = []
    evaluate_trading(panel, _feats(panel), _exp(_mix()), run, leak=True, mix_loader=_loader({"ext": panel}, seen))
    assert seen == [("ext", True)]                         # ⚠ 実験ごとに 1 度だけ読む


def test_exit_is_the_mean_of_member_exits():
    """出口% を返さないメンバーは 100 − 買い%（16-1 の 4）。全員が返さなければ None ＝ 既存の経路。"""
    te = pd.DataFrame({"symbol": ["A", "A", "B"], "ts": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-01"])})
    a, b = np.array([60.0, 40.0, 80.0]), np.array([20.0, 60.0, np.nan])
    spec = mix_specs({"mix": [{"name": "X", "members": [{"method": "a"}, {"method": "b"}]}]})

    buy, exits, n_cols, doc = {"a": a, "b": b}, {"a": None, "b": None}, {}, {}
    _mix_fold(spec, {}, te, buy, exits, n_cols, doc, 1, lambda _m: None)
    np.testing.assert_allclose(buy["X"], [40.0, 50.0, np.nan], equal_nan=True)
    assert exits["X"] is None and doc["X"]["欠けた行"] == 1

    stop = np.array([100.0, 0.0, 0.0])
    buy, exits = {"a": a, "b": b}, {"a": stop, "b": None}
    _mix_fold(spec, {}, te, buy, exits, {}, {}, 1, lambda _m: None)
    np.testing.assert_allclose(exits["X"], [(100.0 + 80.0) / 2, (0.0 + 40.0) / 2, np.nan], equal_nan=True)


def test_trader_methods_limit_the_condition(run):
    """`methods`（20-6 の 4 の 5）: トレーダーの形を合成だけに当てる。持ち続ける は出る・ほかの手法の行は増えない。"""
    panel = _panel()
    trader = [{"tag": TAG, "methods": [MIX]}]
    res, *_ = evaluate_trading(panel, _feats(panel), _exp(_mix(), trader), run, mix_loader=_loader({"ext": panel}))
    got = {m for m in res["手法"] if "〔" in m}
    assert got == {trader_name(MIX, TAG), trader_hold_name(TAG)}
    # methods を省けば全部（2026-10-01 の経路）
    res2, *_ = evaluate_trading(panel, _feats(panel), _exp(_mix(), [{"tag": TAG}]), run, mix_loader=_loader({"ext": panel}))
    assert {m for m in res2["手法"] if "〔" in m} == got | {trader_name(ALL, TAG)}
    np.testing.assert_allclose(_net(res, trader_name(MIX, TAG)), _net(res2, trader_name(MIX, TAG)), rtol=0, atol=1e-9)


def test_config_errors(run):
    panel = _panel()
    bad = [
        [{"name": MIX, "members": [{"method": ALL}]}],                                       # 1 本だけ
        [{"name": MIX, "combine": "majority", "members": [{"method": ALL}, {"experiment": "ext", "method": ALL}]}],
        [{"name": "基準 x", "members": [{"method": ALL}, {"experiment": "ext", "method": ALL}]}],
        [{"name": "a〔b〕", "members": [{"method": ALL}, {"experiment": "ext", "method": ALL}]}],
        [{"name": MIX, "members": [{"method": ALL}, {"method": ALL}]}],                      # 同じメンバー
        [{"name": MIX, "members": [{"method": ALL}, {"experiment": "ext", "method": "無い手法"}]}],
        [{"name": MIX, "members": [{"method": "無い手法"}, {"experiment": "ext", "method": ALL}]}],
    ]
    for mix in bad:
        with pytest.raises(SystemExit):
            evaluate_trading(panel, _feats(panel), _exp(mix), run, mix_loader=_loader({"ext": panel}))
    with pytest.raises(SystemExit):
        evaluate_trading(panel, _feats(panel), _exp(_mix(), [{"tag": TAG, "methods": []}]), run,
                         mix_loader=_loader({"ext": panel}))
    per_symbol = _exp(_mix())
    per_symbol["trading"]["form"] = "per_symbol"
    with pytest.raises(SystemExit):
        evaluate_trading(panel, _feats(panel), per_symbol, run, mix_loader=_loader({"ext": panel}))


def test_name_spellings_and_family():
    assert names.method_slug(MIX) == "mix3-mean"
    assert names.method_slug(trader_name(MIX, "5本・$300")) == "mix3-mean-tr5"
    assert MIX in catalog.mix_names()
    assert catalog.canonical(trader_name(MIX, TAG)) == (None, trader_name(MIX, TAG))
