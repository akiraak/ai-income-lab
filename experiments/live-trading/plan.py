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
    """口座に出す注文。⚠ **1 注文 1 トレーダー**（2026-09-19 の利用者決定「トレーダーごとの成績を正確に知りたいので別々に出す」）。"""
    symbol: str
    side: str                        # buy / sell
    shares: float                    # shares sizing の数量（notional の買いは 0 で value を使う）
    value_usd: float                 # notional の買いの金額（それ以外は参考値）
    sizing: str
    parts: list[dict] = field(default_factory=list)   # [{"trader", "shares", "usd"}] 誰の注文か（⚠ 常に 1 人。記録と画面がこの形を読む）


@dataclass
class Plan:
    intents: list[Intent]
    orders: list[NetOrder]
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


def to_orders(intents: list[Intent], quotes: dict[str, float]) -> list[NetOrder]:
    """意図 1 件を、その人の注文 1 本にする。⚠ **合算しない・内部移転しない**（2026-09-19 の利用者決定）。

    - 「トレーダーごとの成績を正確に知りたいので別々に出す」＝ 1 注文 1 トレーダー。約定価格も手数料もその人のものがそのまま残る
    - 「内部移転はダメです。トレーダーの実際の実績が検証できない」＝ A の売りと B の買いが同じ銘柄で重なっても、両方を口座に出す
      （それまでは口座に出さず、気配の中値・手数料 0 で付け替えていた ＝ その人 1 人では出せない値段が成績に入っていた）
    ⚠ **順は売りが先・買いが後**（同じ口座で同じ銘柄を買った直後に売る形を作らない。現金口座の決まり live-trading.md §0-6 とも合う）。
    代償: 同じ銘柄を n 人が売買する日は注文が n 本になる。
    """
    orders: list[NetOrder] = []
    for it in [i for i in intents if i.side == "sell"] + [i for i in intents if i.side == "buy"]:
        usd = round(it.usd, 2)
        shares = round(it.shares, 6) if it.side == "sell" else (it.shares if it.sizing == "shares" else 0.0)
        orders.append(NetOrder(it.symbol, it.side, shares, usd, it.sizing, [{"trader": it.trader, "shares": round(it.shares, 6), "usd": usd}]))
    return orders


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
    return Plan(intents, to_orders(intents, quotes), events)
