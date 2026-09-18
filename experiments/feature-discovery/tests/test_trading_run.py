"""`cli.run` の閾値つき売買パスを、合成パネルで端から端まで通す。

⚠ **leak の配線検査**（rules.md 13-10）: 未来を知る列を混ぜた表では対 B&H の上乗せが
跳ね上がる。⚠ **跳ねなければ 較正 → 閾値 → 状態機械 のどこかが壊れている。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import runs
from ail.validation import checks
from cli.run import evaluate_trading
import ail.bootstrap  # noqa: F401


def _panel(n_days=800, n_sym=3, seed=0, leak=False):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        y = rng.normal(0.0002, 0.01, n_days)               # わずかな上ドリフト
        d = pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y,
                          "own_ret_1": np.r_[0.0, y[:-1]],
                          "noise": rng.normal(0, 1, n_days)})
        if leak:
            d["LEAK_y"] = y                                # ⚠ わざとした先読み
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def _exp(form="shared"):
    return {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": form},
            "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": ["全部使う（基準）"], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))       # ⚠ 本物の runs/ を汚さない
    return runs.Run("test_trading", {}, seed=0)


def _feats(panel):
    return [c for c in panel.columns if c not in ("symbol", "ts", "y")]


@pytest.mark.parametrize("form", ["shared", "per_symbol"])
def test_outputs_have_the_promised_shape(run, form):
    """result = 手法 × fold × 閾値、per_symbol = さらに × 銘柄（プラン §Phase 2 の 5）。"""
    panel = _panel()
    res, per_sym, summary, daily, _x = evaluate_trading(panel, _feats(panel), _exp(form), run)
    methods = set(res["手法"])
    assert methods == {"全部使う（基準）", "基準 常に上（ドリフト）", "基準 直前リターンの符号"}
    assert set(res["閾値"]) == {50.0, 55.0, 60.0}
    assert len(res) == 3 * 5 * 3                           # 手法 3 × fold 5 × 閾値 3
    assert set(per_sym["銘柄"]) == {"S0", "S1", "S2"}
    assert len(per_sym) == 3 * 5 * 3 * 3
    assert {"取引回数", "保有日率", "見送り日数"} <= set(per_sym.columns)
    assert "閾値" in summary.columns


def test_buy_and_hold_costs_exactly_5bp_per_fold(run):
    """B&H のコストは 1 fold ちょうど 5bp（13-5。粗利 − 純利 = 5）。"""
    panel = _panel()
    res, _s, _g, _d, _x = evaluate_trading(panel, _feats(panel), _exp(), run)
    bh = res[res["手法"] == "基準 常に上（ドリフト）"]
    assert np.allclose(bh["粗利bp"] - bh["純利bp"], 5.0)
    assert (bh["取引回数"] == 3).all()                      # 銘柄ごとに建て 1 回（清算は回数に入れない）
    assert (bh["保有日率"] == 1.0).all()


def test_higher_theta_never_trades_more(run):
    """閾値を上げると取引回数は増えない（直前符号は回転が多いのでここで効く）。"""
    panel = _panel()
    res, _s, _g, _d, _x = evaluate_trading(panel, _feats(panel), _exp(), run)
    mom = res[res["手法"] == "基準 直前リターンの符号"]
    by = mom.groupby("閾値")["取引回数"].sum()
    assert by[50.0] >= by[55.0] >= by[60.0]


def test_leak_makes_the_edge_jump_in_both_forms(run, tmp_path, monkeypatch):
    """⚠ **上乗せの跳ねで配線を検査する**（13-10）。(A)(B) 両形式で跳ねること。"""
    for form in ("shared", "per_symbol"):
        r = runs.Run(f"leak_{form}", {}, seed=0)
        panel = _panel(leak=True)
        exp = _exp(form)
        res, per_sym, summary, daily, _x = evaluate_trading(panel, _feats(panel), exp, r)
        doc = checks.compute_trading(res, summary, per_sym, daily,
                                     {**exp, "trading": exp["trading"]}, n_trials=70, leak=True)
        for th, e in doc["by_threshold"].items():
            assert e["edge_vs_bh"]["mean_bp"] > 50.0, (form, th)   # ⚠ 跳ねなければ配線が壊れている
            assert e["edge_vs_bh"]["positive"] == e["edge_vs_bh"]["folds"]


def test_without_leak_the_edge_stays_small(run):
    """先読みの無い雑音の表では上乗せは跳ねない（跳ねたらまず配線を疑う。13-10）。

    ⚠ **水準を 50bp から 500bp に緩めた**（2026-09-12。較正の数値解を直したついで）。
    ⚠ **緩める前は「上乗せがちょうど 0」だった** — 買い% が定数に潰れていて、雑音の表では
    ⚠ **全手法が B&H と 1 ビットも違わない売買しかしなかったから**である
    （[buy-pct-width-collapse.md](../../../docs/specs/experiments/buy-pct-width-collapse.md)）。
    ⚠ **直したいまは雑音の表でも建てたり休んだりするので、ドリフトの取り損ねで数百 bp 動く。**
    ⚠ **検査の中身は緩めていない** — この検査が守るのは 13-10 の「先読みが無いのに跳ねたら配線を疑う」で、
    ⚠ **leak は ＋5,182bp・符号 5/5**【実測 2026-09-12】。⚠ **雑音は −172〜＋85bp・符号 1〜2/5** で 60 倍離れている。
    """
    panel = _panel()
    exp = _exp()
    res, per_sym, summary, daily, _x = evaluate_trading(panel, _feats(panel), exp, run)
    doc = checks.compute_trading(res, summary, per_sym, daily, exp, n_trials=70)
    for th, e in doc["by_threshold"].items():
        ed = e["edge_vs_bh"]
        assert abs(ed["mean_bp"]) < 500.0, (th, ed)        # ⚠ leak（＋5,182bp）の 1/10 未満
        # ⚠ **符号が 5/5 揃うのは leak だけ**（`test_leak_makes_the_edge_jump_in_both_forms`）
        assert ed["positive"] < ed["folds"], (th, ed)


def test_calibration_coefficients_are_recorded(run, tmp_path):
    """(a, b) と fit 元が `fitted/` に残る（13-2 の 3。⚠ 次の実行では読み込まない）。"""
    import json
    import os
    panel = _panel()
    evaluate_trading(panel, _feats(panel), _exp(), run)
    d = os.path.join(run.dir, "fitted")
    files = sorted(os.listdir(d))
    assert any(f.startswith("calibration_f") for f in files)
    doc = json.load(open(os.path.join(d, files[0]), encoding="utf-8"))
    cal = doc["全部使う（基準）"]
    assert set(cal) == {"a", "b", "source"} and cal["source"] in ("holdout", "train", "constant")


# --- 既定経路の不変（rules.md 14-4 規約 2。プラン holding-days-distribution §4） ------------------

@pytest.mark.parametrize("form", ["shared", "per_symbol"])
def test_default_path_fingerprint_is_unchanged(run, form):
    """⚠ **保有日数・逆売買の列を足しても、既存の出力は 1 ビットも変わらない。**

    指紋は 2026-09-17 に列を足す前のコード（commit 696cf7b）で取った `tests/fixtures/trading_fingerprint.json`。
    ⚠ **同じ機械で取った指紋**（数値は 6 桁に丸めて比べる）。⚠ **合わなければ配線を疑う** — 直すのは指紋ではなく配線。
    """
    import json
    import os
    from tests import _fingerprint as fp
    want = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures",
                                       "trading_fingerprint.json"), encoding="utf-8"))[form]
    got = fp.fingerprint(run, form)
    for key in ("per_symbol", "result", "summary", "checks"):
        assert got[key] == want[key], (form, key, got["sample"], want["sample"])


def test_holds_csv_has_one_row_per_trade_and_matches_per_symbol(run):
    """holds.csv は 1 取引 1 行。手法 × θ × fold × 銘柄で数えると `per_symbol.csv` の取引回数と一致する。"""
    import os
    panel = _panel()
    res, per_sym, summary, daily, extra = evaluate_trading(panel, _feats(panel), _exp(), run)
    holds = extra["holds"]
    assert set(holds.columns) == {"手法", "閾値", "fold", "銘柄", "建てた日", "保有日数", "強制清算"}
    n = holds.groupby(["手法", "閾値", "fold", "銘柄"]).size().rename("n").reset_index()
    m = per_sym.merge(n, on=["手法", "閾値", "fold", "銘柄"], how="left").fillna({"n": 0})
    assert (m["取引回数"] == m["n"]).all()
    # 保有日数の要約列（末尾に足した 3 列）も holds から出した値と一致
    med = holds.groupby(["手法", "閾値", "fold", "銘柄"])["保有日数"].median().rename("m").reset_index()
    mm = per_sym.merge(med, on=["手法", "閾値", "fold", "銘柄"], how="inner")
    assert np.allclose(mm["保有日数中央値"], mm["m"])
    # 強制清算は 銘柄 × fold × 手法 × θ ごとに最大 1 回
    assert holds.groupby(["手法", "閾値", "fold", "銘柄"])["強制清算"].sum().max() <= 1
    # B&H は毎 fold 1 取引・全部強制清算
    bh = holds[holds["手法"] == "基準 常に上（ドリフト）"]
    assert (bh["強制清算"]).all() and len(bh) == 3 * 5 * 3
    run.holds(holds)
    assert os.path.exists(os.path.join(run.dir, "holds.csv"))


def test_reverse_columns_follow_the_identity(run):
    """`逆売買純利bp` は恒等式（逆の上乗せ ≈ −元の純利 − 2 × コスト ＋ 5）に fold 平均で近い。⚠ **既存列は動かない。**"""
    panel = _panel()
    res, per_sym, summary, daily, extra = evaluate_trading(panel, _feats(panel), _exp(), run)
    assert {"逆売買純利bp", "逆売買取引回数"} <= set(res.columns)
    assert "逆売買純利bp" in per_sym.columns
    r50 = res[res["閾値"] == 50.0].pivot(index="fold", columns="手法")
    m = "全部使う（基準）"
    bh = r50["純利bp"]["基準 常に上（ドリフト）"]
    edge_rev = r50["逆売買純利bp"][m] - bh
    ident = -r50["純利bp"][m] - 2.0 * (r50["粗利bp"][m] - r50["純利bp"][m]) + 5.0
    # 最初の合図までの区間と強制清算の帰属の分だけずれる（1 fold 160 日・3 銘柄の合成表では数 bp）
    assert abs(float((edge_rev - ident).mean())) < 50.0
    doc = checks.compute_trading(res, summary, per_sym, daily, _exp(), n_trials=70, extra=extra)
    e = doc["by_threshold"]["50"]
    assert "reverse" in e and "selection_edge" in e and "holding" in e
    assert e["reverse"]["edge_vs_bh"]["folds"] == 5 and "採否" in e["reverse"]["注記"]
    # S の逆は −S: 逆売買の粗利 − 逆の保有日率 × B&H 粗利 ＝ −S（最初の合図以降。fold 平均で近い）
    assert e["selection_edge"]["folds"] == 5
