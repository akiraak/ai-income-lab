"""登録簿 `20261104-perm-split-timing`（`cli.perm_split`）。⚠ 合成の表だけ・本物の DB は読まない。"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from cli import perm_split as ps
from cli.run import evaluate_trading


def _frame(n_days=300, n_sym=6, n_folds=3, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2020-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for f in range(1, n_folds + 1):
        for s in range(n_sym):
            rows.append(pd.DataFrame({"fold": f, "ts": ts + pd.Timedelta(days=(f - 1) * n_days), "symbol": f"S{s}"}))
    fr = pd.concat(rows, ignore_index=True)
    y = rng.normal(0.0003, 0.01, len(fr))
    return fr, y


def test_perm_xs_keeps_each_day_multiset():
    fr, _y = _frame()
    v = np.random.default_rng(1).uniform(0, 100, len(fr))
    v[5] = np.nan
    out = ps.permute_within(v, ps.group_codes(fr, "perm_xs"), np.random.default_rng(0))
    assert np.isnan(out[5]) and np.isnan(out).sum() == 1          # NaN の行は動かない
    assert not np.array_equal(np.nan_to_num(out), np.nan_to_num(v))
    for _k, idx in fr.groupby(["fold", "ts"]).indices.items():
        assert np.array_equal(np.sort(out[idx]), np.sort(v[idx]), equal_nan=True)


def test_perm_ts_keeps_each_symbol_multiset_and_pairs():
    fr, _y = _frame()
    rng = np.random.default_rng(2)
    b = rng.uniform(0, 100, len(fr))
    e = b + 1000.0                                                  # 組のまま動くかを見る印
    out = ps.permute_within(np.column_stack([b, e]), ps.group_codes(fr, "perm_ts"), np.random.default_rng(0))
    assert np.array_equal(np.sort(out[:, 1] - out[:, 0]), np.sort(e - b))   # 組のまま（差の集まりが同じ）
    assert np.allclose(out[:, 1] - out[:, 0], 1000.0)
    for _k, idx in fr.groupby(["fold", "symbol"]).indices.items():
        assert np.array_equal(np.sort(out[idx, 0]), np.sort(b[idx]))
        assert not np.array_equal(out[idx, 0], b[idx])


def test_leak_like_buy_pct_is_near_one_on_both():
    """未来の y にそろえた買い% は、どちらを壊しても上乗せが減る（配線の確かめ）。"""
    fr, y = _frame()
    fr["買い%|L"] = np.where(y > 0, 90.0, 10.0)
    d = ps.perm_split(fr, y, "L", 50.0, 5.0, n=30, seed=0)
    assert d["edge_bp"] > 0
    assert d["perm_xs"]["quantile"] > 0.95 and d["perm_ts"]["quantile"] > 0.95


def test_random_buy_pct_is_mid_range():
    fr, y = _frame()
    qs = []
    for s in range(6):
        fr["買い%|R"] = np.random.default_rng(100 + s).uniform(0, 100, len(fr))
        d = ps.perm_split(fr, y, "R", 50.0, 5.0, n=30, seed=s)
        qs += [d["perm_xs"]["quantile"], d["perm_ts"]["quantile"]]
    assert 0.25 < np.mean(qs) < 0.75
    assert min(qs) < 0.9 or max(qs) > 0.1                           # 張りつかない


def test_same_seed_same_numbers():
    fr, y = _frame()
    fr["買い%|R"] = np.random.default_rng(7).uniform(0, 100, len(fr))
    a = ps.perm_split(fr, y, "R", 55.0, 5.0, n=5, seed=3)
    b = ps.perm_split(fr, y, "R", 55.0, 5.0, n=5, seed=3)
    assert all(a[k]["mean_bp"] == b[k]["mean_bp"] for k in ps.KINDS)


class _FakeRun:
    """`evaluate_trading` が呼ぶ口だけ（DB に触らない）。"""
    def log(self, *_a, **_k):
        pass

    def fitted(self, *_a, **_k):
        pass

    def selected(self, *_a, **_k):
        pass


def test_unpermuted_frame_reproduces_cli_run_bit_for_bit():
    """`cli.run.evaluate_trading` が残した buy_pct ＋ 表の y で器を回し直すと、result の純利と 1 ビットも違わない。"""
    rng = np.random.default_rng(0)
    ts = pd.date_range("2019-01-02", periods=600, freq="D", tz="UTC")
    parts = []
    for i in range(4):
        y = rng.normal(0.0002, 0.01, len(ts))
        parts.append(pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y,
                                   "own_ret_1": np.r_[0.0, y[:-1]], "noise": rng.normal(0, 1, len(ts))}))
    panel = pd.concat(parts, ignore_index=True)
    exp = {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"},
           "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
           "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
           "selectors": ["全部使う（基準）"], "model": "Ridge",
           "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}
    res, *_rest, extra = evaluate_trading(panel, ["own_ret_1", "noise"], exp, _FakeRun())
    frame = extra["buy_pct"]
    y = frame[["ts", "symbol"]].merge(panel[["ts", "symbol", "y"]], on=["ts", "symbol"], how="left")["y"].to_numpy()
    folds = ps.fold_index(frame)
    for th in (50.0, 55.0, 60.0):
        drift = ps.fold_nets(frame, np.full(len(frame), 100.0), None, y, th, 5.0, folds)
        for m in ("全部使う（基準）", ps.DRIFT, "基準 直前リターンの符号"):
            nets = ps.fold_nets(frame, frame[ps.BUY + m].to_numpy(), None, y, th, 5.0, folds)
            ref = res[(res["手法"] == m) & (res["閾値"] == th)].set_index("fold")["純利bp"]
            assert {f: nets[f] for f in ref.index} == ref.to_dict()      # ⚠ ぴったり（許容差なし）
        d = ps.perm_split(frame, y, "全部使う（基準）", th, 5.0, n=0, drift=drift)
        r = res[res["閾値"] == th].pivot(index="fold", columns="手法", values="純利bp")
        assert d["edge_bp"] == float(np.mean([r.loc[f, "全部使う（基準）"] - r.loc[f, ps.DRIFT] for f in sorted(r.index)]))
