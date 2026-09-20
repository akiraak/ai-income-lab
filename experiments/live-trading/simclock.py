"""執行器の「いま」と待ちを 1 か所から引く。既定は本物の時計、シミュレーションでは仮の時計（live-trading.md §0-7）。

仮の時計の正本は `sim/<名前>/sim/control.json` の 1 ファイル:
    仮のいま ＝ sim_epoch ＋（実時間の経過 × speed）        （止まっている・最速のときは sim_epoch のまま）
運転手（simrun）・執行器（run_day）・操作の口（simctl）は同じファイルを読むので、別プロセスでも同じ「いま」を見る。
速さ・停止を変える側は、その瞬間の仮のいまを計算して錨（sim_epoch ／ real_epoch）を打ち直す ＝ 変えても時刻が飛ばない。
最速（`max`）は待たずに、待つはずだった秒数だけ錨を進める（実時間に依らない ＝ 回帰テストが決定的）。

⚠ **仮の時計を受け付けるかどうかはここでは決めない**（`run_day.py` が MODE・接続先・鍵を見て拒否する）。
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from datetime import datetime, timezone

SPEEDS = (1, 10, 60, 300, 1440, "max")   # §0-7 (b)。自由な倍率は受けない


class RealClock:
    sim = False

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.perf_counter()

    def describe(self) -> str:
        return "本物の時計"


def parse_speed(text) -> int | str:
    speed = "max" if str(text).lower() in ("max", "最速") else int(str(text).lstrip("x×"))
    if speed not in SPEEDS:
        raise ValueError(f"速さ {text!r} は選べない（{' ／ '.join(f'×{s}' if s != 'max' else 'max' for s in SPEEDS)}）")
    return speed


def sim_now(ctl: dict, real_now: float) -> float:
    if ctl["paused"] or ctl["speed"] == "max":
        return ctl["sim_epoch"]
    return ctl["sim_epoch"] + (real_now - ctl["real_epoch"]) * ctl["speed"]


def read_control(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path: str, ctl: dict) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ctl, f, ensure_ascii=False)
    os.replace(tmp, path)


def update_control(path: str, change, real_time=time.time) -> dict:
    """読む → 錨をいまへ打ち直す → `change(ctl)` → 書く、を flock の中で行う（運転手と simctl が同時に書いても壊れない）。"""
    with open(path + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        ctl = read_control(path)
        real_now = real_time()
        ctl["sim_epoch"], ctl["real_epoch"] = sim_now(ctl, real_now), real_now
        change(ctl)
        ctl["seq"] = ctl.get("seq", 0) + 1
        _write(path, ctl)
        return ctl


def init_control(path: str, start: datetime, speed=60, paused: bool = False, real_time=time.time) -> dict:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ctl = {"sim_epoch": start.timestamp(), "real_epoch": real_time(), "speed": parse_speed(speed), "paused": paused,
           "step": 0, "stop": False, "seq": 0}
    _write(path, ctl)
    return ctl


def set_speed(path: str, speed, **kw) -> dict:
    speed = parse_speed(speed)
    return update_control(path, lambda c: c.update(speed=speed), **kw)


def pause(path: str, **kw) -> dict:
    return update_control(path, lambda c: c.update(paused=True), **kw)


def resume(path: str, **kw) -> dict:
    return update_control(path, lambda c: c.update(paused=False), **kw)


def jump_to(path: str, when: datetime, **kw) -> dict:
    """窓の外を飛ばす（運転手）。⚠ 進めるだけ。仮の時計は戻らない。"""
    return update_control(path, lambda c: c.update(sim_epoch=max(c["sim_epoch"], when.timestamp())), **kw)


class SimClock:
    sim = True

    def __init__(self, control_path: str, real_time=time.time, real_sleep=time.sleep, tick: float = 0.1):
        self.path = control_path
        self.real_time, self.real_sleep, self.tick = real_time, real_sleep, tick
        read_control(control_path)   # 無ければここで落ちる（黙って本物の時計に落ちない）

    def control(self) -> dict:
        return read_control(self.path)

    def monotonic(self) -> float:
        return sim_now(self.control(), self.real_time())

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.monotonic(), timezone.utc)

    def sleep(self, seconds: float) -> None:
        """仮の秒数を待つ。実時間では 秒数 ÷ 速さ。止まっている間は返らない。速さが途中で変わっても残りを数え直す。"""
        target = self.monotonic() + max(0.0, seconds)
        while True:
            ctl = self.control()
            remain = target - sim_now(ctl, self.real_time())
            if remain <= 0:
                return
            if ctl["paused"]:
                self.real_sleep(self.tick)
            elif ctl["speed"] == "max":
                # ⚠ 読んでから書くまでの間に止められていたら進めない（次の周で待つ）
                update_control(self.path, lambda c: c.update(sim_epoch=max(c["sim_epoch"], target))
                               if c["speed"] == "max" and not c["paused"] else None, real_time=self.real_time)
            else:
                self.real_sleep(min(remain / ctl["speed"], self.tick))

    def describe(self) -> str:
        ctl = self.control()
        speed = "最速" if ctl["speed"] == "max" else f"×{ctl['speed']}"
        return f"仮の時計 {speed}{'・停止中' if ctl['paused'] else ''}"
