"""一時停止 ／ 手じまいの置き場（control.py。前は「人ごとの印」）: 読み書き・済み・壊れたファイルは「無い」と読まない・CLI・取り消しで戻す。"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import control  # noqa: E402


def test_set_read_done_clear(tmp_path):
    d = str(tmp_path / "control")
    assert control.read_flag(d, "T1") is None and control.read_all(d) == {}
    fl = control.set_flag(d, "T3", "liquidate", "akira", "入れ替え")
    assert fl.liquidate and not fl.paused and fl.actor == "akira" and fl.reason == "入れ替え" and fl.since and fl.done is None
    assert not fl.active and fl.describe() == "手じまい中"
    got = control.read_flag(d, "T3")
    assert got == fl
    control.mark_done(d, "T3", "2026-10-06", 5, note="執行器が全部売った")
    got = control.read_flag(d, "T3")
    assert got.done == {"date": "2026-10-06", "fills": 5, "note": "執行器が全部売った"} and got.describe() == "停止（手じまい済み）"
    p = control.set_flag(d, "T4", "paused", "akira", "入金待ち")
    assert p.paused and not p.liquidate and p.describe() == "一時停止"
    assert sorted(control.read_all(d)) == ["T3", "T4"]
    # 立て直すと done は消える（もう一度売る）
    again = control.set_flag(d, "T3", "liquidate", "akira")
    assert again.done is None
    assert control.clear_flag(d, "T3") and control.read_flag(d, "T3") is None and not control.clear_flag(d, "T3")
    with pytest.raises(control.ControlError):
        control.mark_done(d, "T4", "2026-10-06", 0)          # 停止の人に「済み」は書けない
    with pytest.raises(control.ControlError):
        control.set_flag(d, "T4", "halt", "akira")


def test_broken_flag_is_an_error_not_absence(tmp_path):
    d = str(tmp_path / "control")
    os.makedirs(d)
    open(os.path.join(d, "T1.json"), "w").write("{broken")
    with pytest.raises(control.ControlError):
        control.read_flag(d, "T1")
    with pytest.raises(control.ControlError):
        control.read_all(d)
    with pytest.raises(control.ControlError):
        control.path_of(d, "../x")


def test_dir_is_next_to_halt(tmp_path):
    assert control.control_dir(str(tmp_path / "out" / "HALT")) == str(tmp_path / "out" / "control")


def test_cli(tmp_path):
    d = str(tmp_path / "control")
    def run(*args):
        return subprocess.run([sys.executable, os.path.join(HERE, "control.py"), "--dir", d, *args], capture_output=True, text=True)
    r = run("set", "T3", "liquidate", "--reason", "入れ替え", "--by", "akira")
    assert r.returncode == 0 and "書いた: T3 → 手じまい中" in r.stdout
    r = run("show")
    assert r.returncode == 0 and "T3" in r.stdout and "手じまい" in r.stdout and "akira" in r.stdout
    assert json.load(open(os.path.join(d, "T3.json"))) ["liquidate"] is True
    r = run("clear", "T3")
    assert r.returncode == 0 and "消した: T3" in r.stdout
    r = run("set", "T3", "halt")
    assert r.returncode == 2


def test_restore_flag_puts_back_the_form_before(tmp_path):
    """管理画面の「指示を取り消す」: 前の中身に戻す ／ 無かったなら消す。"""
    d = str(tmp_path / "control")
    control.set_flag(d, "T4", "liquidate", "me", "入れ替え")
    before = control.mark_done(d, "T4", "2026-10-06", 5).as_dict()
    control.clear_flag(d, "T4")
    control.restore_flag(d, "T4", before)
    got = control.read_flag(d, "T4")
    assert got.liquidate and got.done["fills"] == 5 and got.reason == "入れ替え" and got.since == before["since"]
    control.restore_flag(d, "T4", None)
    assert control.read_flag(d, "T4") is None
