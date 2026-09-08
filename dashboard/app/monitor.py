"""実運用の監視ループ（プラン §2-3）。設定された環境（cert / prod）ごとに 1 本。

- REST: 認証の残り時間（失効 60 秒前に refresh）、口座・残高・建玉・働いている注文、prod は気配
- 口座ストリーマ websocket: 繋ぎっぱなし。接続状態・最終受信・切断と再接続の回数・直近の通知
- DXLink（prod、またはモック）: 気配の購読。最終受信・件数・再接続
- エラー: ApiError の status / code / message と error.errors[] の抜粋、429 の回数

refresh の成否・切断・再接続・429 は monitor/<venue>-<env>-<日付>.jsonl に追記する（観点 A の材料）。
⚠ 失敗ログインを繰り返すと IP が約 8 時間ブロックされるので、refresh の失敗は指数バックオフし、3 連続で止める。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import record
from ttclient import ApiError, Client

from .config import Settings
from .masking import Redactor

log = logging.getLogger("ail.monitor")

WORKING_STATUSES = ("Received", "Routed", "In Flight", "Live", "Contingent")
BALANCE_FIELDS = (
    "cash-balance",
    "net-liquidating-value",
    "equity-buying-power",
    "derivative-buying-power",
    "available-trading-funds",
    "pending-cash",
    "pending-cash-effect",
    "cash-available-to-withdraw",
    "maintenance-requirement",
    "currency",
    "updated-at",
)
QUOTE_FIELDS = ["eventType", "eventSymbol", "bidPrice", "askPrice", "bidSize", "askSize"]
TRADE_FIELDS = ["eventType", "eventSymbol", "price", "dayVolume", "size"]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def field(obj: dict, name: str):
    """API は dasherized、OpenAPI の schema は camelCase。両方見る（sample.py と同じ）。"""
    if not isinstance(obj, dict):
        return None
    if name in obj:
        return obj[name]
    parts = name.split("-")
    return obj.get(parts[0] + "".join(p.title() for p in parts[1:]))


def order_brief(order: dict) -> dict:
    legs = field(order, "legs") or []
    return {
        "id": field(order, "id"),
        "status": field(order, "status"),
        "order_type": field(order, "order-type"),
        "time_in_force": field(order, "time-in-force"),
        "price": field(order, "price"),
        "price_effect": field(order, "price-effect"),
        "received_at": field(order, "received-at"),
        "updated_at": field(order, "updated-at"),
        "external_identifier": field(order, "external-identifier"),
        "cancellable": field(order, "cancellable"),
        "legs": [
            {
                "symbol": field(l, "symbol"),
                "action": field(l, "action"),
                "quantity": field(l, "quantity"),
                "instrument_type": field(l, "instrument-type"),
                "remaining": field(l, "remaining-quantity"),
            }
            for l in legs
        ],
    }


def position_brief(p: dict) -> dict:
    return {
        "symbol": field(p, "symbol"),
        "instrument_type": field(p, "instrument-type"),
        "quantity": field(p, "quantity"),
        "direction": field(p, "quantity-direction"),
        "average_open_price": field(p, "average-open-price"),
        "close_price": field(p, "close-price"),
        "updated_at": field(p, "updated-at"),
    }


class EventLog:
    """監視で得たイベントの追記。1 日 1 ファイル（UTC）。"""

    def __init__(self, directory: Path, redactor: Redactor) -> None:
        self.dir = directory
        self.redactor = redactor
        self._lock = threading.Lock()
        self.dir.mkdir(parents=True, exist_ok=True)

    def append(self, venue: str, env: str, kind: str, detail: dict | None = None, mock: bool = False) -> dict:
        row = {"at": utcnow_iso(), "venue": venue, "env": env, "kind": kind, "detail": self.redactor(detail or {})}
        if mock:
            row["mock"] = True  # 接続先を差し替えた監視のイベント。判定から外す
        path = self.dir / f"{venue}-{env}-{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def load(self) -> list[dict]:
        rows: list[dict] = []
        if not self.dir.is_dir():
            return rows
        for path in sorted(self.dir.glob("*.jsonl")):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                rows.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue
        rows.sort(key=lambda r: r.get("at") or "")
        return rows

    def tail(self, n: int = 50) -> list[dict]:
        return self.load()[-n:][::-1]


class EnvMonitor:
    def __init__(self, env: str, creds: dict, settings: Settings, redactor: Redactor, events: EventLog, venue: str = "tastytrade") -> None:
        self.env = env
        self.venue = venue
        self.settings = settings
        self.redactor = redactor
        self.events = events
        self.creds = creds
        self.client = Client(env=env, rest_base=creds.get("rest_base"), account_streamer=creds.get("account_streamer"))
        redactor.secret(creds.get("client_secret"), "<client_secret:masked>")
        redactor.secret(creds.get("refresh_token"), "<refresh_token:masked>")
        self.account_number: str | None = None
        self.dxlink_enabled = env == "prod" or bool(creds.get("rest_base"))
        self._lock = threading.Lock()
        self._auth_fail = 0
        self._next_auth_at = 0.0
        self._suspended = False
        self._quote_token: dict | None = None
        self._quote_token_at = 0.0
        self.state: dict = {
            "env": env,
            "venue": venue,
            "label": self.client.conf["label"],
            "rest_base": self.client.base,
            "mock": bool(creds.get("rest_base")),
            "auth": {"ok": False, "scope": None, "expires_at": None, "expires_in": None, "obtained_at": None, "refresh_ok": 0, "refresh_fail": 0, "last_error": None, "suspended": False, "next_attempt_at": None},
            "account": {"label": None, "count": 0, "type": None, "margin_or_cash": None},
            "balances": {},
            "positions": [],
            "live_orders": [],
            "quote": {"state": "off", "symbol": settings.symbol},
            "poll": {"count": 0, "errors": 0, "count_429": 0, "last_ok_at": None, "last_error": None},
            "account_stream": {"state": "off", "url": self.client.conf["account_streamer"], "connected_at": None, "last_message_at": None, "message_count": 0, "order_notifications": 0, "disconnects": 0, "reconnects": 0, "last_error": None, "recent": []},
            "dxlink": {"state": "off" if self.dxlink_enabled else "n/a", "url": None, "connected_at": None, "last_event_at": None, "event_count": 0, "disconnects": 0, "reconnects": 0, "last_error": None, "quote": None, "trade": None, "token_level": None},
            "errors": [],
            "started_at": utcnow_iso(),
        }
        self._recent: deque = deque(maxlen=50)
        self._errors: deque = deque(maxlen=50)

    # ---------------- state helpers

    def _event(self, kind: str, detail: dict | None = None) -> None:
        self.events.append(self.venue, self.env, kind, detail, mock=self.state["mock"])

    def _sub(self, key: str, **kw) -> None:
        with self._lock:
            self.state[key].update(kw)

    def snapshot(self) -> dict:
        with self._lock:
            snap = copy.deepcopy(self.state)
            snap["account_stream"]["recent"] = list(self._recent)
            snap["errors"] = list(self._errors)
        tok = self.client.token
        if tok and tok.expires_at:
            snap["auth"]["remaining_s"] = round(tok.expires_at - time.time(), 1)
        else:
            snap["auth"]["remaining_s"] = None
        snap["halted"] = self.settings.halt_file.exists()
        return snap

    def _record_error(self, where: str, exc: Exception) -> None:
        entry: dict = {"at": utcnow_iso(), "where": where, "type": type(exc).__name__, "message": str(exc)[:300]}
        if isinstance(exc, ApiError):
            entry.update({"status": exc.status, "code": exc.code})
            body = exc.body if isinstance(exc.body, dict) else {}
            errors = ((body.get("error") or {}).get("errors")) if isinstance(body, dict) else None
            if errors:
                entry["errors"] = record.excerpt(errors, limit=8)
        with self._lock:
            self._errors.appendleft(entry)
            self.state["poll"]["errors"] += 1
            self.state["poll"]["last_error"] = entry
            if isinstance(exc, ApiError) and exc.status == 429:
                self.state["poll"]["count_429"] += 1
        kind = "http_429" if isinstance(exc, ApiError) and exc.status == 429 else "api_error"
        self._event(kind, entry)

    # ---------------- 認証

    def ensure_token(self, force: bool = False) -> bool:
        tok = self.client.token
        now = time.time()
        if not force and tok and tok.expires_at and tok.expires_at - now > 60:
            return True
        if self._suspended or now < self._next_auth_at:
            return False
        try:
            resp = self.client.authenticate(
                client_secret=self.creds["client_secret"],
                refresh_token=self.creds["refresh_token"],
                client_id=self.creds.get("client_id"),
            )
        except Exception as exc:
            self._auth_fail += 1
            backoff = min(900 * (2 ** (self._auth_fail - 1)), 6 * 3600)
            self._next_auth_at = now + backoff
            self._suspended = self._auth_fail >= 3
            err = f"{type(exc).__name__}: {str(exc)[:200]}"
            self._sub("auth", ok=False, last_error=err, suspended=self._suspended, next_attempt_at=None if self._suspended else utc_from(self._next_auth_at))
            with self._lock:
                self.state["auth"]["refresh_fail"] += 1
            self._event("refresh_fail", {"error": err, "consecutive": self._auth_fail, "suspended": self._suspended})
            log.warning("[%s] refresh 失敗 %d 回目: %s", self.env, self._auth_fail, err)
            return False
        self.redactor.secret(self.client.token.access_token, "<access_token:masked>")
        if resp.get("id_token"):
            self.redactor.secret(resp["id_token"], "<id_token:masked>")
        self._auth_fail = 0
        self._next_auth_at = 0.0
        with self._lock:
            a = self.state["auth"]
            a.update(
                ok=True,
                scope=resp.get("scope"),
                expires_in=resp.get("expires_in"),
                expires_at=utc_from(self.client.token.expires_at) if self.client.token.expires_at else None,
                obtained_at=utcnow_iso(),
                last_error=None,
                suspended=False,
                next_attempt_at=None,
            )
            a["refresh_ok"] += 1
        self._event("refresh_ok", {"expires_in": resp.get("expires_in"), "scope": resp.get("scope"), "rotated": "refresh_token" in resp})
        return True

    def retry_auth(self) -> None:
        self._suspended = False
        self._auth_fail = 0
        self._next_auth_at = 0.0
        self._sub("auth", suspended=False, next_attempt_at=None)

    @property
    def can_cancel(self) -> bool:
        """停止ボタンが取消まで行えるか。cert は常に可、prod は scope に trade があるときだけ。"""
        if self.env != "prod":
            return True
        tok = self.client.token
        return bool(tok and tok.scope and "trade" in tok.scope.split())

    # ---------------- REST（スレッドで動く）

    def poll_once(self) -> None:
        if not self.ensure_token():
            return
        c = self.client
        try:
            if not self.account_number:
                accounts = c.list_accounts()
                for a in accounts:
                    self.redactor.account(field(a, "account-number"))
                chosen = self.creds.get("account_number") or field(accounts[0], "account-number")
                self.account_number = chosen
                picked = next((a for a in accounts if field(a, "account-number") == chosen), accounts[0])
                self._sub("account", label=self.redactor.label_for(chosen), count=len(accounts), type=field(picked, "account-type-name"), margin_or_cash=field(picked, "margin-or-cash"))
            acct = self.account_number
            balances = c.get_balances(acct)
            positions = c.list_positions(acct)
            orders = c.list_live_orders(acct)
            with self._lock:
                self.state["balances"] = {k: field(balances, k) for k in BALANCE_FIELDS}
                self.state["positions"] = [position_brief(p) for p in positions]
                self.state["live_orders"] = [order_brief(o) for o in orders]
                self.state["poll"]["count"] += 1
                self.state["poll"]["last_ok_at"] = utcnow_iso()
        except ApiError as exc:
            self._record_error("poll", exc)
            if exc.status == 401:
                c.token = None  # 次の周期で取り直す
            return
        except Exception as exc:  # 接続エラーなど
            self._record_error("poll", exc)
            return
        self._poll_quote()

    def _poll_quote(self) -> None:
        if self.env != "prod" and not self.state["mock"]:
            self._sub("quote", state="n/a", reason="sandbox は相場データを配信しない（502）")
            return
        requested = time.time()
        try:
            q = self.client.get_quote(self.settings.symbol)
        except ApiError as exc:
            self._sub("quote", state="error", reason=f"{exc.status} {exc.code}")
            if exc.status != 502:
                self._record_error("quote", exc)
            return
        except Exception as exc:
            self._sub("quote", state="error", reason=f"{type(exc).__name__}")
            return
        updated_at = field(q, "updated-at")
        delay = None
        if updated_at:
            try:
                delay = round(requested - datetime.fromisoformat(str(updated_at).replace("Z", "+00:00")).timestamp(), 3)
            except ValueError:
                pass
        self._sub(
            "quote",
            state="ok",
            symbol=field(q, "symbol") or self.settings.symbol,
            bid=field(q, "bid"),
            ask=field(q, "ask"),
            last=field(q, "last"),
            mid=field(q, "mid"),
            updated_at=updated_at,
            requested_at=utc_from(requested),
            delay_s=delay,
            halted=field(q, "is-trading-halted"),
        )

    # ---------------- 非同期ループ

    async def run(self) -> None:
        tasks = [asyncio.create_task(self._rest_loop(), name=f"{self.env}-rest"), asyncio.create_task(self._account_stream_loop(), name=f"{self.env}-ws")]
        if self.dxlink_enabled:
            tasks.append(asyncio.create_task(self._dxlink_loop(), name=f"{self.env}-dx"))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise

    async def _rest_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.poll_once)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("[%s] poll で例外", self.env)
                self._record_error("poll", exc)
            await asyncio.sleep(self.settings.poll_seconds)

    async def _account_stream_loop(self) -> None:
        import websockets

        backoff = 5.0
        first = True
        while True:
            if not self.client.token or not self.account_number:
                self._sub("account_stream", state="waiting", last_error="認証か口座番号を待っている")
                await asyncio.sleep(3)
                continue
            if not first:
                with self._lock:
                    self.state["account_stream"]["reconnects"] += 1
            first = False
            try:
                await self._account_stream_session(websockets)
                backoff = 5.0
                reason = "closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = f"{type(exc).__name__}: {str(exc)[:200]}"
                self._sub("account_stream", last_error=reason)
            with self._lock:
                st = self.state["account_stream"]
                was_connected = st["state"] == "connected"
                st["state"] = "disconnected"
                if was_connected:
                    st["disconnects"] += 1
            self._event("ws_disconnect", {"stream": "account", "reason": reason})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120.0)

    async def _account_stream_session(self, websockets) -> None:
        url = self.client.conf["account_streamer"]
        async with websockets.connect(url, open_timeout=20, ping_interval=None) as ws:
            # ⚠ 実測（2026-09-05）: "Bearer " が要る。素のトークンだと status:error / "Unknown domain"
            await ws.send(json.dumps({"action": "connect", "value": [self.account_number], "auth-token": f"Bearer {self.client.token.access_token}", "request-id": 1}))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            if ack.get("status") != "ok":
                raise RuntimeError(f"connect が失敗: {ack.get('status')} {ack.get('message')}")
            self._sub("account_stream", state="connected", connected_at=utcnow_iso(), last_error=None)
            self._event("ws_connect", {"stream": "account"})

            async def heartbeat():
                rid = 100
                while True:
                    await asyncio.sleep(20)
                    rid += 1
                    tok = self.client.token
                    if tok:
                        await ws.send(json.dumps({"action": "heartbeat", "auth-token": f"Bearer {tok.access_token}", "request-id": rid}))

            hb = asyncio.create_task(heartbeat())
            try:
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    now = utcnow_iso()
                    mtype = msg.get("type") or msg.get("action")
                    data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                    with self._lock:
                        st = self.state["account_stream"]
                        st["last_message_at"] = now
                        st["message_count"] += 1
                    if mtype == "heartbeat":
                        if msg.get("status") != "ok":
                            self._sub("account_stream", last_error=f"heartbeat {msg.get('status')}: {msg.get('message')}")
                        continue
                    brief = {"at": now, "type": mtype}
                    if mtype == "Order":
                        legs = field(data, "legs") or [{}]
                        brief.update(id=field(data, "id"), status=field(data, "status"), symbol=field(legs[0], "symbol"), action=field(legs[0], "action"), price=field(data, "price"))
                        with self._lock:
                            self.state["account_stream"]["order_notifications"] += 1
                    elif mtype == "AccountBalance":
                        brief.update(cash_balance=field(data, "cash-balance"), net_liq=field(data, "net-liquidating-value"))
                    elif mtype == "CurrentPosition":
                        brief.update(symbol=field(data, "symbol"), quantity=field(data, "quantity"))
                    else:
                        brief.update(status=msg.get("status"))
                    with self._lock:
                        self._recent.appendleft(brief)
            finally:
                hb.cancel()

    async def _dxlink_loop(self) -> None:
        import websockets

        backoff = 5.0
        first = True
        while True:
            if not self.client.token:
                await asyncio.sleep(3)
                continue
            token = await asyncio.to_thread(self._quote_token_info)
            if token is None:
                await asyncio.sleep(600)
                continue
            if not first:
                with self._lock:
                    self.state["dxlink"]["reconnects"] += 1
            first = False
            try:
                await self._dxlink_session(websockets, token)
                reason = "closed"
                backoff = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = f"{type(exc).__name__}: {str(exc)[:200]}"
                self._sub("dxlink", last_error=reason)
                self._quote_token = None  # トークン起因かもしれないので取り直す
            with self._lock:
                st = self.state["dxlink"]
                was_connected = st["state"] == "connected"
                st["state"] = "disconnected"
                if was_connected:
                    st["disconnects"] += 1
            self._event("ws_disconnect", {"stream": "dxlink", "reason": reason})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120.0)

    def _quote_token_info(self) -> dict | None:
        if self._quote_token and time.time() - self._quote_token_at < 23 * 3600:
            return self._quote_token
        try:
            info = self.client.get_quote_token()
        except ApiError as exc:
            self._sub("dxlink", state="unavailable", last_error=f"{exc.status} {exc.code}: {exc.message[:120]}")
            return None
        except Exception as exc:
            self._sub("dxlink", state="unavailable", last_error=f"{type(exc).__name__}")
            return None
        self.redactor.secret(info.get("token"), "<quote_token:masked>")
        self._quote_token = info
        self._quote_token_at = time.time()
        self._sub("dxlink", url=info.get("dxlink-url"), token_level=info.get("level"))
        return info

    async def _dxlink_session(self, websockets, info: dict) -> None:
        url, token, symbol = info["dxlink-url"], info["token"], self.settings.symbol
        async with websockets.connect(url, open_timeout=20, ping_interval=None) as ws:
            await ws.send(json.dumps({"type": "SETUP", "channel": 0, "version": "0.1-DXF-JS/0.3.0", "keepaliveTimeout": 60, "acceptKeepaliveTimeout": 60}))
            authorized = False

            async def keepalive():
                while True:
                    await asyncio.sleep(30)
                    await ws.send(json.dumps({"type": "KEEPALIVE", "channel": 0}))

            ka = asyncio.create_task(keepalive())
            try:
                while True:
                    msg = json.loads(await ws.recv())
                    mtype = msg.get("type")
                    if mtype == "AUTH_STATE" and msg.get("state") == "UNAUTHORIZED":
                        await ws.send(json.dumps({"type": "AUTH", "channel": 0, "token": token}))
                    elif mtype == "AUTH_STATE" and msg.get("state") == "AUTHORIZED" and not authorized:
                        authorized = True
                        self._sub("dxlink", state="connected", connected_at=utcnow_iso(), last_error=None)
                        self._event("ws_connect", {"stream": "dxlink"})
                        await ws.send(json.dumps({"type": "CHANNEL_REQUEST", "channel": 3, "service": "FEED", "parameters": {"contract": "AUTO"}}))
                    elif mtype == "CHANNEL_OPENED":
                        await ws.send(json.dumps({"type": "FEED_SETUP", "channel": 3, "acceptAggregationPeriod": 0.1, "acceptDataFormat": "COMPACT", "acceptEventFields": {"Quote": QUOTE_FIELDS, "Trade": TRADE_FIELDS}}))
                    elif mtype == "FEED_CONFIG":
                        await ws.send(json.dumps({"type": "FEED_SUBSCRIPTION", "channel": 3, "reset": True, "add": [{"type": "Quote", "symbol": symbol}, {"type": "Trade", "symbol": symbol}]}))
                    elif mtype == "FEED_DATA":
                        self._on_feed_data(msg.get("data") or [])
                    elif mtype == "ERROR":
                        raise RuntimeError(f"DXLink ERROR: {msg.get('error')} {msg.get('message')}")
            finally:
                ka.cancel()

    def _on_feed_data(self, data: list) -> None:
        if len(data) < 2:
            return
        label, flat = data[0], data[1]
        fields = QUOTE_FIELDS if label == "Quote" else TRADE_FIELDS if label == "Trade" else None
        if not fields or not isinstance(flat, list):
            return
        now = utcnow_iso()
        width = len(fields)
        with self._lock:
            st = self.state["dxlink"]
            for i in range(0, len(flat) - width + 1, width):
                row = dict(zip(fields, flat[i : i + width]))
                st["event_count"] += 1
                st["last_event_at"] = now
                if label == "Quote":
                    st["quote"] = {"bid": row.get("bidPrice"), "ask": row.get("askPrice"), "bid_size": row.get("bidSize"), "ask_size": row.get("askSize"), "at": now}
                else:
                    st["trade"] = {"price": row.get("price"), "size": row.get("size"), "day_volume": row.get("dayVolume"), "at": now}


def utc_from(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


class Monitors:
    def __init__(self, settings: Settings, redactor: Redactor, events: EventLog) -> None:
        self.settings = settings
        self.items: dict[str, EnvMonitor] = {}
        if settings.demo:
            # デモ: 資格情報があっても本物には繋がず、cert / prod ともモックを相手にする（MOCK バッジが付く）
            for env in ("cert", "prod"):
                self.items[env] = EnvMonitor(env, settings.demo_credentials(env), settings, redactor, events)
        else:
            for env in ("cert", "prod"):
                creds = settings.credentials(env)
                if creds:
                    self.items[env] = EnvMonitor(env, creds, settings, redactor, events)
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(m.run(), name=f"monitor-{env}") for env, m in self.items.items()]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []

    def get(self, env: str) -> EnvMonitor | None:
        return self.items.get(env)

    def snapshot(self) -> dict:
        return {env: m.snapshot() for env, m in self.items.items()}
