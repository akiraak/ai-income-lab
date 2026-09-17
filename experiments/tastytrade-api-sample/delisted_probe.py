"""上場廃止銘柄の日足が取れるかを確かめる読み取り専用プローブ（段 4。[プラン](../../docs/plans/universe-delisted.md)）。

⚠ **生存バイアスを消せる唯一の道が開くかどうかを測る。** 銘柄集合はどれも「いま上場している銘柄」を
過去に当てはめたもので、⚠ **当時存在して消えた会社は 1 本も入っていない**（rules.md 12 章 限界 1）。

⚠ **発注系には一切触れない**（GET だけ）。CLAUDE.md の 2026-09-05 の例外（**API の挙動**の実測）の内側。
⚠ **引く銘柄は結果を見る前に固定した**（プラン §0-1）。⚠ **取れた銘柄だけを後から並べない。**

    .venv/bin/python delisted_probe.py              # instruments（cert・prod）＋ 日足（prod）
    .venv/bin/python delisted_probe.py --no-candles # instruments だけ

測るもの（プラン §0-2）: 1) instruments が知っているか 2) 「もう取引できない」と分かる項目があるか
3) ⚠ **日足が返るか（本題）** 4) cert と prod の違い。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import time

import candle_probe
import record
import ttclient

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("TT_OUT_DIR") or os.path.join(HERE, "out")

# ⚠ **回す前に固定した 8 本**（プラン §0-1）。⚠ **対照 2 本は「API が生きていること」の確認用**
SYMBOLS = [
    ("TWTR", "廃止", "NYSE 上場廃止 2022-11-08（買収・非公開化）"),
    ("ATVI", "廃止", "Nasdaq 上場廃止 2023-10-13（Microsoft の買収完了）"),
    ("SIVB", "廃止", "FDIC 管財人・S&P 500 から 2023-03-15 除外"),
    ("FRC", "廃止", "FDIC 管財人（2023-05）"),
    ("XLNX", "廃止", "AMD が買収（2022-02）"),
    ("CERN", "廃止", "Oracle が買収（2022-06）"),
    ("AAPL", "対照", "上場中（対照）"),
    ("SPY", "対照", "上場中の ETF（対照）"),
]
# ⚠ **応答のどの項目を見るか**（公式リファレンスは項目名を載せていないので、実測で拾う）
FIELDS = ("symbol", "instrument-type", "description", "active", "is-illiquid", "is-index",
          "listed-market", "cusip", "market-time-instrument-collection")
# 日足をどこまで遡って要求するか（⚠ 12,000 日 ≒ 32 年。`cli/fetch.py` と同じ桁）
CANDLE_DAYS = 12000
CANDLE_WAIT_S = 45.0


def probe_instruments(rec: record.Recorder, client: ttclient.Client, env: str) -> list[dict]:
    """`GET /instruments/equities/{symbol}` を 8 本ぶん引く。⚠ **状態と項目をそのまま残す。**"""
    rows = []
    for sym, kind, note in SYMBOLS:
        row = {"step": "instruments", "env": env, "symbol": sym, "kind": kind, "note": note}
        try:
            body = client.get(f"/instruments/equities/{sym}")
            data = (body or {}).get("data") or {}
            row["ok"] = True
            row["status"] = client.last_status
            row["fields"] = {k: data.get(k) for k in FIELDS if k in data}
            row["field_names"] = sorted(data)
        except ttclient.ApiError as exc:
            row["ok"] = False
            row["status"] = exc.status
            row["error_code"] = exc.code
            row["error_message"] = exc.message
        rec.write(row)
        rows.append(row)
        mark = "○" if row["ok"] else "×"
        detail = (f"active={row['fields'].get('active')} 市場={row['fields'].get('listed-market')}"
                  if row["ok"] else f"{row.get('status')} {row.get('error_code')}")
        print(f"  [{env}] {mark} {sym:<5} {kind}  {detail}")
    return rows


def probe_candles(rec: record.Recorder, client: ttclient.Client) -> list[dict]:
    """⚠ **本題**: 廃止銘柄の日足が DXLink で返るか（相場データは本番だけ）。"""
    token_info = client.get_quote_token()
    now_ms = int(time.time() * 1000)
    reqs = [{"symbol": f"{s}{{=d}}", "fromTime": now_ms - CANDLE_DAYS * 86_400_000}
            for s, _k, _n in SYMBOLS]
    authorized, subscribed, handshake, got, first_ms = asyncio.run(
        candle_probe.fetch(token_info["dxlink-url"], token_info["token"], reqs, CANDLE_WAIT_S))
    rec.write({"step": "candles_handshake", "authorized": authorized, "subscribed": subscribed,
               "handshake": handshake, "遡り日数": CANDLE_DAYS, "待ち秒": CANDLE_WAIT_S})
    rows = []
    for (sym, kind, _note), req in zip(SYMBOLS, reqs):
        bars = got[req["symbol"]]
        times = sorted(r[2] for r in bars if isinstance(r[2], (int, float)))
        row = {"step": "candles", "symbol": sym, "kind": kind, "本数": len(bars),
               "最古": str(dt.datetime.fromtimestamp(times[0] / 1000, dt.timezone.utc).date()) if times else None,
               "最新": str(dt.datetime.fromtimestamp(times[-1] / 1000, dt.timezone.utc).date()) if times else None,
               "最初のイベントms": first_ms.get(req["symbol"])}
        rec.write(row)
        rows.append(row)
        print(f"  [candle] {sym:<5} {kind}  {row['本数']:>6} 本  {row['最古']} 〜 {row['最新']}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-candles", action="store_true", help="instruments だけ引く")
    args = ap.parse_args()

    cfg = ttclient.load_env(os.environ.get("TT_ENV_FILE") or os.path.join(HERE, ".env"))
    rec = record.Recorder(OUT_DIR, venue="tastytrade", env="both", mock=False)
    print(f"記録: {rec.path}")
    rec.write({"step": "plan", "銘柄": [{"symbol": s, "kind": k, "note": n} for s, k, n in SYMBOLS],
               "注記": "⚠ 引く銘柄は結果を見る前に固定した（plans/universe-delisted.md §0-1）"})

    summary: dict[str, list[dict]] = {}
    # --- cert（sandbox）---------------------------------------------------
    if cfg.get("TT_CLIENT_SECRET") and cfg.get("TT_REFRESH_TOKEN"):
        c = ttclient.Client(env="cert")
        c.authenticate(client_secret=cfg["TT_CLIENT_SECRET"], refresh_token=cfg["TT_REFRESH_TOKEN"],
                       client_id=cfg.get("TT_CLIENT_ID"))
        print("=== cert（sandbox）の instruments ===")
        summary["cert"] = probe_instruments(rec, c, "cert")
    else:
        print("cert の資格情報が無いので飛ばす")

    # --- prod（読み取りだけ）-----------------------------------------------
    if not (cfg.get("TT_PROD_CLIENT_SECRET") and cfg.get("TT_PROD_REFRESH_TOKEN")):
        print("TT_PROD_* が無い。⚠ 相場データは本番だけなので、日足は測れない", file=sys.stderr)
        return 2
    p = ttclient.Client(env="prod")          # ⚠ 発注の鍵は 1 つも渡さない（読み取りだけ）
    p.authenticate(client_secret=cfg["TT_PROD_CLIENT_SECRET"],
                   refresh_token=cfg["TT_PROD_REFRESH_TOKEN"],
                   client_id=cfg.get("TT_PROD_CLIENT_ID"))
    print("=== prod の instruments ===")
    summary["prod"] = probe_instruments(rec, p, "prod")
    if not args.no_candles:
        print("=== prod の日足（本題）===")
        summary["candles"] = probe_candles(rec, p)

    # ⚠ **判定は「廃止銘柄の日足が返るか」だけ**（instruments が知っていても価格が無ければ使えない）
    delisted = [r for r in summary.get("candles", []) if r["kind"] == "廃止" and r["本数"] > 0]
    control = [r for r in summary.get("candles", []) if r["kind"] == "対照" and r["本数"] > 0]
    verdict = {"step": "verdict", "廃止で日足が取れた本数": len(delisted),
               "対照で日足が取れた本数": len(control),
               "判定": ("⚠ 対照も取れていない（API か資格情報の問題）" if not control
                        else "✅ 廃止銘柄の日足が取れる" if delisted
                        else "⚠ 取れない → 有償データ（point-in-time）の試算へ")}
    rec.write(verdict)
    print(json.dumps(verdict, ensure_ascii=False))
    print(f"→ {rec.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
