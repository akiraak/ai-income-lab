"""レジストリ `20261114-persist-signal-power`（2026-11-14・手動）: `cli.inject_power` の種類 persist ＝ 何十日も続く局面の植え込み。

⚠ 見るもの: 連鎖は ±1・種で決まる・平均滞在がおよそ L ／ 共通の局面は同じ日の全銘柄が同じ・銘柄ごとの局面は銘柄で違う ／
   本物の y を残す（k 0）・y の差 ＝ k × 状態・種が同じなら k をまたいで列は同じ ／ 水準は事前固定 ／
   `pp.one` の `detail` は既定で項目を増やさない（2026-11-13 の出力の形のまま）・足しても上乗せと判定は同じ。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import config
from ail.validation import splits
from cli import inject_power as ip
from cli import planted_power as pp


def _panel(n_days=700, n_sym=6, seed=0, drop=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-01", periods=n_days, freq="B", tz="UTC")
    rows = []
    for j in range(n_sym):
        r = rng.normal(0.0003, 0.01, n_days)
        df = pd.DataFrame({"symbol": f"S{j}", "ts": ts, "close": 50 * np.exp(np.cumsum(r)), "y": np.r_[r[1:], np.nan]})
        df["own_ret_1"] = r
        df["own_x0"] = rng.normal(size=n_days)
        rows.append(df.iloc[drop * j:])                                       # 銘柄ごとに始まりをずらせる
    p = pd.concat(rows, ignore_index=True).dropna(subset=["y"])
    return p.sample(frac=1.0, random_state=1).reset_index(drop=True)           # ⚠ 行の並びに頼らないことを見る


def test_chain_is_pm1_seeded_and_dwell_matches():
    a = ip.chain(200_000, 20, np.random.default_rng(1))
    assert set(np.unique(a)) == {-1.0, 1.0}
    assert np.array_equal(a, ip.chain(200_000, 20, np.random.default_rng(1)))
    for dwell in ip.DWELLS:
        x = ip.chain(400_000, dwell, np.random.default_rng(dwell))
        runs = np.diff(np.flatnonzero(np.diff(x) != 0))                       # 切り替わりの間隔 ＝ 滞在
        assert abs(runs.mean() / dwell - 1.0) < 0.05
        assert abs(x.mean()) < 0.05                                           # 対称 ＝ ＋1 と −1 が半々
    assert len(ip.chain(0, 20, np.random.default_rng(0))) == 0
    with pytest.raises(ValueError):
        ip.chain(10, 0, np.random.default_rng(0))


def test_common_regime_is_shared_by_date_and_each_differs_by_symbol():
    p = _panel(drop=7)
    c = p.assign(x=ip.persist_column(p, 0, "common", 20))
    assert (c.groupby("ts")["x"].nunique() == 1).all()                         # 同じ日は全銘柄が同じ状態
    day = c.drop_duplicates("ts").sort_values("ts")["x"].to_numpy()
    assert 5 < (np.diff(day) != 0).sum() < 120                                 # 700 日 ÷ 20 ≈ 35 回の切り替わり
    e = p.assign(x=ip.persist_column(p, 0, "each", 20))
    wide = e.pivot(index="ts", columns="symbol", values="x")
    assert (wide.nunique(axis=1) > 1).mean() > 0.9                             # 銘柄ごとに別の連鎖
    for _s, g in e.sort_values("ts").groupby("symbol"):
        assert 5 < (np.diff(g["x"].to_numpy()) != 0).sum() < 120
    with pytest.raises(ValueError):
        ip.persist_column(p, 0, "sector", 20)


def test_inject_persist_keeps_real_returns_and_column_is_shared_across_k():
    p = _panel(n_days=300)
    for unit in ip.UNITS:
        a, b, c = (ip.inject_persist(p, 0.0, 3, unit, 60), ip.inject_persist(p, 8.0, 3, unit, 60),
                   ip.inject_persist(p, 8.0, 4, unit, 60))
        assert np.array_equal(a["y"].to_numpy(), p["y"].to_numpy())           # k 0 ＝ 本物の y のまま
        x = a[pp.PLANTED].to_numpy()
        assert set(np.unique(x)) <= {-1.0, 1.0}
        assert np.array_equal(x, b[pp.PLANTED].to_numpy()) and not np.array_equal(x, c[pp.PLANTED].to_numpy())
        assert np.allclose(b["y"].to_numpy() - p["y"].to_numpy(), 8.0e-4 * x)
        assert not np.array_equal(x, ip.inject_persist(p, 0.0, 3, unit, 20)[pp.PLANTED].to_numpy())   # 期待滞在で列が変わる
    assert not np.array_equal(ip.persist_column(p, 3, "common", 60), ip.persist_column(p, 3, "each", 60))


def test_levels_are_fixed_and_iid_is_untouched():
    assert ip.PERSIST_MULTIPLES == (0.0, 0.1, 1.0, 10.0) and ip.UNITS == ("common", "each") and ip.DWELLS == (20, 60)
    assert ip.MULTIPLES == (0.1, 1.0, 10.0) and ip.THETAS == (50.0, 55.0, 60.0) and ip.SEEDS == 5 and ip.LIMIT_BP_PER_FOLD == 300.0
    p = _panel(n_days=300)
    x = np.random.default_rng([3, 20261108]).standard_normal(len(p))          # 2026-11-13 の作り方のコピー
    got = ip.inject(p, 8.0, 3)
    assert np.array_equal(got[pp.PLANTED].to_numpy(), x)
    assert np.array_equal(got["y"].to_numpy(), p["y"].to_numpy() + 8.0e-4 * np.sign(x))


def _one(detail):
    exp = config.resolve_experiment("trade_own_ridge_a")
    exp.setdefault("horizon_min", 1440.0)
    p = ip.inject_persist(_panel(), 30.0, 0, "common", 20).sort_values(["ts", "symbol"]).reset_index(drop=True)
    feats = ["own_ret_1", "own_x0", pp.PLANTED]
    edges = splits.date_edges(p["ts"], int(exp.get("validation", {}).get("folds", 5)), splits.cut_offset(exp.get("validation", {})))
    return p, (pp.one(p, feats, exp, edges, 50.0, detail=True) if detail else pp.one(p, feats, exp, edges, 50.0))


def test_one_detail_adds_fields_without_changing_the_rest():
    _p, plain = _one(False)
    assert set(plain) == {"edges", "verdict", "corr_buy_planted"}             # 既定 ＝ 2026-11-13 の出力の形のまま
    _p, full = _one(True)
    assert {k: full[k] for k in plain} == plain                               # 足しても上乗せ・判定・相関は同じ
    assert 0.0 <= full["hold_ratio"] <= 1.0 and full["trades_per_fold"] >= 0
    assert 0.0 <= full["planted_pos_share"] <= 1.0
    # 30 bp/日 の続く局面は拾える ＝ 買い% が列を追い、持つ日は局面の半分の近く
    assert full["corr_buy_planted"] > 0.8 and 0.2 < full["hold_ratio"] < 0.8


def test_summary_row_has_the_registered_outputs():
    got = [{"edges": [1.0, -1.0], "verdict": "落とす", "corr_buy_planted": 0.5, "hold_ratio": 0.4, "trades_per_fold": 10.0,
            "planted_pos_share": 0.5},
           {"edges": [3.0, 1.0], "verdict": "採る", "corr_buy_planted": 0.7, "hold_ratio": 0.6, "trades_per_fold": 20.0,
            "planted_pos_share": 0.5}]
    row = ip._summary(got, 2, unit="common", dwell=20)
    assert row["採る"] == 0.5 and row["落とす"] == 0.5 and row["保留"] == 0.0
    assert row["edge_mean_bp"] == 1.0 and row["hold_ratio"] == 0.5 and row["trades_per_fold"] == 15.0
    assert "持つ日 0.50" in ip._line(row) and "取引 15 回/fold" in ip._line(row)


def test_oracle_reads_the_regime_itself():
    """事後に足した読み（学習器なし）: 局面そのものを買い% にする ＝ 持つ日は ＋1 の日だけ・強い局面は「採る」。"""
    exp = config.resolve_experiment("trade_own_ridge_a")
    exp.setdefault("horizon_min", 1440.0)
    base = _panel().sort_values(["ts", "symbol"]).reset_index(drop=True)
    edges = splits.date_edges(base["ts"], int(exp.get("validation", {}).get("folds", 5)), splits.cut_offset(exp.get("validation", {})))
    strong = ip.oracle_one(ip.inject_persist(base, 60.0, 0, "common", 20), exp, edges, 5.0)
    assert strong["verdict"] == "採る" and min(strong["edges"]) > 0
    assert set(strong) == {"edges", "verdict", "corr_buy_planted", "hold_ratio", "trades_per_fold", "planted_pos_share"}
    assert abs(strong["hold_ratio"] - strong["planted_pos_share"]) < 0.02     # 持つのは ＋1 の日（建てる日のずれだけ）
    zero = ip.oracle_one(ip.inject_persist(base, 0.0, 0, "common", 20), exp, edges, 5.0)
    assert zero["hold_ratio"] == strong["hold_ratio"] and zero["trades_per_fold"] == strong["trades_per_fold"]   # 列は k によらない
    assert np.mean(zero["edges"]) < np.mean(strong["edges"])
