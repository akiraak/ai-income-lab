"""仮の時計: 速さ × 経過 ＝ 進み ／ 止めると進まない ／ 再開で飛ばない ／ sleep が速さで縮む（実時間は差し替えて決定的に）。"""
from datetime import datetime, timezone

import pytest

import simclock

START = datetime(2026, 10, 1, 19, 45, tzinfo=timezone.utc)


class FakeReal:
    """実時間の代役。sleep したぶんだけ進む。"""

    def __init__(self):
        self.t = 1_000_000.0
        self.slept = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.slept += s


def make(tmp_path, speed=60, paused=False):
    real = FakeReal()
    path = str(tmp_path / "sim" / "control.json")
    simclock.init_control(path, START, speed=speed, paused=paused, real_time=real.time)
    return real, path, simclock.SimClock(path, real_time=real.time, real_sleep=real.sleep)


def test_speed_times_elapsed(tmp_path):
    real, _, clock = make(tmp_path, speed=60)
    real.t += 10
    assert (clock.now() - START).total_seconds() == pytest.approx(600)


def test_pause_stops_and_resume_does_not_jump(tmp_path):
    real, path, clock = make(tmp_path, speed=60)
    real.t += 1
    simclock.pause(path, real_time=real.time)
    real.t += 1000                                     # 止まっている間の実時間は数えない
    assert (clock.now() - START).total_seconds() == pytest.approx(60)
    simclock.resume(path, real_time=real.time)
    assert (clock.now() - START).total_seconds() == pytest.approx(60)
    real.t += 1
    assert (clock.now() - START).total_seconds() == pytest.approx(120)


def test_speed_change_does_not_jump(tmp_path):
    real, path, clock = make(tmp_path, speed=60)
    real.t += 2
    simclock.set_speed(path, 1, real_time=real.time)
    assert (clock.now() - START).total_seconds() == pytest.approx(120)
    real.t += 5
    assert (clock.now() - START).total_seconds() == pytest.approx(125)


def test_sleep_shrinks_with_speed(tmp_path):
    real, _, clock = make(tmp_path, speed=60)
    clock.sleep(600)                                   # 取消までの 600 秒 ＝ 実時間 10 秒
    assert real.slept == pytest.approx(10, abs=0.2)
    assert (clock.now() - START).total_seconds() >= 600


def test_sleep_does_not_return_while_paused(tmp_path):
    real, path, clock = make(tmp_path, speed=60, paused=True)
    ticks = []

    def sleep(s):                                      # 50 周ぶん止まったままにしてから再開する
        real.sleep(s)
        ticks.append(s)
        if len(ticks) == 50:
            simclock.resume(path, real_time=real.time)
    clock.real_sleep = sleep
    clock.sleep(60)
    assert len(ticks) > 50 and (clock.now() - START).total_seconds() >= 60
    assert real.slept == pytest.approx(50 * clock.tick + 1, abs=0.2)


def test_max_speed_advances_without_waiting(tmp_path):
    real, _, clock = make(tmp_path, speed="max")
    real.t += 1000                                     # 最速は実時間に依らない
    assert clock.now() == START
    clock.sleep(600)
    assert real.slept == 0 and (clock.now() - START).total_seconds() == 600


def test_jump_only_forward(tmp_path):
    real, path, clock = make(tmp_path, speed=60)
    simclock.jump_to(path, datetime(2026, 10, 2, 19, 45, tzinfo=timezone.utc), real_time=real.time)
    assert clock.now() == datetime(2026, 10, 2, 19, 45, tzinfo=timezone.utc)
    simclock.jump_to(path, START, real_time=real.time)
    assert clock.now() == datetime(2026, 10, 2, 19, 45, tzinfo=timezone.utc)


def test_only_listed_speeds(tmp_path):
    _, path, _ = make(tmp_path)
    for bad in (2, 0, -60, 100000):
        with pytest.raises(ValueError):
            simclock.set_speed(path, bad)
    assert simclock.set_speed(path, "x300")["speed"] == 300


def test_missing_control_does_not_fall_back_to_real_clock(tmp_path):
    with pytest.raises(OSError):
        simclock.SimClock(str(tmp_path / "nope.json"))
