"""先 10 営業日を当てにいく検知器の配線（[記録](../../../docs/specs/experiments/forward10-target.md) §0）。

⚠ **ここで落としたいのは 4 つ。**
  1. 出力の契約（買い% が 0〜100・検証の行数ぶん。rules.md 14-1）と、⚠ **説明変数が表の全列**であること（`y_fwd_` は入らない）
  2. ⚠ **fit が訓練の行だけで決まること**（検証の行を変えても買い% が変わらない。3 章 B）
  3. leak 対照が跳ねること（13-10。`LEAK_fwd_10` を混ぜて初めて跳ねる）／ 混ぜなければ跳ねない
  4. ⚠ **既存の検知器（D1〜D4・C1〜C3）の登録名が 1 つも変わっていない**こと（rules.md 10-1）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import contracts, registry, runs
from ail.detectors import fwd, scale
from ail.features import labels
from ail.validation import checks
from cli.run import evaluate_trading
import ail.bootstrap  # noqa: F401

W = fwd.WINDOW
NAME = f"H1 先{W}日ゲート（全列・学習）"


def _panel(n_days=900, n_sym=4, seed=0, leak=False):
    """`own` 風の列 3 本 ＋ `y_fwd_10` を持つ合成パネル（本物の表と同じ列の形。⚠ 雑音）。"""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2015-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        r = rng.normal(0.0003, 0.011, n_days)
        close = 100 * np.exp(np.cumsum(r))
        bars = pd.DataFrame({"ts": ts, "close": close})
        x = pd.DataFrame({"own_ret_1": np.r_[0.0, r[:-1]],
                          "own_ret_5": pd.Series(r).rolling(5).sum().shift(1).to_numpy(),
                          "own_vol_20": pd.Series(r).rolling(20).std().shift(1).to_numpy()})
        y = labels.build_scales(bars, [W], leak=leak)
        d = pd.concat([bars, x, y], axis=1)
        d["symbol"] = f"S{i}"
        d["y"] = np.r_[r[1:], np.nan]                 # 1 日先の損益（rules.md 8 章）
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


def _exp():
    return {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"},
            "validation": {"split": "walk_forward_dates", "folds": 3, "seed": 0},
            "k": 8, "cost_bp": 5.0,
            "horizon_min": W * 1.5 * 1440.0, "bar_minutes": 1440.0,
            "detectors": [NAME], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_fwd", {}, seed=0)


# --- 1. 出力の契約 ---------------------------------------------------------

def test_detector_returns_one_buy_pct_per_test_row_from_all_columns():
    p = _panel()
    tr, te = _split(p)
    buy, doc = registry.resolve("detector", NAME)(tr, te, _feats(p), _ctx())
    assert len(buy) == len(te)
    assert float(np.nanmin(buy)) >= 0.0 and float(np.nanmax(buy)) <= 100.0
    assert doc["scale"] == W and doc["target"] == f"{labels.SCALE_PREFIX}{W}"
    # ⚠ **表の説明変数を全部使う**（選別しない）。⚠ **学習の対象 `y_fwd_` は説明変数に入らない**
    assert doc["columns"] == ["own_ret_1", "own_ret_5", "own_vol_20"]
    assert not any(c.startswith(labels.SCALE_PREFIX) for c in doc["columns"])
    assert doc["source"] in ("holdout", "train", "constant")


def test_stops_when_the_label_is_missing():
    p = _panel().drop(columns=[f"{labels.SCALE_PREFIX}{W}"])
    tr, te = _split(p)
    with pytest.raises(SystemExit, match="label_scales"):
        fwd.forward_gate(W, tr, te, _feats(p), _ctx())


# --- 2. fit は訓練の内側だけ ---------------------------------------------

def test_fit_uses_only_the_training_rows():
    """検証の行を半分に減らしても、残った行の買い% は 1 ビットも変わらない（検証の行で何も学んでいない）。"""
    p = _panel()
    tr, te = _split(p)
    full, _ = fwd.forward_gate(W, tr, te, _feats(p), _ctx())
    half, _ = fwd.forward_gate(W, tr, te.iloc[: len(te) // 2], _feats(p), _ctx())
    assert np.array_equal(full[: len(te) // 2], half)


# --- 3. leak 対照 ----------------------------------------------------------

def test_leak_makes_the_edge_jump(run):
    p = _panel(leak=True)
    assert f"{labels.LEAK_SCALE_PREFIX}{W}" in _feats(p)     # ⚠ 答えの列が説明変数に入っている
    res, per_sym, summary, daily, extra = evaluate_trading(p, _feats(p), _exp(), run)
    doc = checks.compute_trading(res, summary, per_sym, daily, _exp(), n_trials=70, leak=True, extra=extra)
    for th, e in doc["by_threshold"].items():
        assert e["edge_vs_bh"]["mean_bp"] > 50.0, th
        assert e["edge_vs_bh"]["positive"] == e["edge_vs_bh"]["folds"], th


def test_without_leak_the_edge_stays_small(run):
    p = _panel()
    res, per_sym, summary, daily, extra = evaluate_trading(p, _feats(p), _exp(), run)
    doc = checks.compute_trading(res, summary, per_sym, daily, _exp(), n_trials=70, extra=extra)
    for e in doc["by_threshold"].values():
        assert abs(e["edge_vs_bh"]["mean_bp"]) < 300.0


# --- 4. 既存の登録名は変えない ---------------------------------------------

def test_existing_detector_names_are_untouched():
    have = set(registry.available("detector"))
    assert NAME in have
    assert scale.SCALES == {"短期": 20, "中期": 60, "長期": 200}
    for want in ("D1 短期ゲート（20日・学習）", "D2 中期ゲート（60日・学習）", "D3 長期ゲート（200日・学習）",
                 "D4 合成ゲート（3スケール平均）", "C1 短期 SMA20 フィルタ", "C2 中期 SMA60 フィルタ",
                 "C3 長期 SMA200 フィルタ"):
        assert want in have


# --- 5. 先 5 ／ 20 ／ 40 日の登録名と列の絞り（rules.md 25 章。2026-10-18） ---------------------------------

def test_extra_windows_are_registered_and_h1_untouched():
    """新しい登録名 3 つが解けて、`H1` と `WINDOW` は変わっていない（rules.md 10-1）。"""
    assert fwd.WINDOW == 10 and NAME == "H1 先10日ゲート（全列・学習）"
    assert fwd.EXTRA_WINDOWS == {"H2": 5, "H3": 20, "H4": 40}
    for n in fwd.EXTRA_NAMES:
        registry.resolve("detector", n)
    assert fwd.EXTRA_NAMES == ("H2 先5日ゲート（全列・学習）", "H3 先20日ゲート（全列・学習）", "H4 先40日ゲート（全列・学習）")


def _panel_multi(leak=False):
    """`y_fwd_5` と `own_trend20_dd`（損切り係の列の代役）を持つ合成パネル。"""
    p = _panel(leak=leak)
    rng = np.random.default_rng(1)
    p["own_trend20_dd"] = -np.abs(rng.normal(0, 0.05, len(p)))
    y5 = []
    for _s, g in p.groupby("symbol", sort=False):
        lc = np.log(g["close"].to_numpy())
        y5.append(pd.Series(np.r_[lc[5:] - lc[:-5], [np.nan] * 5], index=g.index))
    p["y_fwd_5"] = pd.concat(y5)
    return p


def test_fwd_columns_own_drops_trend_columns_and_keeps_leak():
    """`ctx["fwd_columns"] = "own"` で `own_trend*` を見ない。`LEAK_` はそのまま入る（13-10）。無ければ全列（既存どおり）。"""
    p = _panel_multi(leak=True)
    tr, te = _split(p)
    feats = [c for c in _feats(p) if c not in ("y_fwd_5",)]
    _, doc_all = fwd.forward_gate(5, tr, te, feats, _ctx())
    assert "own_trend20_dd" in doc_all["columns"] and doc_all["fwd_columns"] == "all"
    _, doc_own = fwd.forward_gate(5, tr, te, feats, {**_ctx(), "fwd_columns": "own"})
    assert "own_trend20_dd" not in doc_own["columns"] and doc_own["fwd_columns"] == "own"
    assert any(c.startswith("LEAK_fwd_") for c in doc_own["columns"])
    assert all(c in doc_all["columns"] for c in doc_own["columns"])
    with pytest.raises(SystemExit):
        fwd.forward_gate(5, tr, te, feats, {**_ctx(), "fwd_columns": "trend"})


def test_h2_uses_its_own_window_label():
    """`H2 先5日ゲート` は `y_fwd_5` を学ぶ（`y_fwd_10` は要らない）。買い% は 0〜100・検証の行数ぶん。"""
    p = _panel_multi()
    tr, te = _split(p)
    fn = registry.resolve("detector", fwd.name_of("H2", 5))
    bp, doc = fn(tr, te, _feats(p), _ctx())
    assert len(bp) == len(te) and np.nanmin(bp) >= 0 and np.nanmax(bp) <= 100
    assert doc["target"] == "y_fwd_5" and doc["scale"] == 5
