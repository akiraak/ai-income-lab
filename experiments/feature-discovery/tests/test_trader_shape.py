"""トレーダーと同じ形で机上にかける（rules.md 20 章）。

⚠ `[trading] trader` の無い config は経路が 1 行も変わらない ／ 2b の予算でどの日も全銘柄が 1 株以上買える ／
2a の枠は欠けた銘柄があっても動かない ／ 判定の相手は同じ条件の「持ち続ける」／ 鍵に〔トレーダー・…〕が残る。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import catalog, names, runs
from ail.validation import checks
from cli.run import evaluate_trading, trader_hold_name, trader_name

TAG_A, TAG_B = "5本・$300", "全銘柄・全部を買える予算"


def _panel(n_days=700, n_sym=6, seed=0, leak=False, late: str | None = None):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        y = rng.normal(0.0002, 0.01, n_days)
        close = 20.0 * (i + 1) * np.exp(np.cumsum(y))       # 値段が動く ＝ 最高値の予算に意味がある
        d = pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y, "close": close,
                          "own_ret_1": np.r_[0.0, y[:-1]], "noise": rng.normal(0, 1, n_days)})
        if leak:
            d["LEAK_y"] = y
        if late == f"S{i}":
            d = d.iloc[n_days // 2:]                         # 途中から上場（2a の T のような銘柄）
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def _exp(trader=None):
    t = {"style": "threshold", "thresholds": [50], "form": "shared"}
    if trader is not None:
        t["trader"] = trader
    return {"trading": t, "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": ["全部使う（基準）", "乱択（基準）"], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


TRADER = [{"tag": TAG_A, "symbols": ["S0", "S1", "S5"], "budget_usd": 300}, {"tag": TAG_B}]


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_trader", {}, seed=0)


def _feats(panel):
    return [c for c in panel.columns if c not in ("symbol", "ts", "y", "close")]


def test_existing_rows_do_not_change_when_trader_is_added(run):
    """⚠ `trader` は行を足すだけ。素の行（既存の鍵にまとまる）は 1 ビットも変わらない。"""
    panel = _panel()
    a, *_ = evaluate_trading(panel, _feats(panel), _exp(), run)
    b, *_ = evaluate_trading(panel, _feats(panel), _exp(TRADER), run)
    plain = b[b["手法"].isin(set(a["手法"]))].reset_index(drop=True)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), plain, check_exact=True, check_dtype=False)


def test_rows_baselines_and_counting(run):
    panel = _panel()
    res, _ps, _s, daily, extra = evaluate_trading(panel, _feats(panel), _exp(TRADER), run)
    want = {trader_name("全部使う（基準）", t) for t in (TAG_A, TAG_B)}
    holds = {trader_hold_name(t) for t in (TAG_A, TAG_B)}
    got = {m for m in res["手法"] if "〔" in m}
    assert got == want | holds                             # ⚠ 乱択（基準）と「基準 」の行は通さない
    assert (res[res["手法"].isin(got)].groupby("手法").size() == 5).all()
    assert len(extra["trader"]) == len(want) * 5
    assert all((m, 50.0) in daily for m in got)
    counted = [m for m in got if catalog.is_trial({"検証方式": "閾値売買", "手法名": m})]
    assert sorted(counted) == sorted(want)                 # ⚠ 持ち続ける は数えない（20-3 の 1）


def test_full_budget_buys_every_symbol_on_every_day(run):
    """2b（20-2）: 予算 ＝ 評価期間の最高値 × 銘柄数 ⇒ 持ち続ける は全銘柄を 1 株以上持つ。"""
    panel = _panel()
    res, _ps, _s, _d, extra = evaluate_trading(panel, _feats(panel), _exp(TRADER), run)
    d = extra["trader"][extra["trader"]["条件"] == TAG_B]
    assert (d["銘柄数"] == 6).all()
    assert (d["株価で見送り"] == 0).all()
    # 持ち続ける ＝ 初日に全銘柄を買い、末尾まで持つ ＝ 投下率は 0 より大きく 1 以下
    assert ((d["持ち続ける投下率"] > 0) & (d["持ち続ける投下率"] <= 1 + 1e-12)).all()
    from ail.validation import simulate as sim
    te = panel[panel["ts"] >= panel["ts"].max() - pd.Timedelta(days=100)]
    budget = float(te["close"].max()) * 6
    r = sim.simulate_topk(np.full(len(te), 100.0), te["y"].values, te["ts"].values, te["symbol"].values,
                          50.0, 6, 5.0, exit_pct=np.zeros(len(te)), price=te["close"].values, budget_usd=budget)
    assert r["skipped_price"] == 0 and r["symbols_bought"] == 6


def test_fixed_symbols_keep_the_slot_when_one_is_missing(run):
    """2a（20-1 の 2）: 決めた銘柄の 1 本が途中から上場でも K は決めた本数 ＝ 枠 $100 が動かない。"""
    panel = _panel(late="S5")
    _res, _ps, _s, _d, extra = evaluate_trading(panel, _feats(panel), _exp(TRADER), run)
    d = extra["trader"][extra["trader"]["条件"] == TAG_A]
    assert (d["銘柄数"] == 3).all() and (d["予算USD"] == 300).all()


def test_checks_compare_with_hold(run):
    panel = _panel()
    res, ps, summary, daily, extra = evaluate_trading(panel, _feats(panel), _exp(TRADER), run)
    doc = checks.compute_trading(res, summary, ps, daily, _exp(TRADER), n_trials=10, extra=extra)
    assert {e["tag"] for e in doc["trader"]} == {TAG_A, TAG_B}
    for e in doc["trader"]:
        assert e["vs_hold"]["folds"] == 5 and e["vs_random"]["folds"] == 5
        assert len(e["予算USD"]) == 5


def test_leak_makes_trader_rows_jump(run):
    """⚠ 13-10 の配線検査をトレーダーの形でも: 未来を知る列を混ぜると 対 持ち続ける が跳ねる。"""
    edge = {}
    for tag, p in (("clean", _panel()), ("leak", _panel(leak=True))):
        res, *_ = evaluate_trading(p, _feats(p), _exp(TRADER), run)
        m = res[res["手法"] == trader_name("全部使う（基準）", TAG_B)].sort_values("fold")["純利bp"].values
        h = res[res["手法"] == trader_hold_name(TAG_B)].sort_values("fold")["純利bp"].values
        edge[tag] = float((m - h).mean())
    assert edge["leak"] > edge["clean"] + 1000


def test_config_errors(run):
    panel = _panel()
    with pytest.raises(SystemExit):
        evaluate_trading(panel, _feats(panel), _exp([{"tag": "x", "symbols": ["ZZZ"]}]), run)
    with pytest.raises(SystemExit):
        evaluate_trading(panel, _feats(panel), _exp([{"tag": "a〔b〕"}]), run)
    with pytest.raises(SystemExit):
        evaluate_trading(panel.drop(columns="close"), _feats(panel), _exp([{"tag": "x"}]), run)


# --- 検証結果一覧（rules.md 20-3 の 2・20-4）--------------------------------------------------

def test_canonical_keeps_the_trader_variant_for_catalog_ids():
    assert catalog.canonical("F1-7 IC の安定性") == ("F1-7", "F1-7")          # ⚠ 既存の行は変わらない
    assert catalog.canonical("F1-7 IC の安定性〔トレーダー・5本・$300〕") == ("F1-7", "F1-7〔トレーダー・5本・$300〕")
    assert catalog.canonical("基準 持ち続ける〔トレーダー・5本・$300〕") == (None, "持ち続ける〔トレーダー・5本・$300〕")


def test_edge_is_measured_against_hold_of_the_same_condition():
    rows = []
    for f in range(3):
        rows += [{"手法": trader_name("F1-7 IC の安定性", TAG_A), "fold": f, "閾値": 50.0, "純利bp": 10.0},
                 {"手法": trader_hold_name(TAG_A), "fold": f, "閾値": 50.0, "純利bp": 4.0 if f else 20.0},
                 {"手法": "基準 常に上（ドリフト）", "fold": f, "閾値": 50.0, "純利bp": 1000.0}]
    edge, pat = catalog._edge_vs_bh(pd.DataFrame(rows), trader_name("F1-7 IC の安定性", TAG_A), "50")
    assert edge == pytest.approx(2.0 / 3) and pat == "2/3 −＋＋"


def test_name_spellings():
    assert names.method_slug("F1-7〔トレーダー・5本・$300〕") == "f1-7-tr5"
    assert names.method_slug("D2 中期ゲート（60日・学習）〔トレーダー・全銘柄・全部を買える予算〕") == "d2-gate60-trall"
    assert names.method_slug("持ち続ける〔トレーダー・5本・$300〕") == "hold-tr5"
