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
