"""毎日の起動の記録（朝の準備・売買の回）。`run-live.sh` が終わるとき（どの rc でも）に 1 行足す。プラン docs/plans/archive/launch-record-auto.md。

    python3 launchlog.py add --kind prepare --start 2026-10-19T13:00:01Z --rc 1 --stage update --steps update=1,paper=0,yardstick=0 --note "…"
    python3 launchlog.py show [--days 20]          # 直近の行（日付・種類・rc・秒・理由）

⚠ 置き場は `<out>/launches.jsonl`（DB の lines。`LT_OUT_DIR` があればそこ）。⚠ **`out/<日付>/` には書かない**
   （朝の準備で今日のディレクトリができると、管理画面の見張り「今日の起動が無い」と `yardstick.py` が読み違える）。
⚠ 書けなくても起動の rc は変えない（`run-live.sh` が警告 1 行にする）。売買の判断には使わない ＝ 読むのは `yardstick.py` と管理画面だけ。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _livefs import livefs  # noqa: E402

NAME = "launches.jsonl"
NOTE_MAX = 200
ET = ZoneInfo("America/New_York")
_JWT = re.compile(r"eyJ[\w-]+(?:\.[\w-]+)*")


def default_out_dir() -> str:
    return os.environ.get("LT_OUT_DIR") or os.path.join(HERE, "out")


def scrub(note: str | None) -> str:
    """理由の 1 行: 改行をつぶし、JWT の形を伏せ、200 字で切る。"""
    s = " ".join((note or "").split())
    s = _JWT.sub("<jwt>", s)
    return s[:NOTE_MAX]


def make_row(kind: str, start: str, rc: int, *, mode: str = "", stage: str = "", steps: dict[str, int] | None = None,
             note: str = "", end: str | None = None, host: str | None = None) -> dict:
    t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
    t1 = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else datetime.now(timezone.utc)
    return {"date": t0.astimezone(ET).date().isoformat(), "kind": kind, "mode": mode or ("prepare" if kind == "prepare" else ""),
            "start": t0.astimezone(timezone.utc).isoformat(timespec="seconds"), "end": t1.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "secs": max(0, round((t1 - t0).total_seconds())), "rc": int(rc), "stage": stage, "steps": dict(steps or {}),
            "note": scrub(note) if int(rc) != 0 else "", "host": host if host is not None else socket.gethostname()}


def add(row: dict, out_dir: str | None = None) -> None:
    livefs.append(os.path.join(out_dir or default_out_dir(), NAME), json.dumps(row, ensure_ascii=False, sort_keys=True))


def read(out_dir: str | None = None) -> list[dict]:
    """全部の行（古い順）。無ければ空。⚠ 読み手（管理画面）は DB が無くても落とさない。"""
    lines = livefs.read_lines(os.path.join(out_dir or default_out_dir(), NAME), missing_ok=True)
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            continue
    return out


def by_day(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """日付 → {kind: その日の最後の行}（手で起こし直した日は最後の回が勝つ）。"""
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        out.setdefault(r.get("date", ""), {})[r.get("kind", "")] = r
    return out


def _steps(text: str) -> dict[str, int]:
    out = {}
    for part in (text or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip()] = int(v)
            except ValueError:
                continue
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--kind", required=True, choices=["prepare", "trade"])
    a.add_argument("--start", required=True, help="起動した時刻（ISO 8601）")
    a.add_argument("--rc", required=True, type=int)
    a.add_argument("--mode", default="")
    a.add_argument("--stage", default="", help="最後に入った段")
    a.add_argument("--steps", default="", help="段ごとの rc（update=1,paper=0）")
    a.add_argument("--note", default="", help="rc ≠ 0 の理由 1 行")
    a.add_argument("--out-dir", default=None)
    s = sub.add_parser("show")
    s.add_argument("--days", type=int, default=20)
    s.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "add":
        add(make_row(args.kind, args.start, args.rc, mode=args.mode, stage=args.stage, steps=_steps(args.steps), note=args.note),
            args.out_dir)
        return 0
    days = by_day(read(args.out_dir))
    for d in sorted(days)[-args.days:]:
        for k in ("prepare", "trade"):
            r = days[d].get(k)
            if r:
                print(f"{d} {k:8} rc={r['rc']} {r['secs']:>4} 秒 {r.get('mode', '')} {r.get('stage', '')} {r.get('note', '')}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
