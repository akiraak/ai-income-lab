"""名簿の面（`/roster`。dashboard.md §13-10）: トレーダーの **状態**（停止 ／ 稼働 ／ 一時停止 ／ 手じまい中）と、人が出す **指示**（開始 ／ 一時停止 ／ 手じまい）。

プラン docs/plans/archive/trader-roster-dashboard.md → docs/plans/archive/trader-status-flow.md（2026-10-09 利用者決定「その案でよい」）。
状態は置き場（名簿 `roster.json` ＋ `control/<人>.json`）から決める。指示は **執行器の次の回を通るまで** 状態に入れず「指示」として見せる
（指示の時刻 ＞ 最後の発注の回の `start`。指示の記録は管理画面の操作の履歴）。⚠ 置き場の形・執行器の動きは変えていない。

⚠ **読むだけ・決めるだけ**。書くのは `ops.py`（名簿 ＝ 執行器の `roster.py`・`control/` ＝ 執行器の `control.py`）。売買は執行器の次の回。
⚠ 押せるかどうかの判定はここ 1 か所（画面のボタンの出し分けと POST の拒否が同じ関数を見る）。
⚠ 標準ライブラリだけ。記録は `live.py` の読み手を使う（DB ＝ livefs）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, time as _time
from pathlib import Path

from . import live as lv
from .livestore import livefs

# 執行器の回の時間帯（cron 15:40 ET 起動 → 予測 → 15:50 の合図 → 16:05 の取消）。この間は名簿を書かない（K3）。
# ⚠ 管理画面は `run.lock` を取らない（一瞬でも取ると本物の執行器が拒否される）＝ 時刻で見る
QUIET_FROM, QUIET_TO = _time(15, 40), _time(16, 10)

# 状態（執行器がいまその人をどう動かしているか）。⚠ 「停止」＝ 動かしていない・持ち株 0（名簿の外 ＋ 手じまい済み）
STATUS = {"stopped": "停止", "active": "稼働", "paused": "一時停止", "liquidating": "手じまい中"}
STATUS_ICON = {"stopped": "■", "active": "▶", "paused": "⏸", "liquidating": "🧹"}
# 指示（人がボタンで出す変更）→ 次の回を通った後の状態
ORDER = {"start": "開始", "paused": "一時停止", "liquidate": "手じまい"}
ORDER_ICON = {"start": "▶", "paused": "⏸", "liquidate": "🧹"}
RESULT = {"start": "active", "paused": "paused", "liquidate": "liquidating"}
WEEKDAY = "月火水木金土日"
RUN_AT_ET = "15:50"          # 合図の時刻（表示の「次の回」）


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


def file_status(name: str, members: list[str], flag: dict | None) -> str:
    """置き場だけから決める状態（plan §2-1）。"""
    if name not in members:
        return "stopped"
    kind = (flag or {}).get("kind")
    if kind == "liquidate":
        return "stopped" if (flag or {}).get("done") else "liquidating"
    if kind == "paused":
        return "paused"
    return "active"


def _dt(iso) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else None


def last_run_at(live_dir: Path) -> datetime | None:
    """いちばん新しい **発注の回**（`--mode submit`）の `start` の時刻。指示がこれより後なら「まだ回を通っていない」。"""
    for d in lv.dates(live_dir)[:30]:
        starts = [e for e in lv._read_jsonl(live_dir / "out" / d / "events.jsonl") if e.get("kind") == "start" and e.get("mode") == "submit"]
        for e in reversed(starts):
            dt = _dt(e.get("now_et"))
            if dt:
                return dt
    return None


def next_run(now_et=None) -> str:
    """執行器の次の回（表示用。シアトル時間を先に）。営業日の 15:40 ET より前なら今日・それ以外は次の営業日。"""
    now = now_et or lv.now_et()
    d = now.date()
    try:
        cal = lv._calendar()
        if not (cal.is_trading_day(d) and now.time() < QUIET_FROM):
            d += timedelta(days=1)
            for _ in range(10):
                if cal.is_trading_day(d):
                    break
                d += timedelta(days=1)
    except Exception:          # 暦に無い年 ＝ 日付は出さない（推測で書かない）
        return f"次の営業日 {lv.et_to_seattle(RUN_AT_ET, d)}"
    return f"{d.month}/{d.day}（{WEEKDAY[d.weekday()]}） {lv.et_to_seattle(RUN_AT_ET, d)}"


def pending_of(fstatus: str, row: dict | None, run_at: datetime | None) -> dict | None:
    """まだ回を通っていない指示（plan §2-2）。`row` ＝ 操作の履歴のその人の最新の行。
    ⚠ 置き場がもう指示の結果と違う（CLI で書き換えた等）なら指示とは見ない（置き場が正）。"""
    d = (row or {}).get("detail") or {}
    kind = d.get("instruction")
    at = _dt((row or {}).get("at"))
    if kind not in ORDER or at is None or (run_at is not None and at <= run_at) or RESULT[kind] != fstatus:
        return None
    prev = d.get("prev") if d.get("prev") in STATUS else fstatus
    return {"kind": kind, "label": ORDER[kind], "icon": ORDER_ICON[kind], "at": row.get("at"), "actor": row.get("actor"),
            "reason": d.get("reason") or "", "prev": prev, "detail": d}


def statuses(live_dir: Path, roster_entries: list | None, flags: dict[str, dict], machine: dict, instructions: dict[str, dict]) -> dict[str, dict]:
    """全員（設定のある人）の {status, file_status, pending}。⚠ シミュレーションは仮の時計なので指示を待たせない（置き場がそのまま状態）。"""
    members = [e.name for e in roster_entries] if roster_entries is not None else []
    run_at = None if machine.get("mode") == "sim" else last_run_at(live_dir)
    out = {}
    for t in lv.traders(live_dir):
        fs = file_status(t["name"], members, flags.get(t["name"]))
        pend = None if machine.get("mode") == "sim" else pending_of(fs, instructions.get(t["name"]), run_at)
        out[t["name"]] = {"status": pend["prev"] if pend else fs, "file_status": fs, "pending": pend}
    return out


def badges(live_dir: Path, roster_entries: list | None, flags: dict[str, dict], machine: dict, instructions: dict[str, dict]) -> dict[str, str]:
    """概要の段の札（稼働で指示なしの人は札なし）。⚠ 名簿がまだ無い機械では、名簿の外を「停止」と札にしない（live.env の一覧で動いている）。"""
    out = {}
    for name, s in statuses(live_dir, roster_entries, flags, machine, instructions).items():
        if s["pending"]:
            out[name] = f"{STATUS[s['status']]} · 指示: {s['pending']['label']}"
        elif s["status"] != "active" and (roster_entries is not None or flags.get(name)):
            out[name] = STATUS[s["status"]]
    return out


def board(live_dir: Path, roster_entries: list | None, flags: dict[str, dict], machine: dict, *, demo: bool = False, now_et=None,
          instructions: dict[str, dict] | None = None) -> dict:
    """全員（設定のある人）の状態・指示と、出せる指示（出せないなら理由）。"""
    tr = lv.traders(live_dir)
    states = lv.states(live_dir)
    start = last_start(live_dir)
    cap = cap_of(start)
    members = [e.name for e in roster_entries] if roster_entries is not None else []
    since = {e.name: e for e in (roster_entries or [])}
    real = machine.get("mode") != "sim" and not demo
    quiet = quiet_reason(machine, now_et)
    st = statuses(live_dir, roster_entries, flags, machine, instructions or {})

    rows = []
    for t in tr:
        s = st[t["name"]]
        rows.append({**{k: t[k] for k in ("name", "label", "test", "budget_usd", "symbols")}, **s, "status_label": STATUS[s["status"]],
                     "status_icon": STATUS_ICON[s["status"]], "flag": flags.get(t["name"]), "holdings": holdings(states, t["name"]),
                     "entry": since.get(t["name"]), "order": members.index(t["name"]) if t["name"] in members else None})
    rows.sort(key=lambda r: (r["order"] is None, r["order"] if r["order"] is not None else 0, r["name"]))
    buying = sum(r["budget_usd"] for r in rows if r["file_status"] == "active")      # 次の回に買う人（回を通る前の「開始」も入る）
    for r in rows:
        r["actions"] = actions(r, buying=buying, cap=cap, real=real, sim=machine.get("mode") == "sim", quiet=quiet)
    unknown = [n for n in members if n not in {r["name"] for r in rows}]
    return {"rows": rows, "members": members, "roster_exists": roster_entries is not None, "unknown": unknown,
            "buying_usd": buying, "cap": cap, "last_start": start, "quiet": quiet, "next_run": next_run(now_et),
            "flags_error": (flags.get("?") or {}).get("error"),
            "reconcile": reconcile(live_dir, states)}


def actions(r: dict, *, buying: float, cap: float | None, real: bool, sim: bool, quiet: str | None) -> dict[str, str | None]:
    """出せる指示 → None（押せる）か、押せない理由。⚠ POST の拒否もこれを見る（plan §1-2）。
    回を通る前の指示がある人は「取り消す」だけ。手じまい中の人は売り切るまで待つ（指示なし）。"""
    if r.get("pending"):
        return {"cancel": quiet}
    st = r["status"]
    out: dict[str, str | None] = {}
    if st in ("stopped", "paused"):
        why = quiet
        if r["test"] and real:
            why = why or "試験用の人（test = true）は本番では動かさない"
        if r["name"].startswith("sim_") != sim:
            why = why or ("シミュレーションの人（sim_）は実売買では動かさない" if not sim else "シミュレーションモードで動かすのは sim_ の人だけ")
        if cap is not None and buying + r["budget_usd"] > cap + 1e-9:
            why = why or f"予算の合計 ${buying + r['budget_usd']:,.0f} が上限 ${cap:,.0f}（live.env）を超える"
        out["start"] = why
    if st == "active":
        out["paused"] = quiet
    if st in ("active", "paused"):
        out["liquidate"] = quiet
    return out
