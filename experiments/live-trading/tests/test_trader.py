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
