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
        "underlying-symbol": leg["symbol"],
        "underlying-instrument-type": leg["instrument-type"],
        "status": status,
        "cancellable": status in ("Received", "Routed", "Live"),
        "editable": True,
        "edited": False,
        "external-identifier": body.get("external-identifier"),
        "ext-client-order-id": f"mock-{order_id}",
        "received-at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
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
    if body.get("order-type") == "Market":
        return True
    try:
        return float(body.get("price", "0")) < 3.0
    except (TypeError, ValueError):
        return False


def advance(order_id: int) -> None:
    """Received → Routed → Live →（約定するなら）Filled と時間をかけて進める。"""
    def run():
        for status, delay in (("Routed", 0.15), ("In Flight", 0.1), ("Live", 0.2)):
            time.sleep(delay)
            with LOCK:
                order = STATE["orders"].get(order_id)
                if not order or order["status"] in ("Cancelled", "Filled", "Rejected"):
                    return
                order["status"] = status
                STATE["events"].append({"type": "Order", "data": dict(order), "timestamp": now_ms()})
        if order_id in STATE["fill_wanted"]:
            time.sleep(0.3)
            with LOCK:
                order = STATE["orders"].get(order_id)
                if not order or order["status"] != "Live":
                    return
                price = "1.00" if order["order-type"] == "Market" else order.get("price")
                leg = order["legs"][0]
                leg["remaining-quantity"] = "0"
                leg["fills"] = [
                    {
                        "quantity": leg["quantity"],
                        "fill-price": price,
                        "filled-at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        "destination-venue": "MOCK",
                    }
                ]
                order["status"] = "Filled"
                order["cancellable"] = False
                symbol, qty = leg["symbol"], int(float(leg["quantity"]))
                signed = qty if leg["action"].startswith("Buy") else -qty
                STATE["positions"][symbol] = STATE["positions"].get(symbol, 0) + signed
                STATE["events"].append({"type": "Order", "data": dict(order), "timestamp": now_ms()})

    threading.Thread(target=run, daemon=True).start()


STATE["fill_wanted"] = set()


class Handler(BaseHTTPRequestHandler):
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
        if path == "/oauth/token":
            if not self._check_headers(need_auth=False):
                return
            body = self._body()
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
            price = 1.0 if body.get("order-type") == "Market" else float(body.get("price", 0))
            qty = float(leg.get("quantity", 1))
            effect = {
                "change-in-buying-power": f"{price * qty:.2f}",
                "change-in-buying-power-effect": "Debit" if leg["action"].startswith("Buy") else "Credit",
                "new-buying-power": f"{100000 - price * qty:.2f}",
                "current-buying-power": "100000.00",
            }
            fees = {"total-fees": "0.00", "total-fees-effect": "None"}
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
                if fills_immediately(body):
                    STATE["fill_wanted"].add(order["id"])
                STATE["events"].append({"type": "Order", "data": dict(order), "timestamp": now_ms()})
            advance(order["id"])
            return self._send(
                201,
                envelope(
                    {"order": order, "warnings": [], "buying-power-effect": effect, "fee-calculation": fees},
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
                time.sleep(0.2)
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
            updated = datetime.now(timezone.utc) - timedelta(milliseconds=120)
            return self._send(
                200,
                envelope(
                    {
                        "items": [
                            {
                                "symbol": symbol,
                                "instrument-type": "Equity",
                                "bid": "560.10",
                                "ask": "560.12",
                                "mid": "560.11",
                                "last": "560.11",
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
    args = parser.parse_args()

    STATE["market_data"] = args.market_data
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
