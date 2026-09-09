"""tastytrade DXLink（Candle）からの取得。⚠ **読み取りだけ。発注系の経路には一切触れない。**

⚠ 実測で分かっている制約（2026-09-08）:
  - 1 購読あたり **約 8,000 本**で頭打ち（1 分足なら約 6 週間、日足なら約 32 年）
  - ⚠ **`toTime` は無視される**ので、過去へ窓を刻んで遡ることはできない
  - ⚠ 期間の値 1 は書かない（`{=m}` は取れるが `{=1m}` は 0 件）
  - `Candle` の購読上限は 1 セッション 100 件【公表値】
  - ⚠ 最後の 1 本は更新され続けるので、同じ時刻の行が何度も届く（時刻で畳んで最後を採る）
  - ⚠ **NaN の番兵行が各銘柄 1 本混じる。** 落とさないと「最古の足」を誤る
  - 相場データは**本番（prod）の資格情報でしか出ない**（sandbox の `/market-data` は全経路 502）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from ail.registry import register

SAMPLE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                                      "tastytrade-api-sample"))
FIELDS = ["eventType", "eventSymbol", "time", "open", "high", "low", "close", "volume"]


def _ttclient():
    """⚠ **サンプル側の venv でしか import できない**（websockets が要る）ので、呼ばれるまで遅らせる。"""
    if SAMPLE not in sys.path:
        sys.path.insert(0, SAMPLE)
    import ttclient
    return ttclient


async def _fetch_batch(dxlink_url: str, token: str, symbols: list[str], period: str,
                       from_ms: int, idle_s: float, max_s: float) -> dict[str, dict]:
    """1 セッションで symbols を購読し、{購読シンボル: {時刻: 生の行}} を返す。

    ⚠ 終わりの合図が無いので、⚠ **idle_s 秒だけ新しいデータが来なければ終わり**とみなす。
    """
    import websockets

    subs = [f"{s}{{={period}}}" for s in symbols]
    got: dict[str, dict[int, list]] = {s: {} for s in subs}
    t0 = time.perf_counter()
    last_data = [t0]

    async with websockets.connect(dxlink_url, open_timeout=20, ping_interval=None,
                                  max_size=None) as ws:
        await ws.send(json.dumps({"type": "SETUP", "channel": 0, "version": "0.1-DXF-JS/0.3.0",
                                  "keepaliveTimeout": 60, "acceptKeepaliveTimeout": 60}))
        authorized = subscribed = False

        async def keepalive():
            while True:
                await asyncio.sleep(30)
                await ws.send(json.dumps({"type": "KEEPALIVE", "channel": 0}))

        ka = asyncio.create_task(keepalive())
        try:
            while time.perf_counter() - t0 < max_s:
                if subscribed and time.perf_counter() - last_data[0] > idle_s:
                    break
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
                except asyncio.TimeoutError:
                    continue
                mtype = msg.get("type")
                if mtype == "ERROR":
                    print(f"  ⚠ ERROR {msg.get('error')}: {msg.get('message')}", flush=True)
                elif mtype == "AUTH_STATE" and msg.get("state") == "UNAUTHORIZED":
                    await ws.send(json.dumps({"type": "AUTH", "channel": 0, "token": token}))
                elif mtype == "AUTH_STATE" and msg.get("state") == "AUTHORIZED" and not authorized:
                    authorized = True
                    await ws.send(json.dumps({"type": "CHANNEL_REQUEST", "channel": 3,
                                              "service": "FEED",
                                              "parameters": {"contract": "AUTO"}}))
                elif mtype == "CHANNEL_OPENED":
                    await ws.send(json.dumps({"type": "FEED_SETUP", "channel": 3,
                                              "acceptAggregationPeriod": 1.0,
                                              "acceptDataFormat": "COMPACT",
                                              "acceptEventFields": {"Candle": FIELDS}}))
                elif mtype == "FEED_CONFIG" and not subscribed:
                    subscribed = True
                    last_data[0] = time.perf_counter()
                    await ws.send(json.dumps({"type": "FEED_SUBSCRIPTION", "channel": 3,
                                              "reset": True,
                                              "add": [{"type": "Candle", "symbol": s,
                                                       "fromTime": from_ms} for s in subs]}))
                elif mtype == "FEED_DATA":
                    last_data[0] = time.perf_counter()
                    label, flat = msg["data"][0], msg["data"][1]
                    if label != "Candle":
                        continue
                    w = len(FIELDS)
                    for i in range(0, len(flat), w):
                        row = flat[i:i + w]
                        sym, t = row[1], row[2]
                        if sym in got and isinstance(t, (int, float)):
                            got[sym][int(t)] = row     # ⚠ 同じ時刻は最後に来たもので上書き
        finally:
            ka.cancel()
    return got


def _to_bars(rows: dict[int, list]) -> "pandas.DataFrame":  # noqa: F821
    """生の行を `BAR_COLUMNS` の表にする。⚠ **NaN の番兵行はここで落とす**。"""
    import pandas as pd
    out = []
    for t, row in sorted(rows.items()):
        vals = row[3:8]
        if any(v == "NaN" or v is None for v in vals):
            continue
        out.append([t, *[float(v) for v in vals]])
    return pd.DataFrame(out, columns=["time_ms", "open", "high", "low", "close", "volume"])


@register("source", "tastytrade")
def fetch(symbols: list[str], period: str, days: int, batch: int = 16,
          idle: float = 8.0, max_s: float = 300.0, verbose: bool = True) -> dict:
    """銘柄ごとの足を取る。返り値は {シンボル: DataFrame}。⚠ **書き込みはしない**（呼び手が store に渡す）。"""
    ttclient = _ttclient()
    cfg = ttclient.load_env(os.path.join(SAMPLE, ".env"))
    if not (cfg.get("TT_PROD_CLIENT_SECRET") and cfg.get("TT_PROD_REFRESH_TOKEN")):
        raise SystemExit("TT_PROD_CLIENT_SECRET / TT_PROD_REFRESH_TOKEN が無い（相場データは本番だけ）")

    client = ttclient.Client(env="prod")
    client.authenticate(client_secret=cfg["TT_PROD_CLIENT_SECRET"],
                        refresh_token=cfg["TT_PROD_REFRESH_TOKEN"],
                        client_id=cfg.get("TT_PROD_CLIENT_ID"))
    info = client.get_quote_token()
    from_ms = int(time.time() * 1000) - days * 86_400_000

    out = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        if verbose:
            print(f"[{i // batch + 1}] {len(chunk)} 銘柄: {' '.join(chunk)}", flush=True)
        got = asyncio.run(_fetch_batch(info["dxlink-url"], info["token"], chunk, period,
                                       from_ms, idle, max_s))
        for sub, rows in got.items():
            sym = sub.split("{")[0]
            bars = _to_bars(rows)
            out[sym] = bars
            if verbose:
                print(f"    {sub:<14} {len(bars):>6} 本" + ("" if len(bars) else "  ⚠ 0 本"),
                      flush=True)
    return out
