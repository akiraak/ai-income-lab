"""口座への執行: dry-run → 発注 → 約定確認 → 取消・再送。記録は `Masker` を通す（プラン §2 の 2・3）。

安全（§2 の 3）:
  - HALT があれば発注しない（取消は通す）
  - 本番の鍵は `ttclient.Client` の 3 段そのまま（dry-run ／ 取消 ／ 発注）。取消の鍵で発注は開かない
  - `Session offline` は `retry_interval` 秒おきに `retries` 回まで再送。再送の前に `external-identifier` で重複を探す
  - 認証失敗は再試行しない（IP ブロック）
  - 未約定は `cancel_after` 秒で取り消す
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
if SAMPLE_DIR not in sys.path:
    sys.path.insert(0, SAMPLE_DIR)

import record  # noqa: E402
from ttclient import ApiError, Client, ProductionGuard  # noqa: E402

from plan import NetOrder  # noqa: E402

ORDER_SOURCE = "ai-income-lab/live-trading"
WORKING = {"Received", "Routed", "In Flight", "Live", "Contingent", "Cancel Requested"}
FINAL = {"Filled", "Cancelled", "Rejected", "Expired", "Removed"}


def field_(obj, name):
    return obj.get(name) if isinstance(obj, dict) else None


def is_session_offline(exc: ApiError) -> bool:
    text = f"{exc.code} {exc.message} {exc.body}".lower()
    return "session offline" in text or "session_offline" in text


def is_transient(exc: ApiError) -> bool:
    """再送してよい拒否: `Session offline`（cert で市場時間内にも出る）と 5xx（nginx の HTML 502。2026-09-18 深夜の cert で実測）。

    ⚠ 注文の中身が悪い 4xx は再送しない。認証の 401 も再送しない（IP ブロック）。
    """
    return is_session_offline(exc) or exc.status >= 500


@dataclass
class Fill:
    symbol: str
    side: str
    shares: float
    price: float
    filled_at: str | None
    order_id: str | int | None
    fee_usd: float = 0.0


@dataclass
class ExecResult:
    order: NetOrder
    external_id: str
    dry_run: dict | None = None
    submitted: dict | None = None
    transitions: list[dict] = field(default_factory=list)
    final_status: str | None = None
    fills: list[Fill] = field(default_factory=list)
    attempts: int = 0
    error: dict | None = None
    cancelled: bool = False
    quote_at_signal: dict | None = None
    elapsed_ms: float | None = None


class Executor:
    def __init__(self, client: Client, account_number: str, rec: record.Recorder, halt_file: str,
                 mode: str = "dry-run", retries: int = 3, retry_interval: float = 60.0,
                 cancel_after: float = 600.0, poll_interval: float = 0.5, sleep=time.sleep):
        if mode not in ("plan", "dry-run", "submit"):
            raise ValueError(f"mode {mode!r} は無い（plan / dry-run / submit）")
        self.client = client
        self.account = account_number
        self.rec = rec
        self.halt_file = halt_file
        self.mode = mode
        self.retries = retries
        self.retry_interval = retry_interval
        self.cancel_after = cancel_after
        self.poll_interval = poll_interval
        self.sleep = sleep

    # ---------- 部品 ----------

    def halted(self) -> bool:
        return os.path.exists(self.halt_file)

    def build(self, order: NetOrder, external_id: str) -> dict:
        action = "Buy to Open" if order.side == "buy" else "Sell to Close"
        if order.sizing == "notional" and order.side == "buy":
            return self.client.build_equity_order(order.symbol, None, action=action, order_type="Notional Market",
                                                  value=f"{order.value_usd:.2f}", external_identifier=external_id, source=ORDER_SOURCE)
        qty = order.shares
        qty_s = str(int(qty)) if float(qty).is_integer() else f"{qty:.4f}"
        return self.client.build_equity_order(order.symbol, qty_s, action=action, order_type="Market",
                                              external_identifier=external_id, source=ORDER_SOURCE)

    def _fills_of(self, order_obj: dict) -> list[dict]:
        legs = field_(order_obj, "legs") or []
        return list((legs[0].get("fills") or []) if legs else [])

    # ---------- 1 注文 ----------

    def run_one(self, order: NetOrder, quote: dict | None = None) -> ExecResult:
        ext = f"lt-{uuid.uuid4().hex[:16]}"
        res = ExecResult(order=order, external_id=ext, quote_at_signal=quote)
        body = self.build(order, ext)
        t0 = time.perf_counter()
        try:
            if self.mode == "plan":
                res.final_status = "planned"
                return res
            if self.halted():
                res.final_status = "halted"
                res.error = {"type": "Halt", "message": f"HALT がある（{self.halt_file}）。発注しない"}
                return res
            for attempt in range(1, self.retries + 1):
                try:
                    res.dry_run = record.excerpt(self.client.dry_run_order(self.account, body), limit=12)
                    break
                except ApiError as exc:
                    if is_transient(exc) and attempt < self.retries:
                        res.transitions.append({"at_ms": round((time.perf_counter() - t0) * 1000, 1), "status": f"dry-run retry:{exc.status} {exc.code}"})
                        self.sleep(self.retry_interval)
                        continue
                    raise
            if self.mode == "dry-run":
                res.final_status = "dry-run"
                return res
            submitted = None
            for attempt in range(1, self.retries + 1):
                res.attempts = attempt
                if self.halted():
                    res.final_status = "halted"
                    res.error = {"type": "Halt", "message": "再送の前に HALT を見つけた"}
                    return res
                # 再送の前に「もう入っていないか」（API は重複排除しない）
                if attempt > 1:
                    found = self.client.find_order_by_external_id(self.account, ext)
                    if found:
                        submitted = {"order": found}
                        break
                try:
                    submitted = self.client.submit_order(self.account, body)
                    break
                except ApiError as exc:
                    if is_transient(exc) and attempt < self.retries:
                        res.transitions.append({"at_ms": round((time.perf_counter() - t0) * 1000, 1), "status": f"retry:{exc.status} {exc.code}"})
                        self.sleep(self.retry_interval)
                        continue
                    raise
            if not submitted:
                res.final_status = "not_submitted"
                return res
            order_obj = submitted["order"]
            order_id = field_(order_obj, "id")
            res.submitted = {"order_id": order_id, "status": field_(order_obj, "status"), "warnings": record.excerpt(submitted.get("warnings"))}
            res.transitions += self.client.wait_for_status(self.account, order_id, FINAL, timeout=self.cancel_after, interval=self.poll_interval)
            final = self.client.get_order(self.account, order_id)
            status = field_(final, "status")
            if status not in FINAL:
                # 窓の終わり: 未約定は取り消す
                try:
                    self.client.cancel_order(self.account, order_id)
                    res.cancelled = True
                    res.transitions += self.client.wait_for_status(self.account, order_id, FINAL, timeout=30, interval=self.poll_interval)
                    final = self.client.get_order(self.account, order_id)
                    status = field_(final, "status")
                except (ApiError, ProductionGuard) as exc:
                    res.error = {"type": type(exc).__name__, "message": str(exc)[:300]}
            res.final_status = status
            for f in self._fills_of(final):
                res.fills.append(Fill(order.symbol, order.side, float(f.get("quantity") or 0), float(f.get("fill-price") or 0),
                                      f.get("filled-at"), order_id))
            return res
        except ProductionGuard as exc:
            res.final_status = "guarded"
            res.error = {"type": "ProductionGuard", "message": str(exc)}
            return res
        except ApiError as exc:
            res.final_status = "error"
            res.error = {"type": "ApiError", "status": exc.status, "code": exc.code, "message": exc.message[:300], "body": record.excerpt(exc.body, limit=10)}
            return res
        finally:
            res.elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    def run_all(self, orders: list[NetOrder], quotes_raw: dict[str, dict]) -> list[ExecResult]:
        results = []
        for o in orders:
            results.append(self.run_one(o, quotes_raw.get(o.symbol)))
        return results


def allocate_fills(res: ExecResult) -> list[dict]:
    """1 注文の約定を、誰の何株ぶんかで按分する（合算して出した注文をトレーダーの台帳に戻す）。

    約定価格は加重平均、数量は parts の比で配る（端数は最後の人に寄せる）。
    """
    total_qty = sum(f.shares for f in res.fills)
    if total_qty <= 0:
        return []
    avg = sum(f.shares * f.price for f in res.fills) / total_qty
    parts = res.order.parts
    want = sum(p["shares"] for p in parts)
    out = []
    given = 0.0
    for i, p in enumerate(parts):
        share = total_qty * (p["shares"] / want) if want > 0 else 0.0
        if i == len(parts) - 1:
            share = total_qty - given
        share = round(share, 6)
        given += share
        out.append({"trader": p["trader"], "symbol": res.order.symbol, "side": res.order.side, "shares": share, "price": round(avg, 4)})
    return out
