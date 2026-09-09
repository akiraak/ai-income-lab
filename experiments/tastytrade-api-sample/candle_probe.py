"""過去の足（Candle）が DXLink で取れるかを測るだけの読み取り専用プローブ。

発注系の経路には一切触れない。CLAUDE.md の例外（tastytrade の **API の挙動**の実測）の内側で回す。
相場データは本番（prod）の資格情報でしか出ないので、`.env` の TT_PROD_* を使う。

    .venv/bin/python candle_probe.py                     # 既定の 5 本
    .venv/bin/python candle_probe.py "SPY{=d}:3650" "AAPL{=m}:5"

引数は `シンボル{=期間}:遡る日数`。⚠ 期間の値 1 は書かない（`{=m}` は取れるが `{=1m}` は 0 件）。
"""

import asyncio
import datetime as dt
import json
import sys
import time

import ttclient

FIELDS = ["eventType", "eventSymbol", "time", "open", "high", "low", "close", "volume"]


async def fetch(dxlink_url: str, token: str, reqs: list[dict], wait_s: float):
    """SETUP → AUTH → CHANNEL_REQUEST → FEED_SETUP → FEED_SUBSCRIPTION（Candle は fromTime 付き）。"""
    import websockets

    got: dict[str, list] = {r["symbol"]: [] for r in reqs}
    handshake: list[dict] = []
    first_ms: dict[str, float] = {}
    t0 = time.perf_counter()

    async with websockets.connect(dxlink_url, open_timeout=20, ping_interval=None) as ws:
        await ws.send(
            json.dumps(
                {"type": "SETUP", "channel": 0, "version": "0.1-DXF-JS/0.3.0", "keepaliveTimeout": 60, "acceptKeepaliveTimeout": 60}
            )
        )
        authorized = subscribed = False

        async def keepalive():
            while True:
                await asyncio.sleep(30)
                await ws.send(json.dumps({"type": "KEEPALIVE", "channel": 0}))

        ka = asyncio.create_task(keepalive())
        try:
            while time.perf_counter() - t0 < wait_s:
                remaining = wait_s - (time.perf_counter() - t0)
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining)))
                except asyncio.TimeoutError:
                    break
                mtype = msg.get("type")
                if mtype in ("AUTH_STATE", "CHANNEL_OPENED", "FEED_CONFIG", "ERROR"):
                    handshake.append(
                        {"at_ms": round((time.perf_counter() - t0) * 1000, 1), "type": mtype, "state": msg.get("state"), "error": msg.get("error")}
                    )
                if mtype == "AUTH_STATE" and msg.get("state") == "UNAUTHORIZED":
                    await ws.send(json.dumps({"type": "AUTH", "channel": 0, "token": token}))
                elif mtype == "AUTH_STATE" and msg.get("state") == "AUTHORIZED" and not authorized:
                    authorized = True
                    await ws.send(json.dumps({"type": "CHANNEL_REQUEST", "channel": 3, "service": "FEED", "parameters": {"contract": "AUTO"}}))
                elif mtype == "CHANNEL_OPENED":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "FEED_SETUP",
                                "channel": 3,
                                "acceptAggregationPeriod": 1.0,
                                "acceptDataFormat": "COMPACT",
                                "acceptEventFields": {"Candle": FIELDS},
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
                                "add": [{"type": "Candle", "symbol": r["symbol"], "fromTime": r["fromTime"]} for r in reqs],
                            }
                        )
                    )
                elif mtype == "FEED_DATA":
                    label, flat = msg["data"][0], msg["data"][1]
                    if label != "Candle":
                        continue
                    width = len(FIELDS)
                    for i in range(0, len(flat), width):
                        row = flat[i : i + width]
                        if row[1] in got:
                            got[row[1]].append(row)
                            first_ms.setdefault(row[1], round((time.perf_counter() - t0) * 1000, 1))
        finally:
            ka.cancel()

    return authorized, subscribed, handshake, got, first_ms


def main() -> None:
    cfg = ttclient.load_env(".env")
    if not (cfg.get("TT_PROD_CLIENT_SECRET") and cfg.get("TT_PROD_REFRESH_TOKEN")):
        print("TT_PROD_CLIENT_SECRET / TT_PROD_REFRESH_TOKEN が無い（相場データは本番だけ）")
        return

    client = ttclient.Client(env="prod")
    client.authenticate(
        client_secret=cfg["TT_PROD_CLIENT_SECRET"],
        refresh_token=cfg["TT_PROD_REFRESH_TOKEN"],
        client_id=cfg.get("TT_PROD_CLIENT_ID"),
    )
    token_info = client.get_quote_token()
    print(f"quote token level={token_info.get('level')} url={token_info.get('dxlink-url')}")

    now_ms = int(time.time() * 1000)
    day_ms = 86_400_000
    if len(sys.argv) > 1:
        specs = [a.rsplit(":", 1) for a in sys.argv[1:]]
        reqs = [{"symbol": s, "fromTime": now_ms - int(d) * day_ms} for s, d in specs]
    else:
        reqs = [
            {"symbol": "SPY{=d}", "fromTime": now_ms - 12000 * day_ms},
            {"symbol": "AAPL{=d}", "fromTime": now_ms - 3650 * day_ms},
            {"symbol": "SPY{=m}", "fromTime": now_ms - 730 * day_ms},
            {"symbol": "SPY{=5m}", "fromTime": now_ms - 30 * day_ms},
            {"symbol": "SPY{=1m}", "fromTime": now_ms - 3 * day_ms},
        ]

    authorized, subscribed, handshake, got, first_ms = asyncio.run(fetch(token_info["dxlink-url"], token_info["token"], reqs, 45.0))
    print(f"authorized={authorized} subscribed={subscribed}")
    print("handshake=" + json.dumps(handshake, ensure_ascii=False))
    for req in reqs:
        rows = got[req["symbol"]]
        if not rows:
            print(f"{req['symbol']}: 0 本")
            continue
        times = sorted(r[2] for r in rows if isinstance(r[2], (int, float)))
        oldest = dt.datetime.fromtimestamp(times[0] / 1000, dt.timezone.utc).date()
        newest = dt.datetime.fromtimestamp(times[-1] / 1000, dt.timezone.utc)
        print(f"{req['symbol']}: {len(rows)} 本 oldest={oldest} newest={newest:%Y-%m-%d %H:%M} first_event_ms={first_ms.get(req['symbol'])}")


if __name__ == "__main__":
    main()
