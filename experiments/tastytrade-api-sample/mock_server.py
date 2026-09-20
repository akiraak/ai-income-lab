#!/usr/bin/env python3
"""sample.py を資格情報なしで検証するためのモックサーバ。

公式 OpenAPI 3.1 仕様・sandbox の疑似約定規則・streaming の AsyncAPI に合わせて
最小限だけ実装してある。**tastytrade の挙動の【実測】には使えない**（自作の模型なので）。
サンプルコードの配線（ヘッダ・JSON の形・状態遷移の追い方・記録）を確かめるためだけのもの。

    python3 mock_server.py --port 8765 &
    TT_REST_BASE=http://127.0.0.1:8765 TT_ACCOUNT_STREAMER=ws://127.0.0.1:8766 \
    TT_CLIENT_SECRET=x TT_REFRESH_TOKEN=y python3 sample.py --step all --seconds 5
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import itertools
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ACCOUNT = "5WT00042"
STATE = {
    "orders": {},          # id -> order
    "positions": {},       # symbol -> quantity
    "order_seq": itertools.count(1),
    "events": [],          # 口座ストリーマに流す通知
    "market_data": False,  # False なら cert と同じく 502
    "fill_noise": 0.0,  # >0 なら成行を「気配 × (1 ± noise)」で約定させる（実売買のモック。既定は sandbox と同じ $1）
    "quote": 560.11,  # 気配の中心（--fill-noise のときは日ごとにここも揺らす）
    "quotes": {},  # 銘柄ごとの気配 {symbol: {"mid": .., "spread_bp": ..}}（実売買のシミュレーション用。無い銘柄は上の 1 本）
    "faults": {},  # 故障の注入（/_mock/fault）。{種類: 残り回数}。使うたびに 1 減る ＝ 注文の無い日をまたいで、次の該当の要求で起きる
    "sim_control": None,  # 仮の時計の control.json（--sim-control）。None なら本物の時計・遅延もそのまま
    "rng": None,
    "rate_limit_after": 0,  # >0 ならその回数を超えた照会に 429
    "request_count": 0,
    "dxlink_url": "ws://127.0.0.1:8767",
}
LOCK = threading.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def fake_jwt(lifetime_s: int = 900) -> str:
    iat = int(time.time())
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return ".".join(
        [
            seg({"alg": "RS256", "typ": "JWT"}),
            seg({"iat": iat, "exp": iat + lifetime_s, "scope": "read trade openid", "sub": "mock-user"}),
            "mocksignature",
        ]
    )


def sim_clock() -> dict | None:
    """仮の時計（live-trading の `simclock.py` と同じ 1 ファイル・同じ式）。読めなければ本物の時計に落ちる（モックは記録の時刻欄にしか使わない）。"""
    if not STATE["sim_control"]:
        return None
    try:
        with open(STATE["sim_control"], encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def now_utc() -> datetime:
    ctl = sim_clock()
    if not ctl:
        return datetime.now(timezone.utc)
    moving = not ctl["paused"] and ctl["speed"] != "max"
    return datetime.fromtimestamp(ctl["sim_epoch"] + ((time.time() - ctl["real_epoch"]) * ctl["speed"] if moving else 0), timezone.utc)


def wait(seconds: float) -> None:
    """状態遷移の遅延。仮の時計があれば速さで縮める（最速は待たない）＝ 速さを上げても「取消までに約定する」が変わらない。"""
    ctl = sim_clock()
    if not ctl:
        time.sleep(seconds)
    elif ctl["speed"] != "max":
        time.sleep(seconds / ctl["speed"])


def quote_of(symbol: str | None) -> tuple[float, float, float]:
    """(bid, ask, mid)。銘柄ごとの気配があればそれ、無ければ全銘柄で 1 本（今までどおり ±$0.01）。"""
    q = STATE["quotes"].get(symbol)
    if not q:
        return STATE["quote"] - 0.01, STATE["quote"] + 0.01, STATE["quote"]
    half = q["mid"] * q.get("spread_bp", 2.0) / 2 / 1e4
    return q["mid"] - half, q["mid"] + half, q["mid"]


FAULT_KINDS = ("session_offline", "http_5xx", "http_429", "auth_5xx", "auth_401", "reject_funds", "no_fill")


def take_fault(kind: str) -> bool:
    """故障を 1 回ぶん使う。⚠ 拒否の文面・コードは想像で作ったもの（tastytrade の【実測】ではない。Session offline だけ 2026-09-08 の実物の写し）。"""
    with LOCK:
        left = STATE["faults"].get(kind, 0)
        if left <= 0:
            return False
        STATE["faults"][kind] = left - 1
        return True


def envelope(data, context: str) -> dict:
    return {"data": data, "context": context}


def make_order(body: dict, status: str) -> dict:
    order_id = next(STATE["order_seq"])
    leg = body["legs"][0]
    return {
        "id": order_id,
        "account-number": ACCOUNT,
        "time-in-force": body.get("time-in-force"),
        "order-type": body.get("order-type"),
        "price": body.get("price"),
        "price-effect": body.get("price-effect"),
        "size": leg.get("quantity"),
        "value": body.get("value"),
        "value-effect": body.get("value-effect"),
        "underlying-symbol": leg["symbol"],
        "underlying-instrument-type": leg["instrument-type"],
        "status": status,
        "cancellable": status in ("Received", "Routed", "Live"),
        "editable": True,
        "edited": False,
        "external-identifier": body.get("external-identifier"),
        "ext-client-order-id": f"mock-{order_id}",
        "received-at": now_utc().isoformat(timespec="milliseconds"),
        "legs": [
            {
                "instrument-type": leg["instrument-type"],
                "symbol": leg["symbol"],
                "quantity": leg.get("quantity"),
                "remaining-quantity": leg.get("quantity"),
                "action": leg["action"],
                "fills": [],
            }
        ],
    }


def fills_immediately(body: dict) -> bool:
    """sandbox の規則: 成行は常に $1 で約定、$3 未満の指値は即約定、$3 以上は Live のまま。"""
    if body.get("order-type") in ("Market", "Notional Market"):
        return True
    try:
        return float(body.get("price", "0")) < 3.0
    except (TypeError, ValueError):
        return False


def advance(order_id: int) -> None:
    """Received → Routed → Live →（約定するなら）Filled と時間をかけて進める。"""
    def run():
        for status, delay in (("Routed", 0.15), ("In Flight", 0.1), ("Live", 0.2)):
            wait(delay)
            with LOCK:
                order = STATE["orders"].get(order_id)
                if not order or order["status"] in ("Cancelled", "Filled", "Rejected"):
                    return
                order["status"] = status
                STATE["events"].append({"type": "Order", "data": dict(order), "timestamp": now_ms()})
        if order_id in STATE["fill_wanted"]:
            wait(0.3)
            with LOCK:
                order = STATE["orders"].get(order_id)
                if not order or order["status"] != "Live":
                    return
                leg = order["legs"][0]
                if order["order-type"] in ("Market", "Notional Market"):
                    if STATE["fill_noise"] > 0:
                        # 実売買のモック: 気配 × (1 ± noise)。買いは高く・売りは安く寄る（スプレッドの半分に相当）
                        rng = STATE["rng"]
                        drift = rng.uniform(0, STATE["fill_noise"]) if leg["action"].startswith("Buy") else -rng.uniform(0, STATE["fill_noise"])
                        price = f"{quote_of(leg['symbol'])[2] * (1 + drift):.2f}"
                    else:
                        price = "1.00"
                else:
                    price = order.get("price")
                if order["order-type"] == "Notional Market":
                    # 金額指定: 数量 = 金額 ÷ 約定価格（小数）
                    leg["quantity"] = f"{float(order['value']) / float(price):.4f}"
                    order["size"] = leg["quantity"]
                leg["remaining-quantity"] = "0"
                leg["fills"] = [
                    {
                        "quantity": leg["quantity"],
                        "fill-price": price,
                        "filled-at": now_utc().isoformat(timespec="milliseconds"),
                        "destination-venue": "MOCK",
                    }
                ]
                order["status"] = "Filled"
                order["cancellable"] = False
                symbol, qty = leg["symbol"], float(leg["quantity"])
                signed = qty if leg["action"].startswith("Buy") else -qty
                total = round(STATE["positions"].get(symbol, 0) + signed, 6)
                STATE["positions"][symbol] = int(total) if total == int(total) else total
                STATE["events"].append({"type": "Order", "data": dict(order), "timestamp": now_ms()})

    threading.Thread(target=run, daemon=True).start()


STATE["fill_wanted"] = set()


class Handler(BaseHTTPRequestHandler):
    # ヘッダと本文が別の send になるので、Nagle ＋ 遅延 ACK で 1 応答 40ms 待たされる（2026-09-19 にシミュレーションの約定待ち 1,200 回で 54 秒【実測】）
    disable_nagle_algorithm = True
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 既定のログはうるさいので落とす
        pass

    # ---------- 送受信 ----------

    def _send(self, code: int, payload) -> None:
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, code: int, error_code: str, message: str) -> None:
        self._send(code, {"error": {"code": error_code, "message": message}})

    def _bad_gateway(self) -> None:
        body = b"<html><head><title>502 Bad Gateway</title></head></html>"   # cert の nginx と同じ形（HTML）
        self.send_response(502)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _check_headers(self, need_auth: bool = True) -> bool:
        ua = self.headers.get("User-Agent") or ""
        if "/" not in ua:
            # 本物は nginx が HTML の 401 を返す。それに寄せる
            body = b"<html><head><title>401 Authorization Required</title></head></html>"
            self.send_response(401)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False
        if need_auth and not (self.headers.get("Authorization") or "").startswith("Bearer "):
            self._error(401, "unauthorized", "No valid access token was provided")
            return False
        return True

    def _rate_limited(self) -> bool:
        limit = STATE["rate_limit_after"]
        if not limit:
            return False
        with LOCK:
            STATE["request_count"] += 1
            over = STATE["request_count"] > limit
        if over:
            self._error(429, "too_many_requests", "Request rate exceeded")
        return over

    # ---------- ルーティング ----------

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/_mock/quote":
            # 実売買のモック専用: 「翌営業日」の気配に進める（認証なし。テストの台本が叩く）
            #   {"quote": 100.5}                                   全銘柄で 1 本（今までどおり）
            #   {"quotes": {"T": 25.6, "SPY": 765.9}, "spread_bp": 2}  銘柄ごと（シミュレーション。前の日の気配は捨てる）
            body = self._body()
            with LOCK:
                STATE["quote"] = float(body.get("quote", STATE["quote"]))
                if "quotes" in body:
                    STATE["quotes"] = {sym: {"mid": float(mid), "spread_bp": float(body.get("spread_bp", 2.0))} for sym, mid in body["quotes"].items()}
            return self._send(200, {"quote": STATE["quote"], "quotes": len(STATE["quotes"])})
        if path == "/_mock/positions":
            # 実売買のシミュレーション専用: 口座の建玉を置く ／ ずらす（認証なし）。
            #   {"positions": {"T": 4}}   置き換え（運転手が続きから起こすとき、台帳の合計を入れる）
            #   {"adjust": {"T": -4}}     ずらす（筋書き position_loss ＝ 台帳にあるはずの株が口座から消える）
            body = self._body()
            with LOCK:
                if "positions" in body:
                    STATE["positions"] = {sym: float(q) for sym, q in body["positions"].items() if float(q)}
                for sym, delta in (body.get("adjust") or {}).items():
                    STATE["positions"][sym] = round(STATE["positions"].get(sym, 0) + float(delta), 6)
                return self._send(200, {"positions": dict(STATE["positions"])})
        if path == "/_mock/fault":
            # 実売買のシミュレーション専用: {"faults": {"session_offline": 2, "no_fill": 1}} を足す（認証なし。運転手の筋書きが叩く）
            body = self._body()
            bad = [k for k in (body.get("faults") or {}) if k not in FAULT_KINDS]
            if bad:
                return self._send(400, {"error": f"そんな故障は無い: {bad}（{FAULT_KINDS}）"})
            with LOCK:
                for kind, times in (body.get("faults") or {}).items():
                    STATE["faults"][kind] = STATE["faults"].get(kind, 0) + int(times)
                return self._send(200, {"faults": dict(STATE["faults"])})
        if path == "/oauth/token":
            if not self._check_headers(need_auth=False):
                return
            body = self._body()
            if take_fault("auth_5xx"):
                return self._bad_gateway()
            if take_fault("auth_401"):
                return self._error(401, "invalid_credentials", "（故障の注入）認証に失敗した")
            if body.get("grant_type") != "refresh_token" or not body.get("refresh_token"):
                return self._error(400, "invalid_request", "grant_type と refresh_token が要る")
            if not body.get("client_secret"):
                return self._error(401, "invalid_credentials", "client_secret が無い")
            return self._send(
                200,
                {"access_token": fake_jwt(), "token_type": "Bearer", "expires_in": 900, "scope": "read trade openid"},
            )

        if not self._check_headers():
            return
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "accounts" and parts[2] == "orders":
            body = self._body()
            dry_run = len(parts) == 4 and parts[3] == "dry-run"
            leg = body["legs"][0]
            # 本番の API が持つ注文種別の写し（【記憶・未確認】。dryrun2 の記録で直す）
            if body.get("order-type") not in ("Limit", "Market", "Stop", "Stop Limit", "Notional Market"):
                return self._error(422, "invalid_order_type", f"order-type {body.get('order-type')!r} は無い（Limit / Market / Stop / Stop Limit / Notional Market）")
            if body.get("order-type") == "Notional Market":
                if "quantity" in leg or not body.get("value"):
                    return self._error(422, "invalid_notional_order", "Notional Market は value を持ち、レッグに quantity を付けない")
                price = quote_of(leg["symbol"])[2] if STATE["fill_noise"] > 0 else 1.0
                qty = float(body["value"]) / price
            else:
                price = (quote_of(leg["symbol"])[2] if STATE["fill_noise"] > 0 else 1.0) if body.get("order-type") == "Market" else float(body.get("price", 0))
                qty = float(leg.get("quantity", 1))
            effect = {
                "change-in-buying-power": f"{price * qty:.2f}",
                "change-in-buying-power-effect": "Debit" if leg["action"].startswith("Buy") else "Credit",
                "new-buying-power": f"{100000 - price * qty:.2f}",
                "current-buying-power": "100000.00",
            }
            fees = {"total-fees": "0.00", "total-fees-effect": "None"}
            if STATE["fill_noise"] > 0:
                # 実売買のモック: 手数料の内訳を返す（執行器が注文ごとの内訳を記録し、トレーダーの台帳に入れる経路を通すため）。
                # ⚠ 鍵の名前は OpenAPI の記憶【未確認】、額は想像（株の手数料 $0・売りにだけ規制費。本物は dryrun2 と本番の 1 発注で確かめる）
                reg = 0.0 if leg["action"].startswith("Buy") else max(0.01, round(price * qty * 0.00003, 2))
                eff = "Debit" if reg else "None"
                fees = {"regulatory-fees": f"{reg:.2f}", "regulatory-fees-effect": eff, "clearing-fees": "0.00", "clearing-fees-effect": "None",
                        "commission": "0.00", "commission-effect": "None", "proprietary-index-option-fees": "0.00",
                        "proprietary-index-option-fees-effect": "None", "total-fees": f"{reg:.2f}", "total-fees-effect": eff}
            if not dry_run:
                if take_fault("session_offline"):
                    return self._error(422, "preflight_check_failure", "Session offline")
                if take_fault("http_5xx"):
                    return self._bad_gateway()
                if take_fault("http_429"):
                    return self._error(429, "too_many_requests", "（故障の注入）Rate limit exceeded")
                if leg["action"].startswith("Buy") and take_fault("reject_funds"):
                    return self._error(422, "margin_check_failed", "（故障の注入）You have insufficient buying power for this order")
            if dry_run:
                order = make_order(body, "Received")
                order["id"] = "dry-run"
                return self._send(
                    200,
                    envelope(
                        {
                            "order": order,
                            "warnings": [],
                            "buying-power-effect": effect,
                            "fee-calculation": fees,
                        },
                        f"/accounts/{parts[1]}/orders/dry-run",
                    ),
                )
            order = make_order(body, "Received")
            with LOCK:
                STATE["orders"][order["id"]] = order
                if fills_immediately(body) and not (STATE["faults"].get("no_fill", 0) > 0 and body.get("order-type") in ("Market", "Notional Market")):
                    STATE["fill_wanted"].add(order["id"])
                elif fills_immediately(body):
                    STATE["faults"]["no_fill"] -= 1   # 成行が約定しないまま Live で残る（執行器が取消までの秒数を待って取り消す）
                STATE["events"].append({"type": "Order", "data": dict(order), "timestamp": now_ms()})
            accepted = dict(order)   # 応答は受けた時点の写し（進める側のスレッドが同じ dict を書き換える。遅延 0 だと応答の status が揺れる）
            advance(order["id"])
            return self._send(
                201,
                envelope(
                    {"order": accepted, "warnings": [], "buying-power-effect": effect, "fee-calculation": fees},
                    f"/accounts/{parts[1]}/orders",
                ),
            )
        return self._error(404, "record_not_found", f"POST {path} は未実装")

    def do_DELETE(self):
        if not self._check_headers():
            return
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "accounts" and parts[2] == "orders":
            order_id = int(parts[3])
            with LOCK:
                order = STATE["orders"].get(order_id)
                if not order:
                    return self._error(404, "record_not_found", "そんな注文は無い")
                if order["status"] == "Filled":
                    return self._error(422, "order_not_cancellable", "約定済みの注文は取り消せない")
                order["status"] = "Cancel Requested"
                STATE["events"].append({"type": "Order", "data": dict(order), "timestamp": now_ms()})
            def finish():
                wait(0.2)
                with LOCK:
                    order["status"] = "Cancelled"
                    order["cancellable"] = False
                    STATE["events"].append({"type": "Order", "data": dict(order), "timestamp": now_ms()})
            threading.Thread(target=finish, daemon=True).start()
            return self._send(200, envelope(order, f"/accounts/{parts[1]}/orders/{order_id}"))
        return self._error(404, "record_not_found", "未実装")

    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if not self._check_headers():
            return
        if self._rate_limited():
            return

        if path == "/customers/me/accounts":
            return self._send(
                200,
                envelope(
                    {
                        "items": [
                            {
                                "account": {
                                    "account-number": ACCOUNT,
                                    "account-type-name": "Individual",
                                    "margin-or-cash": "Margin",
                                    "is-closed": False,
                                    "opened-at": "2026-09-01T00:00:00.000Z",
                                },
                                "authority-level": "owner",
                            }
                        ]
                    },
                    "/customers/me/accounts",
                ),
            )

        if path == f"/accounts/{ACCOUNT}/balances":
            return self._send(
                200,
                envelope(
                    {
                        "account-number": ACCOUNT,
                        "cash-balance": "100000.0",
                        "net-liquidating-value": "100000.0",
                        "equity-buying-power": "200000.0",
                        "derivative-buying-power": "100000.0",
                        "currency": "USD",
                        "updated-at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    },
                    path,
                ),
            )

        if path == f"/accounts/{ACCOUNT}/trading-status":
            return self._send(
                200,
                envelope(
                    {
                        "account-number": ACCOUNT,
                        "is-closing-only": False,
                        "equities-margin-calculation-type": "Reg T",
                        "options-level": "No Restrictions",
                    },
                    path,
                ),
            )

        if path == f"/accounts/{ACCOUNT}/positions":
            with LOCK:
                items = [
                    {
                        "account-number": ACCOUNT,
                        "symbol": symbol,
                        "instrument-type": "Equity",
                        "quantity": str(abs(qty)),
                        "quantity-direction": "Long" if qty > 0 else "Short",
                        "average-open-price": "1.0",
                    }
                    for symbol, qty in STATE["positions"].items()
                    if qty
                ]
            return self._send(200, envelope({"items": items}, path))

        if path == f"/accounts/{ACCOUNT}/orders/live":
            with LOCK:
                items = [dict(o) for o in STATE["orders"].values()]
            return self._send(200, envelope({"items": items}, path))

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "accounts" and parts[2] == "orders":
            with LOCK:
                order = STATE["orders"].get(int(parts[3]))
            if not order:
                return self._error(404, "record_not_found", "そんな注文は無い")
            return self._send(200, envelope(dict(order), path))

        if path == "/market-data/by-type":
            if not STATE["market_data"]:
                # cert と同じ。502 は HTML で返る
                body = b"<html><head><title>502 Bad Gateway</title></head></html>"
                self.send_response(502)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            symbol = (query.get("equity") or ["SPY"])[0]
            updated = now_utc() - timedelta(milliseconds=120)
            bid, ask, mid = quote_of(symbol)
            return self._send(
                200,
                envelope(
                    {
                        "items": [
                            {
                                "symbol": symbol,
                                "instrument-type": "Equity",
                                "bid": f"{bid:.2f}" if symbol not in STATE["quotes"] else f"{bid:.4f}",
                                "ask": f"{ask:.2f}" if symbol not in STATE["quotes"] else f"{ask:.4f}",
                                "mid": f"{mid:.2f}" if symbol not in STATE["quotes"] else f"{mid:.4f}",
                                "last": f"{mid:.2f}",
                                "bid-size": "300",
                                "ask-size": "200",
                                "is-trading-halted": False,
                                "updated-at": updated.isoformat(timespec="milliseconds"),
                            }
                        ]
                    },
                    path,
                ),
            )

        if path == "/api-quote-tokens":
            return self._send(
                200,
                envelope({"token": fake_jwt(86400), "dxlink-url": STATE["dxlink_url"], "level": "api"}, path),
            )

        if path == "/market-time/equities/sessions/current":
            return self._send(
                200,
                envelope({"instrument-collection": "Equity", "state": "Open", "start-at": "2026-09-05T13:30:00Z"}, path),
            )

        return self._error(404, "record_not_found", f"GET {path} は未実装")


# ---------------------------------------------------------------- websocket


async def account_streamer(websocket):
    """connect → heartbeat を受け、STATE["events"] に積まれた通知を流す。"""
    sent = 0
    async for raw in websocket:
        msg = json.loads(raw)
        action = msg.get("action")
        if action == "connect":
            with LOCK:
                sent = len(STATE["events"])  # 接続前の分は流さない
            await websocket.send(
                json.dumps(
                    {
                        "status": "ok",
                        "action": "connect",
                        "web-socket-session-id": "mock0001",
                        "value": msg.get("value") or [ACCOUNT],
                        "request-id": msg.get("request-id"),
                    }
                )
            )
            asyncio.create_task(_pump_events(websocket, sent))
        elif action == "heartbeat":
            await websocket.send(
                json.dumps(
                    {"status": "ok", "action": "heartbeat", "web-socket-session-id": "mock0001", "request-id": msg.get("request-id")}
                )
            )


async def _pump_events(websocket, cursor: int) -> None:
    try:
        while True:
            await asyncio.sleep(0.1)
            with LOCK:
                pending = STATE["events"][cursor:]
                cursor = len(STATE["events"])
            for event in pending:
                await websocket.send(json.dumps(event))
    except Exception:
        return


async def dxlink_server(websocket):
    """DXLink の SETUP/AUTH/CHANNEL/FEED をなぞり、COMPACT の Quote を 200ms ごとに送る。"""
    feed_task = None
    async for raw in websocket:
        msg = json.loads(raw)
        mtype = msg.get("type")
        if mtype == "SETUP":
            await websocket.send(json.dumps({"type": "SETUP", "channel": 0, "version": "mock", "keepaliveTimeout": 60}))
            await websocket.send(json.dumps({"type": "AUTH_STATE", "channel": 0, "state": "UNAUTHORIZED"}))
        elif mtype == "AUTH":
            await websocket.send(json.dumps({"type": "AUTH_STATE", "channel": 0, "state": "AUTHORIZED", "userId": "mock"}))
        elif mtype == "CHANNEL_REQUEST":
            await websocket.send(json.dumps({"type": "CHANNEL_OPENED", "channel": msg["channel"], "service": "FEED"}))
        elif mtype == "FEED_SETUP":
            await websocket.send(
                json.dumps({"type": "FEED_CONFIG", "channel": msg["channel"], "dataFormat": "COMPACT", "aggregationPeriod": 0.1})
            )
        elif mtype == "FEED_SUBSCRIPTION":
            channel = msg["channel"]
            symbols = [s["symbol"] for s in msg.get("add", []) if s["type"] == "Quote"]

            async def feed():
                bid = 560.10
                while True:
                    await asyncio.sleep(0.2)
                    bid += 0.01
                    for symbol in symbols:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "FEED_DATA",
                                    "channel": channel,
                                    "data": ["Quote", ["Quote", symbol, round(bid, 2), round(bid + 0.02, 2), 300.0, 200.0]],
                                }
                            )
                        )

            feed_task = asyncio.create_task(feed())
        elif mtype == "KEEPALIVE":
            await websocket.send(json.dumps({"type": "KEEPALIVE", "channel": 0}))
    if feed_task:
        feed_task.cancel()


async def run_ws(port: int, handler) -> None:
    import websockets

    async with websockets.serve(handler, "127.0.0.1", port):
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description="tastytrade API のモック（配線の確認用）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ws-port", type=int, default=8766)
    parser.add_argument("--dxlink-port", type=int, default=8767)
    parser.add_argument("--market-data", action="store_true", help="本番のように気配を返す（既定は cert と同じ 502）")
    parser.add_argument("--rate-limit-after", type=int, default=0, help="この回数を超えた GET に 429 を返す")
    parser.add_argument("--fill-noise", type=float, default=0.0, help="成行を気配 × (1 ± noise) で約定させる（実売買のモック。0 なら sandbox と同じ $1）")
    parser.add_argument("--seed", type=int, default=0, help="--fill-noise の乱数の種")
    parser.add_argument("--sim-control", default=None, help="仮の時計の control.json（実売買のシミュレーション）。時刻欄と遅延がそれに従う。無ければ本物の時計")
    args = parser.parse_args()
    import random
    STATE["fill_noise"] = args.fill_noise
    STATE["rng"] = random.Random(args.seed)

    STATE["market_data"] = args.market_data
    STATE["sim_control"] = args.sim_control
    STATE["rate_limit_after"] = args.rate_limit_after
    STATE["dxlink_url"] = f"ws://127.0.0.1:{args.dxlink_port}"

    http = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    print(f"REST  http://127.0.0.1:{args.port}")
    print(f"口座  ws://127.0.0.1:{args.ws_port}")
    print(f"気配  ws://127.0.0.1:{args.dxlink_port}")

    async def both():
        await asyncio.gather(run_ws(args.ws_port, account_streamer), run_ws(args.dxlink_port, dxlink_server))

    try:
        asyncio.run(both())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
