"""状態機械・株数・予算の上限・銘柄ごとの合算（プラン §2-3・§6 の「予算」）。"""
import pytest

from plan import aggregate, build_plan, decide, size_intents
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


def test_aggregate_internal_transfer_and_net():
    # A が SPY を 2 株売り、B が 3 株買う → 2 株は内部移転、口座には 1 株の買いだけ
    from plan import Intent
    q = {"SPY": 100.0}
    its = [Intent("A", "SPY", "sell", 2, 200, "shares", 0, 90), Intent("B", "SPY", "buy", 3, 300, "shares", 90, 0)]
    orders, transfers = aggregate(its, q)
    assert len(transfers) == 1 and transfers[0].seller == "A" and transfers[0].buyer == "B" and transfers[0].shares == 2
    assert len(orders) == 1 and orders[0].side == "buy" and orders[0].shares == 1 and orders[0].parts == [{"trader": "B", "shares": 1, "usd": 100.0}]
    # 同数なら口座への注文は 0 件
    its = [Intent("A", "SPY", "sell", 2, 200, "shares", 0, 90), Intent("B", "SPY", "buy", 2, 200, "shares", 90, 0)]
    orders, transfers = aggregate(its, q)
    assert orders == [] and transfers[0].shares == 2


def test_aggregate_sums_two_buyers():
    from plan import Intent
    its = [Intent("A", "SPY", "buy", 1, 100, "shares", 90, 0), Intent("B", "SPY", "buy", 2, 200, "shares", 90, 0)]
    orders, _ = aggregate(its, {"SPY": 100.0})
    assert orders[0].shares == 3 and [p["trader"] for p in orders[0].parts] == ["A", "B"]


def test_notional_sizing():
    t = _trader(budget=10.0, symbols=("SPY", "QQQ"), sizing="notional")   # 1 銘柄 $5
    st = TraderState("A")
    its, ev = size_intents(t, st, [{"symbol": "SPY", "side": "buy", "buy_pct": 60, "exit_pct": 0}], {"SPY": 560.0}, "2026-10-01")
    assert its[0].usd == 5.0 and its[0].sizing == "notional"
    orders, _ = aggregate(its, {"SPY": 560.0})
    assert orders[0].sizing == "notional" and orders[0].value_usd == 5.0 and orders[0].shares == 0.0


def test_build_plan_end_to_end():
    a, b = _trader("A", 200.0, ("SPY",)), _trader("B", 200.0, ("SPY",))
    states = {"A": TraderState("A"), "B": TraderState("B")}
    states["A"].apply_buy("SPY", 1, 100.0, "2026-10-01")
    sigs = [_sig("A", "SPY", 0, 90), _sig("B", "SPY", 90, 0)]
    p = build_plan([a, b], states, sigs, {"SPY": 100.0}, "2026-10-02")
    assert len(p.intents) == 2 and len(p.transfers) == 1 and len(p.orders) == 1 and p.orders[0].shares == 1


def test_transfer_rounds_to_whole_shares_when_shares_party_involved():
    from plan import Intent
    q = {"SPY": 100.0}
    its = [Intent("A", "SPY", "sell", 1, 100, "shares", 0, 90), Intent("B", "SPY", "buy", 0.5, 50, "notional", 90, 0)]
    orders, transfers = aggregate(its, q)
    assert transfers == [] and len(orders) == 2       # 端株の買いと整数株の売りは別々に口座へ
    its = [Intent("A", "SPY", "sell", 2, 200, "shares", 0, 90), Intent("B", "SPY", "buy", 1.4, 140, "notional", 90, 0)]
    orders, transfers = aggregate(its, q)
    assert transfers[0].shares == 1 and [o.side for o in orders] == ["buy", "sell"]
