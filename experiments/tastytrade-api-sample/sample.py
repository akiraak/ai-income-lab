#!/usr/bin/env python3
"""tastytrade の API 取引サンプル — 6 手順を順に動かして JSONL に記録する。

  1 認証 → 2 口座照会 → 3 現在値 → 4 dry-run・指値・取消 → 5 約定・反対売買 → 6 ストリーミング
  （＋ rate: レート制限の当たり方）

使い方:
    python3 sample.py --step all          # 1〜6 を順に
    python3 sample.py --step 4            # 手順 4 だけ
    python3 sample.py --step rate         # レート制限の確認

⚠ 既定は sandbox（cert）。本番（prod）では発注系（手順 4・5）を拒否する。
   実弾を通すのは TT_ALLOW_PROD_ORDERS=1 と --i-know-this-is-real-money が揃ったときだけ。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone

import record
from ttclient import ApiError, Client, ProductionGuard, load_env

HERE = os.path.dirname(os.path.abspath(__file__))
# 記録先。管理画面（dashboard/）が別の場所に集めるときは TT_OUT_DIR で差し替える
OUT_DIR = os.environ.get("TT_OUT_DIR") or os.path.join(HERE, "out")
# 停止の合図。管理画面の停止ボタンがこのファイルを置く。あるあいだは発注系の手順を拒否する
HALT_FILE = os.environ.get("TT_HALT_FILE") or os.path.join(OUT_DIR, "HALT")
ORDER_STEPS = {"4", "5", "5limit", "6"}  # 注文を出す手順（6 は通知を起こすために指値 → 取消を 1 往復させる）
SYMBOL = "SPY"
# sandbox の疑似約定: 成行は常に $1、$3 未満の指値は即約定、$3 以上は Live のまま
LIMIT_PRICE_NO_FILL = "10.00"


def field(obj: dict, name: str):
    """API は dasherized、OpenAPI の schema は camelCase。両方見る。"""
    if name in obj:
        return obj[name]
    parts = name.split("-")
    camel = parts[0] + "".join(p.title() for p in parts[1:])
    return obj.get(camel)


def jwt_expiry(token: str) -> dict | None:
    """access token は署名付き JWT。検証せず exp だけ読んで寿命を記録する。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    exp, iat = claims.get("exp"), claims.get("iat")
    out = {"exp": exp, "iat": iat, "scope": claims.get("scope")}
    if exp and iat:
        out["lifetime_s"] = exp - iat
    if exp:
        out["exp_utc"] = datetime.fromtimestamp(exp, timezone.utc).isoformat(timespec="seconds")
    return out


# ---------------------------------------------------------------- 手順 1


def step_auth(rec: record.Recorder, cfg: dict, client: Client) -> None:
    with rec.step(1, "認証（OAuth2 refresh → access）") as row:
        rec.mask.add(cfg["TT_CLIENT_SECRET"], "<client_secret:masked>")
        rec.mask.add(cfg["TT_REFRESH_TOKEN"], "<refresh_token:masked>")
        resp = client.authenticate(
            client_secret=cfg["TT_CLIENT_SECRET"],
            refresh_token=cfg["TT_REFRESH_TOKEN"],
            client_id=cfg.get("TT_CLIENT_ID") or None,
        )
        rec.mask.add(client.token.access_token, "<access_token:masked>")
        row["detail"] = {
            "expires_in_s": resp.get("expires_in"),
            "token_type": resp.get("token_type"),
            "scope": resp.get("scope"),
            "refresh_token_rotated": "refresh_token" in resp,  # 公式は「回さない」
            "jwt": jwt_expiry(client.token.access_token),
            "response_keys": sorted(resp.keys()),
        }
        row["result"] = "authenticated"


def step_auth_expiry(rec: record.Recorder, client: Client) -> None:
    """観点 A の実測。15 分待って本当に 401 になるかを見る（--verify-expiry のときだけ）。"""
    with rec.step(11, "access token の失効（15 分待って 401 を確認）") as row:
        assert client.token
        deadline = client.token.expires_at or (client.token.obtained_at + 900)
        wait = max(0.0, deadline - time.time() + 20)
        print(f"\n    {wait:.0f} 秒待つ ... ", end="", flush=True)
        time.sleep(wait)
        try:
            client.list_accounts()
            row["result"] = "still_valid"
            row["ok"] = False
            row["detail"] = {"note": "失効予定時刻を過ぎても 200 が返った"}
        except ApiError as exc:
            row["result"] = f"expired_{exc.status}"
            row["detail"] = {"status": exc.status, "code": exc.code, "waited_s": round(wait, 1)}


# ---------------------------------------------------------------- 手順 2


def step_accounts(rec: record.Recorder, client: Client, cfg: dict) -> str:
    account_number = None
    with rec.step(2, "口座照会（一覧・残高・買付余力）") as row:
        accounts = client.list_accounts()
        for account in accounts:
            rec.mask.add_account(field(account, "account-number"))
        account_number = cfg.get("TT_ACCOUNT_NUMBER") or field(accounts[0], "account-number")
        rec.mask.add_account(account_number)
        balances = client.get_balances(account_number)
        try:
            status = client.get_trading_status(account_number)
            trading = {
                "is-closing-only": field(status, "is-closing-only"),
                "equities-margin-calculation-type": field(status, "equities-margin-calculation-type"),
                "options-level": field(status, "options-level"),
            }
        except ApiError as exc:
            trading = {"error": f"{exc.status} {exc.code}"}
        row["detail"] = {
            "account_count": len(accounts),
            "account_types": [field(a, "account-type-name") for a in accounts],
            "margin_or_cash": [field(a, "margin-or-cash") for a in accounts],
            "balances": {
                "cash-balance": field(balances, "cash-balance"),
                "net-liquidating-value": field(balances, "net-liquidating-value"),
                "equity-buying-power": field(balances, "equity-buying-power"),
                "derivative-buying-power": field(balances, "derivative-buying-power"),
                "currency": field(balances, "currency"),
            },
            "trading_status": trading,
        }
        row["result"] = "ok"
    return account_number


# ---------------------------------------------------------------- 手順 3


def step_quote(rec: record.Recorder, client: Client, env_label: str) -> None:
    with rec.step(3, f"現在値（{SYMBOL} / REST market-data）", env=env_label) as row:
        requested_at = time.time()
        try:
            quote = client.get_quote(SYMBOL)
        except ApiError as exc:
            # cert は全経路 502。動かないことを記録として残す（プラン §2-1）
            row["ok"] = exc.status == 502 and client.env == "cert"
            row["result"] = f"unavailable_{exc.status}"
            row["detail"] = {
                "status": exc.status,
                "code": exc.code,
                "message": exc.message[:200],
                "expected": "sandbox は相場データを配信しない（公表値。/docs/sandbox）" if client.env == "cert" else None,
            }
            return
        updated_at = field(quote, "updated-at")
        delay_s = None
        if updated_at:
            try:
                ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                delay_s = round(requested_at - ts.timestamp(), 3)
            except ValueError:
                pass
        row["detail"] = {
            "symbol": field(quote, "symbol"),
            "bid": field(quote, "bid"),
            "ask": field(quote, "ask"),
            "mid": field(quote, "mid"),
            "last": field(quote, "last"),
            "updated-at": updated_at,
            "requested_at_utc": datetime.fromtimestamp(requested_at, timezone.utc).isoformat(timespec="milliseconds"),
            "delay_s": delay_s,
            "trading-halted": field(quote, "is-trading-halted"),
        }
        row["result"] = "ok"


# ---------------------------------------------------------------- 手順 4


def step_limit_and_cancel(rec: record.Recorder, client: Client, account_number: str) -> None:
    with rec.step(4, f"dry-run → 指値 ${LIMIT_PRICE_NO_FILL} → 照会 → 取消") as row:
        order = client.build_equity_order(
            SYMBOL, quantity=1, action="Buy to Open", order_type="Limit", price=LIMIT_PRICE_NO_FILL
        )
        external_id = order["external-identifier"]

        t0 = time.perf_counter()
        dry = client.dry_run_order(account_number, order)
        dry_ms = round((time.perf_counter() - t0) * 1000, 1)
        bpe = dry.get("buying-power-effect") or {}
        fee = dry.get("fee-calculation") or {}

        # 再送の前に「もう入っていないか」を見る経路（API は重複排除しない）
        already = client.find_order_by_external_id(account_number, external_id)

        t1 = time.perf_counter()
        submitted = client.submit_order(account_number, order)
        submit_ms = round((time.perf_counter() - t1) * 1000, 1)
        order_id = field(submitted["order"], "id")

        transitions = client.wait_for_status(account_number, order_id, {"Live", "Rejected", "Filled"}, timeout=20)

        t2 = time.perf_counter()
        cancelled = client.cancel_order(account_number, order_id)
        cancel_ms = round((time.perf_counter() - t2) * 1000, 1)
        after_cancel = client.wait_for_status(account_number, order_id, {"Cancelled", "Rejected"}, timeout=20)

        row["detail"] = {
            "external_identifier": external_id,
            "order_id": order_id,
            "duplicate_check_before_submit": already is not None,
            "dry_run": {
                "elapsed_ms": dry_ms,
                "status": field(dry.get("order", {}), "status"),
                "buying-power-effect": {
                    "change-in-buying-power": field(bpe, "change-in-buying-power"),
                    "change-in-buying-power-effect": field(bpe, "change-in-buying-power-effect"),
                    "new-buying-power": field(bpe, "new-buying-power"),
                },
                "fee-calculation": {
                    "total-fees": field(fee, "total-fees"),
                    "total-fees-effect": field(fee, "total-fees-effect"),
                },
                "warnings": record.excerpt(dry.get("warnings")),
            },
            "submit": {
                "elapsed_ms": submit_ms,
                "status_at_submit": field(submitted["order"], "status"),
                "transitions_after_submit": transitions,
            },
            "cancel": {"elapsed_ms": cancel_ms, "status_at_cancel": field(cancelled, "status"), "transitions_after_cancel": after_cancel},
            "roundtrip_ms": round((time.perf_counter() - t1) * 1000, 1),
        }
        final = (after_cancel or [{}])[-1].get("status")
        row["result"] = f"final_{final}"
        row["ok"] = final == "Cancelled"


# ---------------------------------------------------------------- 手順 5


def step_fill_and_flatten(
    rec: record.Recorder,
    client: Client,
    account_number: str,
    order_type: str = "Market",
    price: str | None = None,
    step_number: int = 5,
    title: str = "成行 1 株買い → 建玉 → 成行 1 株売り",
) -> None:
    """約定 → 建玉 → 反対売買。

    既定は成行（sandbox は常に $1 で約定）。⚠ 市場が閉まっていると建玉を作る成行は
    `tif_no_after_hours_opening_market_orders` で弾かれるので、時間外は指値 $2（$3 未満 ＝ 即約定の規則）で試す。
    """
    with rec.step(step_number, title) as row:
        detail = {}
        try:
            session = client.get_market_session()
            detail["market_session"] = {"state": field(session, "state"), "instrument-collection": field(session, "instrument-collection")}
        except ApiError as exc:
            detail["market_session"] = {"error": f"{exc.status} {exc.code}"}

        buy = client.build_equity_order(SYMBOL, 1, action="Buy to Open", order_type=order_type, price=price)
        client.dry_run_order(account_number, buy)  # 本発注の前に必ず通す
        t0 = time.perf_counter()
        submitted = client.submit_order(account_number, buy)
        buy_id = field(submitted["order"], "id")
        buy_transitions = client.wait_for_status(account_number, buy_id, {"Filled", "Rejected", "Expired"}, timeout=60)
        buy_ms = round((time.perf_counter() - t0) * 1000, 1)
        filled = client.get_order(account_number, buy_id)
        fills = (field(filled, "legs") or [{}])[0].get("fills", [])

        time.sleep(1.0)
        positions = client.list_positions(account_number)
        held = [p for p in positions if field(p, "symbol") == SYMBOL]

        sell = client.build_equity_order(SYMBOL, 1, action="Sell to Close", order_type=order_type, price=price)
        client.dry_run_order(account_number, sell)
        t1 = time.perf_counter()
        sold = client.submit_order(account_number, sell)
        sell_id = field(sold["order"], "id")
        sell_transitions = client.wait_for_status(account_number, sell_id, {"Filled", "Rejected", "Expired"}, timeout=60)
        sell_ms = round((time.perf_counter() - t1) * 1000, 1)

        time.sleep(1.0)
        after = [p for p in client.list_positions(account_number) if field(p, "symbol") == SYMBOL]

        detail.update(
            {
                "buy": {
                    "order_id": buy_id,
                    "elapsed_ms": buy_ms,
                    "transitions": buy_transitions,
                    "fills": record.excerpt(fills),
                },
                "position_after_buy": [
                    {
                        "symbol": field(p, "symbol"),
                        "quantity": field(p, "quantity"),
                        "quantity-direction": field(p, "quantity-direction"),
                        "average-open-price": field(p, "average-open-price"),
                    }
                    for p in held
                ],
                "sell": {"order_id": sell_id, "elapsed_ms": sell_ms, "transitions": sell_transitions},
                "position_after_sell": [
                    {"symbol": field(p, "symbol"), "quantity": field(p, "quantity")} for p in after
                ],
            }
        )
        row["detail"] = detail
        last_buy = (buy_transitions or [{}])[-1].get("status")
        last_sell = (sell_transitions or [{}])[-1].get("status")
        row["result"] = f"buy_{last_buy}/sell_{last_sell}"
        row["ok"] = last_buy == "Filled" and last_sell == "Filled"


# ---------------------------------------------------------------- 手順 6


def _order_activity(client: Client, account_number: str, t0: float) -> dict:
    """ストリーミング中に指値 → 取消を 1 往復させ、REST 照会側で見えた時刻を返す。

    通知（websocket）と照会（REST）で同じ状態がいつ見えたかを比べるための材料（Step 4-2）。
    """
    time.sleep(2.0)  # 接続が落ち着いてから
    order = client.build_equity_order(
        SYMBOL, 1, action="Buy to Open", order_type="Limit", price=LIMIT_PRICE_NO_FILL
    )
    submitted = client.submit_order(account_number, order)
    order_id = field(submitted["order"], "id")
    submitted_at_ms = round((time.perf_counter() - t0) * 1000, 1)
    rest_seen: dict[str, float] = {}
    for _ in range(40):
        status = field(client.get_order(account_number, order_id), "status")
        rest_seen.setdefault(status, round((time.perf_counter() - t0) * 1000, 1))
        if status == "Live":
            break
        time.sleep(0.25)
    client.cancel_order(account_number, order_id)
    for _ in range(40):
        status = field(client.get_order(account_number, order_id), "status")
        rest_seen.setdefault(status, round((time.perf_counter() - t0) * 1000, 1))
        if status in ("Cancelled", "Rejected"):
            break
        time.sleep(0.25)
    return {
        "order_id": order_id,
        "external_identifier": order["external-identifier"],
        "submitted_at_ms": submitted_at_ms,
        "rest_first_seen_ms": rest_seen,
    }


async def _account_streamer(client: Client, account_number: str, seconds: float, activity=None) -> dict:
    """口座ストリーマ（注文・建玉・残高の通知）。connect → heartbeat の順で送る。"""
    import websockets

    url = client.conf["account_streamer"]
    messages: list[dict] = []
    started = time.perf_counter()
    async with websockets.connect(url, open_timeout=20, ping_interval=None) as ws:
        connect_sent = time.perf_counter()
        # ⚠ 実測（2026-09-05）: ここは "Bearer " が要る。公式文書の例は素のトークンだが、
        # 素のまま送ると status:error / message:"Unknown domain" という無関係な応答になる
        auth_token = f"Bearer {client.token.access_token}"
        await ws.send(
            json.dumps(
                {
                    "action": "connect",
                    "value": [account_number],
                    "auth-token": auth_token,
                    "request-id": 1,
                }
            )
        )
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        ack_ms = round((time.perf_counter() - connect_sent) * 1000, 1)
        if ack.get("status") != "ok":
            raise RuntimeError(f"口座ストリーマの connect が失敗した: {ack.get('status')} {ack.get('message')}")

        async def heartbeat():
            request_id = 100
            while True:
                await asyncio.sleep(20)
                request_id += 1
                await ws.send(
                    json.dumps({"action": "heartbeat", "auth-token": auth_token, "request-id": request_id})
                )

        hb = asyncio.create_task(heartbeat())
        loop = asyncio.get_running_loop()
        activity_task = loop.run_in_executor(None, activity, started) if activity else None
        stream_seen: dict[str, float] = {}
        try:
            while time.perf_counter() - started < seconds:
                remaining = seconds - (time.perf_counter() - started)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                at_ms = round((time.perf_counter() - started) * 1000, 1)
                data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                order_status = data.get("status")
                if msg.get("type") == "Order" and order_status:
                    stream_seen.setdefault(order_status, at_ms)
                messages.append(
                    {
                        "at_ms": at_ms,
                        "type": msg.get("type") or msg.get("action"),
                        "status": msg.get("status"),
                        "order_status": order_status,
                    }
                )
        finally:
            hb.cancel()
    out = {
        "url": url,
        "connect_ack_ms": ack_ms,
        "connect_ack": {
            "status": ack.get("status"),
            "action": ack.get("action"),
            "message": ack.get("message"),
            "echoes_value": "value" in ack,  # 文書では口座番号が返るとあるが、実際は返らない
        },
        "message_count": len(messages),
        "stream_first_seen_ms": stream_seen,
        "messages": messages[:40],
    }
    if activity_task is not None:
        try:
            out["activity"] = await asyncio.wait_for(asyncio.shield(activity_task), timeout=30)
            rest_seen = out["activity"]["rest_first_seen_ms"]
            # 正なら「通知のほうが先に見えた」。REST は照会間隔（0.25 秒）の分だけ遅れる
            out["rest_minus_stream_ms"] = {
                status: round(rest_seen[status] - stream_seen[status], 1)
                for status in rest_seen
                if status in stream_seen
            }
        except Exception as exc:  # 発注側が失敗しても記録は残す
            out["activity"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


async def _dxlink_quotes(dxlink_url: str, token: str, symbol: str, seconds: float) -> dict:
    """DXLink（気配）。SETUP → AUTH → CHANNEL_REQUEST → FEED_SETUP → FEED_SUBSCRIPTION の順。"""
    import websockets

    quote_fields = ["eventType", "eventSymbol", "bidPrice", "askPrice", "bidSize", "askSize"]
    trade_fields = ["eventType", "eventSymbol", "price", "dayVolume", "size"]
    events: list[dict] = []
    handshake: list[dict] = []
    started = time.perf_counter()
    first_event_ms = None

    async with websockets.connect(dxlink_url, open_timeout=20, ping_interval=None) as ws:
        await ws.send(
            json.dumps(
                {"type": "SETUP", "channel": 0, "version": "0.1-DXF-JS/0.3.0", "keepaliveTimeout": 60, "acceptKeepaliveTimeout": 60}
            )
        )
        authorized = False
        subscribed = False

        async def keepalive():
            while True:
                await asyncio.sleep(30)
                await ws.send(json.dumps({"type": "KEEPALIVE", "channel": 0}))

        ka = asyncio.create_task(keepalive())
        try:
            while time.perf_counter() - started < seconds:
                remaining = seconds - (time.perf_counter() - started)
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining)))
                except asyncio.TimeoutError:
                    break
                mtype = msg.get("type")
                if mtype in ("SETUP", "AUTH_STATE", "CHANNEL_OPENED", "FEED_CONFIG", "ERROR"):
                    handshake.append(
                        {"at_ms": round((time.perf_counter() - started) * 1000, 1), "type": mtype, "state": msg.get("state"), "error": msg.get("error")}
                    )
                if mtype == "AUTH_STATE" and msg.get("state") == "UNAUTHORIZED":
                    await ws.send(json.dumps({"type": "AUTH", "channel": 0, "token": token}))
                elif mtype == "AUTH_STATE" and msg.get("state") == "AUTHORIZED" and not authorized:
                    authorized = True
                    await ws.send(
                        json.dumps({"type": "CHANNEL_REQUEST", "channel": 3, "service": "FEED", "parameters": {"contract": "AUTO"}})
                    )
                elif mtype == "CHANNEL_OPENED":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "FEED_SETUP",
                                "channel": 3,
                                "acceptAggregationPeriod": 0.1,
                                "acceptDataFormat": "COMPACT",
                                "acceptEventFields": {"Quote": quote_fields, "Trade": trade_fields},
                            }
                        )
                    )
                elif mtype == "FEED_CONFIG" and not subscribed:
                    subscribed = True
                    await ws.send(
                        json.dumps(
                            {
                                "type": "FEED_SUBSCRIPTION",
                                "channel": 3,
                                "reset": True,
                                "add": [{"type": "Quote", "symbol": symbol}, {"type": "Trade", "symbol": symbol}],
                            }
                        )
                    )
                elif mtype == "FEED_DATA":
                    at_ms = round((time.perf_counter() - started) * 1000, 1)
                    first_event_ms = first_event_ms if first_event_ms is not None else at_ms
                    label, flat = msg["data"][0], msg["data"][1]
                    width = len(quote_fields) if label == "Quote" else len(trade_fields)
                    for i in range(0, len(flat), width):
                        events.append({"at_ms": at_ms, "row": flat[i : i + width]})
        finally:
            ka.cancel()

    return {
        "dxlink_url": dxlink_url,
        "authorized": authorized,
        "subscribed": subscribed,
        "handshake": handshake,
        "event_count": len(events),
        "first_event_ms": first_event_ms,
        "events_head": events[:10],
        "events_tail": events[-5:],
    }


def step_cleanup(rec: record.Recorder, client: Client, account_number: str) -> None:
    """働いている注文を取り消して、次の実行を素の状態から始められるようにする。

    ⚠ 時間外に出した指値は `Received` のまま残り、次のセッションで約定してしまう。
    残したまま手順 5 を回すと `illegal_buy_and_sell_on_same_symbol` で弾かれる。
    """
    with rec.step(0, "後片付け（働いている注文の取消）") as row:
        working = [
            o
            for o in client.list_live_orders(account_number)
            if field(o, "status") in ("Received", "Routed", "In Flight", "Live")
        ]
        cancelled = []
        for order in working:
            order_id = field(order, "id")
            cancelled.append(
                {
                    "order_id": order_id,
                    "was": field(order, "status"),
                    "now": field(client.cancel_order(account_number, order_id), "status"),
                }
            )
        positions = [
            {"symbol": field(p, "symbol"), "quantity": field(p, "quantity")}
            for p in client.list_positions(account_number)
        ]
        row["detail"] = {"cancelled": cancelled, "remaining_positions": positions}
        row["result"] = f"cancelled_{len(cancelled)}"


def step_streaming(rec: record.Recorder, client: Client, account_number: str, seconds: float, quote_client: Client | None) -> None:
    with rec.step(6, f"口座ストリーマ {seconds:.0f} 秒（指値 → 取消を通知で受ける）") as row:
        # 通知を受けるものが無いと 0 件で終わるので、受信中に指値 → 取消を 1 往復させる
        can_order = client.env == "cert" or client.allow_prod_orders
        activity = (lambda t0: _order_activity(client, account_number, t0)) if can_order else None
        detail = asyncio.run(_account_streamer(client, account_number, seconds, activity))
        if not can_order:
            detail["activity"] = {"skipped": "本番では発注しないので通知を起こさない"}
        row["detail"] = detail
        notifications = sum(1 for m in detail["messages"] if m.get("type") == "Order")
        row["result"] = f"messages_{detail['message_count']}/order通知_{notifications}"
        # ハートビートの ok は通知が届いた証拠にならない。connect の成功と Order 通知で判定する
        row["ok"] = detail["connect_ack"]["status"] == "ok" and (notifications > 0 or not can_order)

    with rec.step(61, f"DXLink 気配 {SYMBOL} {seconds:.0f} 秒", env="prod" if quote_client else "n/a") as row:
        if quote_client is None:
            row["result"] = "未実測"
            row["ok"] = False
            row["detail"] = {"reason": "本番の資格情報（TT_PROD_*）が無い。sandbox は相場データを配信しない"}
            return
        token_info = quote_client.get_quote_token()
        rec.mask.add(token_info["token"], "<quote_token:masked>")
        row["detail"] = asyncio.run(
            _dxlink_quotes(token_info["dxlink-url"], token_info["token"], SYMBOL, seconds)
        )
        row["detail"]["entitlement_level"] = token_info.get("level")
        row["result"] = f"events_{row['detail']['event_count']}"
        row["ok"] = row["detail"]["event_count"] > 0


# ---------------------------------------------------------------- レート制限


def step_rate_limit(rec: record.Recorder, client: Client, account_number: str) -> None:
    """照会だけを連打して 429 の出方を見る。⚠ 発注は連打しない（プラン §Phase 4-3）。"""
    with rec.step(7, "レート制限（照会 60 回/分 → 10 回/秒）") as row:
        detail = {}
        for label, count, gap in (("60_per_minute", 60, 1.0), ("10_per_second", 30, 0.1)):
            statuses: dict[str, int] = {}
            first_429 = None
            headers_at_429 = None
            latencies = []
            for i in range(count):
                t0 = time.perf_counter()
                try:
                    client.get_balances(account_number)
                    key = "200"
                except ApiError as exc:
                    key = str(exc.status)
                    if exc.status == 429 and first_429 is None:
                        first_429 = i + 1
                        headers_at_429 = dict(client.session.headers)
                latencies.append(round((time.perf_counter() - t0) * 1000, 1))
                statuses[key] = statuses.get(key, 0) + 1
                if first_429:
                    break
                time.sleep(gap)
            latencies.sort()
            detail[label] = {
                "requests": sum(statuses.values()),
                "statuses": statuses,
                "first_429_at_request": first_429,
                "latency_ms": {
                    "min": latencies[0] if latencies else None,
                    "median": latencies[len(latencies) // 2] if latencies else None,
                    "max": latencies[-1] if latencies else None,
                },
            }
            if first_429:
                detail[label]["note"] = "429 が出たので以降は止めた（バックオフ）"
                time.sleep(5)
        row["detail"] = detail
        row["result"] = "429_seen" if any(v.get("first_429_at_request") for v in detail.values()) else "no_429"


# ---------------------------------------------------------------- 実行


def make_quote_client(cfg: dict, rec: record.Recorder) -> Client | None:  # noqa: D401
    """「本番で読み、sandbox に発注する」形（公式が勧める）。読み取り専用に使う。"""
    if not (cfg.get("TT_PROD_CLIENT_SECRET") and cfg.get("TT_PROD_REFRESH_TOKEN")):
        return None
    client = Client(
        env="prod",
        allow_prod_orders=False,
        rest_base=cfg.get("TT_PROD_REST_BASE") or None,
    )
    rec.mask.add(cfg["TT_PROD_CLIENT_SECRET"], "<prod_client_secret:masked>")
    rec.mask.add(cfg["TT_PROD_REFRESH_TOKEN"], "<prod_refresh_token:masked>")
    client.authenticate(
        client_secret=cfg["TT_PROD_CLIENT_SECRET"],
        refresh_token=cfg["TT_PROD_REFRESH_TOKEN"],
        client_id=cfg.get("TT_PROD_CLIENT_ID") or None,
    )
    rec.mask.add(client.token.access_token, "<prod_access_token:masked>")
    return client


def step_prod_probe(rec: record.Recorder, cfg: dict, seconds: float = 15.0) -> None:
    """入金前の本番口座で相場データが取れるかを見る（プラン 未確定 #3 の切り分け）。

    **読み取りだけ。発注はしない**（allow_prod_orders=False 固定）。
    ⚠ 入金が着いたら二度と測れないので、着金前に 1 回だけ通す。
    """
    with rec.step(8, "入金前の本番口座で気配が取れるか（未確定 #3）", env="prod") as row:
        client = make_quote_client(cfg, rec)
        if client is None:
            row["ok"] = False
            row["result"] = "未実測"
            row["detail"] = {"reason": "TT_PROD_CLIENT_SECRET / TT_PROD_REFRESH_TOKEN が無い"}
            return

        detail: dict = {"note": "入金の着金前に取得した記録。発注は行っていない"}

        accounts = client.list_accounts()
        for account in accounts:
            rec.mask.add_account(field(account, "account-number"))
        account_number = field(accounts[0], "account-number")
        detail["accounts"] = [
            {
                "account-type-name": field(a, "account-type-name"),
                "margin-or-cash": field(a, "margin-or-cash"),
                "opened-at": field(a, "opened-at"),
                "is-closed": field(a, "is-closed"),
            }
            for a in accounts
        ]
        # 資金の状態そのものが「入金前」の証拠になるので、残高は絞らず丸ごと残す
        detail["balances"] = record.excerpt(client.get_balances(account_number), limit=40)
        try:
            detail["trading_status"] = record.excerpt(client.get_trading_status(account_number), limit=40)
        except ApiError as exc:
            detail["trading_status"] = {"error": f"{exc.status} {exc.code}: {exc.message[:120]}"}

        # (1) REST の気配スナップショット。市場が閉まっていても最終値が返るので週末でも判定できる
        try:
            quote = client.get_quote(SYMBOL)
            detail["rest_market_data"] = {
                "ok": True,
                "symbol": field(quote, "symbol"),
                "bid": field(quote, "bid"),
                "ask": field(quote, "ask"),
                "last": field(quote, "last"),
                "close": field(quote, "close"),
                "updated-at": field(quote, "updated-at"),
            }
        except ApiError as exc:
            detail["rest_market_data"] = {"ok": False, "status": exc.status, "code": exc.code, "message": exc.message[:200]}

        # (2) quote token。onboarding が未完了なら quote_streamer.customer_not_found_error
        token_info = None
        try:
            token_info = client.get_quote_token()
            rec.mask.add(token_info["token"], "<quote_token:masked>")
            detail["api_quote_token"] = {"ok": True, "level": token_info.get("level"), "dxlink-url": token_info.get("dxlink-url")}
        except ApiError as exc:
            detail["api_quote_token"] = {"ok": False, "status": exc.status, "code": exc.code, "message": exc.message[:200]}

        # (3) DXLink に実際に繋いで AUTHORIZED まで行くか
        if token_info:
            stream = asyncio.run(_dxlink_quotes(token_info["dxlink-url"], token_info["token"], SYMBOL, seconds))
            detail["dxlink"] = {
                "authorized": stream["authorized"],
                "subscribed": stream["subscribed"],
                "event_count": stream["event_count"],
                "handshake": stream["handshake"],
                "events_head": stream["events_head"][:3],
                "caveat": "⚠ 市場が閉まっている時間帯は配信自体が無いので、event_count 0 は権限の否定にはならない",
            }

        row["detail"] = detail
        rest_ok = detail["rest_market_data"].get("ok")
        token_ok = detail["api_quote_token"].get("ok")
        row["ok"] = bool(rest_ok or token_ok)
        row["result"] = f"rest_{'ok' if rest_ok else 'ng'}/token_{'ok' if token_ok else 'ng'}"


def step_prod_dry_run(rec: record.Recorder, cfg: dict) -> None:
    """本番で dry-run だけ通し、着金前の買付余力が実際に使えるかを見る。

    `equity-buying-power` 1000.0 と `available-trading-funds` 0.0 が食い違っていたので、
    **どちらが効くか**を注文の検証だけで確かめる。⚠ dry-run は何もルーティングしない。
    """
    with rec.step(9, "本番 dry-run（着金前の買付余力は使えるか）", env="prod") as row:
        client = Client(
            env="prod",
            allow_prod_orders=False,
            allow_prod_dry_run=True,
            rest_base=cfg.get("TT_PROD_REST_BASE") or None,
        )
        rec.mask.add(cfg["TT_PROD_CLIENT_SECRET"], "<prod_client_secret:masked>")
        rec.mask.add(cfg["TT_PROD_REFRESH_TOKEN"], "<prod_refresh_token:masked>")
        client.authenticate(
            client_secret=cfg["TT_PROD_CLIENT_SECRET"],
            refresh_token=cfg["TT_PROD_REFRESH_TOKEN"],
            client_id=cfg.get("TT_PROD_CLIENT_ID") or None,
        )
        rec.mask.add(client.token.access_token, "<prod_access_token:masked>")

        account_number = field(client.list_accounts()[0], "account-number")
        rec.mask.add_account(account_number)
        balances = client.get_balances(account_number)

        # 約定させないため、気配より十分低い指値にする（dry-run なのでルーティングもされない）
        quote = client.get_quote(SYMBOL)
        reference = float(field(quote, "bid") or field(quote, "last") or 0)
        price = f"{max(1.0, reference * 0.8):.2f}"

        cases = []
        # 1 株（余力内）と 10 株（余力超え）を並べると、どちらの数字が効いているかが分かる
        for quantity, label in ((1, "1 株（余力内のはず）"), (10, "10 株（$1,000 を超える）")):
            order = client.build_equity_order(SYMBOL, quantity, order_type="Limit", price=price)
            case = {"quantity": quantity, "label": label, "limit_price": price}
            try:
                dry = client.dry_run_order(account_number, order)
                bpe = dry.get("buying-power-effect") or {}
                fee = dry.get("fee-calculation") or {}
                case.update(
                    {
                        "accepted": True,
                        "order_status": field(dry.get("order", {}), "status"),
                        "buying-power-effect": {
                            k: field(bpe, k)
                            for k in (
                                "change-in-buying-power",
                                "change-in-buying-power-effect",
                                "current-buying-power",
                                "new-buying-power",
                                "is-spread",
                            )
                        },
                        "fee-calculation": {
                            "total-fees": field(fee, "total-fees"),
                            "total-fees-effect": field(fee, "total-fees-effect"),
                        },
                        "warnings": record.excerpt(dry.get("warnings")),
                        "errors": record.excerpt(dry.get("errors")),
                    }
                )
            except ApiError as exc:
                case.update(
                    {
                        "accepted": False,
                        "status": exc.status,
                        "code": exc.code,
                        "message": exc.message[:300],
                        "body": record.excerpt(exc.body, limit=10),
                    }
                )
            cases.append(case)

        row["detail"] = {
            "note": "dry-run のみ。注文は一切ルーティングしていない",
            "balances_at_run": {
                k: field(balances, k)
                for k in (
                    "cash-balance",
                    "pending-cash",
                    "equity-buying-power",
                    "available-trading-funds",
                    "net-liquidating-value",
                )
            },
            "reference_quote": {"bid": field(quote, "bid"), "last": field(quote, "last")},
            "cases": cases,
        }
        one, ten = cases[0], cases[1]
        row["result"] = f"1株={'通る' if one['accepted'] else '弾かれる'}/10株={'通る' if ten['accepted'] else '弾かれる'}"


def main() -> int:
    parser = argparse.ArgumentParser(description="tastytrade API サンプル（6 手順）")
    parser.add_argument("--step", default="all", help="all / 1..6 / 5limit / cleanup / rate / probe / dryrun（カンマ区切り可）")
    parser.add_argument("--env", default=None, choices=["cert", "prod"], help="既定は .env の TT_ENV、無ければ cert")
    parser.add_argument("--seconds", type=float, default=60.0, help="手順 6 の受信時間")
    parser.add_argument("--verify-expiry", action="store_true", help="手順 1 のあと 15 分待って 401 を確認する")
    parser.add_argument(
        "--allow-prod-dry-run",
        action="store_true",
        help="本番の dry-run だけ許す（検証のみ。注文はルーティングされない）",
    )
    parser.add_argument("--i-know-this-is-real-money", action="store_true", help="本番で発注系を許す（Phase 6）")
    args = parser.parse_args()

    # TT_ENV_FILE は自己検査で手元の .env を読ませないための逃げ道（selftest.sh が使う）
    cfg = load_env(os.environ.get("TT_ENV_FILE") or os.path.join(HERE, ".env"))
    env = args.env or cfg.get("TT_ENV", "cert")
    allow_prod_orders = args.i_know_this_is_real_money and cfg.get("TT_ALLOW_PROD_ORDERS") == "1"

    steps = ["1", "2", "3", "4", "5", "6"] if args.step == "all" else [s.strip() for s in args.step.split(",")]
    # 接続先を差し替えた実行（モック）は記録に mock: true を付け、判定から外す
    is_mock = bool(cfg.get("TT_REST_BASE") or cfg.get("TT_PROD_REST_BASE"))

    if os.path.exists(HALT_FILE) and (set(steps) & ORDER_STEPS):
        print(
            f"拒否: 停止フラグがある（{HALT_FILE}）。停止中は発注系の手順 {sorted(set(steps) & ORDER_STEPS)} を実行しない。"
            "解除は管理画面の「再開」か、このファイルの削除",
            file=sys.stderr,
        )
        return 3

    # probe / dryrun は本番の資格情報だけで動く（sandbox の準備を待たずに入金前の窓を押さえるため）
    if steps in (["probe"], ["dryrun"], ["probe", "dryrun"], ["dryrun", "probe"]):
        missing = [k for k in ("TT_PROD_CLIENT_SECRET", "TT_PROD_REFRESH_TOKEN") if not cfg.get(k)]
        if missing:
            print(f"エラー: {', '.join(missing)} が無い。.env に本番の資格情報を入れる", file=sys.stderr)
            return 2
        if "dryrun" in steps and not args.allow_prod_dry_run:
            print("エラー: 本番の dry-run には --allow-prod-dry-run が要る", file=sys.stderr)
            return 2
        rec = record.Recorder(OUT_DIR, venue="tastytrade", env="prod", mock=is_mock)
        print("=== 環境: prod — 読み取りのみ（発注はしない）===")
        print(f"記録: {rec.path}")
        try:
            for step in steps:
                if step == "probe":
                    step_prod_probe(rec, cfg, seconds=args.seconds if args.seconds < 60 else 15.0)
                else:
                    step_prod_dry_run(rec, cfg)
        except (ApiError, ProductionGuard, OSError) as exc:
            print(f"中断: {exc}", file=sys.stderr)
            return 1
        print(f"\n完了。記録: {rec.path}")
        return 0

    missing = [k for k in ("TT_CLIENT_SECRET", "TT_REFRESH_TOKEN") if not cfg.get(k)]
    if missing:
        print(f"エラー: {', '.join(missing)} が無い。.env.example を .env にコピーして埋める", file=sys.stderr)
        return 2

    client = Client(
        env=env,
        allow_prod_orders=allow_prod_orders,
        allow_prod_dry_run=args.allow_prod_dry_run,
        rest_base=cfg.get("TT_REST_BASE") or None,
        account_streamer=cfg.get("TT_ACCOUNT_STREAMER") or None,
    )
    banner = client.conf["label"]
    print(f"=== 環境: {env} — {banner} / {client.base} ===")
    if env == "prod":
        print("!!! 本番環境です。発注系は " + ("許可されています（実弾）" if allow_prod_orders else "拒否されます") + " !!!")

    rec = record.Recorder(OUT_DIR, venue="tastytrade", env=env, mock=is_mock)
    print(f"記録: {rec.path}")

    failed = False
    try:
        step_auth(rec, cfg, client)
        if args.verify_expiry:
            step_auth_expiry(rec, client)
            step_auth(rec, cfg, client)  # 取り直してから続ける
        account_number = step_accounts(rec, client, cfg)

        quote_client = None
        if {"3", "6"} & set(steps):
            quote_client = make_quote_client(cfg, rec)

        for step in steps:
            try:
                if step == "3":
                    step_quote(rec, client, env_label=env)
                    if quote_client is not None:
                        step_quote(rec, quote_client, env_label="prod")
                elif step == "4":
                    step_limit_and_cancel(rec, client, account_number)
                elif step == "5":
                    step_fill_and_flatten(rec, client, account_number)
                elif step == "5limit":
                    # 時間外の迂回路。sandbox の「$3 未満の指値は即約定」が時間外にも効くかを見る
                    step_fill_and_flatten(
                        rec,
                        client,
                        account_number,
                        order_type="Limit",
                        price="2.00",
                        step_number=51,
                        title="指値 $2.00 で 1 株買い → 建玉 → 反対売買（時間外の迂回路）",
                    )
                elif step == "6":
                    step_streaming(rec, client, account_number, args.seconds, quote_client)
                elif step == "cleanup":
                    step_cleanup(rec, client, account_number)
                elif step == "rate":
                    step_rate_limit(rec, client, account_number)
                elif step == "probe":
                    step_prod_probe(rec, cfg, seconds=15.0)
                elif step in ("1", "2"):
                    pass  # 認証と口座照会は上で必ず通している
                else:
                    print(f"未知の手順: {step}", file=sys.stderr)
            except ProductionGuard as exc:
                print(f"[step {step}] 拒否: {exc}", file=sys.stderr)
                failed = True
            except (ApiError, OSError) as exc:
                print(f"[step {step}] 失敗: {exc}", file=sys.stderr)
                failed = True
    except (ApiError, ProductionGuard, OSError) as exc:
        print(f"中断: {exc}", file=sys.stderr)
        return 1

    print(f"\n完了。記録: {rec.path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
