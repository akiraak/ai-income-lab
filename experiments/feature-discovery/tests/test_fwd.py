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


# --- 25-5. H5: 1 本で買い% と保有日数を出す ----------------------------------------------

def _panel_pick(leak=False):
    """`y_fwd_5 ／ 10 ／ 20 ／ 40` を持つ合成パネル（H5 は 4 本の期間のラベルが要る）。"""
    p = _panel(leak=leak)
    for w in (5, 20, 40):
        col = []
        for _s, g in p.groupby("symbol", sort=False):
            lc = np.log(g["close"].to_numpy())
            col.append(pd.Series(np.r_[lc[w:] - lc[:-w], [np.nan] * w], index=g.index))
        p[f"y_fwd_{w}"] = pd.concat(col)
    return p.dropna().reset_index(drop=True)


def test_h5_returns_buy_hold_and_zero_exit_and_leaves_h1_untouched():
    """4 つ返す（買い%, 出口% 0, 保有日数 ∈ {5,10,20,40}, 記録）。買い% は 4 本の最大・保有日数は最大だった期間（rules.md 25-5 の 1・2）。"""
    assert fwd.PICK_WINDOWS == (5, 10, 20, 40) and fwd.WINDOW == 10
    fn = registry.resolve("detector", fwd.PICK_NAME)
    p = _panel_pick()
    tr, te = _split(p)
    ctx = _ctx()
    bp, ep, hd, doc = fn(tr, te, _feats(p), ctx)
    assert len(bp) == len(ep) == len(hd) == len(te)
    assert np.all(ep == 0.0) and set(np.unique(hd)) <= {5, 10, 20, 40}
    # 4 本を別々に出して最大と突き合わせる（同じ ctx・同じ学び方）
    singles = {w: np.asarray(fwd.forward_gate(w, tr, te, _feats(p), _ctx())[0], dtype=float) for w in fwd.PICK_WINDOWS}
    stack = np.vstack([singles[w] for w in fwd.PICK_WINDOWS])
    np.testing.assert_allclose(bp, stack.max(axis=0))
    np.testing.assert_array_equal(hd, np.asarray(fwd.PICK_WINDOWS)[np.argmax(stack, axis=0)])
    assert doc["windows"] == [5, 10, 20, 40] and sum(doc["選んだ期間の分布"].values()) == len(te)
    assert fwd.HOLD_KEY not in ctx                       # 検知器は ctx に置かない（置くのは fold_buy_pct）


def test_fold_buy_pct_puts_hold_days_in_ctx_and_drain_empties_it(run):
    from cli.run import fold_buy_pct
    p = _panel_pick()
    tr, te = _split(p)
    te = te.reset_index(drop=True)
    ctx = _ctx()
    dets = registry.resolve_all("detector", [fwd.PICK_NAME, NAME])
    exp = {**_exp(), "detectors": [fwd.PICK_NAME, NAME]}
    buy, exits, _n, _doc = fold_buy_pct(tr, te, _feats(p), exp, ctx, model=registry.resolve("model", "Ridge"),
                                        selectors={}, detectors=dets, baselines={}, form="shared", k=8,
                                        groups=te.groupby("symbol").indices, f=1, picked=[], log=lambda *_: None)
    holds = fwd.drain_holds(ctx)
    assert set(holds) == {fwd.PICK_NAME} and len(holds[fwd.PICK_NAME]) == len(te)   # H1 は置かない
    assert fwd.drain_holds(ctx) == {} and fwd.HOLD_KEY not in ctx
    assert exits[NAME] is None and np.all(exits[fwd.PICK_NAME] == 0.0)


# --- 25-7. 土台を引いた買い%（2026-10-19。案 A） ----------------------------------------------

def test_centered_names_are_registered_and_plain_gates_untouched():
    """登録名 4 つ（`… 土台差`）が解けて、既存の H1〜H5 の登録名と `center` の既定（False）は変わっていない。"""
    assert fwd.CENTERED_WINDOWS == {"H1": 10, "H2": 5, "H3": 20, "H4": 40}
    assert fwd.CENTERED_NAMES == ("H1 先10日ゲート（全列・学習・土台差）", "H2 先5日ゲート（全列・学習・土台差）",
                                  "H3 先20日ゲート（全列・学習・土台差）", "H4 先40日ゲート（全列・学習・土台差）")
    for n in fwd.CENTERED_NAMES:
        registry.resolve("detector", n)
    assert NAME in registry.available("detector") and fwd.EXTRA_NAMES == ("H2 先5日ゲート（全列・学習）", "H3 先20日ゲート（全列・学習）", "H4 先40日ゲート（全列・学習）")
    import inspect
    assert inspect.signature(fwd.forward_gate).parameters["center"].default is False


def test_center_by_base_maps_the_base_to_50_and_keeps_order():
    """π と同じ確率は 50・単調・0〜100 の内。π ＝ 0.5 なら何も変わらない。"""
    b = np.array([0.0, 30.0, 50.0, 58.0, 70.0, 100.0])
    c = fwd.center_by_base(b, 0.58)
    assert abs(c[3] - 50.0) < 1e-9 and np.all(np.diff(c) > 0) and c.min() >= 0 and c.max() <= 100
    assert c[2] < 50.0 and c[4] > 50.0
    assert np.allclose(fwd.center_by_base(np.array([20.0, 50.0, 80.0]), 0.5), [20.0, 50.0, 80.0])
    assert np.allclose(fwd.center_by_base(np.array([60.0]), 0.6), [50.0])


def test_centered_gate_equals_the_plain_gate_passed_through_the_base():
    """`H2 …（土台差）` ＝ `H2 …` の買い% を訓練の上がる割合 π で中心化したもの。記録に `中心化`・`土台`。素の H2 の値と記録は変わらない。"""
    p = _panel_multi()
    tr, te = _split(p)
    plain, pdoc = registry.resolve("detector", fwd.name_of("H2", 5))(tr, te, _feats(p), _ctx())
    cent, cdoc = registry.resolve("detector", fwd.centered_name_of("H2", 5))(tr, te, _feats(p), _ctx())
    assert len(cent) == len(te) and np.nanmin(cent) >= 0 and np.nanmax(cent) <= 100
    assert cdoc["中心化"] is True and abs(cdoc["土台"] - pdoc["上がる割合"]) < 1e-9 and "中心化" not in pdoc
    base = float((tr["y_fwd_5"].dropna() > 0).mean())          # ⚠ 記録の「上がる割合」は 4 桁に丸めてあるので、π は訓練の行から取り直す
    assert np.allclose(cent, fwd.center_by_base(plain, base))
    assert cdoc["target"] == "y_fwd_5" and cdoc["columns"] == pdoc["columns"]
    # 素の H2 を続けて呼んでも同じ（中心化が共有の状態を汚さない）
    again, adoc = registry.resolve("detector", fwd.name_of("H2", 5))(tr, te, _feats(p), _ctx())
    assert np.allclose(again, plain) and adoc == pdoc
