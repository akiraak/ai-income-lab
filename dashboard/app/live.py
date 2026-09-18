"""実売買の画面（`/live`）。トレーダー別の予算・モデル・建玉・損益・今日の合図と注文・差 1〜4 の直近 20 日。

⚠ **読むだけ。発注は画面から出さない**（公開面でも見える）。停止は既存の停止ボタン（`HALT` を執行器も見る）。
正本は執行器（`experiments/live-trading/`）が書いたもので、この画面は数え直さない・書かない
（[dashboard.md §13](../../docs/specs/dashboard.md)）:

  - トレーダーの定義        → `config/traders/*.toml`
  - 台帳（持ち分・実現損益） → `state/<env>/<名前>.json`
  - 日次の記録              → `out/<日付>/{signals,quotes,orders,transfers,ledger,events,positions,balances}.jsonl`

⚠ **標準ライブラリだけ**。⚠ **どのファイルが無くても落とさない**（g3plus には執行器の記録を置かないので空でも 200）。
⚠ 記録は執行器が `Masker` を通して書いているが、画面の応答はさらに `Redactor` を通す。
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

DAYS = 20
COMBINE_LABEL = {"asis": "そのまま", "mean": "平均", "majority": "多数決", "unanimous": "全員一致"}
SIZING_LABEL = {"shares": "整数株", "notional": "金額指定"}
KIND_LABEL = {"fixed": "固定", "file": "CSV", "experiment": "実験"}
STATUS_CLASS = {"Filled": "ok", "dry-run": "na", "planned": "na", "Cancelled": "warn", "Rejected": "ng", "error": "ng",
                "guarded": "warn", "halted": "warn", "not_submitted": "ng", "Expired": "warn"}


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    # ⚠ 偶数個のときの平均は二進の端数が出る（17.615000000000002）。差 1 は 0.01bp まで（`_diff1_bp` と同じ桁）
    return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 2)


# ---------------------------------------------------------------- トレーダー

def traders(live_dir: Path) -> list[dict]:
    out = []
    for path in sorted((live_dir / "config" / "traders").glob("*.toml")):
        try:
            with path.open("rb") as f:
                doc = tomllib.load(f)
        except (OSError, ValueError):
            continue
        models = []
        for m in doc.get("models") or []:
            kind = str(m.get("kind", ""))
            models.append({"kind": kind, "kind_label": KIND_LABEL.get(kind, kind), "name": str(m.get("name") or kind),
                           "buy": m.get("buy"), "exit": m.get("exit"), "path": m.get("path")})
        symbols = list(doc.get("symbols") or [])
        out.append({
            "name": str(doc.get("name") or path.stem),
            "file": path.name,
            "test": bool(doc.get("test", False)),
            "budget_usd": float(doc.get("budget_usd", 0) or 0),
            "symbols": symbols,
            "universe": doc.get("universe"),
            "n_symbols": len(symbols) if symbols else None,
            "models": models,
            "combine": doc.get("combine", "asis"),
            "combine_label": COMBINE_LABEL.get(str(doc.get("combine", "asis")), str(doc.get("combine", "asis"))),
            "threshold": doc.get("threshold", 50.0),
            "sizing": doc.get("sizing", "shares"),
            "sizing_label": SIZING_LABEL.get(str(doc.get("sizing", "shares")), str(doc.get("sizing", "shares"))),
            "note": doc.get("note", ""),
        })
    return out


def states(live_dir: Path) -> dict[str, dict[str, dict]]:
    """env → トレーダー名 → 台帳。"""
    out: dict[str, dict[str, dict]] = {}
    root = live_dir / "state"
    if not root.is_dir():
        return out
    for env_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(env_dir.glob("*.json")):
            st = _read_json(path)
            if not st:
                continue
            holdings = st.get("holdings") or {}
            out.setdefault(env_dir.name, {})[path.stem] = {
                "holdings": holdings,
                "cost_in_use_usd": round(sum(float(h.get("shares", 0)) * float(h.get("avg_price", 0)) for h in holdings.values()), 2),
                "realized_usd": round(float(st.get("realized_usd", 0) or 0), 2),
                "fees_usd": round(float(st.get("fees_usd", 0) or 0), 4),
                "last_date": st.get("last_date"),
                "n_trades": len(st.get("history") or []),
                "pending_settlement": st.get("pending_settlement") or [],
            }
    return out


# ---------------------------------------------------------------- 日次

def _diff1_bp(order: dict) -> list[float]:
    """差 1: 合図時の気配（mid）→ 約定。⚠ **正 ＝ 不利**（買いは高く・売りは安く約定）。"""
    q = order.get("quote_at_signal") or {}
    mid = q.get("mid")
    if not mid:
        return []
    out = []
    for f in order.get("fills") or []:
        px = f.get("price")
        if not px:
            continue
        signed = (px - mid) / mid * 1e4
        out.append(round(signed if order.get("side") == "buy" else -signed, 2))
    return out


def day(live_dir: Path, date: str) -> dict:
    d = live_dir / "out" / date
    orders = _read_jsonl(d / "orders.jsonl")
    events = _read_jsonl(d / "events.jsonl")
    signals = _read_jsonl(d / "signals.jsonl")
    transfers = _read_jsonl(d / "transfers.jsonl")
    ledger = _read_jsonl(d / "ledger.jsonl")
    positions = _read_jsonl(d / "positions.jsonl")
    balances = _read_jsonl(d / "balances.jsonl")
    for o in orders:
        o["diff1_bp"] = _diff1_bp(o)
        o["status_class"] = STATUS_CLASS.get(str(o.get("final_status")), "na")
        o["fills_qty"] = round(sum(float(f.get("shares", 0)) for f in o.get("fills") or []), 6)
        o["fill_price"] = (sum(float(f.get("shares", 0)) * float(f.get("price", 0)) for f in o.get("fills") or []) / o["fills_qty"]) if o["fills_qty"] else None
        o["traders"] = [p.get("trader") for p in o.get("parts") or []]
    envs = sorted({r.get("env") for r in orders + events + signals if r.get("env")})
    modes = sorted({r.get("mode") for r in orders if r.get("mode")})
    all_diff1 = [x for o in orders for x in o["diff1_bp"]]
    starts = [e for e in events if e.get("kind") == "start"]
    problems = [e for e in events if e.get("kind") in ("halted", "out_of_window", "auth_failed", "signal_error", "quote_failed", "over_budget", "over_day_cap", "auth_5xx_retry")]
    retries = sum(1 for o in orders for t in o.get("transitions") or [] if "retry" in str(t.get("status", "")))
    # 差 4（無人運転）: 拒否・再送・HALT・窓の外。差 3 は Phase 3（紙上の対照）の後で埋まる
    return {
        "date": date,
        "envs": envs,
        "modes": modes,
        "mock": any(r.get("mock") for r in orders + events),
        "test": any(r.get("test") for r in orders + signals),
        "runs": len(starts),
        "signals": signals,
        "orders": orders,
        "transfers": transfers,
        "ledger": ledger,
        "positions": positions,
        "balances": [{"when": b.get("when"), "env": b.get("env"), **{k: (b.get("balances") or {}).get(k) for k in ("cash-balance", "equity-buying-power", "net-liquidating-value")}} for b in balances],
        "events": events,
        "problems": problems,
        "n_orders": len(orders),
        "n_filled": sum(1 for o in orders if o.get("final_status") == "Filled"),
        "n_bad": sum(1 for o in orders if o["status_class"] == "ng"),
        "retries": retries,
        "diff1_median_bp": _median(all_diff1),
        "diff1_max_bp": max(all_diff1) if all_diff1 else None,
        "no_signal": sum(1 for e in events if e.get("kind") == "no_signal"),
        "skipped": sum(1 for e in events if e.get("kind") in ("too_small", "over_budget", "over_day_cap", "no_quote")),
    }


def dates(live_dir: Path) -> list[str]:
    root = live_dir / "out"
    if not root.is_dir():
        return []
    return sorted((p.name for p in root.iterdir() if p.is_dir() and len(p.name) == 10 and p.name[4] == "-"), reverse=True)


def index(live_dir: Path, days: int = DAYS) -> dict:
    tr = traders(live_dir)
    st = states(live_dir)
    ds = dates(live_dir)
    recent = [day(live_dir, d) for d in ds[:days]]
    today = recent[0] if recent else None
    # トレーダー別の直近の台帳（ledger.jsonl の最新行）
    latest_ledger: dict[str, dict] = {}
    for dd in recent:
        for row in dd["ledger"]:
            key = f"{row.get('env')}/{row.get('trader')}"
            if key not in latest_ledger:
                latest_ledger[key] = row
    for t in tr:
        t["states"] = {env: s.get(t["name"]) for env, s in st.items() if s.get(t["name"])}
        t["ledger"] = {env: latest_ledger.get(f"{env}/{t['name']}") for env in {k.split("/")[0] for k in latest_ledger} if latest_ledger.get(f"{env}/{t['name']}")}
    all_diff1 = [x for dd in recent for o in dd["orders"] for x in o["diff1_bp"]]
    summary = {
        "days": len(recent),
        "orders": sum(dd["n_orders"] for dd in recent),
        "filled": sum(dd["n_filled"] for dd in recent),
        "bad": sum(dd["n_bad"] for dd in recent),
        "retries": sum(dd["retries"] for dd in recent),
        "problem_days": sum(1 for dd in recent if dd["problems"] or dd["n_bad"]),
        "diff1_median_bp": _median(all_diff1),
        "diff1_n": len(all_diff1),
        "real_days": sum(1 for dd in recent if not dd["mock"] and not dd["test"] and "submit" in dd["modes"]),
    }
    return {
        "live_dir": str(live_dir),
        "empty": not tr and not recent,
        "traders": tr,
        "configured": [t for t in tr if not t["test"]],
        "test_traders": [t for t in tr if t["test"]],
        "envs": sorted(st.keys()),
        "today": today,
        "recent": recent,
        "summary": summary,
        "days": days,
    }
