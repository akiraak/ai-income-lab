"""時系列分類器の配線（[プラン](../../../docs/plans/tsc-minirocket-hydra-quant.md) §4）。

⚠ **ここで落としたいのは 4 つ。**
  1. `seq` 層の列が「k 日前のリターン」になっていること（向きの取り違え）
  2. ⚠ **窓を道筋に直すときに、古い → 新しいの向きが正しいこと**（逆に並べても動いてしまう）
  3. 検知器の出力の契約（買い% が 0〜100・検証の行数ぶん・使う列は窓と `LEAK_` だけ。rules.md 15-1）
  4. ⚠ **fit が訓練だけで行われること**（検証の行を減らしても、残った行の買い% が変わらない）と、leak 対照が跳ねること
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import contracts, registry, runs
from ail.detectors import tsc
from ail.features import labels, own, seq
from cli.run import evaluate_trading
import ail.bootstrap  # noqa: F401

NAMES = {"T1 MiniRocket（60日窓）": 9996, "T2 Hydra（60日窓）": 3072, "T3 QUANT（60日窓）": 653}


def _bars(n, seed):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.011, n)))
    ts = pd.date_range("2015-01-02", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"ts": ts, "close": c})


def _panel(n_days=420, n_sym=3, seed=0, leak=False):
    """`seq` 層とラベルを持つ合成パネル（本物の表と同じ列の形。`cli/build.py` と同じく dropna を 1 か所で当てる）。"""
    rows = []
    for i in range(n_sym):
        b = _bars(n_days, seed + i)
        d = pd.concat([b, seq.build_one(b), labels.build_one(b, 1, leak=leak)], axis=1)
        d["own_ret_1"] = np.log(b["close"]).diff()
        d["symbol"] = f"S{i}"
        rows.append(d)
    out = pd.concat(rows, ignore_index=True).sort_values(["ts", "symbol"])
    return out.dropna().reset_index(drop=True)


def _ctx():
    return {"seed": 0, "model": registry.resolve("model", "Ridge"), "k": 60}


# --- 1. seq 層 ------------------------------------------------------------

def test_seq_columns_are_the_returns_k_days_ago():
    b = _bars(200, 0)
    x = seq.build_one(b)
    r = np.log(b["close"]).diff()
    assert list(x.columns) == [seq.column(k) for k in range(seq.WINDOW)]
    for k in (0, 1, 59):
        assert np.allclose(x[seq.column(k)].dropna(), r.shift(k).dropna())
    # ⚠ **r0 は own 層の `own_ret_1` と同じ値**（own.py の式に触らないために別の層に置いた）
    o = own.build_one(b.assign(open=b["close"], high=b["close"], low=b["close"], volume=1.0))
    assert np.allclose(x[seq.column(0)].dropna(), o["own_ret_1"].dropna())
    # ⚠ 窓の列は特徴量として扱われる（メタではない）
    assert all(not contracts.is_meta(c) for c in x.columns)


def test_window_columns_stop_when_a_column_is_missing():
    feats = [seq.column(k) for k in range(seq.WINDOW - 1)]
    with pytest.raises(SystemExit):
        seq.window_columns(feats)


# --- 2. 窓 → 道筋の向き ---------------------------------------------------

def test_window_path_runs_from_oldest_to_newest_and_ends_at_zero():
    b = _bars(300, 1)                              # ⚠ 頭の 60 行は窓が埋まらず dropna で落ちる（残り 240 行）
    d = pd.concat([b, seq.build_one(b)], axis=1).dropna().reset_index(drop=True)
    feats = contracts.feature_columns(d)
    w = tsc.window_paths(d, feats)
    assert w.shape == (len(d), 1, seq.WINDOW) and w.dtype == np.float32
    lc = np.log(d["close"].to_numpy())
    i = 150                                        # 表の i 行目 ＝ 足 i
    full = np.log(b["close"].to_numpy())
    j = int(np.flatnonzero(b["ts"] == d["ts"].iloc[i])[0])
    expect = full[j - 59:j + 1] - full[j]          # ⚠ log c[i−59+j] − log c[i]（古い → 新しい）
    assert np.allclose(w[i, 0], expect, atol=1e-5)
    assert w[i, 0, -1] == 0.0
    assert np.isclose(lc[i] - lc[i - 1], np.diff(w[i, 0])[-1], atol=1e-5)


# --- 3. 検知器の出力の契約 -------------------------------------------------

@pytest.fixture(scope="module")
def split():
    p = _panel()
    cut = pd.to_datetime(p["ts"]).quantile(0.7)
    return p, p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


@pytest.mark.parametrize("name", list(NAMES))
def test_detector_returns_one_buy_pct_per_test_row(name, split):
    p, tr, te = split
    fn = registry.resolve("detector", name)
    buy, doc = fn(tr, te, contracts.feature_columns(p), _ctx())
    assert len(buy) == len(te)
    assert float(np.min(buy)) >= 0.0 and float(np.max(buy)) <= 100.0
    assert doc["変換の出力の列"] == NAMES[name]                 # ⚠ aeon の既定のまま（水準を振っていない）
    # ⚠ **使う列は窓だけ**（own 35 列を混ぜると「入力の形」の差として読めない）
    assert doc["columns"] == [seq.column(k) for k in range(seq.WINDOW)]


@pytest.mark.parametrize("name", ["T3 QUANT（60日窓）", "T1 MiniRocket（60日窓）"])
def test_fit_uses_only_the_training_rows(name, split):
    """⚠ **検証の行を半分に減らしても、残った行の買い% は変わらない**（検証で何かを fit していたら変わる）。

    ⚠ **許容差 1e-4 点の理由**【実測 2026-09-15】: 変換の出力と標準化の後は ⚠ **ビット単位で一致**するが、
    ⚠ **Ridge の予測（float32 の行列積）は行列の形で丸めが変わり**、予測で 2〜4e-8・買い% で最大 4.5e-6 点ずれる
    （1 行ずつ予測した値とも同じ大きさでずれる）。⚠ **検証で fit していれば 1 点の桁でずれる**ので、この許容差で見分けられる。
    """
    p, tr, te = split
    fn = registry.resolve("detector", name)
    feats = contracts.feature_columns(p)
    full, _ = fn(tr, te, feats, _ctx())
    half = len(te) // 2
    part, _ = fn(tr, te.iloc[:half].reset_index(drop=True), feats, _ctx())
    assert np.allclose(full[:half], part, rtol=0, atol=1e-4)


# --- 4. leak 対照 ----------------------------------------------------------

@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_tsc", {}, seed=0)


def test_leak_column_is_passed_and_makes_the_edge_jump(run):
    """⚠ **`LEAK_` の列を変換の出力の横に足すので、leak 対照で上乗せが跳ねる**（rules.md 13-10）。"""
    p = _panel(n_days=600, n_sym=4, leak=True)
    feats = contracts.feature_columns(p)
    exp = {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"},
           "validation": {"split": "walk_forward_dates", "folds": 3, "seed": 0},
           "k": 60, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
           "detectors": ["T3 QUANT（60日窓）"], "model": "Ridge",
           "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}
    res, *_ = evaluate_trading(p, feats, exp, run)
    m = res[(res["手法"] == "T3 QUANT（60日窓）") & (res["閾値"] == 50.0)].set_index("fold")["純利bp"]
    bh = res[(res["手法"] == "基準 常に上（ドリフト）") & (res["閾値"] == 50.0)].set_index("fold")["純利bp"]
    edge = m - bh
    assert (edge > 1000.0).all(), edge.to_dict()
