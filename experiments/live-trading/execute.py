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
    fees: dict | None = None      # dry-run の fee-calculation（丸ごと）
    bp_effect: dict | None = None  # dry-run の buying-power-effect（丸ごと）
    submitted_at: float | None = None  # 発注した時刻（単調な時計。取消の期限を数えるためだけ・記録には出さない）

    def amounts(self) -> dict:
        """金額の内訳（2026-09-19 の利用者決定「手数料など金額の内訳も保存する」）。1 注文 1 トレーダーなので、そのままその人の内訳になる。

        ⚠ 手数料の出どころは **dry-run の見積り**（発注の前に API が返す `fee-calculation`）。約定しなかった注文は 0。
        """
        gross = sum(f.shares * f.price for f in self.fills)
        fee = fee_total_usd(self.fees) if self.fills else 0.0
        sign = -1.0 if self.order.side == "buy" else 1.0
        return {"gross_usd": round(gross, 4), "fee_usd": round(fee, 4), "fee_source": "dry_run_estimate" if self.fees is not None else None,
                "net_usd": round(sign * gross - fee, 4), "fee_breakdown": self.fees, "buying_power_effect": self.bp_effect}


def fee_total_usd(fees: dict | None) -> float:
    """`fee-calculation` の合計を「払う額（正）」にする。Credit（戻り）は負。"""
    try:
        total = abs(float((fees or {}).get("total-fees") or 0))
    except (TypeError, ValueError):
        return 0.0
    return -total if (fees or {}).get("total-fees-effect") == "Credit" else total


class Executor:
    def __init__(self, client: Client, account_number: str, rec: record.Recorder, halt_file: str,
                 mode: str = "dry-run", retries: int = 3, retry_interval: float = 60.0,
                 cancel_after: float = 600.0, poll_interval: float = 0.5, sleep=time.sleep, clock=None, journal=None,
                 pipeline: bool = True):
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
        self.journal = journal if mode == "submit" else None   # 控え（journal.py）。発注の直前と直後に書く
        self.pipeline = pipeline   # submit のとき 2 段（先に全部出す → 約定をまとめて確かめる）。False で直列
        self.clock = clock   # 仮の時計（シミュレーション）。None なら約定待ちは今までどおり `client.wait_for_status`（本物の時計）

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

    def wait_for_status(self, order_id, targets: set[str], timeout: float) -> list[dict]:
        """約定待ち。仮の時計があるときは、取消までの秒数（`cancel_after`）を仮の時計の上で数える。"""
        if self.clock is None:
            return self.client.wait_for_status(self.account, order_id, targets, timeout=timeout, interval=self.poll_interval)
        transitions: list[dict] = []
        started = self.clock.monotonic()
        last = None
        while self.clock.monotonic() - started < timeout:
            status = field_(self.client.get_order(self.account, order_id), "status")
            if status != last:
                transitions.append({"at_ms": round((self.clock.monotonic() - started) * 1000, 1), "status": status})
                last = status
            if status in targets:
                break
            self.clock.sleep(self.poll_interval)
        return transitions

    def _fills_of(self, order_obj: dict) -> list[dict]:
        legs = field_(order_obj, "legs") or []
        return list((legs[0].get("fills") or []) if legs else [])

    # ---------- 1 注文 ----------

    def _now(self) -> float:
        return self.clock.monotonic() if self.clock is not None else time.monotonic()

    def run_one(self, order: NetOrder, quote: dict | None = None) -> ExecResult:
        """1 注文を最後まで（dry-run → 発注 → 約定待ち → 未約定は取消）。"""
        res, order_id = self.submit_one(order, quote)
        return res if order_id is None else self.finish_one(res, order_id)

    def submit_one(self, order: NetOrder, quote: dict | None = None) -> tuple[ExecResult, object]:
        """前半: 控え → dry-run → 発注まで。戻り値の 2 つ目は注文番号（発注に至らなかったら None ＝ `res` はもう確定）。"""
        ext = f"lt-{uuid.uuid4().hex[:16]}"
        res = ExecResult(order=order, external_id=ext, quote_at_signal=quote)
        body = self.build(order, ext)
        t0 = time.perf_counter()
        try:
            if self.mode == "plan":
                res.final_status = "planned"
                return res, None
            if self.halted():
                res.final_status = "halted"
                res.error = {"type": "Halt", "message": f"HALT がある（{self.halt_file}）。発注しない"}
                return res, None
            for attempt in range(1, self.retries + 1):
                try:
                    preview = self.client.dry_run_order(self.account, body)
                    res.dry_run = record.excerpt(preview, limit=12)
                    res.fees, res.bp_effect = field_(preview, "fee-calculation"), field_(preview, "buying-power-effect")
                    break
                except ApiError as exc:
                    if is_transient(exc) and attempt < self.retries:
                        res.transitions.append({"at_ms": round((time.perf_counter() - t0) * 1000, 1), "status": f"dry-run retry:{exc.status} {exc.code}"})
                        self.sleep(self.retry_interval)
                        continue
                    raise
            if self.mode == "dry-run":
                res.final_status = "dry-run"
                return res, None
            submitted = None
            if self.journal:
                self.journal.intent(ext, order, fee_total_usd(res.fees))   # ⚠ 発注より先に控える（落ちた後、誰の注文かを確実に戻すため）
            for attempt in range(1, self.retries + 1):
                res.attempts = attempt
                if self.halted():
                    res.final_status = "halted"
                    res.error = {"type": "Halt", "message": "再送の前に HALT を見つけた"}
                    return res, None
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
                return res, None
            order_obj = submitted["order"]
            order_id = field_(order_obj, "id")
            if self.journal:
                self.journal.submitted(ext, order_id)
            res.submitted = {"order_id": order_id, "status": field_(order_obj, "status"), "warnings": record.excerpt(submitted.get("warnings"))}
            res.submitted_at = self._now()
            return res, order_id
        except ProductionGuard as exc:
            res.final_status = "guarded"
            res.error = {"type": "ProductionGuard", "message": str(exc)}
            return res, None
        except ApiError as exc:
            res.final_status = "error"
            res.error = {"type": "ApiError", "status": exc.status, "code": exc.code, "message": exc.message[:300], "body": record.excerpt(exc.body, limit=10)}
            return res, None
        finally:
            res.elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    def finish_one(self, res: ExecResult, order_id) -> ExecResult:
        """後半: 約定を待ち、未約定は取り消し、約定を読む。⚠ **取消までの秒数は「その注文を出した時刻」から数える**
        （まとめて待つ形でも、1 本ごとの期限は直列のときと同じ ＝ 発注から `cancel_after` 秒）。"""
        order = res.order
        t0 = time.perf_counter()
        try:
            left = max(0.0, self.cancel_after - (self._now() - (res.submitted_at if res.submitted_at is not None else self._now())))
            res.transitions += self.wait_for_status(order_id, FINAL, left)
            final = self.client.get_order(self.account, order_id)
            status = field_(final, "status")
            if status not in FINAL:
                # 窓の終わり: 未約定は取り消す
                try:
                    self.client.cancel_order(self.account, order_id)
                    res.cancelled = True
                    res.transitions += self.wait_for_status(order_id, FINAL, 30)
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
            res.elapsed_ms = round((res.elapsed_ms or 0.0) + (time.perf_counter() - t0) * 1000, 1)

    def run_all(self, orders: list[NetOrder], quotes_raw: dict[str, dict], on_result=None) -> list[ExecResult]:
        """`on_result(res)` は 1 注文が片付くたびに呼ぶ（台帳の保存と控えの done を注文ごとに済ませるため）。

        ⚠ **発注するとき（submit）は 2 段**（2026-09-20。利用者決定 D6）: ① 全部の注文を順に「控え → dry-run → 発注」まで出し、
        ② その後で 1 本ずつ約定を確かめる。1 本ごとに約定を待つ直列の形だと、待ちが本数ぶん積み上がって窓に収まらない
        （120 本で約 19 分【推測。sandbox の実測 dry-run 0.9 ＋ 発注 1.9 ＋ 約定 6.8 秒から】）。
        順（売りが先・買いが後）・控え・1 注文ごとの台帳の保存・1 本ごとの取消の期限は直列のときと同じ。
        ①の途中で落ちても、出した注文は控えに注文番号つきで残る ＝ 次の起動が照会して台帳に戻す（§0-8 の段 1）。
        `pipeline=False`（`run_day.py --serial`）で元の直列に戻せる。
        """
        results: list[ExecResult] = []
        if self.mode != "submit" or not self.pipeline:
            for o in orders:
                res = self.run_one(o, quotes_raw.get(o.symbol))
                if on_result:
                    on_result(res)
                results.append(res)
            return results
        pending: list[tuple[ExecResult, object]] = []
        for o in orders:
            res, order_id = self.submit_one(o, quotes_raw.get(o.symbol))
            results.append(res)
            if order_id is None:
                if on_result:
                    on_result(res)          # 発注に至らなかった（拒否・HALT など）＝ もう確定
            else:
                pending.append((res, order_id))
        for res, order_id in pending:
            self.finish_one(res, order_id)
            if on_result:
                on_result(res)
        return results


def allocate_fills(res: ExecResult) -> list[dict]:
    """1 注文の約定をトレーダーの台帳に戻す。⚠ 2026-09-19 から 1 注文 1 トレーダーなので、約定も手数料もその人に全部付く
    （`parts` が複数の形も読めるまま残す ＝ 数量と手数料は parts の比で配り、端数は最後の人に寄せる）。約定価格は加重平均。
    """
    total_qty = sum(f.shares for f in res.fills)
    if total_qty <= 0:
        return []
    avg = sum(f.shares * f.price for f in res.fills) / total_qty
    parts = res.order.parts
    want = sum(p["shares"] for p in parts)
    fee_total = res.amounts()["fee_usd"]
    out = []
    given = 0.0
    for i, p in enumerate(parts):
        share = total_qty * (p["shares"] / want) if want > 0 else 0.0
        if i == len(parts) - 1:
            share = total_qty - given
        share = round(share, 6)
        given += share
        out.append({"trader": p["trader"], "symbol": res.order.symbol, "side": res.order.side, "shares": share, "price": round(avg, 4),
                    "fee": round(fee_total * (share / total_qty), 4)})
    return out
