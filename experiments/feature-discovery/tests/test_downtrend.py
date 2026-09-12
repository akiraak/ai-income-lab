"""下降トレンドの検知の配線（[プラン](../../../docs/plans/archive/downtrend-detection.md)）。

⚠ **ここで落としたいのは 4 つ。**
  1. 検知器の出力の契約（買い% が 0〜100・検証の行数ぶん。rules.md 14-1）
  2. ⚠ **較正と門の holdout が、長いラベルのぶんパージされていること**（重なると楽観側に外れる）
  3. ⚠ **乱択ゲートが保有日数を保つこと**（保たないと「休んだだけ」との分離が効かない。14-6 b）
  4. leak 対照が跳ねること（13-10。⚠ **スケールごとの答えを混ぜて初めて長いスケールでも跳ねる**）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import contracts, registry, runs
from ail.detectors import scale
from ail.features import labels, trend
from ail.models.holdout import date_holdout, purge_days
from ail.validation import checks, gate, simulate as sim
from cli.run import evaluate_trading
import ail.bootstrap  # noqa: F401

# ⚠ 本物の窓（20/60/200）は合成パネルでは回らないので、⚠ **形だけ同じ短い窓**で通す
WINDOWS = (5, 10, 20)


def _panel(n_days=900, n_sym=4, seed=0, leak=False, windows=WINDOWS):
    """`trend` 層とスケールのラベルを持つ合成パネル（本物の表と同じ列の形）。"""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2015-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        r = rng.normal(0.0003, 0.011, n_days)
        close = 100 * np.exp(np.cumsum(r))
        bars = pd.DataFrame({"ts": ts, "close": close})
        x = trend.build_one(bars, windows)
        y = labels.build_scales(bars, windows, leak=leak)
        d = pd.concat([bars, x, y], axis=1)
        d["symbol"] = f"S{i}"
        d["y"] = np.r_[r[1:], np.nan]                 # 1 日先の損益（rules.md 8 章）
        d["own_ret_1"] = np.r_[0.0, r[:-1]]
        rows.append(d)
    out = pd.concat(rows, ignore_index=True).sort_values(["ts", "symbol"])
    # ⚠ **本物の表と同じく `dropna` を 1 か所で当てる**（`cli/build.py`）。
    # ⚠ **頭は助走・尻はスケールのラベルで削れる** — 両端が削れるのは表の性質であって不具合ではない
    return out.dropna().reset_index(drop=True)


def _feats(panel):
    return contracts.feature_columns(panel)


def _exp(detectors, windows=WINDOWS):
    return {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"},
            "validation": {"split": "walk_forward_dates", "folds": 3, "seed": 0},
            "k": 4, "cost_bp": 5.0,
            "horizon_min": purge_days(windows[-1]) * 1440.0, "bar_minutes": 1440.0,
            "detectors": detectors, "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_downtrend", {}, seed=0)


# --- 1. 出力の契約（rules.md 14-1）--------------------------------------

def test_detector_returns_one_buy_pct_per_test_row():
    """⚠ **検知器が返すのは買い% 1 本だけ。** 0〜100 に収まり、検証の行数と一致する。"""
    p = _panel()
    cut = pd.to_datetime(p["ts"]).quantile(0.7)
    tr, te = p[p["ts"] < cut], p[p["ts"] >= cut]
    ctx = {"seed": 0, "model": registry.resolve("model", "Ridge"), "k": 4}
    for w in WINDOWS:
        buy, doc = scale.scale_gate(w, tr, te, _feats(p), ctx)
        assert len(buy) == len(te)
        assert float(np.nanmin(buy)) >= 0.0 and float(np.nanmax(buy)) <= 100.0
        assert doc["scale"] == w and doc["target"] == f"{labels.SCALE_PREFIX}{w}"
        # ⚠ **そのスケールの列しか使わない**（列の差ではなくスケールの差を見るため）
        assert all(c.startswith(f"{trend.PREFIX}trend{w}_") for c in doc["columns"])


def test_classic_filter_is_the_sign_of_the_moving_average_gap():
    """古典フィルタは ⚠ **移動平均の上か下かだけ**（学習しない）。"""
    p = _panel()
    cut = pd.to_datetime(p["ts"]).quantile(0.7)
    tr, te = p[p["ts"] < cut], p[p["ts"] >= cut]
    buy, doc = scale.classic_filter(20, tr, te, _feats(p), {"seed": 0})
    assert set(np.unique(buy)) <= {0.0, 100.0}
    assert np.array_equal(buy > 50, te[f"{trend.PREFIX}trend20_dist"].to_numpy() > 0)
    assert doc["columns"] == []          # ⚠ 学習に使う列が無い ＝ 「本数」は 0


# --- 2. holdout のパージ ------------------------------------------------

def test_date_holdout_purges_by_the_label_length():
    """⚠ **頭の終わり ＋ W 日 ≤ 尻の始まり。** 重なると較正も門の値も楽観側に外れる。"""
    ts = pd.Series(pd.date_range("2020-01-01", periods=4000, freq="D", tz="UTC"))
    head, hold = date_holdout(ts, label_bars=200)
    gap = ts[hold].min() - ts[head].max()
    assert gap >= pd.Timedelta(days=purge_days(200))
    assert purge_days(200) == 300.0                       # 営業日 200 本 → 暦 300 日（保守側）


def test_date_holdout_gives_up_when_training_is_thin():
    """⚠ **切れないときは None**（素通しして呼び元が決める）。"""
    ts = pd.Series(pd.date_range("2020-01-01", periods=300, freq="D", tz="UTC"))
    assert date_holdout(ts, label_bars=200) is None


# --- 3. 乱択ゲート（rules.md 14-6 b）------------------------------------

def test_random_gate_keeps_the_number_of_held_days():
    """⚠ **休む日数はそのまま・休む日だけをずらす。** 売買回数は巡回の継ぎ目で ±1 まで。"""
    rng = np.random.default_rng(0)
    n = 400
    pos = (np.arange(n) % 90 < 55).astype(int)            # 保有と見送りが交互に来る系列
    y = rng.normal(0.0002, 0.01, n)
    base = sim.simulate(pos * 100.0, y, 50.0, 5.0)
    r = sim.shifted_gate(base["pos"], y, 5.0, rng)
    assert int(r["pos"].sum()) == int(base["pos"].sum())  # ⚠ 保有日数はぴったり同じ
    assert abs(r["trades"] - base["trades"]) <= 1
    assert not np.array_equal(r["pos"], base["pos"])      # 日付の対応は壊れている


def test_random_gate_diagnostic_is_recorded_and_not_used_for_judgement(run):
    """乱択ゲートは checks に載るが、⚠ **判定の量（対 B&H 上乗せ）には入らない。**"""
    p = _panel()
    exp = _exp(_detector_names(WINDOWS))
    res, per_sym, summary, daily, extra = evaluate_trading(p, _feats(p), exp, run)
    assert "乱択ゲート純利bp" in res.columns
    assert set(extra) == {"hold", "rand"}
    doc = checks.compute_trading(res, summary, per_sym, daily, exp, n_trials=70, extra=extra)
    for e in doc["by_threshold"].values():
        rg = e["random_gate"]
        assert "採否には使わない" in rg["注記"]
        assert rg["folds"] == e["edge_vs_bh"]["folds"]


# --- 4. leak 対照（rules.md 13-10）--------------------------------------

def _detector_names(windows):
    """合成パネルの窓に合わせた検知器を、その場で登録して使う。"""
    names = []
    for w in windows:
        name = f"TEST スケール {w}"
        if name not in registry.available("detector"):
            registry.register("detector", name)(
                lambda tr, te, feats, ctx, _w=w: scale.scale_gate(_w, tr, te, feats, ctx))
        names.append(name)
    return names


def test_leak_makes_the_edge_jump(run):
    """⚠ **スケールごとの答えを混ぜたら跳ねること。** 跳ねなければ配線が壊れている。"""
    p = _panel(leak=True)
    exp = _exp(_detector_names(WINDOWS))
    res, per_sym, summary, daily, extra = evaluate_trading(p, _feats(p), exp, run)
    doc = checks.compute_trading(res, summary, per_sym, daily, exp,
                                 n_trials=70, leak=True, extra=extra)
    for th, e in doc["by_threshold"].items():
        assert e["edge_vs_bh"]["mean_bp"] > 50.0, th
        assert e["edge_vs_bh"]["positive"] == e["edge_vs_bh"]["folds"], th


def test_without_leak_the_edge_stays_small(run):
    """先読みの無い雑音の表では跳ねない（跳ねたらまず配線を疑う。rules.md 11 章 規約 7）。"""
    p = _panel()
    exp = _exp(_detector_names(WINDOWS))
    res, per_sym, summary, daily, extra = evaluate_trading(p, _feats(p), exp, run)
    doc = checks.compute_trading(res, summary, per_sym, daily, exp, n_trials=70, extra=extra)
    for e in doc["by_threshold"].values():
        assert abs(e["edge_vs_bh"]["mean_bp"]) < 300.0


def test_gate_is_measured_for_detectors(run):
    """⚠ **門は検知器でも測る**（値は記録するだけ。回すかどうかはプラン §2-6 の決定）。"""
    p = _panel()
    exp = _exp(_detector_names(WINDOWS))
    doc = gate.evaluate_gate(p, _feats(p), exp, run=None)
    assert doc["kind"] == "detector"
    assert set(doc["methods"]) == set(exp["detectors"])
    for m in doc["methods"].values():
        assert m["width_pt"] is None or 0.0 <= m["width_pt"] <= 100.0


# --- 5. エピソード表（rules.md 14-8）------------------------------------

def test_episode_table_finds_the_declines_and_is_marked_as_diagnostic():
    """⚠ **山 → 谷が下限を超えた区間だけ拾う。** ⚠ **採否に使わないことを注記で明示する。**"""
    ts = pd.date_range("2020-01-01", periods=300, freq="D", tz="UTC")
    step = np.r_[np.full(100, 20.0), np.full(60, -60.0), np.full(140, 20.0)]   # 上げ → 下げ → 回復
    bh = [pd.Series(step, index=ts)]
    method = [pd.Series(np.zeros(300), index=ts)]          # ⚠ 何もしない ＝ 下げを丸ごと避けた形
    hold = [pd.Series(np.zeros(300), index=ts)]
    ep = checks._episodes(bh, method, hold, min_drop_bp=1000.0)
    assert ep["回数"] == 1
    e = ep["episodes"][0]
    assert e["B&Hbp"] == pytest.approx(-3600.0)
    assert e["上乗せbp"] == pytest.approx(3600.0)          # 休んだぶんがそのまま上乗せ
    assert e["保有日率"] == 0.0
    assert "検定に使わない" in ep["注記"]
