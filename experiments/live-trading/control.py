#!/usr/bin/env python3
"""人ごとの印（停止 ／ 手じまい）。管理画面と CLI が書き、執行器が毎日の回で読む（プラン docs/plans/trader-control-flags.md。2026-10-05 利用者決定）。

    python control.py show                       # 印の一覧
    python control.py set T3 liquidate --reason "入れ替え"   # 手じまい（翌営業日以降の回で持ち株を全部売り、以後は買わない）
    python control.py set T4 paused --reason "入金待ち"      # 停止（休ませる。持ち株はそのまま・売買しない）
    python control.py clear T4                   # 印を消す（普通の売買に戻る）

置き場: <記録ディレクトリ>/control/<人>.json（`HALT` と同じ記録ディレクトリ ＝ `TT_OUT_DIR` か tastytrade-api-sample/out。⚠ ファイルのまま）。
中身: {"paused": bool, "liquidate": bool, "since": iso, "actor": str, "reason": str, "done": {"date", "fills"} | null}
⚠ 壊れた印は「印なし」と読まない（`ControlError` ＝ 執行器は起動を拒む。`NOT_PRODUCTION` の印と同じ考え）。
⚠ 印を立てても、その場では何も売らない。売るのは執行器の毎日の回（発注できる時間帯・許可・`HALT` は今までどおり）。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
DIR_NAME = "control"


class ControlError(Exception):
    pass


@dataclass
class Flag:
    name: str
    paused: bool = False
    liquidate: bool = False
    since: str | None = None
    actor: str | None = None
    reason: str = ""
    done: dict | None = None        # 手じまいが済んだ印 {"date", "fills", "note"}。以後その人は飛ばす

    @property
    def active(self) -> bool:
        """印が無い ＝ 普通に売買する人。"""
        return not (self.paused or self.liquidate)

    def describe(self) -> str:
        if self.liquidate:
            return "手じまい（済み）" if self.done else "手じまい"
        if self.paused:
            return "停止"
        return "—"

    def as_dict(self) -> dict:
        return asdict(self)


def control_dir(halt_file: str) -> str:
    """印の置き場 ＝ `HALT` と同じ記録ディレクトリの `control/`。"""
    return os.path.join(os.path.dirname(halt_file), DIR_NAME)


def default_dir() -> str:
    halt = os.environ.get("TT_HALT_FILE") or os.path.join(os.environ.get("TT_OUT_DIR") or os.path.join(SAMPLE_DIR, "out"), "HALT")
    return control_dir(halt)


def path_of(dir_: str, name: str) -> str:
    if not name or "/" in name or name.startswith("."):
        raise ControlError(f"印の名前が不正: {name!r}")
    return os.path.join(dir_, f"{name}.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_flag(dir_: str, name: str) -> Flag | None:
    """印を読む。無ければ None。⚠ 壊れていれば ControlError（「無い」と読まない）。"""
    p = path_of(dir_, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("dict ではない")
        return Flag(name=name, paused=bool(d.get("paused")), liquidate=bool(d.get("liquidate")), since=d.get("since"),
                    actor=d.get("actor"), reason=str(d.get("reason") or ""), done=d.get("done") if isinstance(d.get("done"), dict) else None)
    except (OSError, ValueError) as exc:
        raise ControlError(f"印を読めない: {p}: {exc}") from exc


def read_all(dir_: str) -> dict[str, Flag]:
    out: dict[str, Flag] = {}
    for p in sorted(glob.glob(os.path.join(dir_, "*.json"))):
        name = os.path.splitext(os.path.basename(p))[0]
        fl = read_flag(dir_, name)
        if fl is not None:
            out[name] = fl
    return out


def _write(dir_: str, fl: Flag) -> str:
    os.makedirs(dir_, exist_ok=True)
    p = path_of(dir_, fl.name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in fl.as_dict().items() if k != "name"}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def set_flag(dir_: str, name: str, kind: str, actor: str, reason: str = "") -> Flag:
    """印を立てる（`paused` ／ `liquidate`）。手じまいは停止を兼ねる（立て直すと `done` は消える ＝ もう一度売る）。"""
    if kind not in ("paused", "liquidate"):
        raise ControlError(f"印の種類は paused か liquidate: {kind!r}")
    fl = Flag(name=name, paused=(kind == "paused"), liquidate=(kind == "liquidate"), since=_now(), actor=actor, reason=reason[:200], done=None)
    _write(dir_, fl)
    return fl


def clear_flag(dir_: str, name: str) -> bool:
    p = path_of(dir_, name)
    if not os.path.exists(p):
        return False
    os.remove(p)
    return True


def mark_done(dir_: str, name: str, date: str, fills: int, note: str = "") -> Flag:
    fl = read_flag(dir_, name)
    if fl is None or not fl.liquidate:
        raise ControlError(f"{name} に手じまいの印が無い")
    fl.done = {"date": date, "fills": fills, "note": note}
    _write(dir_, fl)
    return fl


def main() -> int:
    ap = argparse.ArgumentParser(description="人ごとの印（停止 ／ 手じまい）")
    ap.add_argument("--dir", default=None, help="印の置き場（既定は記録ディレクトリの control/）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    s = sub.add_parser("set")
    s.add_argument("name"), s.add_argument("kind", choices=["paused", "liquidate"])
    s.add_argument("--reason", default=""), s.add_argument("--by", default="cli")
    c = sub.add_parser("clear")
    c.add_argument("name")
    args = ap.parse_args()
    d = args.dir or default_dir()
    try:
        if args.cmd == "show":
            flags = read_all(d)
            print(f"印の置き場: {d}（{len(flags)} 人）")
            for fl in flags.values():
                print(f"  {fl.name:8s} {fl.describe():10s} {fl.since or ''} {fl.actor or ''} {fl.reason}" + (f"  済み {fl.done}" if fl.done else ""))
            return 0
        if args.cmd == "set":
            fl = set_flag(d, args.name, args.kind, args.by, args.reason)
            print(f"立てた: {fl.name} {fl.describe()}（{path_of(d, fl.name)}）。⚠ 売買が変わるのは次の執行器の回から")
            return 0
        print(("消した: " if clear_flag(d, args.name) else "印は無かった: ") + args.name)
        return 0
    except ControlError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
