"""トレーダー × 銘柄の状態（0 ／ 1・持ち分・取得単価・受渡し待ち）。口座は合算しか見せないので執行器が持つ。

1 人 1 ファイル `state/<名前>.json`。⚠ **書き換えは原子的に**（tmp → rename）。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field


# 注文の数量は小数 4 桁（`execute.Executor.build`）、按分した持ち分は 6 桁。合算注文の按分でできた端数の持ち分（例 2.992573）を
# 全部売ると、約定の数量は 2.9926 で返る。⚠ この差で台帳が落ちると「約定したのに状態が保存されない」。逆に少なく約定した残り（0.000028 株）を
# 建玉として残すと、次の日に数量 0.0000 の売りを出し、買い直しもできない（どちらも 2026-09-19 にシミュレーションで見つけた）
QTY_TOL = 1e-4


@dataclass
class Holding:
    shares: float
    avg_price: float
    opened: str  # 建てた日（YYYY-MM-DD）

    @property
    def cost(self) -> float:
        return self.shares * self.avg_price


@dataclass
class TraderState:
    name: str
    holdings: dict[str, Holding] = field(default_factory=dict)
    realized_usd: float = 0.0          # 実現損益の累計（手数料・規制費は fees_usd に別建て）
    fees_usd: float = 0.0
    pending_settlement: list[dict] = field(default_factory=list)  # [{"date": 売った日, "amount": 代金}]。現金口座の T+1
    last_date: str | None = None
    history: list[dict] = field(default_factory=list)  # 持ち分の変化（監査用。1 約定 1 行）

    def position(self, symbol: str) -> int:
        return 1 if symbol in self.holdings and self.holdings[symbol].shares > 0 else 0

    def cost_in_use(self) -> float:
        """建玉の取得原価の合計 ＝ 予算のうち使っている分。"""
        return sum(h.cost for h in self.holdings.values())

    def unsettled(self, today: str, settle_days: int = 1) -> float:
        """受渡し前の売却代金。⚠ 現金口座では受渡し（T+1）の前に再投資すると good faith violation になりうる（live-trading.md §0-6）。

        暦日で 1 日を数える。売った日の次の営業日がそのまま T+1 なので、営業日にしか動かない執行器では営業日で数えるのと同じ結果になる
        （同日に売った代金は受渡し待ち ＝ その日の買いには使わない）。
        """
        from datetime import date as _date, timedelta
        d = _date.fromisoformat(today)
        total = 0.0
        for p in self.pending_settlement:
            if _date.fromisoformat(p["date"]) + timedelta(days=settle_days) > d:
                total += float(p["amount"])
        return total

    def apply_buy(self, symbol: str, shares: float, price: float, date: str, fee: float = 0.0, note: str = "") -> None:
        h = self.holdings.get(symbol)
        if h and h.shares > 0:
            new_shares = h.shares + shares
            h.avg_price = (h.cost + shares * price) / new_shares
            h.shares = new_shares
        else:
            self.holdings[symbol] = Holding(shares=shares, avg_price=price, opened=date)
        self.fees_usd += fee
        self.history.append({"date": date, "symbol": symbol, "side": "buy", "shares": shares, "price": price, "fee": fee, "note": note})

    def apply_sell(self, symbol: str, shares: float, price: float, date: str, fee: float = 0.0, note: str = "") -> None:
        h = self.holdings.get(symbol)
        if not h or h.shares <= 0:
            raise ValueError(f"{self.name}: {symbol} を持っていないのに売れない")
        if shares > h.shares + QTY_TOL:
            raise ValueError(f"{self.name}: {symbol} の持ち分 {h.shares} を超える売り {shares}")
        shares = min(shares, h.shares)   # 丸めの差（下の QTY_TOL）は持ち分に合わせる ＝ 全部売ったら 0 になる
        self.realized_usd += (price - h.avg_price) * shares
        self.fees_usd += fee
        h.shares = round(h.shares - shares, 6)
        if h.shares <= QTY_TOL:   # 丸めの残り（例 0.997728 を 0.9977 で売った残り 0.000028）は建玉として残さない ＝ 次の日に「0 株の売り」を出さない
            del self.holdings[symbol]
        self.pending_settlement.append({"date": date, "amount": shares * price})
        self.history.append({"date": date, "symbol": symbol, "side": "sell", "shares": shares, "price": price, "fee": fee, "note": note})

    def to_dict(self) -> dict:
        d = asdict(self)
        d["holdings"] = {k: asdict(v) for k, v in self.holdings.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TraderState":
        st = cls(name=d["name"])
        st.holdings = {k: Holding(**v) for k, v in (d.get("holdings") or {}).items()}
        st.realized_usd = float(d.get("realized_usd", 0.0))
        st.fees_usd = float(d.get("fees_usd", 0.0))
        st.pending_settlement = list(d.get("pending_settlement") or [])
        st.last_date = d.get("last_date")
        st.history = list(d.get("history") or [])
        return st


def state_path(state_dir: str, name: str) -> str:
    return os.path.join(state_dir, f"{name}.json")


def load_state(state_dir: str, name: str) -> TraderState:
    path = state_path(state_dir, name)
    if not os.path.exists(path):
        return TraderState(name=name)
    with open(path, encoding="utf-8") as f:
        return TraderState.from_dict(json.load(f))


def save_state(state_dir: str, st: TraderState) -> str:
    os.makedirs(state_dir, exist_ok=True)
    path = state_path(state_dir, st.name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st.to_dict(), f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path
