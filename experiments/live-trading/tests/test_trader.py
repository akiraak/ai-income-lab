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
    # ⚠ 予算は比べない: 2026-10-04 利用者決定で T1 だけ $300 → $600（C11 の緩和 ＝ 予算だけは編集してよい。live-trading.md §0-15）
    assert [(t.name, t.symbols, t.sizing, t.threshold, t.combine, t.test, [(m.kind, m.name, m.method) for m in t.models]) for t in got] == \
           [(t.name, t.symbols, t.sizing, t.threshold, t.combine, t.test, [(m.kind, m.name, m.method) for m in t.models]) for t in want]
    assert all(t.budget_usd == 300.0 for t in want)
    assert {t.name: t.budget_usd for t in got} == {"T1": 600.0, "T2": 300.0, "T3": 300.0}
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
    # T4・T5 は実験名が 1 本ずつ（2026-09-28 の時点では signals.py が (実験名, 銘柄) で引いた。2026-10-18 から役割の人は同じ実験名の別の手法を持てる ＝ test_horizon_*）
    for t in ts.values():
        names = [m.name for m in t.models]
        assert len(names) == len(set(names)), t.name
    # `T5` は直下に居ない ＝ `--traders T5` では起動できない。⚠ `T4` は 2026-10-04 に直下へ（下の test_production_people_2026_10_04）
    assert not os.path.exists(os.path.join(CONF, "T5.toml"))


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
    (g,) = load_traders(["sim_T6"], CONF)
    assert g.test
    assert (g.symbols, g.sizing, g.threshold, g.budget_usd, g.combine, [(m.kind, m.name, m.method) for m in g.models]) == \
        (t.symbols, t.sizing, t.threshold, t.budget_usd, t.combine, [(m.kind, m.name, m.method) for m in t.models])


def test_production_people_2026_10_04():
    """2026-10-04 利用者決定（プラン trader-loop.md）: 残す `T1`・入れる `T4`・`T6`（候補の原本を写して予算だけ $600）・1 人 $600。
    合計 $1,800 は上限 $1,900（`--max-total-budget 1900`）の内・既定の $1,000 は超える（⚠ live.env で上げるまで起動できない ＝ 意図どおり）。
    `T2`・`T3` の設定は直下に残る（持ち株を売り切るまで。売買履歴も残す ＝ C11）。"""
    from trader import load_traders
    ts = {t.name: t for t in load_traders(["T1", "T4", "T6"], CONF)}
    assert all(t.budget_usd == 600.0 and not t.test and t.sizing == "shares" and t.symbols == ("T", "PFE", "NKE", "VZ", "BAC") for t in ts.values())
    assert 1000.0 < sum(t.budget_usd for t in ts.values()) <= 1900.0
    for name, src in (("T4", MULTI), ("T6", BEST)):
        (w,) = load_traders([name], src)
        g = ts[name]
        assert w.budget_usd == 300.0 and (g.symbols, g.sizing, g.threshold, g.combine, g.test, [(m.kind, m.name, m.method) for m in g.models]) == \
            (w.symbols, w.sizing, w.threshold, w.combine, w.test, [(m.kind, m.name, m.method) for m in w.models])
    old = {t.name: t for t in load_traders(["T2", "T3"], CONF)}
    assert old["T2"].budget_usd == 300.0 and old["T3"].budget_usd == 300.0      # 外す人は編集しない


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


# ---------- 先の期間が違う「買い係 ／ 売り係 ／ 損切り係」の候補（config/traders/candidates/horizon/。2026-10-19 プラン trader-model-roles.md §5 Phase 4）----------

HORIZON = os.path.join(CONF, "candidates", "horizon")
H = {5: "H2 先5日ゲート（全列・学習）", 10: "H1 先10日ゲート（全列・学習）", 20: "H3 先20日ゲート（全列・学習）", 40: "H4 先40日ゲート（全列・学習）"}
X1 = "X1 出口だけ 高値20日から−10%で降りる"


HC = {w: n.replace("（全列・学習）", "（全列・学習・土台差）") for w, n in H.items()}
# 2026-10-19 Phase 6（利用者「損切りを入れないトレーダーを用意する。そのうえで A」）: 損切り係なし T12〜T15 ／ 土台差 × 損切りなし T16〜T19
NO_STOP = {"T12": (10, 5), "T13": (20, 5), "T14": (40, 10), "T15": (5, 20)}
CENTERED = {"T16": (10, 5), "T17": (20, 5), "T18": (40, 10), "T19": (5, 20)}


def test_horizon_candidates_without_stop_and_centered_are_wired_as_decided():
    """`T12`〜`T15` ＝ `T7`〜`T10` から損切り係を外しただけ（同じ作り置き H1〜H4）／ `T16`〜`T19` ＝ さらに買い係・売り係を土台差の
    検知器（`trade_own_fwdmulti_c`）にした人。どちらも最短保有 ＝ 買い係の期間・θ 50・5 本・整数株・$300・本番の人ではない。"""
    from trader import load_traders
    for group, exp, names in ((NO_STOP, "trade_own_fwdmulti_a", H), (CENTERED, "trade_own_fwdmulti_c", HC)):
        ts = {t.name: t for t in load_traders(list(group), HORIZON)}
        for name, (hb, he) in group.items():
            t = ts[name]
            assert t.has_roles and not t.test and t.sizing == "shares" and t.threshold == 50.0 and t.budget_usd == 300.0
            assert t.symbols == ("T", "PFE", "NKE", "VZ", "BAC") and t.combine == "asis"
            assert [(m.kind, m.name, m.method, m.role) for m in t.models] == [("experiment", exp, names[hb], "buy"), ("experiment", exp, names[he], "exit")]
            assert t.min_hold_days == hb and not t.stop_models and not t.stop_blocks_buy
            assert not os.path.exists(os.path.join(CONF, f"{name}.toml"))
    # 損切りなしの人の作り置きは T7〜T10 と同じ (実験名, 手法) ＝ sim8 に新しい予測は要らない
    import simpredict
    a = set(simpredict.experiment_models(load_traders(["T7", "T8", "T9", "T10"], HORIZON)))
    b = set(simpredict.experiment_models(load_traders(list(NO_STOP), HORIZON)))
    assert b < a and all(m[0] == "trade_own_fwdmulti_a" for m in b)


def test_horizon_sim_copies_sim8_sim9_match_and_lift_the_total_budget():
    """sim8（`sim_T12`〜`sim_T15`）／ sim9（`sim_T16`〜`sim_T19`）は候補の写し。4 人 × $300 ＝ $1,200 は `max_total_budget_usd` で上げる。"""
    import simdata
    from trader import load_traders
    key = lambda t: (t.symbols, t.sizing, t.threshold, t.budget_usd, t.combine, t.combine_buy, t.combine_exit, t.min_hold_days,   # noqa: E731
                     t.stop_blocks_buy, [(m.kind, m.name, m.method, m.role) for m in t.models])
    for sim, group in (("sim8", NO_STOP), ("sim9", CENTERED)):
        cfg = simdata.load_config(sim, os.path.join(os.path.dirname(CONF), "sim"))
        assert cfg.traders == ["sim_" + n for n in group] and cfg.max_total_budget_usd == 1200.0
        want = {t.name: t for t in load_traders(list(group), HORIZON)}
        got = {t.name: t for t in load_traders(cfg.traders, CONF)}
        for name, w in want.items():
            g = got["sim_" + name]
            assert g.test and not w.test and key(g) == key(w)
        assert sum(got[n].budget_usd for n in cfg.traders) == 1200.0


def test_horizon_candidates_are_wired_as_decided():
    """`T7`〜`T10` ＝ 買い係 先 h_b 日 ／ 売り係 先 h_e 日（同じ実験 `trade_own_fwdmulti_a` の別の手法）／ 損切り係 X1 −10%・最短保有 ＝ h_b・
    θ 50・3 人と同じ 5 本・整数株・$300（プラン §1・§2。利用者決定「期間を別々にすることが大切」・K9 (a) ＝ `stop_blocks_buy` は書かない）。
    本番の人ではない（直下に無い）。"""
    from trader import load_traders
    ts = {t.name: t for t in load_traders(["T7", "T8", "T9", "T10"], HORIZON)}
    want = {"T7": (10, 5), "T8": (20, 5), "T9": (40, 10), "T10": (5, 20)}
    for name, (hb, he) in want.items():
        t = ts[name]
        assert t.has_roles and not t.test and t.sizing == "shares" and t.threshold == 50.0 and t.budget_usd == 300.0
        assert t.symbols == ("T", "PFE", "NKE", "VZ", "BAC")
        assert [(m.kind, m.name, m.method, m.role) for m in t.models] == [
            ("experiment", "trade_own_fwdmulti_a", H[hb], "buy"), ("experiment", "trade_own_fwdmulti_a", H[he], "exit"),
            ("experiment", "trade_own_stopexit_a", X1, "stop")]
        assert t.min_hold_days == hb and not t.stop_blocks_buy                 # 最短保有 ＝ 買い係が見た期間・往復あり（K9 (a)）
        assert t.combine == "asis" and t.combine_buy is None and t.combine_exit is None and t.buy_rule == t.exit_rule == "asis"
        assert len(t.buy_models) == len(t.exit_models) == len(t.stop_models) == 1 and not t.watch_models
        assert not os.path.exists(os.path.join(CONF, f"{name}.toml"))        # `--traders T7` では起動できない
    # 4 人の違いは期間だけ（名前・note・min_hold_days・手法のほかは同じ）
    assert len({(t.symbols, t.sizing, t.threshold, t.budget_usd, t.combine) for t in ts.values()}) == 1


def test_horizon_sim_copies_match_and_sim7_lifts_the_total_budget():
    """sim7 の人（`sim_T7`〜`sim_T10`）は候補の写し（`test = true` と名前だけ違う）。4 人 × $300 ＝ $1,200 は執行器の既定の上限 $1,000 を
    超えるので、`config/sim/sim7.toml` の `max_total_budget_usd` を運転手が執行器に渡す（simrun.budget_args。⚠ sim の中だけ）。"""
    import simdata
    from trader import load_traders
    want = {t.name: t for t in load_traders(["T7", "T8", "T9", "T10"], HORIZON)}
    got = {t.name: t for t in load_traders(["sim_" + n for n in want], CONF)}
    for name, w in want.items():
        g = got["sim_" + name]
        assert g.test and not w.test
        key = lambda t: (t.symbols, t.sizing, t.threshold, t.budget_usd, t.combine, t.combine_buy, t.combine_exit, t.min_hold_days,   # noqa: E731
                         t.stop_blocks_buy, [(m.kind, m.name, m.method, m.role) for m in t.models])
        assert key(g) == key(w)
    cfg = simdata.load_config("sim7", os.path.join(os.path.dirname(CONF), "sim"))
    assert cfg.traders == ["sim_T7", "sim_T8", "sim_T9", "sim_T10"]
    total = sum(got[n].budget_usd for n in cfg.traders)
    assert total == 1200.0 > 1000.0 and cfg.max_total_budget_usd == total
