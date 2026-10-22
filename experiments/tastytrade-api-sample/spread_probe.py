"""小型 50 ／ 中位 48 ／ いまの 5 本の気配の差（スプレッド）を REST のスナップショットで測る読み取り専用プローブ
（[プラン](../../docs/plans/archive/smallcap-spread-probe.md)。2026-10-22 利用者「1 を進めて」）。

⚠ **発注系には一切触れない**（GET だけ。`Client` に許可の引数を 1 つも渡さない）。CLAUDE.md の 2026-09-05 の例外
（**API の挙動**の実測）の内側。相場データは本番（prod）の資格情報でしか出ない（sandbox は全経路 502）。
⚠ **引く銘柄は結果を見る前に固定した**（universe の TOML と `T1` の設定をそのまま読む。取れなかった銘柄も行に残す）。

    .venv/bin/python spread_probe.py --label A                 # 3 集合 103 本を 1 回ずつ引く（1 本 0.3 秒あける ＝ 約 40 秒）
    .venv/bin/python spread_probe.py --label B --csv out.csv   # 行を CSV にも書く（作業用。記録は DB）

測るもの: 1 本ずつ `/market-data/by-type` の bid ／ ask ／ bid-size ／ ask-size ／ last ／ updated-at。
気配の差 ＝ (ask − bid) ／ mid × 10^4 bp（mid ＝ (bid ＋ ask) ／ 2）。⚠ 往復の費用と同じ物差し（買いは ask・売りは bid）。
線（プラン §3。⚠ 回す前に固定）: 5.2bp ＝ 小型 50 θ50 の損益分岐 ／ 20bp ＝ 段 2b の費用の変種 ／ $120 ＝ 1 銘柄の枠。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import statistics
import sys
import time
import tomllib

import record
import ttclient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.environ.get("TT_OUT_DIR") or os.path.join(HERE, "out")

UNIVERSE = os.path.join(ROOT, "experiments", "feature-discovery", "config", "universe")
TRADER_T1 = os.path.join(ROOT, "experiments", "live-trading", "config", "traders", "T1.toml")

BREAK_EVEN_BP = 5.2    # 小型 50 θ50 の損益分岐（smallcap-stage2b.md §2-3）
COST_HIGH_BP = 20.0    # 段 2b の費用の変種（rules.md 28-3）
FRAME_USD = 120.0      # 1 銘柄の枠 $600 ÷ 5 本（earnings-smallcap.md §3）
PAUSE_S = 0.3          # 1 本ごとにあける
TRIES = 3              # 429 のときだけ待って引き直す回数


def load_sets() -> list[tuple[str, list[str]]]:
    """⚠ 銘柄は設定をそのまま読む（ここに書き写さない ＝ 差し替えが起きない）。"""
    with open(TRADER_T1, "rb") as f:
        five = list(tomllib.load(f)["symbols"])
    sets = [("5本", five)]
    for label, name in (("中位48", "midcap48"), ("小型50", "small50")):
        with open(os.path.join(UNIVERSE, f"{name}.toml"), "rb") as f:
            sets.append((label, list(tomllib.load(f)["groups"]["company"])))
    return sets


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _age_s(updated_at: str | None, now: dt.datetime) -> float | None:
    if not updated_at:
        return None
    try:
        t = dt.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return round((now - t).total_seconds(), 1)


def read_quote(client: ttclient.Client, symbol: str) -> tuple[dict | None, dict | None]:
    """1 本引く。429 だけ待って引き直す（それ以外の失敗は 1 回で記録に残す）。"""
    for attempt in range(1, TRIES + 1):
        t0 = time.perf_counter()
        try:
            q = client.get_quote(symbol)
            return q, {"latency_ms": round((time.perf_counter() - t0) * 1000, 1), "tries": attempt}
        except ttclient.ApiError as e:
            if e.status == 429 and attempt < TRIES:
                wait = e.retry_after if e.retry_after is not None else 5.0 * attempt
                print(f"  {symbol}: 429 → {wait:.0f} 秒待つ", file=sys.stderr)
                time.sleep(wait)
                continue
            return None, {"error": e.code, "status": e.status, "message": str(e)[:200], "tries": attempt}
    return None, {"error": "exhausted", "tries": TRIES}


def quote_row(label: str, symbol: str, q: dict | None, meta: dict, now: dt.datetime) -> dict:
    row = {"step": "quote", "set": label, "symbol": symbol, **meta}
    if q is None:
        return row
    bid, ask = _f(q.get("bid")), _f(q.get("ask"))
    row.update({"bid": bid, "ask": ask, "bid_size": _f(q.get("bid-size")), "ask_size": _f(q.get("ask-size")),
                "last": _f(q.get("last")), "updated_at": q.get("updated-at"), "age_s": _age_s(q.get("updated-at"), now)})
    if bid and ask and bid > 0 and ask > 0:
        mid = (bid + ask) / 2
        row["mid"] = round(mid, 4)
        row["spread_bp"] = round((ask - bid) / mid * 1e4, 2)
        if ask < bid:
            row["crossed"] = True
    return row


def _q(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 2)


def summarize(label: str, rows: list[dict]) -> dict:
    sp = [r["spread_bp"] for r in rows if r.get("spread_bp") is not None]
    mids = [r["mid"] for r in rows if r.get("mid") is not None]
    bs = [r["bid_size"] for r in rows if r.get("bid_size") is not None]
    as_ = [r["ask_size"] for r in rows if r.get("ask_size") is not None]
    ages = [r["age_s"] for r in rows if r.get("age_s") is not None]
    return {
        "step": "summary", "set": label, "本数": len(rows), "気配あり": len(sp),
        "中央値bp": _q(sp, 0.5), "p25bp": _q(sp, 0.25), "p75bp": _q(sp, 0.75), "最大bp": max(sp) if sp else None,
        f"≤{BREAK_EVEN_BP}bp": sum(1 for x in sp if x <= BREAK_EVEN_BP), f"≤{COST_HIGH_BP:g}bp": sum(1 for x in sp if x <= COST_HIGH_BP),
        f"mid>${FRAME_USD:g}": sum(1 for m in mids if m > FRAME_USD), "mid中央値$": _q(mids, 0.5),
        "bid_size中央値": _q(bs, 0.5), "ask_size中央値": _q(as_, 0.5), "age_s中央値": _q(ages, 0.5),
        "取れなかった": [r["symbol"] for r in rows if r.get("spread_bp") is None],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="", help="写しの名前（A ／ B）。記録の行に残す")
    ap.add_argument("--csv", default="", help="行を CSV にも書く（作業用）")
    args = ap.parse_args()

    cfg = ttclient.load_env(os.environ.get("TT_ENV_FILE") or os.path.join(HERE, ".env"))
    if not (cfg.get("TT_PROD_CLIENT_SECRET") and cfg.get("TT_PROD_REFRESH_TOKEN")):
        print("TT_PROD_CLIENT_SECRET / TT_PROD_REFRESH_TOKEN が無い（相場データは本番だけ）", file=sys.stderr)
        return 2
    sets = load_sets()
    rec = record.Recorder(OUT_DIR, venue="tastytrade", env="prod", mock=False)
    rec.mask.add(cfg["TT_PROD_CLIENT_SECRET"], "<prod_client_secret:masked>")
    rec.mask.add(cfg["TT_PROD_REFRESH_TOKEN"], "<prod_refresh_token:masked>")
    client = ttclient.Client(env="prod")   # ⚠ 許可の引数は 1 つも渡さない（読むだけ）
    client.authenticate(client_secret=cfg["TT_PROD_CLIENT_SECRET"], refresh_token=cfg["TT_PROD_REFRESH_TOKEN"],
                        client_id=cfg.get("TT_PROD_CLIENT_ID") or None)
    rec.mask.add(client.token.access_token, "<prod_access_token:masked>")
    try:
        session = client.get_market_session()
    except ttclient.ApiError as e:
        session = {"error": e.code}
    started = dt.datetime.now(dt.timezone.utc)
    rec.write({"step": "plan", "probe": "spread", "label": args.label, "started_at": started.isoformat(),
               "sets": {k: v for k, v in sets}, "lines": {"break_even_bp": BREAK_EVEN_BP, "cost_high_bp": COST_HIGH_BP, "frame_usd": FRAME_USD},
               "market_session": {k: session.get(k) for k in ("state", "open-at", "close-at", "instrument-collection") if isinstance(session, dict)},
               "注記": "⚠ 銘柄と線は結果を見る前に固定した（plans/smallcap-spread-probe.md §3）。GET だけ・発注しない"})
    print(f"記録: {rec.path}  写し {args.label or '-'}  市場 {session.get('state') if isinstance(session, dict) else session}")

    all_rows: list[dict] = []
    summaries: list[dict] = []
    for label, symbols in sets:
        rows = []
        for sym in symbols:
            q, meta = read_quote(client, sym)
            row = quote_row(label, sym, q, meta, dt.datetime.now(dt.timezone.utc))
            rec.write(row)
            rows.append(row)
            time.sleep(PAUSE_S)
        s = summarize(label, rows)
        rec.write(s)
        summaries.append(s)
        all_rows.extend(rows)
        print(f"{label}: {s['気配あり']}/{s['本数']} 本  中央値 {s['中央値bp']}bp  p25 {s['p25bp']}  p75 {s['p75bp']}  最大 {s['最大bp']}  "
              f"≤{BREAK_EVEN_BP}bp {s[f'≤{BREAK_EVEN_BP}bp']} 本  ≤{COST_HIGH_BP:g}bp {s[f'≤{COST_HIGH_BP:g}bp']} 本  mid>${FRAME_USD:g} {s[f'mid>${FRAME_USD:g}']} 本  "
              f"age中央値 {s['age_s中央値']}s  取れなかった {s['取れなかった']}")
    rec.write({"step": "done", "label": args.label, "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "elapsed_s": round((dt.datetime.now(dt.timezone.utc) - started).total_seconds(), 1), "rows": len(all_rows)})
    if args.csv:
        keys = ["set", "symbol", "bid", "ask", "mid", "spread_bp", "bid_size", "ask_size", "last", "updated_at", "age_s", "latency_ms", "tries", "error", "status", "crossed"]
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(all_rows)
        print(f"CSV → {args.csv}")
    print(f"→ {rec.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
