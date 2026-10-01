"""トレーダーの定義と合成規則（プラン §2-1・§6 の「合成規則」）。"""
import os

import pytest

from trader import combine, load_trader, parse_trader

CONF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "traders")


def _doc(**over):
    base = {"name": "t", "budget_usd": 100.0, "symbols": ["SPY"], "models": [{"kind": "fixed", "buy": 100, "exit": 0}], "test": True}
    base.update(over)
    return base


def test_asis_single_model_equals_input():
    # モデル 1 本の「そのまま」は既存の閾値売買と一致（入力をそのまま返す）
    assert combine("asis", [63.2], [41.0], 50.0) == (63.2, 41.0)


def test_asis_rejects_two_models():
    with pytest.raises(ValueError):
        combine("asis", [60, 70], [40, 30], 50)


def test_mean():
    assert combine("mean", [60, 40], [30, 50], 50) == (50.0, 40.0)


def test_majority_odd_models():
    assert combine("majority", [60, 40, 70], [30, 60, 55], 50) == (100.0, 100.0)
    assert combine("majority", [60, 40, 45], [30, 60, 40], 50) == (0.0, 0.0)


def test_unanimous():
    # 全員が θ を超えたときだけ買う（min）。出口は 1 本でも超えたら手仕舞う（max）
    assert combine("unanimous", [60, 55], [30, 70], 50) == (55.0, 70.0)
    assert combine("unanimous", [60, 45], [30, 40], 50)[0] == 45.0


def test_validate_rejects_low_threshold():
    with pytest.raises(ValueError):
        parse_trader(_doc(threshold=45))


def test_validate_requires_test_flag_for_fixed():
    with pytest.raises(ValueError):
        parse_trader(_doc(test=False))


def test_validate_majority_needs_odd():
    with pytest.raises(ValueError):
        parse_trader(_doc(combine="majority", models=[{"kind": "fixed", "buy": 1, "exit": 1}] * 2))


def test_validate_asis_single():
    with pytest.raises(ValueError):
        parse_trader(_doc(combine="asis", models=[{"kind": "fixed", "buy": 1, "exit": 1}] * 2))


def test_load_test_a_and_universe():
    t = load_trader("test_a", CONF)
    assert t.test and t.symbols == ("T",) and t.models[0].kind == "file"
    assert os.path.exists(t.models[0].path)
    u = parse_trader(_doc(universe="us63", symbols=[]))
    assert len(u.symbols) == 63 and len(set(u.symbols)) == 63


# ---------- 実際に動かす 3 人の候補（config/traders/candidates/。dryrun2 の結果で片方を写す）----------

import pytest  # noqa: E402


@pytest.mark.parametrize("kind,n_symbols", [("notional", {"T1": 63, "T2": 48, "T3": 63}), ("shares", {"T1": 5, "T2": 5, "T3": 5})])
def test_candidates_parse_and_stay_inside_scale_a(kind, n_symbols):
    from trader import load_traders
    ts = load_traders(["T1", "T2", "T3"], os.path.join(CONF, "candidates", kind))
    assert {t.name: len(t.symbols) for t in ts} == n_symbols
    assert all(not t.test and t.sizing == kind and t.combine == "asis" for t in ts)
    assert [t.threshold for t in ts] == [50.0, 55.0, 50.0]
    assert sum(t.budget_usd for t in ts) <= 1000.0                                  # 規模 A の上限（run_day の既定）の内
    assert [(m.kind, m.name, m.method) for t in ts for m in t.models] == [
        ("experiment", "trade_own_ridge_a", "全部使う（基準）"),
        ("experiment", "trade_ownex_lgbm_a", "全部使う（基準）"),
        ("experiment", "trade_ownseq_ridge_a", "T3 QUANT（60日窓）")]


def test_candidates_are_not_loadable_by_name():
    """候補は `config/traders/` の直下に無い ＝ `--traders T1` では読めない（半端な設定で起動できない）。"""
    from trader import load_trader
    if os.path.exists(os.path.join(CONF, "T1.toml")):
        pytest.skip("T1.toml を確定した後")
    with pytest.raises(Exception):
        load_trader("T1", CONF)


def test_experiment_rows_of_another_day_or_method_are_not_used(tmp_path):
    import json
    from signals import SignalError, model_outputs
    from trader import ModelSpec
    p = tmp_path / "predict.jsonl"
    rows = [{"date": "2026-09-18", "model": "m", "method": "A", "symbol": "T", "buy": 60, "exit": 40},
            {"date": "2026-09-21", "model": "m", "method": "A", "symbol": "VZ", "buy": 55, "exit": 45}]
    import livefs
    livefs.append_many(p, [json.dumps(r, ensure_ascii=False) for r in rows])      # ⚠ 予測も DB（livefs）
    spec = ModelSpec(kind="experiment", name="m", method="A")
    assert model_outputs(spec, ("T", "VZ"), "2026-09-21", str(p)) == {"VZ": (55.0, 45.0)}     # 古い日の行では売買しない
    with pytest.raises(SignalError):
        model_outputs(ModelSpec(kind="experiment", name="m", method="B"), ("VZ",), "2026-09-21", str(p))


def test_confirmed_traders_are_the_shares_candidates():
    """2026-09-21 の確定（dryrun2 で金額指定は最低 $5 ＝ T1・T3 の枠 $4.76 では買えない → 3 人とも整数株・D3 の 5 本）。候補と中身が同じこと。"""
    from trader import load_traders
    if not os.path.exists(os.path.join(CONF, "T1.toml")):
        pytest.skip("T1.toml を確定する前")
    got = load_traders(["T1", "T2", "T3"], CONF)
    want = load_traders(["T1", "T2", "T3"], os.path.join(CONF, "candidates", "shares"))
    assert [(t.name, t.symbols, t.sizing, t.threshold, t.budget_usd, t.combine, t.test, [(m.kind, m.name, m.method) for m in t.models]) for t in got] == \
           [(t.name, t.symbols, t.sizing, t.threshold, t.budget_usd, t.combine, t.test, [(m.kind, m.name, m.method) for m in t.models]) for t in want]
    assert all(list(t.symbols) == ["T", "PFE", "NKE", "VZ", "BAC"] and t.sizing == "shares" for t in got)


# ---------- 複数モデルの候補（config/traders/candidates/multi/。2026-09-28 Phase 4。⚠ テストだけ ＝ 執行器は変えない）----------

MULTI = os.path.join(CONF, "candidates", "multi")


def test_multi_candidates_parse_and_are_wired_as_decided():
    """実例 A `T4` ＝ いまの 3 本を mean・θ 50 ／ 実例 B `T5` ＝ 主モデル ＋ 出口だけの損切りモデルを unanimous・θ 50（K1・K2・K6）。
    どちらも本番の人ではない（直下に無い）。予算は 2 人で $600 ＝ 執行器の既定の上限 $1,000 の内（sim5 は 2 人だけ）。"""
    from trader import load_traders
    ts = {t.name: t for t in load_traders(["T4", "T5"], MULTI)}
    assert all(not t.test and t.sizing == "shares" and t.threshold == 50.0 and t.symbols == ("T", "PFE", "NKE", "VZ", "BAC") for t in ts.values())
    assert sum(t.budget_usd for t in ts.values()) <= 1000.0
    assert ts["T4"].combine == "mean" and [(m.name, m.method) for m in ts["T4"].models] == [
        ("trade_own_ridge_a", "全部使う（基準）"), ("trade_ownex_lgbm_a", "全部使う（基準）"), ("trade_ownseq_ridge_a", "T3 QUANT（60日窓）")]
    assert ts["T5"].combine == "unanimous" and [(m.name, m.method) for m in ts["T5"].models] == [
        ("trade_own_ridge_a", "全部使う（基準）"), ("trade_own_stopexit_a", "X1 出口だけ 高値20日から−10%で降りる")]
    # 同じ実験名の違う手法を 1 人の中で 2 本持てない（signals.py は (実験名, 銘柄) で行を引く）
    for t in ts.values():
        names = [m.name for m in t.models]
        assert len(names) == len(set(names)), t.name
    # 候補は直下に居ない ＝ `--traders T4` では起動できない
    for name in ("T4", "T5"):
        assert not os.path.exists(os.path.join(CONF, f"{name}.toml"))


def test_sim_copies_match_the_multi_candidates():
    """sim5 の人（sim_T4・sim_T5）は候補の写し（test = true と名前だけ違う）。"""
    from trader import load_traders
    got = {t.name: t for t in load_traders(["sim_T4", "sim_T5"], CONF)}
    want = {t.name: t for t in load_traders(["T4", "T5"], MULTI)}
    for name, w in want.items():
        g = got["sim_" + name]
        assert g.test and not w.test
        assert (g.symbols, g.sizing, g.threshold, g.budget_usd, g.combine, [(m.kind, m.name, m.method) for m in g.models]) == \
            (w.symbols, w.sizing, w.threshold, w.budget_usd, w.combine, [(m.kind, m.name, m.method) for m in w.models])


# ---------- 検証結果一覧の上位をもとにした候補（config/traders/candidates/best/。2026-10-01 プラン best-model-trader.md Step 3）----------

BEST = os.path.join(CONF, "candidates", "best")


def test_best_candidate_is_wired_as_decided_and_matches_its_sim_copy():
    """`T6` ＝ F2-2 RFE（`sel_small4_1995`）1 本を asis・θ 50・3 人と同じ 5 本・整数株・$300（利用者決定「(b) で進めて」）。
    本番の人ではない（直下に無い）。sim6 の `sim_T6` は写し（test = true と名前だけ違う）。"""
    from trader import load_traders
    (t,) = load_traders(["T6"], BEST)
    assert not t.test and t.combine == "asis" and t.threshold == 50.0 and t.budget_usd == 300.0
    assert t.symbols == ("T", "PFE", "NKE", "VZ", "BAC") and t.sizing == "shares"
    assert [(m.kind, m.name, m.method) for m in t.models] == [("experiment", "sel_small4_1995", "F2-2 RFE")]
    assert not os.path.exists(os.path.join(CONF, "T6.toml"))
    (g,) = load_traders(["sim_T6"], CONF)
    assert g.test
    assert (g.symbols, g.sizing, g.threshold, g.budget_usd, g.combine, [(m.kind, m.name, m.method) for m in g.models]) == \
        (t.symbols, t.sizing, t.threshold, t.budget_usd, t.combine, [(m.kind, m.name, m.method) for m in t.models])


def test_asis_is_refused_for_the_multi_shape():
    """`asis` は 1 本だけ ＝ 複数モデルの人を `asis` にすると読めない（合成規則を書き忘れた設定で起動できない）。"""
    with pytest.raises(ValueError):
        parse_trader(_doc(test=False, combine="asis", models=[{"kind": "experiment", "name": "a"}, {"kind": "experiment", "name": "b"}]))


def test_unanimous_with_exit_only_model_buys_on_the_main_model():
    """K2: 出口だけのモデルは買い% 100 を返す → unanimous の買いは主モデルの値そのまま・出口は max（どちらかが言ったら降りる）。
    ⚠ mean に入れると買い% が半分に割れる（使えない形）。"""
    main_buy, main_exit, stop_exit = 63.2, 36.8, 100.0
    assert combine("unanimous", [main_buy, 100.0], [main_exit, stop_exit], 50.0) == (main_buy, stop_exit)
    assert combine("unanimous", [main_buy, 100.0], [main_exit, 0.0], 50.0) == (main_buy, main_exit)       # 規則が立たない日は主モデルの出口
    assert combine("mean", [main_buy, 100.0], [main_exit, 0.0], 50.0)[0] == pytest.approx((main_buy + 100.0) / 2)
