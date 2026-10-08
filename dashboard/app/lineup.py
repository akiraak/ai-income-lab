"""名簿の面（`/roster`。dashboard.md §13-10。プラン docs/plans/archive/trader-roster-dashboard.md）: トレーダーの一生（候補 → 稼働 ⇄ 停止 → 手じまい → 外す）。

⚠ **読むだけ・決めるだけ**。書くのは `ops.py`（名簿 ＝ 執行器の `roster.py`・印 ＝ 執行器の `control.py`）。売買は執行器の次の回。
⚠ 押せるかどうかの判定はここ 1 か所（画面のボタンの出し分けと POST の拒否が同じ関数を見る）。
⚠ 標準ライブラリだけ。記録は `live.py` の読み手を使う（DB ＝ livefs）。
"""

from __future__ import annotations

import json
from datetime import time as _time
from pathlib import Path

from . import live as lv
from .livestore import livefs

# 執行器の回の時間帯（cron 15:40 ET 起動 → 予測 → 15:50 の合図 → 16:05 の取消）。この間は名簿を書かない（K3）。
# ⚠ 管理画面は `run.lock` を取らない（一瞬でも取ると本物の執行器が拒否される）＝ 時刻で見る
QUIET_FROM, QUIET_TO = _time(15, 40), _time(16, 10)

STATUS = {"candidate": "候補", "active": "稼働", "paused": "停止", "liquidating": "手じまい中", "liquidated": "手じまい済み"}


def quiet_reason(machine: dict, now_et=None) -> str | None:
    """名簿を書かない時間なら理由。⚠ シミュレーション・デモは仮の時計なので見ない。"""
    if machine.get("mode") == "sim":
        return None
    now = now_et or lv.now_et()
    if not lv._calendar().is_trading_day(now.date()):
        return None
    if QUIET_FROM <= now.time() < QUIET_TO:
        span = f"{lv.et_to_seattle(f'{QUIET_FROM:%H:%M}', now.date())}〜{lv.et_to_seattle(f'{QUIET_TO:%H:%M}', now.date())}"
        return f"執行器の回の時間（{span}）は名簿を書かない。終わってから押す"
    return None


def last_start(live_dir: Path) -> dict | None:
    """いちばん新しい回の `start` の事象（上限・名簿で動いたか）。"""
    for d in lv.dates(live_dir)[:30]:
        starts = [e for e in lv._read_jsonl(live_dir / "out" / d / "events.jsonl") if e.get("kind") == "start"]
        if starts:
            return {**starts[-1], "date": d}
    return None


def cap_of(start: dict | None) -> float | None:
    """前の回の予算の合計の上限（`live.env` の値を執行器が記録したもの）。⚠ 記録が無ければ None（＝ 画面では事前に確かめない。執行器が守る）。"""
    v = (start or {}).get("max_total_budget")
    return float(v) if isinstance(v, (int, float)) else None


def holdings(states: dict[str, dict[str, dict]], name: str) -> dict[str, float]:
    """その人の持ち株（全 env を合わせる ＝ 外してよいかは保守側に見る）。"""
    out: dict[str, float] = {}
    for per in states.values():
        for sym, h in ((per.get(name) or {}).get("holdings") or {}).items():
            q = float(h.get("shares", 0) or 0)
            if q:
                out[sym] = out.get(sym, 0.0) + q
    return out


def journal_open(live_dir: Path) -> dict[str, list[dict]]:
    """env → 控えの未完（done の無い intent）。`journal.py` の `unfinished` と同じ数え方。"""
    out: dict[str, list[dict]] = {}
    root = live_dir / "state"
    for env in livefs.listdir(root, missing_ok=True):
        entries: dict[str, dict] = {}
        for line in livefs.read_lines(root / env / "journal.jsonl", missing_ok=True):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            ext = row.get("ext")
            if row.get("op") == "intent":
                entries[ext] = row
            elif row.get("op") == "done":
                entries.pop(ext, None)
        if entries:
            out[env] = list(entries.values())
    return out


def reconcile(live_dir: Path, states: dict[str, dict[str, dict]]) -> dict:
    """口座 − 売買履歴（`reconcile.py show` と同じ見方）。口座は最後の記録の建玉（⚠ いまの口座ではない）。"""
    for d in lv.dates(live_dir)[:30]:
        rows = lv._read_jsonl(live_dir / "out" / d / "positions.jsonl")
        if not rows:
            continue
        row = rows[-1]
        env = row.get("env") or "prod"
        account: dict[str, float] = {}
        for p in row.get("positions") or []:
            q = float(p.get("quantity", 0) or 0) * (-1 if p.get("quantity-direction") == "Short" else 1)
            if q:
                account[str(p.get("symbol"))] = account.get(str(p.get("symbol")), 0.0) + q
        ledger: dict[str, float] = {}
        for st in (states.get(env) or {}).values():
            for sym, h in (st.get("holdings") or {}).items():
                ledger[sym] = ledger.get(sym, 0.0) + float(h.get("shares", 0) or 0)
        syms = sorted(set(account) | set(ledger))
        lines = [{"symbol": s, "account": account.get(s, 0.0), "ledger": ledger.get(s, 0.0), "diff": round(account.get(s, 0.0) - ledger.get(s, 0.0), 6)} for s in syms]
        opened = journal_open(live_dir).get(env, [])
        return {"date": d, "when": row.get("when"), "env": env, "lines": lines, "n_diff": sum(1 for x in lines if x["diff"]), "journal_open": len(opened)}
    return {"date": None, "lines": [], "n_diff": 0, "journal_open": 0}


def board(live_dir: Path, roster_entries: list | None, flags: dict[str, dict], machine: dict, *, demo: bool = False, now_et=None) -> dict:
    """全員（設定のある人）の状態と、押せるボタン（押せないなら理由）。"""
    tr = lv.traders(live_dir)
    states = lv.states(live_dir)
    start = last_start(live_dir)
    cap = cap_of(start)
    members = [e.name for e in roster_entries] if roster_entries is not None else []
    since = {e.name: e for e in (roster_entries or [])}
    real = machine.get("mode") != "sim" and not demo
    quiet = quiet_reason(machine, now_et)

    def status_of(name: str) -> str:
        fl = flags.get(name) or {}
        if name not in members:
            return "candidate"
        if fl.get("kind") == "liquidate":
            return "liquidated" if fl.get("done") else "liquidating"
        if fl.get("kind") == "paused":
            return "paused"
        return "active"

    rows = []
    for t in tr:
        st = status_of(t["name"])
        rows.append({**{k: t[k] for k in ("name", "label", "test", "budget_usd", "symbols")}, "status": st, "status_label": STATUS[st],
                     "flag": flags.get(t["name"]), "holdings": holdings(states, t["name"]), "entry": since.get(t["name"]),
                     "order": members.index(t["name"]) if t["name"] in members else None})
    rows.sort(key=lambda r: (r["order"] is None, r["order"] if r["order"] is not None else 0, r["name"]))
    buying = sum(r["budget_usd"] for r in rows if r["status"] == "active")
    opened = journal_open(live_dir)
    for r in rows:
        r["actions"] = actions(r, buying=buying, cap=cap, real=real, sim=machine.get("mode") == "sim", quiet=quiet,
                               journal=sum(1 for es in opened.values() for e in es if e.get("trader") == r["name"]))
    unknown = [n for n in members if n not in {r["name"] for r in rows}]
    return {"rows": rows, "members": members, "roster_exists": roster_entries is not None, "unknown": unknown,
            "buying_usd": buying, "cap": cap, "last_start": start, "quiet": quiet, "reconcile": reconcile(live_dir, states)}


def actions(r: dict, *, buying: float, cap: float | None, real: bool, sim: bool, quiet: str | None, journal: int = 0) -> dict[str, str | None]:
    """ボタン → None（押せる）か、押せない理由。⚠ POST の拒否もこれを見る。"""
    st = r["status"]
    out: dict[str, str | None] = {}
    if st == "candidate":
        why = quiet
        if r["test"] and real:
            why = why or "試験用の人（test = true）は本番の名簿に入れない"
        if r["name"].startswith("sim_") != sim:
            why = why or ("シミュレーションの人（sim_）は実売買の名簿に入れない" if not sim else "シミュレーションモードの名簿は sim_ の人だけ")
        if cap is not None and buying + r["budget_usd"] > cap + 1e-9:
            why = why or f"予算の合計 ${buying + r['budget_usd']:,.0f} が上限 ${cap:,.0f}（live.env）を超える"
        out["start"] = why
        return out
    out["paused"] = None if st in ("active",) else "稼働中の人だけ"
    out["liquidate"] = None if st in ("active", "paused") else "稼働中か停止中の人だけ"
    out["clear"] = None if st in ("paused", "liquidated") else ("手じまいが済むまで待つ" if st == "liquidating" else "印が無い")
    why = quiet
    if r["holdings"]:
        why = why or f"持ち株が残っている（{len(r['holdings'])} 銘柄）＝ 先に手じまい"
    if journal:
        why = why or f"控えの未完が {journal} 件（執行器の次の回が照会して閉じる）"
    if st == "liquidating":
        why = why or "手じまいが済むまで待つ"
    out["remove"] = why
    return out
