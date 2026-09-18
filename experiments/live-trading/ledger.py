"""トレーダー別の損益（日次 1 行）。⚠ **損益で手法を採らない**（CLAUDE.md の例外の条件）。記録として残すだけ。"""

from __future__ import annotations

from state import TraderState
from trader import Trader


def daily_row(trader: Trader, st: TraderState, quotes: dict[str, float], date: str) -> dict:
    market = 0.0
    priced = 0
    for sym, h in st.holdings.items():
        px = quotes.get(sym)
        if px:
            market += h.shares * px
            priced += 1
        else:
            market += h.cost  # 気配が無い銘柄は原価で置く（印を付ける）
    cost = st.cost_in_use()
    return {
        "date": date,
        "trader": trader.name,
        "test": trader.test,
        "budget_usd": trader.budget_usd,
        "cost_in_use_usd": round(cost, 2),
        "market_value_usd": round(market, 2),
        "unrealized_usd": round(market - cost, 2),
        "realized_usd": round(st.realized_usd, 2),
        "fees_usd": round(st.fees_usd, 4),
        "unsettled_usd": round(st.unsettled(date), 2),
        "holdings": {s: {"shares": h.shares, "avg_price": h.avg_price, "opened": h.opened} for s, h in st.holdings.items()},
        "holdings_priced": priced,
        "drawdown_pct_of_budget": round(-(market - cost + st.realized_usd) / trader.budget_usd * 100, 2) if trader.budget_usd else None,
    }
