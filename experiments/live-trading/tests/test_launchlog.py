"""毎日の起動の記録（launchlog.py ＋ run-live.sh の trap。プラン docs/plans/launch-record-auto.md）。ネットワークを使わない。"""
import json
import os
import subprocess

import launchlog
import livefs

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


def test_a_row_is_written_outside_the_date_directories_and_read_back(tmp_path):
    out = str(tmp_path / "out")
    row = launchlog.make_row("prepare", "2026-10-12T13:00:01Z", 1, steps={"update": 1, "paper": 0},
                             note="urllib.error.HTTPError: HTTP Error 500: Internal Server Error", end="2026-10-12T13:06:39Z", host="h")
    launchlog.add(row, out)
    assert livefs.listdir(out) == ["launches.jsonl"]           # ⚠ out/<日付>/ を作らない（見張り「今日の起動が無い」を崩さない）
    back = launchlog.read(out)
    assert back == [row]
    assert row["date"] == "2026-10-12" and row["secs"] == 398 and row["mode"] == "prepare"


def test_the_date_is_the_new_york_date():
    # 21:30 PDT の 10/12 ＝ 00:30 ET の 10/13
    assert launchlog.make_row("trade", "2026-10-13T04:30:00Z", 0, end="2026-10-13T04:31:00Z", host="h")["date"] == "2026-10-13"


def test_the_note_is_one_line_without_a_jwt_and_only_when_it_failed():
    long = "Error " + "x" * 500
    assert len(launchlog.scrub(long)) == launchlog.NOTE_MAX
    assert launchlog.scrub("token eyJhbGciOi.eyJzdWIi.sig\nnext") == "token <jwt> next"
    ok = launchlog.make_row("trade", "2026-10-13T19:40:00Z", 0, note="何か", end="2026-10-13T19:52:00Z", host="h")
    assert ok["note"] == ""


def test_the_last_row_of_the_day_wins(tmp_path):
    out = str(tmp_path / "out")
    for rc, start in ((1, "2026-10-12T13:00:01Z"), (0, "2026-10-12T15:00:00Z")):
        launchlog.add(launchlog.make_row("prepare", start, rc, end=start, host="h"), out)
    assert launchlog.by_day(launchlog.read(out))["2026-10-12"]["prepare"]["rc"] == 0


def _run_live(tmp_path, out_dir, *args):
    env = dict(os.environ, LT_OUT_DIR=out_dir, LT_MODE_DIR=str(tmp_path / "mode"))
    return subprocess.run(["bash", os.path.join(ROOT, "run-live.sh"), *args], env=env, capture_output=True, text=True, timeout=60)


def test_run_live_records_its_exit_even_when_it_refuses(tmp_path):
    out = str(tmp_path / "out")
    p = _run_live(tmp_path, out)                                # --traders が無い ＝ rc 64
    assert p.returncode == 64
    rows = launchlog.read(out)
    assert len(rows) == 1 and rows[0]["kind"] == "trade" and rows[0]["rc"] == 64 and rows[0]["stage"] == "start"


def test_run_live_keeps_its_rc_when_the_record_cannot_be_written(tmp_path):
    nowhere = "/nonexistent-ail-launchlog/out"                  # どの DB にも当たらない道 ＝ livefs が止める
    p = _run_live(tmp_path, nowhere)
    assert p.returncode == 64
    assert "起動の記録を書けなかった" in p.stderr


def test_show_prints_one_line_per_kind(tmp_path, capsys):
    out = str(tmp_path / "out")
    launchlog.add(launchlog.make_row("prepare", "2026-10-12T13:00:01Z", 1, note="HTTP Error 500", end="2026-10-12T13:06:39Z", host="h"), out)
    launchlog.add(launchlog.make_row("trade", "2026-10-12T19:40:01Z", 0, mode="submit", stage="executor",
                                     end="2026-10-12T19:51:54Z", host="h"), out)
    assert launchlog.main(["show", "--out-dir", out]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["2026-10-12 prepare  rc=1  398 秒 prepare  HTTP Error 500", "2026-10-12 trade    rc=0  713 秒 submit executor"]
    assert json.loads(livefs.read_lines(os.path.join(out, "launches.jsonl"))[0])["kind"] == "prepare"
