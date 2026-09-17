"""上場廃止銘柄の日足が配信から取れるかを確かめる（段 4）。⚠ **読み取りだけ。発注系には触れない。**

    ./.venv/bin/python -m cli.delisted --dry-run      # ⚠ 資格情報なしで配線だけ通す
    ./.venv/bin/python -m cli.delisted                # 本番の資格情報で 1 回だけ実測

⚠ **なぜ確かめるのか。** いまの銘柄集合は「今日も上場している銘柄」だけで作ってあり、
⚠ **潰れた銘柄・買収された銘柄が標本から落ちている**（生存バイアス）。
⚠ **配信から廃止銘柄の日足が取れるなら消せる。取れないなら有償データの試算まで**
（[plan](../../../docs/plans/delisted-symbols.md)。金銭が出るので提案で止める）。

⚠ **2 段階で見る**（プラン §2-1）。
  A. 銘柄の台帳に行が残っているか（REST `GET /instruments/equities/{symbol}`）
  B. ⚠ **日足が何本返るか・最古と最新はいつか**（DXLink の `Candle`。⚠ **判定はこちらで決める**）

⚠ **対照を必ず混ぜる。** `AAPL` `SPY` が 0 本なら、それは「廃止銘柄が取れない」ではなく
⚠ **こちらの配線か資格情報の問題**である（判定を書かずに直す）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

import pandas as pd

from ail.data import store
from ail.data.sources import tastytrade as tt

# ⚠ **結果を見る前に決めた 12 本**（プラン §2-2）。⚠ **事由と日付は【推測】**で、判定には使わない
SYMBOLS: tuple[tuple[str, str, str], ...] = (
    ("SIVB", "破綻", "2023-03 に破綻して上場廃止【推測】"),
    ("FRC", "破綻", "2023-05 に破綻して上場廃止【推測】"),
    ("TWTR", "非公開化", "2022-10 に買収され非公開化【推測】"),
    ("ATVI", "買収", "2023-10 に買収完了【推測】"),
    ("VMW", "買収", "2023-11 に買収完了【推測】"),
    ("SPLK", "買収", "2024-03 に買収完了【推測】"),
    ("PXD", "買収", "2024-05 に買収完了【推測】"),
    ("XLNX", "買収", "2022-02 に買収完了【推測】"),
    ("CERN", "買収", "2022-06 に買収完了【推測】"),
    ("ABMD", "買収", "2022-12 に買収完了【推測】"),
    ("FB", "⚠ 改名の対照", "2022-06 に META へティッカー変更【推測】。⚠ 廃止ではない"),
    ("AAPL", "⚠ 生きている対照", "⚠ これが 0 本なら配線か資格情報の問題"),
    ("SPY", "⚠ 生きている対照", "⚠ これが 0 本なら配線か資格情報の問題"),
)
DAYS = 365 * 12          # ⚠ 1 購読 約 8,000 本の上限内（日足なら約 32 年ぶん取れる）


def lookup_equity(client, symbol: str) -> dict:
    """A: 銘柄の台帳に行が残っているか。⚠ **無い（404）ことも結果である。**"""
    try:
        doc = client.get(f"/instruments/equities/{symbol}")
    except Exception as exc:                      # ⚠ ApiError も通信の失敗も、そのまま結果に残す
        status = getattr(exc, "status", None)
        return {"found": False, "status": status, "error": str(exc)[:200]}
    d = (doc or {}).get("data", doc) or {}
    return {"found": True, "active": d.get("active"), "type": d.get("instrument-type"),
            "description": d.get("description"), "listed-market": d.get("listed-market"),
            "is-illiquid": d.get("is-illiquid")}


def daily_bars(dxlink_url: str, token: str, symbols: list[str],
               days: int = DAYS, idle: float = 12.0, max_s: float = 180.0) -> dict[str, pd.DataFrame]:
    """B: 日足。⚠ **取得と同じ経路を使う**（`ail/data/sources/tastytrade.py`）ので、結果がそのまま段 4 の答えになる。"""
    from_ms = int(time.time() * 1000) - days * 86_400_000
    got = asyncio.run(tt._fetch_batch(dxlink_url, token, symbols, "d", from_ms, idle, max_s))
    return {sub.split("{")[0]: tt._to_bars(rows) for sub, rows in got.items()}


def probe(rows=SYMBOLS, *, lookup, candles) -> list[dict]:
    """A と B を 1 本の表にする。⚠ **判定はしない**（数字を出すだけ。読み方は記録に書く）。"""
    bars = candles([s for s, _, _ in rows])
    out = []
    for sym, frame, note in rows:
        b = bars.get(sym, pd.DataFrame(columns=["time_ms"]))
        first = last = None
        if len(b):
            first = str(pd.to_datetime(b["time_ms"].min(), unit="ms", utc=True).date())
            last = str(pd.to_datetime(b["time_ms"].max(), unit="ms", utc=True).date())
        out.append({"symbol": sym, "枠": frame, "事由": note, "instrument": lookup(sym),
                    "bars": int(len(b)), "first": first, "last": last})
    return out


def _mock_client():
    """⚠ **資格情報なしでも配線を通す。** 応答の形だけを真似る（数字は嘘なので記録に残さない）。"""
    class C:
        def get(self, path):
            sym = path.rsplit("/", 1)[-1]
            if sym in ("SIVB", "FRC", "TWTR"):
                raise RuntimeError("404 record_not_found: mock")
            return {"data": {"active": True, "instrument-type": "Equity",
                             "description": f"mock {sym}", "listed-market": "XNAS"}}
    return C()


def _mock_candles(symbols: list[str]) -> dict[str, pd.DataFrame]:
    day = 86_400_000
    now = int(time.time() * 1000) // day * day
    out = {}
    for s in symbols:
        n = 0 if s in ("SIVB", "FRC", "TWTR") else 5
        out[s] = pd.DataFrame({"time_ms": [now - i * day for i in range(n)],
                               "open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n,
                               "close": [1.0] * n, "volume": [1.0] * n})
    return out


def render(rows: list[dict]) -> str:
    head = f"{'銘柄':<6}{'枠':<14}{'台帳':<22}{'日足':>7}  最古 〜 最新"
    lines = [head, "-" * len(head)]
    for r in rows:
        i = r["instrument"]
        book = ("⚠ 無" if not i.get("found")
                else f"あり active={i.get('active')}")
        span = f"{r['first']} 〜 {r['last']}" if r["bars"] else "⚠ 0 本"
        lines.append(f"{r['symbol']:<6}{r['枠']:<14}{book:<22}{r['bars']:>7}  {span}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="⚠ 資格情報なしでモックの応答に通す")
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--out", default=os.path.join(store.ROOT, "out", "delisted.json"))
    args = ap.parse_args()

    if args.dry_run:
        client = _mock_client()
        rows = probe(lookup=lambda s: lookup_equity(client, s), candles=_mock_candles)
    else:
        ttclient = tt._ttclient()
        cfg = ttclient.load_env(os.path.join(tt.SAMPLE, ".env"))
        if not (cfg.get("TT_PROD_CLIENT_SECRET") and cfg.get("TT_PROD_REFRESH_TOKEN")):
            raise SystemExit("TT_PROD_CLIENT_SECRET / TT_PROD_REFRESH_TOKEN が無い（相場データは本番だけ）")
        # ⚠ **読み取りだけ。** ⚠ **発注系は既定で拒否されるまま**（allow_prod_orders は触らない）
        client = ttclient.Client(env="prod")
        client.authenticate(client_secret=cfg["TT_PROD_CLIENT_SECRET"],
                            refresh_token=cfg["TT_PROD_REFRESH_TOKEN"],
                            client_id=cfg.get("TT_PROD_CLIENT_ID"))
        info = client.get_quote_token()
        rows = probe(lookup=lambda s: lookup_equity(client, s),
                     candles=lambda syms: daily_bars(info["dxlink-url"], info["token"], syms, args.days))

    print(render(rows))
    alive = [r for r in rows if r["枠"].endswith("生きている対照")]
    if not all(r["bars"] for r in alive):
        print("\n⚠ **生きている対照が 0 本。** ⚠ **判定を書いてはいけない**（配線か資格情報の問題）。")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "dry_run": bool(args.dry_run), "days": args.days, "rows": rows},
                  f, ensure_ascii=False, indent=2)
    print(f"→ {os.path.relpath(args.out, store.ROOT)}")


if __name__ == "__main__":
    main()
