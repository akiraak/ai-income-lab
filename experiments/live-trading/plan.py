"""合図 → 状態機械 → 目標 → 差分 → 予算の検査 → 3 人を銘柄ごとに合算 → 注文（プラン §2-1・§2-3）。

⚠ **純粋関数**（読み書きしない・API を呼ばない）。気配は引数で受ける。

状態機械は feature-discovery の `simulate()`（rules.md 13-4）と同じ向き:
  未保有で 買い% > θ → 建てる ／ 保有中で 出口% > θ → 手仕舞う ／ それ以外は何もしない（買い専用・買い増しなし）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from signals import Signal
from state import TraderState
from trader import Trader


@dataclass(frozen=True)
class Intent:
    """トレーダー 1 人の 1 銘柄の売買の意図（口座に出す前）。"""
    trader: str
    symbol: str
    side: str            # buy / sell
    shares: float        # 売りは持ち分の全部。買いは sizing で決めた株数（notional なら参考値）
    usd: float           # 買いは目標金額、売りは参考値（気配 × 株数）
    sizing: str          # shares / notional
    buy_pct: float
    exit_pct: float


@dataclass
class NetOrder:
    """口座に出す注文（銘柄ごとに全員ぶんを合算した後）。"""
    symbol: str
    side: str                        # buy / sell
    shares: float                    # shares sizing の数量（notional の買いは 0 で value を使う）
    value_usd: float                 # notional の買いの金額（それ以外は参考値）
    sizing: str
    parts: list[dict] = field(default_factory=list)   # [{"trader", "shares", "usd"}] 誰の何株ぶんか（約定を按分する鍵）


@dataclass
class Transfer:
    """同じ銘柄を A が売り B が買う日の内部移転（口座には出ない。差 3 のコスト 0 として残す）。"""
    symbol: str
    seller: str
    buyer: str
    shares: float
    price: float


@dataclass
class Plan:
    intents: list[Intent]
    orders: list[NetOrder]
    transfers: list[Transfer]
    events: list[dict]   # 見送り・予算超え・株数 0 などの理由


def decide(trader: Trader, state: TraderState, signals: list[Signal]) -> tuple[list[dict], list[dict]]:
    """状態機械。戻り値は (意図の素, 事象)。意図の素は {"symbol", "side", buy_pct, exit_pct}。"""
    raw: list[dict] = []
    events: list[dict] = []
    for sg in signals:
        if sg.trader != trader.name:
            continue
        pos = state.position(sg.symbol)
        if pos == 0 and sg.buy > trader.threshold:
            raw.append({"symbol": sg.symbol, "side": "buy", "buy_pct": sg.buy, "exit_pct": sg.exit})
        elif pos == 1 and sg.exit > trader.threshold:
            raw.append({"symbol": sg.symbol, "side": "sell", "buy_pct": sg.buy, "exit_pct": sg.exit})
        else:
            events.append({"kind": "hold" if pos else "skip", "trader": trader.name, "symbol": sg.symbol, "buy_pct": sg.buy, "exit_pct": sg.exit})
    return raw, events


def size_intents(trader: Trader, state: TraderState, raw: list[dict], quotes: dict[str, float], today: str,
                 max_day_usd: float | None = None) -> tuple[list[Intent], list[dict]]:
    """株数と金額を決め、⚠ **予算の上限で買いを拒む**（プラン §2-3）。

    予算の空き ＝ 予算 − 建玉の取得原価 − 受渡し待ちの売却代金（現金口座の T+1。保守側）。
    買いは合図の順（銘柄集合の並び）に入れ、空きを超えたものから見送る。
    """
    intents: list[Intent] = []
    events: list[dict] = []
    per_symbol = trader.per_symbol_usd
    available = trader.budget_usd - state.cost_in_use() - state.unsettled(today)
    day_total = 0.0
    for r in raw:
        symbol, side = r["symbol"], r["side"]
        px = quotes.get(symbol)
        if px is None or px <= 0:
            events.append({"kind": "no_quote", "trader": trader.name, "symbol": symbol, "side": side})
            continue
        if side == "sell":
            h = state.holdings[symbol]
            intents.append(Intent(trader.name, symbol, "sell", h.shares, h.shares * px, trader.sizing, r["buy_pct"], r["exit_pct"]))
            continue
        # 買い
        target = min(per_symbol, available)
        if trader.sizing == "shares":
            shares = math.floor(target / px)
            usd = shares * px
            if shares < 1:
                events.append({"kind": "too_small", "trader": trader.name, "symbol": symbol, "target_usd": round(target, 2), "price": px,
                               "note": "整数株では 1 株も買えない（プラン §2-3。端株が通れば sizing = notional）"})
                continue
        else:
            usd = math.floor(target * 100) / 100
            shares = usd / px
            if usd < 1.0:
                events.append({"kind": "too_small", "trader": trader.name, "symbol": symbol, "target_usd": round(target, 2), "price": px})
                continue
        if usd > available + 1e-9:
            events.append({"kind": "over_budget", "trader": trader.name, "symbol": symbol, "usd": round(usd, 2), "available": round(available, 2)})
            continue
        if max_day_usd is not None and day_total + usd > max_day_usd + 1e-9:
            events.append({"kind": "over_day_cap", "trader": trader.name, "symbol": symbol, "usd": round(usd, 2), "cap": max_day_usd})
            continue
        available -= usd
        day_total += usd
        intents.append(Intent(trader.name, symbol, "buy", shares, usd, trader.sizing, r["buy_pct"], r["exit_pct"]))
    return intents, events


def aggregate(intents: list[Intent], quotes: dict[str, float]) -> tuple[list[NetOrder], list[Transfer]]:
    """銘柄ごとに全員ぶんを合算する。A の売りと B の買いは気配で内部移転し、残りだけ口座に出す。

    ⚠ sizing が混在する銘柄（shares の人と notional の人）は、残りの買いを shares 側に丸めない。
    買いの残りは「notional の買いが含まれていれば notional、そうでなければ shares」で出す。
    """
    by_symbol: dict[str, list[Intent]] = {}
    for it in intents:
        by_symbol.setdefault(it.symbol, []).append(it)
    orders: list[NetOrder] = []
    transfers: list[Transfer] = []
    for symbol, its in by_symbol.items():
        px = quotes[symbol]
        buys = [i for i in its if i.side == "buy"]
        sells = [i for i in its if i.side == "sell"]
        # 内部移転: 売り手の株を買い手へ（買い手の目標株数まで）
        buy_left = {i.trader: i.shares for i in buys}
        sell_left = {i.trader: i.shares for i in sells}
        for s in sells:
            for b in buys:
                if sell_left[s.trader] <= 1e-9:
                    break
                take = min(sell_left[s.trader], buy_left[b.trader])
                if s.sizing == "shares" or b.sizing == "shares":
                    # 整数株の人が絡む移転は整数株だけ（端株の持ち分を整数株の台帳に作らない）
                    take = float(math.floor(take + 1e-9))
                if take <= 1e-9:
                    continue
                transfers.append(Transfer(symbol, s.trader, b.trader, round(take, 6), px))
                sell_left[s.trader] -= take
                buy_left[b.trader] -= take
        net_buy = [(b, buy_left[b.trader]) for b in buys if buy_left[b.trader] > 1e-9]
        net_sell = [(s, sell_left[s.trader]) for s in sells if sell_left[s.trader] > 1e-9]
        if net_buy:
            sizing = "notional" if any(b.sizing == "notional" for b, _ in net_buy) else "shares"
            shares = sum(q for _, q in net_buy)
            usd = sum(q * px for _, q in net_buy)
            orders.append(NetOrder(symbol, "buy", shares if sizing == "shares" else 0.0, round(usd, 2), sizing,
                                   [{"trader": b.trader, "shares": round(q, 6), "usd": round(q * px, 2)} for b, q in net_buy]))
        if net_sell:
            sizing = "notional" if any(s.sizing == "notional" for s, _ in net_sell) else "shares"
            shares = sum(q for _, q in net_sell)
            orders.append(NetOrder(symbol, "sell", round(shares, 6), round(shares * px, 2), sizing,
                                   [{"trader": s.trader, "shares": round(q, 6), "usd": round(q * px, 2)} for s, q in net_sell]))
    return orders, transfers


def build_plan(traders: list[Trader], states: dict[str, TraderState], signals: list[Signal],
               quotes: dict[str, float], today: str, max_day_usd: float | None = None) -> Plan:
    intents: list[Intent] = []
    events: list[dict] = []
    for t in traders:
        raw, ev = decide(t, states[t.name], signals)
        events.extend(ev)
        its, ev2 = size_intents(t, states[t.name], raw, quotes, today, max_day_usd)
        intents.extend(its)
        events.extend(ev2)
    orders, transfers = aggregate(intents, quotes)
    return Plan(intents, orders, transfers, events)
