"""状態機械・株数・予算の上限・銘柄ごとの合算（プラン §2-3・§6 の「予算」）。"""
import pytest

from plan import build_plan, decide, size_intents, to_orders
from signals import Signal
from state import TraderState
from trader import parse_trader


def _trader(name="A", budget=100.0, symbols=("SPY",), sizing="shares", threshold=50.0):
    return parse_trader({"name": name, "budget_usd": budget, "symbols": list(symbols), "sizing": sizing, "threshold": threshold,
                         "models": [{"kind": "fixed", "buy": 0, "exit": 0}], "test": True})


def _sig(trader, symbol, buy, exit_):
    return Signal(trader, symbol, buy, exit_, ((f"m", buy, exit_),), True)


def test_state_machine_buy_only_and_no_add():
    t = _trader()
    st = TraderState("A")
    raw, ev = decide(t, st, [_sig("A", "SPY", 60, 40)])
    assert [r["side"] for r in raw] == ["buy"]
    st.apply_buy("SPY", 1, 100.0, "2026-10-01")
    raw, ev = decide(t, st, [_sig("A", "SPY", 60, 40)])   # 保有中に買い% が高くても買い増ししない
    assert raw == [] and ev[0]["kind"] == "hold"
    raw, _ = decide(t, st, [_sig("A", "SPY", 10, 60)])    # 保有中は出口% だけ読む
    assert [r["side"] for r in raw] == ["sell"]
    st2 = TraderState("A")
    raw, ev = decide(t, st2, [_sig("A", "SPY", 10, 90)])  # 未保有で売り指標が立っても何もしない
    assert raw == [] and ev[0]["kind"] == "skip"


def test_sizing_floor_and_too_small():
    t = _trader(budget=100.0, symbols=("SPY", "T"))          # 1 銘柄 $50
    st = TraderState("A")
    raw = [{"symbol": "SPY", "side": "buy", "buy_pct": 60, "exit_pct": 0}, {"symbol": "T", "side": "buy", "buy_pct": 60, "exit_pct": 0}]
    its, ev = size_intents(t, st, raw, {"SPY": 560.0, "T": 25.6}, "2026-10-01")
    assert [i.symbol for i in its] == ["T"] and its[0].shares == 1 and its[0].usd == 25.6
    assert ev[0]["kind"] == "too_small" and ev[0]["symbol"] == "SPY"


def test_budget_cap_refuses_buy_beyond_available():
    t = _trader(budget=100.0, symbols=("A1", "A2", "A3"))   # 1 銘柄 $33.3
    st = TraderState("A")
    st.apply_buy("A1", 3, 30.0, "2026-09-30")                 # 原価 $90 使用中 → 空き $10
    raw = [{"symbol": "A2", "side": "buy", "buy_pct": 60, "exit_pct": 0}]
    its, ev = size_intents(t, st, raw, {"A2": 10.0}, "2026-10-01")
    assert its and its[0].shares == 1                          # 空き $10 で 1 株
    st.apply_buy("A2", 1, 10.0, "2026-10-01")                  # 空き 0
    its, ev = size_intents(t, st, [{"symbol": "A3", "side": "buy", "buy_pct": 60, "exit_pct": 0}], {"A3": 10.0}, "2026-10-01")
    assert its == [] and ev[0]["kind"] == "too_small"


def test_unsettled_cash_blocks_reinvest():
    t = _trader(budget=100.0, symbols=("SPY",))
    st = TraderState("A")
    st.apply_buy("SPY", 1, 90.0, "2026-10-01")
    st.apply_sell("SPY", 1, 95.0, "2026-10-02")                # 受渡し待ち $95
    its, ev = size_intents(t, st, [{"symbol": "SPY", "side": "buy", "buy_pct": 60, "exit_pct": 0}], {"SPY": 90.0}, "2026-10-02")
    assert its == [] and ev[0]["kind"] == "too_small"          # 空き $5
    its, ev = size_intents(t, st, [{"symbol": "SPY", "side": "buy", "buy_pct": 60, "exit_pct": 0}], {"SPY": 90.0}, "2026-10-03")
    assert its and its[0].shares == 1                          # T+1 で戻る


def test_day_cap():
    t = _trader(budget=1000.0, symbols=("A1", "A2"))
    st = TraderState("A")
    raw = [{"symbol": s, "side": "buy", "buy_pct": 60, "exit_pct": 0} for s in ("A1", "A2")]
    its, ev = size_intents(t, st, raw, {"A1": 100.0, "A2": 100.0}, "2026-10-01", max_day_usd=600.0)
    assert [i.symbol for i in its] == ["A1"] and ev[0]["kind"] == "over_day_cap"


def test_opposite_traders_both_go_to_the_market_sell_first():
    """2026-09-19 の利用者決定「内部移転はダメです。トレーダーの実際の実績が検証できない」:
    A の売りと B の買いが同じ銘柄で重なっても付け替えない。両方を口座に出し、売りが先。"""
    from plan import Intent
    q = {"SPY": 100.0, "QQQ": 50.0}
    its = [Intent("B", "SPY", "buy", 3, 300, "shares", 90, 0), Intent("A", "SPY", "sell", 2, 200, "shares", 0, 90), Intent("B", "QQQ", "buy", 1, 50, "shares", 90, 0)]
    orders = to_orders(its, q)
    assert [(o.side, o.symbol, o.shares, o.parts) for o in orders] == [
        ("sell", "SPY", 2, [{"trader": "A", "shares": 2, "usd": 200}]),
        ("buy", "SPY", 3, [{"trader": "B", "shares": 3, "usd": 300}]),          # 3 株まるごと市場で買う（A の 2 株を中値で受け取らない）
        ("buy", "QQQ", 1, [{"trader": "B", "shares": 1, "usd": 50}])]


def test_two_buyers_get_one_order_each():
    """2026-09-19 の利用者決定: トレーダーごとの成績を正確に知るため、口座への注文はトレーダーごとに別々（合算しない・按分しない）。"""
    from plan import Intent
    its = [Intent("A", "SPY", "buy", 1, 100, "shares", 90, 0), Intent("B", "SPY", "buy", 2, 200, "shares", 90, 0)]
    orders = to_orders(its, {"SPY": 100.0})
    assert [(o.shares, [p["trader"] for p in o.parts]) for o in orders] == [(1, ["A"]), (2, ["B"])]


def test_mixed_sizing_never_gives_the_whole_share_trader_a_fraction():
    """整数株の人と金額指定の人が同じ日に同じ銘柄を売買しても、整数株の人の注文は整数株の Market のまま（§0-7 (j) の 1）。"""
    from plan import Intent
    its = [Intent("A", "SPY", "buy", 5, 500, "shares", 90, 0), Intent("B", "SPY", "buy", 0.3, 30, "notional", 90, 0),
           Intent("C", "SPY", "sell", 2.5, 250, "notional", 0, 90), Intent("D", "SPY", "sell", 1, 100, "shares", 0, 90)]
    orders = to_orders(its, {"SPY": 100.0})
    by = {o.parts[0]["trader"]: o for o in orders}
    assert len(orders) == 4 and all(len(o.parts) == 1 for o in orders) and [o.side for o in orders] == ["sell", "sell", "buy", "buy"]
    assert by["A"].sizing == "shares" and by["A"].shares == 5 and by["D"].shares == 1
    assert by["B"].sizing == "notional" and by["B"].shares == 0.0 and by["B"].value_usd == 30.0 and by["C"].shares == 2.5


def test_notional_sizing():
    t = _trader(budget=10.0, symbols=("SPY", "QQQ"), sizing="notional")   # 1 銘柄 $5
    st = TraderState("A")
    its, ev = size_intents(t, st, [{"symbol": "SPY", "side": "buy", "buy_pct": 60, "exit_pct": 0}], {"SPY": 560.0}, "2026-10-01")
    assert its[0].usd == 5.0 and its[0].sizing == "notional"
    orders = to_orders(its, {"SPY": 560.0})
    assert orders[0].sizing == "notional" and orders[0].value_usd == 5.0 and orders[0].shares == 0.0


def test_build_plan_end_to_end():
    a, b = _trader("A", 200.0, ("SPY",)), _trader("B", 200.0, ("SPY",))
    states = {"A": TraderState("A"), "B": TraderState("B")}
    states["A"].apply_buy("SPY", 1, 100.0, "2026-10-01")
    sigs = [_sig("A", "SPY", 0, 90), _sig("B", "SPY", 90, 0)]
    p = build_plan([a, b], states, sigs, {"SPY": 100.0}, "2026-10-02")
    assert len(p.intents) == 2 and [(o.side, o.parts[0]["trader"], o.shares) for o in p.orders] == [("sell", "A", 1), ("buy", "B", 2)]
    assert not hasattr(p, "transfers")
