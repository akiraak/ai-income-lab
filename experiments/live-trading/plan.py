"""合図 → 状態機械 → 目標 → 差分 → 予算の検査 → 3 人を銘柄ごとに合算 → 注文（プラン §2-1・§2-3）。

⚠ **純粋関数**（読み書きしない・API を呼ばない）。気配は引数で受ける。

状態機械は feature-discovery の `simulate()`（rules.md 13-4）と同じ向き:
  未保有で 買い% > θ → 建てる ／ 保有中で 出口% > θ → 手仕舞う ／ それ以外は何もしない（買い専用・買い増しなし）。
役割のある人（2026-10-18。プラン trader-model-roles.md §4 の 3・rules.md 25-2 の 3・25-4）はこれに 3 つ足す:
  最短保有 `min_hold_days` ＝ 建ててから d 営業日未満（NYSE の暦で `opened` から数える）は出口% を読まない ／
  損切り係の「毎日読む出口%」`Signal.stop` > θ なら最短保有の中でも手仕舞う ／
  `stop_blocks_buy` ＝ 損切り係が言っている日は買い% > θ でも建てない。
⚠ 役割の無い人（`stop` が None・`min_hold_days` が None）は 1 ビットも変わらない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date as _date, timedelta

from signals import Signal
from state import TraderState
from trader import Trader


@dataclass
class DayCap:
    """1 日の買いの合計の上限（§0-2）。⚠ **全トレーダー・全起動を通して 1 つ**を使い回す。

    ⚠ 2026-09-20 まではローカル変数で数えていたので、**トレーダーごと・1 起動ごとに 0 から**になっていた
    （3 人なら実質 3 倍）。いまは `run_day` が**その日の記録から `spent` を数え直して**作る（DB は持たない）。
    """
    limit: float | None = None
    spent: float = 0.0

    def allows(self, usd: float) -> bool:
        return self.limit is None or self.spent + usd <= self.limit + 1e-9

    def take(self, usd: float) -> None:
        self.spent = round(self.spent + usd, 6)


# 口座へ出したが金額が残らない終わり方（取消・拒否・エラー）。⚠ これらは「使った」に数えない
SPENT_EXCLUDE = {"Cancelled", "Rejected", "Expired", "error", "halted", "guarded", "not_submitted", "dry-run", "planned"}


def spent_today(orders: list[dict]) -> float:
    """その日すでに買いに使った額（⚠ **記録が正本**。`out/<日付>/orders.jsonl` の行をそのまま渡す）。

    数えるのは**口座へ出した買い**（`mode == "submit"`）だけ。約定したものは**約定額**、まだ分からないものは
    **注文額**（⚠ 保守側 ＝ 多めに数えて買いを絞る）。取消・拒否・エラー・dry-run・売りは数えない。
    """
    total = 0.0
    for o in orders:
        if not isinstance(o, dict) or o.get("side") != "buy" or o.get("mode") != "submit":
            continue
        filled = sum(float(f.get("shares", 0) or 0) * float(f.get("price", 0) or 0) for f in o.get("fills") or [])
        if filled:
            total += filled
        elif str(o.get("final_status")) not in SPENT_EXCLUDE:
            total += float(o.get("value_usd") or 0)
    return round(total, 6)


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


def held_trading_days(opened: str, today: str, calendar=None) -> int:
    """建ててから今日までの営業日の数 ＝ `opened` の翌日から `today` までの NYSE の営業日（休場日・土日は数えない）。

    建てた日は 0・翌営業日が 1。`today` が `opened` 以前なら 0。暦は `market_calendar.nyse()`（管理画面・発注できる時間帯と同じもの）。
    """
    if calendar is None:
        import market_calendar
        calendar = market_calendar.nyse()
    o, d = _date.fromisoformat(opened), _date.fromisoformat(today)
    if d <= o:
        return 0
    return len(calendar.trading_days(o + timedelta(days=1), d))


def decide(trader: Trader, state: TraderState, signals: list[Signal], today: str | None = None, calendar=None) -> tuple[list[dict], list[dict]]:
    """状態機械。戻り値は (意図の素, 事象)。意図の素は {"symbol", "side", buy_pct, exit_pct}。

    `today` は最短保有（`min_hold_days`）のある人にだけ要る（無ければ ValueError ＝ 設定の誤りで「読まずに売る」をしない）。
    役割のある人の意図の素と事象には `stop_pct`・`reason`（exit ／ stop）・最短保有の数字が付く。役割の無い人は 1 ビットも変わらない。
    """
    raw: list[dict] = []
    events: list[dict] = []
    for sg in signals:
        if sg.trader != trader.name:
            continue
        pos = state.position(sg.symbol)
        stop_says = sg.stop is not None and sg.stop > trader.threshold      # 損切り係（役割の無い人は常に False）
        extra = {"stop_pct": sg.stop} if trader.has_roles else {}
        if pos == 0:
            if sg.buy > trader.threshold and trader.stop_blocks_buy and stop_says:
                events.append({"kind": "stop_blocks_buy", "trader": trader.name, "symbol": sg.symbol, "buy_pct": sg.buy, "exit_pct": sg.exit, "stop_pct": sg.stop,
                               "note": "損切り係が出口を言っている日は買わない（stop_blocks_buy。rules.md 25-4）"})
            elif sg.buy > trader.threshold:
                raw.append({"symbol": sg.symbol, "side": "buy", "buy_pct": sg.buy, "exit_pct": sg.exit, **extra})
            else:
                events.append({"kind": "skip", "trader": trader.name, "symbol": sg.symbol, "buy_pct": sg.buy, "exit_pct": sg.exit, **extra})
            continue
        # ---- 保有中
        muted: dict = {}
        if trader.min_hold_days is not None:
            if today is None:
                raise ValueError(f"{trader.name}: 最短保有（min_hold_days）のある人は today が要る")
            held = held_trading_days(state.holdings[sg.symbol].opened, today, calendar)
            if held < trader.min_hold_days:
                muted = {"held_days": held, "min_hold_days": trader.min_hold_days, "exit_muted": True}
        if sg.exit > trader.threshold and not muted:
            raw.append({"symbol": sg.symbol, "side": "sell", "buy_pct": sg.buy, "exit_pct": sg.exit, **extra, **({"reason": "exit"} if trader.has_roles else {})})
        elif stop_says:
            raw.append({"symbol": sg.symbol, "side": "sell", "buy_pct": sg.buy, "exit_pct": sg.exit, **extra, "reason": "stop"})
            events.append({"kind": "stop_exit", "trader": trader.name, "symbol": sg.symbol, "exit_pct": sg.exit, "stop_pct": sg.stop, **muted,
                           "note": "損切り係の出口% が売買基準値を超えた（最短保有の中でも読む）"})
        else:
            events.append({"kind": "hold", "trader": trader.name, "symbol": sg.symbol, "buy_pct": sg.buy, "exit_pct": sg.exit, **extra, **muted})
    return raw, events


def liquidation_raw(trader: Trader, state: TraderState) -> tuple[list[dict], list[dict]]:
    """手じまいの印（control.py）が立っている人: 合図を読まず、持ち株の全部を「売り」の意図の素にする（買いは組まない）。

    ⚠ `decide` は変えない（印の無い人は 1 ビットも変わらない）。買い% ／ 出口% は無いので 0 ／ 100 を置く（記録の形を揃えるため）。
    """
    raw = [{"symbol": sym, "side": "sell", "buy_pct": 0.0, "exit_pct": 100.0, "liquidate": True}
           for sym, h in sorted(state.holdings.items()) if h.shares > 0]
    events = [{"kind": "liquidate_flag", "trader": trader.name, "symbols": [r["symbol"] for r in raw],
               "note": "手じまい中なので、合図を読まず持ち株を全部売る（買いは出さない）"}]
    return raw, events


def size_intents(trader: Trader, state: TraderState, raw: list[dict], quotes: dict[str, float], today: str,
                 max_day_usd: float | DayCap | None = None) -> tuple[list[Intent], list[dict]]:
    """株数と金額を決め、⚠ **予算の上限で買いを拒む**（プラン §2-3）。

    予算の空き ＝ 予算 − 建玉の取得原価 − 受渡し待ちの売却代金（現金口座の T+1。保守側）。
    買いは合図の順（銘柄集合の並び）に入れ、空きを超えたものから見送る。
    """
    intents: list[Intent] = []
    events: list[dict] = []
    per_symbol = trader.per_symbol_usd
    available = trader.budget_usd - state.cost_in_use() - state.unsettled(today)
    # ⚠ 1 日の上限は **全トレーダー・全起動で 1 つ**（§0-2）。数値で渡されたときは、その場限りの上限として包む
    cap = max_day_usd if isinstance(max_day_usd, DayCap) else DayCap(max_day_usd)
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
        if not cap.allows(usd):
            events.append({"kind": "over_day_cap", "trader": trader.name, "symbol": symbol, "usd": round(usd, 2),
                           "cap": cap.limit, "spent_today_usd": round(cap.spent, 2)})
            continue
        available -= usd
        cap.take(usd)
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
               quotes: dict[str, float], today: str, max_day_usd: float | DayCap | None = None) -> Plan:
    intents: list[Intent] = []
    events: list[dict] = []
    cap = max_day_usd if isinstance(max_day_usd, DayCap) else DayCap(max_day_usd)   # ⚠ 1 日の上限は全員で 1 つ
    for t in traders:
        raw, ev = decide(t, states[t.name], signals, today)
        events.extend(ev)
        its, ev2 = size_intents(t, states[t.name], raw, quotes, today, cap)
        intents.extend(its)
        events.extend(ev2)
    return Plan(intents, to_orders(intents, quotes), events)
