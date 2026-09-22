"""機械全体のモード（実売買 ／ シミュレーション）と起動の排他（live-trading.md §0-7 (a)）。

⚠ **実売買とシミュレーションは排他**（2026-09-19 利用者決定）。モードは機械に 1 つだけで、片方のモードではもう片方の部品が起動を拒む。
  - 正本は `MODE`（git 管理外）。⚠ **無ければ `real`** ＝ いまの動きを 1 行も変えない
  - `run.lock`（flock）を執行器も運転手も起動時に取り、終わるまで持つ ＝ 二重起動と、動作中の切り替えを防ぐ
  - 切り替えは人が CLI で行う（`simctl.py mode …`）。`mode.log` に残す

置き場は既定でこのディレクトリ。`LT_MODE_DIR` はテスト用の差し替え（⚠ モック以外への submit では使えない ＝ `run_day.py` が拒否する）。
"""

from __future__ import annotations

import fcntl
import glob
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SIM_TRADER_PREFIX = "sim_"


class ModeError(Exception):
    """モードが読めない・切り替えられない。⚠ 読めないときはどちらのモードとしても動かさない。"""


@dataclass(frozen=True)
class Mode:
    mode: str = "real"          # real ／ sim
    name: str | None = None     # シミュレーションの名前（sim/<名前>/）
    since: str | None = None
    by: str | None = None

    @property
    def is_sim(self) -> bool:
        return self.mode == "sim"


def base_dir() -> str:
    return os.environ.get("LT_MODE_DIR") or HERE


def overridden() -> bool:
    return bool(os.environ.get("LT_MODE_DIR"))


def mode_file() -> str:
    return os.path.join(base_dir(), "MODE")


def sim_base() -> str:
    return os.path.join(base_dir(), "sim")


def sim_root(name: str) -> str:
    """シミュレーションの記録の木（`config/traders`・`state/cert`・`out/<日付>`・`sim/`・`HALT`）。本物の `out/`・`state/` とは別。"""
    if not NAME_RE.match(name or ""):
        raise ModeError(f"シミュレーションの名前 {name!r} は使えない（英小文字・数字・_・- で 32 字まで）")
    return os.path.join(sim_base(), name)


def control_file(name: str) -> str:
    return os.path.join(sim_root(name), "sim", "control.json")


def inside(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def read_mode() -> Mode:
    path = mode_file()
    if not os.path.exists(path):
        return Mode()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        mode = doc["mode"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ModeError(f"MODE が読めない（{path}: {exc}）。直すか消す（消すと real）") from exc
    if mode not in ("real", "sim"):
        raise ModeError(f"MODE の mode が {mode!r}（real ／ sim のどちらか）")
    if mode == "sim":
        sim_root(doc.get("name"))   # 名前の検査
    return Mode(mode=mode, name=doc.get("name") if mode == "sim" else None, since=doc.get("since"), by=doc.get("by"))


def banner(m: Mode, detail: str = "") -> str:
    """CLI の 1 行目に出すモード（どこを見てもモードが分かるように）。"""
    if m.is_sim:
        return f"=== シミュレーション {m.name}{f'（{detail}）' if detail else ''} — 実売買ではない ==="
    return f"=== 実売買モード{f'（{detail}）' if detail else ''} ==="


class RunLock:
    """`run.lock`。取れなければ起動しない。⚠ ファイルは消さない（flock と unlink は競合する）。中身は持ち主の覚え書き。"""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(base_dir(), "run.lock")
        self._fd: int | None = None

    def acquire(self, mode: str, what: str) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        info = {"mode": mode, "what": what, "pid": os.getpid(), "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(info, ensure_ascii=False).encode("utf-8"))
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            os.ftruncate(self._fd, 0)
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def holder(self) -> dict | None:
        """いまの持ち主の覚え書き（取れなかった側が理由を出すのに使う）。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.loads(f.read() or "null")
        except (OSError, ValueError):
            return None

    def held_by_parent_sim(self) -> bool:
        """運転手（simrun）が鍵を持ったまま起こした執行器か。⚠ 親が sim として持っているときだけ通す。"""
        h = self.holder() or {}
        return h.get("mode") == "sim" and h.get("pid") == os.getppid()


def open_real_positions(state_dir: str | None = None) -> list[str]:
    """本物の状態（`state/prod/*.json`）に残っている建玉。「トレーダー:銘柄」の一覧。"""
    if state_dir is None:
        _ensure_record_db()
    state_dir = state_dir or os.path.join(base_dir(), "state", "prod")
    found = []
    from _livefs import livefs

    for path in livefs.find(state_dir, "*.json"):
        try:
            doc = json.loads(livefs.read_doc(path) or "")
        except (OSError, ValueError):
            found.append(f"{os.path.basename(path)}:（読めない）")
            continue
        for sym, h in (doc.get("holdings") or {}).items():
            if float((h or {}).get("shares") or 0) > 0:
                found.append(f"{doc.get('name') or os.path.basename(path)}:{sym}")
    return found


def _ensure_record_db() -> None:
    """機械の置き場の記録（`mode.log`・`state/prod`）が入る DB。⚠ テスト・作業用の置き場（LT_MODE_DIR）に DB が無ければそこに作る
    （本物の live.sqlite には落とさない。本物の置き場はリポジトリ直下の live.sqlite に入る）。"""
    from _livefs import livefs

    try:
        livefs.locate(os.path.join(base_dir(), "mode.log"))
    except livefs.LiveFsError:
        livefs.init(base_dir(), "sim")


def switch(to: str, name: str | None = None, by: str = "cli", real_trading_will_stop: bool = False) -> Mode:
    """モードを切り替える。⚠ 何かが動いている（`run.lock` が取れない）ときは拒否。

    ⚠ 本物の建玉が残っているときに sim へ入るなら `real_trading_will_stop` が要る
      （シミュレーション中は実売買の執行器が動かない ＝ 手仕舞いも出ない）。
    """
    if to not in ("real", "sim"):
        raise ModeError(f"モード {to!r} は無い（real ／ sim）")
    if to == "sim":
        sim_root(name)
    before = read_mode()
    lock = RunLock()
    if not lock.acquire(before.mode, f"switch→{to}"):
        raise ModeError(f"切り替えを拒否: 何かが動いている（run.lock の持ち主 {lock.holder()}）")
    try:
        _ensure_record_db()
        if to == "sim":
            held = open_real_positions()
            if held and not real_trading_will_stop:
                raise ModeError("切り替えを拒否: 本物の建玉が残っている（" + ", ".join(held) + "）。"
                                "⚠ シミュレーション中は実売買の執行器が動かない ＝ 手仕舞いも出ない。承知のうえなら --real-trading-will-stop")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        after = Mode(mode=to, name=name if to == "sim" else None, since=now, by=by)
        os.makedirs(base_dir(), exist_ok=True)
        tmp = mode_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"mode": after.mode, "name": after.name, "since": after.since, "by": after.by}, f, ensure_ascii=False)
        os.replace(tmp, mode_file())
        from _livefs import livefs

        livefs.append(os.path.join(base_dir(), "mode.log"),
                      json.dumps({"at": now, "from": before.mode, "from_name": before.name, "to": after.mode, "name": after.name, "by": by,
                                  "real_trading_will_stop": bool(real_trading_will_stop)}, ensure_ascii=False))
        return after
    finally:
        lock.release()
