"""訓練分割の成績で銘柄を選ぶ（rules.md 29 章）。

⚠ `select` の無い条件は 1 ビットも変わらない ／ 選抜は訓練分割だけを読む（評価期間を書き換えても選びが変わらない）／
候補は枠で買える株だけ ／ 行 ＝ 手法 ＋ 持ち続ける ＋ 乱択選抜（基準線は数えない）／ 綴り。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import catalog, names, runs
from ail.validation import checks
from ail.validation import select as symsel
from cli.run import evaluate_trading, trader_hold_name, trader_name, trader_random_name
from tests.test_trader_shape import TRADER, _exp, _feats, _panel

TAG_R, TAG_N = "選抜5・訓練リターン・$600", "選抜5・モデル純利・$600"
SELECT = [{"tag": TAG_R, "select": {"by": "ret", "k": 2}, "budget_usd": 600, "methods": ["全部使う（基準）"]},
          {"tag": TAG_N, "select": {"by": "net", "k": 2}, "budget_usd": 600, "methods": ["全部使う（基準）"]}]


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_select", {}, seed=0)


def _tr(n=400, seed=1):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    rows = []
    for i, s in enumerate(("A", "B", "C", "D")):
        y = rng.normal(0.0, 0.01, n) + (0.002 if s == "C" else -0.002 if s == "D" else 0.0)
        rows.append(pd.DataFrame({"symbol": s, "ts": ts, "y": y, "close": 10.0 * (i + 1), "f1": rng.normal(0, 1, n)}))
    return pd.concat(rows, ignore_index=True).sort_values("ts", kind="stable").reset_index(drop=True)


def test_pool_is_the_stocks_the_slot_can_buy():
    tr = _tr()
    assert symsel.pool(tr, None) == ["A", "B", "C", "D"]
    assert symsel.pool(tr, 25.0) == ["A", "B"]           # 終値 10 ／ 20 ／ 30 ／ 40 のうち枠 $25 以下
    assert symsel.pool(tr.drop(columns=["close"]), 25.0) == ["A", "B", "C", "D"]


def test_by_return_ranks_the_last_window_and_respects_the_cap():
    tr = _tr()
    chosen, table = symsel.by_return(tr, 2)
    assert chosen == ["C", "A"] or chosen[0] == "C"       # 上がり続けた C が 1 位・下がり続けた D は最下位
    assert chosen[0] == "C" and "D" not in chosen
    assert list(table.columns) == symsel.SCORE_COLUMNS and table["chosen"].sum() == 2
    # 枠で C・D が買えなければ A・B から
    chosen_cap, _ = symsel.by_return(tr, 2, cap=25.0)
    assert set(chosen_cap) == {"A", "B"}
    # 窓の中の行が足りない銘柄は候補から外れる
    short = pd.concat([tr[tr["symbol"] != "C"], tr[tr["symbol"] == "C"].tail(50)], ignore_index=True)
    chosen_short, t2 = symsel.by_return(short, 2)
    assert "C" not in chosen_short and np.isnan(float(t2.loc[t2["symbol"] == "C", "score"].iloc[0]))
    # 同点は銘柄名の昇順・NaN は外す
    assert symsel.top({"b": 1.0, "a": 1.0, "c": float("nan")}, 5) == ["a", "b"]


def test_by_model_net_skips_symbols_that_never_traded():
    """θ が高くて尻で 1 回も取引しない銘柄は「成績なし」＝ 選ばない（純利 0 の同点を銘柄名の順で選ぶ退化を防ぐ。29-3）。"""
    from ail import registry
    tr = _tr(n=1200)
    model = registry.resolve("model", "Ridge")
    selector = registry.resolve_all("selector", ["全部使う（基準）"])["全部使う（基準）"]
    exp = {"k": 1, "model": "Ridge"}
    ctx = {"seed": 0, "model": model, "k": 1}
    chosen, table = symsel.by_model_net(tr, ["f1"], exp, ctx, model, selector, 1, [50.0, 99.0], 5.0, 2)
    assert chosen[99.0] == [] and (table.loc[table["閾値"] == 99.0, "trades"] == 0).all()   # θ 99 は誰も買わない
    assert table.loc[table["閾値"] == 99.0, "score"].isna().all()
    assert len(chosen[50.0]) <= 2 and list(table.columns) == symsel.SCORE_COLUMNS[:-1] + ["trades", "chosen"]


def test_random_sets_are_seeded_and_within_the_pool():
    a = symsel.random_sets(["A", "B", "C", "D"], 2, 0, 3)
    b = symsel.random_sets(["A", "B", "C", "D"], 2, 0, 3)
    assert a == b and all(len(x) == 2 and set(x) <= {"A", "B", "C", "D"} for x in a)
    assert symsel.random_sets(["A"], 2, 0, 1) == [["A"]] and symsel.random_sets([], 2, 0, 1) == [[]]


def test_existing_rows_do_not_change_when_select_is_added(run):
    """⚠ `select` の条件は行を足すだけ。素の行も 20 章の条件の行も 1 ビットも変わらない。"""
    panel = _panel(late="S5")
    a, _ps, _s, da, ea = evaluate_trading(panel, _feats(panel), _exp(TRADER), run)
    b, _ps, _s, db, eb = evaluate_trading(panel, _feats(panel), _exp(TRADER + SELECT), run)
    same = b[b["手法"].isin(set(a["手法"]))].reset_index(drop=True)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), same, check_exact=True, check_dtype=False)
    for key, series in da.items():
        for x, yv in zip(series, db[key]):
            pd.testing.assert_series_equal(x, yv, check_exact=True)
    old_diag = eb["trader"][eb["trader"]["条件"].isin({c["tag"] for c in TRADER})].reset_index(drop=True)
    pd.testing.assert_frame_equal(ea["trader"].reset_index(drop=True), old_diag[ea["trader"].columns], check_exact=True)


def test_select_rows_baselines_and_counting(run):
    panel = _panel()
    res, _ps, _s, daily, extra = evaluate_trading(panel, _feats(panel), _exp(SELECT), run)
    want = {trader_name("全部使う（基準）", t) for t in (TAG_R, TAG_N)}
    base = {trader_hold_name(t) for t in (TAG_R, TAG_N)} | {trader_random_name(t) for t in (TAG_R, TAG_N)}
    got = {m for m in res["手法"] if "〔" in m}
    assert got == want | base
    assert (res[res["手法"].isin(got)].groupby("手法").size() == 5).all()
    counted = [m for m in got if catalog.is_trial({"検証方式": "閾値売買", "手法名": m})]
    assert sorted(counted) == sorted(want)                 # ⚠ 持ち続ける・乱択選抜 は数えない（29-4）
    d = extra["trader"]
    assert set(d["条件"]) == {TAG_R, TAG_N} and (d["銘柄数"] == 2).all() and (d["予算USD"] == 600).all()
    assert (d["選んだ本数"] <= 2).all() and (d["候補数"] >= d["選んだ本数"]).all()
    assert set(d["選び方"]) == {"ret", "net"}
    # 枠 $300 で買える株だけが選ばれる（S4・S5 は値段 100・120 から動く ＝ 候補になりうる。全部の銘柄名が表の中）
    for syms in d["選んだ銘柄"]:
        assert set(syms.split()) <= {f"S{i}" for i in range(6)}
    # 判定の相手は同じ条件の「持ち続ける」（20-3 と同じ配線）・checks も読める
    doc = checks.compute_trading(res, _s, _ps, daily, _exp(SELECT), n_trials=10, extra=extra)
    assert {e["tag"] for e in doc["trader"]} == {TAG_R, TAG_N}
    for e in doc["trader"]:
        assert e["vs_hold"]["folds"] == 5 and e["vs_random"]["folds"] == 5


def test_selection_reads_only_the_training_split(run):
    """⚠ 最後の fold の評価期間（どの訓練分割にも入らない）を書き換えても、選んだ銘柄は 1 本も変わらない。"""
    panel = _panel()
    cut = panel["ts"].quantile(0.9)
    loud = panel.copy()
    loud.loc[(loud["ts"] >= cut) & (loud["symbol"] == "S3"), "y"] = 0.05     # S3 が評価期間で毎日 5% 上がる
    a = evaluate_trading(panel, _feats(panel), _exp(SELECT), run)[4]["trader"]
    b = evaluate_trading(loud, _feats(loud), _exp(SELECT), run)[4]["trader"]
    last = int(a["fold"].max())
    pick_a = a[a["fold"] == last].sort_values(["条件", "閾値"])["選んだ銘柄"].tolist()
    pick_b = b[b["fold"] == last].sort_values(["条件", "閾値"])["選んだ銘柄"].tolist()
    assert pick_a == pick_b
    # 書き換えた評価期間の成績そのものは動く（配線が生きている）
    assert not np.allclose(a[a["fold"] == last]["純利bp"].values, b[b["fold"] == last]["純利bp"].values)


def test_config_errors(run):
    panel = _panel()
    bad = [
        {"tag": "x", "select": {"by": "zzz", "k": 2}, "budget_usd": 600},
        {"tag": "x", "select": {"by": "ret", "k": 0}, "budget_usd": 600},
        {"tag": "x", "select": {"by": "ret", "k": 2}},                              # 予算が要る
        {"tag": "x", "select": {"by": "ret", "k": 2}, "budget_usd": 600, "symbols": ["S0"]},
        {"tag": "x", "select": {"by": "net", "k": 2}, "budget_usd": 600},           # 手法 2 本（全部使う ＋ 乱択は飛ばすので 1 本 …）
    ]
    for c in bad[:4]:
        with pytest.raises(SystemExit):
            evaluate_trading(panel, _feats(panel), _exp([c]), run)
    # net は条件を当てる手法が 1 本のときだけ: 乱択（基準）は飛ばすので「全部使う」1 本 ＝ 通る
    evaluate_trading(panel, _feats(panel), _exp([bad[4]]), run)
    exp2 = _exp([bad[4]])
    exp2["selectors"] = ["全部使う（基準）", "F1-7 IC の安定性"]
    with pytest.raises(SystemExit):
        evaluate_trading(panel, _feats(panel), exp2, run)


def test_name_spellings():
    for key, want in ((f"全部使う（基準）〔トレーダー・{TAG_R}〕", "all-sel5-ret-600"),
                      (f"全部使う（基準）〔トレーダー・{TAG_N}〕", "all-sel5-net-600"),
                      (f"持ち続ける〔トレーダー・{TAG_R}〕", "hold-sel5-ret-600"),
                      (f"乱択選抜〔トレーダー・{TAG_N}〕", "randsel-sel5-net-600"),
                      ("全部使う（基準）〔トレーダー・選抜5・訓練リターン・1本$600〕", "all-sel5-ret-600each"),      # 29-9 の訂正版（枠 $600 ／ 銘柄）
                      ("持ち続ける〔トレーダー・選抜5・モデル純利・1本$600〕", "hold-sel5-net-600each")):
        assert names.method_slug(key) == want, key
