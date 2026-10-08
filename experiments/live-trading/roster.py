#!/usr/bin/env python3
"""名簿（誰を動かすか）。管理画面と CLI が書き、執行器が `--traders @roster` で毎日の回に読む（プラン docs/plans/trader-roster-dashboard.md。2026-10-07 利用者決定）。

    python roster.py show                           # 名簿
    python roster.py add T4 --reason "入れ替え"     # 開始（名簿に入れる。売買が始まるのは執行器の次の回から）
    python roster.py remove T3                      # 外す（名簿から抜く。⚠ 持ち株が 0 かは画面が確かめる ＝ CLI は確かめない）

置き場: <記録ディレクトリ>/roster.json（`HALT` の隣。⚠ 印の置き場 `control/` の中には置かない ＝ 印は `control/*.json` を全部読む）。
中身: {"traders": [{"name", "since", "actor", "reason"}], "updated_at": iso}。並びは名簿に入った順（予算の合計が上限を超えたら、後から入った人から休ませる）。
⚠ 無い ／ 壊れた名簿は「誰も居ない」と読まない（`RosterError` ＝ 執行器は起動を拒む）。⚠ 名簿に入れても、その場では何も買わない。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
FILE_NAME = "roster.json"
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
TOKEN = "@roster"           # run_day.py --traders @roster


class RosterError(Exception):
    pass


@dataclass
class Entry:
    name: str
    since: str | None = None
    actor: str | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def roster_path(halt_file: str) -> str:
    """名簿の置き場 ＝ `HALT` と同じ記録ディレクトリの `roster.json`。"""
    return os.path.join(os.path.dirname(halt_file), FILE_NAME)


def default_path() -> str:
    halt = os.environ.get("TT_HALT_FILE") or os.path.join(os.environ.get("TT_OUT_DIR") or os.path.join(SAMPLE_DIR, "out"), "HALT")
    return roster_path(halt)


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise RosterError(f"識別名が不正: {name!r}")
    return name


SEATTLE = ZoneInfo("America/Los_Angeles")


def seattle(iso: str | None) -> str:
    """表示用: 記録の UTC の時刻 → シアトル時間（CLAUDE.md「利用者に見せる日時はシアトル時間」）。⚠ ファイルの中身は UTC のまま。"""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone(SEATTLE).strftime("%Y-%m-%d %H:%M %Z")
    except ValueError:
        return iso


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(path: str) -> list[Entry] | None:
    """名簿を読む。無ければ None。⚠ 壊れていれば RosterError（「無い」とも「空」とも読まない）。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("traders"), list):
            raise ValueError("{\"traders\": [...]} の形ではない")
        out: list[Entry] = []
        for row in d["traders"]:
            if not isinstance(row, dict):
                raise ValueError(f"行が dict ではない: {row!r}")
            out.append(Entry(name=_check_name(row.get("name")), since=row.get("since"), actor=row.get("actor"), reason=str(row.get("reason") or "")))
    except (OSError, ValueError, RosterError) as exc:
        raise RosterError(f"名簿を読めない: {path}: {exc}") from exc
    names = [e.name for e in out]
    if len(set(names)) != len(names):
        raise RosterError(f"名簿を読めない: {path}: 同じ人が 2 度居る {names}")
    return out


def names(path: str) -> list[str]:
    """執行器の入口: 名簿の識別名（入った順）。⚠ 無い ／ 壊れた名簿は RosterError。"""
    entries = read(path)
    if entries is None:
        raise RosterError(f"名簿が無い: {path}（管理画面の「開始」か roster.py add で作る）")
    return [e.name for e in entries]


def _write(path: str, entries: list[Entry]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"traders": [e.as_dict() for e in entries], "updated_at": _now()}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def add(path: str, name: str, actor: str, reason: str = "") -> Entry:
    """名簿に入れる（末尾）。名簿が無ければ作る。既に居れば RosterError。"""
    _check_name(name)
    entries = read(path) or []
    if any(e.name == name for e in entries):
        raise RosterError(f"{name} は既に名簿に居る")
    e = Entry(name=name, since=_now(), actor=actor, reason=reason[:200])
    _write(path, entries + [e])
    return e


def remove(path: str, name: str) -> bool:
    """名簿から抜く。居なければ False。"""
    entries = read(path)
    if entries is None or not any(e.name == name for e in entries):
        return False
    _write(path, [e for e in entries if e.name != name])
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="名簿（誰を動かすか）")
    ap.add_argument("--path", default=None, help="名簿の置き場（既定は記録ディレクトリの roster.json）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    a = sub.add_parser("add")
    a.add_argument("name"), a.add_argument("--reason", default=""), a.add_argument("--by", default="cli")
    r = sub.add_parser("remove")
    r.add_argument("name")
    args = ap.parse_args()
    p = args.path or default_path()
    try:
        if args.cmd == "show":
            entries = read(p)
            if entries is None:
                print(f"名簿が無い: {p}")
                return 0
            print(f"名簿: {p}（{len(entries)} 人）")
            for e in entries:
                print(f"  {e.name:8s} {seattle(e.since)} {e.actor or ''} {e.reason}")
            return 0
        if args.cmd == "add":
            e = add(p, args.name, args.by, args.reason)
            print(f"入れた: {e.name}（{p}）。⚠ 売買が始まるのは執行器の次の回から")
            return 0
        print(("外した: " if remove(p, args.name) else "名簿に居なかった: ") + args.name)
        return 0
    except RosterError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
