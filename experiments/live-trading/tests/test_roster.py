"""名簿（roster.py）と執行器の `--traders @roster`（プラン docs/plans/archive/trader-roster-dashboard.md。2026-10-07 利用者決定）。

名簿の読み書き ／ 無い・壊れた名簿は起動を拒む ／ 空の名簿は何もしない ／ 名簿の人は明示と同じ動き ／
予算の合計が上限を超えたら、後から名簿に入った人だけを休ませる（明示の回は今までどおり回ごと拒否）。
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import control  # noqa: E402
import roster  # noqa: E402
from tests.test_run_day_flags import mock_server, rows, run, setup, state  # noqa: E402,F401


def test_roster_read_add_remove(tmp_path):
    p = str(tmp_path / "roster.json")
    assert roster.read(p) is None
    with pytest.raises(roster.RosterError, match="名簿が無い"):
        roster.names(p)
    roster.add(p, "T1", "akira", "残す")
    roster.add(p, "T4", "akira")
    assert roster.names(p) == ["T1", "T4"]
    with pytest.raises(roster.RosterError, match="既に"):
        roster.add(p, "T1", "akira")
    with pytest.raises(roster.RosterError, match="不正"):
        roster.add(p, "../x", "akira")
    assert roster.remove(p, "T1") is True and roster.remove(p, "T1") is False
    assert roster.names(p) == ["T4"]
    e = roster.read(p)[0]
    assert e.actor == "akira" and e.since
    roster.remove(p, "T4")
    assert roster.names(p) == []                        # 空の名簿は「無い」と別


@pytest.mark.parametrize("body", ["{broken", '{"traders": "T1"}', '{"traders": [{"name": "T1"}, {"name": "T1"}]}', '{"traders": [{"name": "a/b"}]}'])
def test_broken_roster_is_an_error(tmp_path, body):
    p = tmp_path / "roster.json"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(roster.RosterError, match="読めない"):
        roster.read(str(p))


def test_roster_is_not_read_as_a_flag(tmp_path):
    """名簿は control/ の外（印は control/*.json を全部読む）。"""
    halt = str(tmp_path / "HALT")
    assert os.path.dirname(roster.roster_path(halt)) != control.control_dir(halt)


def test_run_day_reads_the_roster_like_explicit_names(tmp_path, mock_server):
    tdir, _ = setup(tmp_path)
    p = roster.roster_path(str(tmp_path / "HALT"))
    roster.add(p, "lq_a", "akira")
    roster.add(p, "ac_b", "akira")
    from tests.test_run_day_flags import post
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 2, "QQQ": 1}})
    r = run(tmp_path, tdir, ["--traders", "@roster", "--mode", "submit"], mock_server)
    assert r.returncode == 0, r.stdout + r.stderr
    # test_no_flags_means_the_old_path と同じ注文
    assert [(o["parts"][0]["trader"], o["symbol"], o["side"]) for o in rows(tmp_path, "orders")] == [("lq_a", "QQQ", "sell"), ("ac_b", "SPY", "buy")]
    start = [e for e in rows(tmp_path, "events") if e["kind"] == "start"][0]
    assert start["roster"] is True and start["traders"] == ["lq_a", "ac_b"] and start["max_total_budget"] == 10000.0


def test_missing_or_broken_roster_refuses_and_empty_roster_does_nothing(tmp_path, mock_server):
    tdir, _ = setup(tmp_path)
    r = run(tmp_path, tdir, ["--traders", "@roster", "--mode", "submit"], mock_server)
    assert r.returncode == 2 and "名簿が無い" in r.stderr
    p = tmp_path / "roster.json"
    p.write_text("{broken", encoding="utf-8")
    r = run(tmp_path, tdir, ["--traders", "@roster", "--mode", "submit"], mock_server)
    assert r.returncode == 2 and "名簿を読めない" in r.stderr
    p.write_text('{"traders": []}', encoding="utf-8")
    r = run(tmp_path, tdir, ["--traders", "@roster", "--mode", "submit"], mock_server)
    assert r.returncode == 0 and "名簿が空" in r.stdout
    assert not os.path.exists(tmp_path / "out" / "2026-10-06")


def test_over_budget_holds_back_the_last_joined_only(tmp_path, mock_server):
    """2 人で $4,000・上限 $2,000: 名簿の回は後から入った ac_b だけ休ませる ／ 明示の回は今までどおり拒否。"""
    tdir, _ = setup(tmp_path)
    p = roster.roster_path(str(tmp_path / "HALT"))
    roster.add(p, "lq_a", "akira")
    roster.add(p, "ac_b", "akira")
    from tests.test_run_day_flags import post
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 2, "QQQ": 1}})
    r = run(tmp_path, tdir, ["--traders", "@roster", "--mode", "submit", "--max-total-budget", "2000"], mock_server)
    assert r.returncode == 0, r.stdout + r.stderr
    assert [(o["parts"][0]["trader"], o["symbol"], o["side"]) for o in rows(tmp_path, "orders")] == [("lq_a", "QQQ", "sell")]
    ev = [e for e in rows(tmp_path, "events") if e["kind"] == "over_total_budget"]
    assert [e["trader"] for e in ev] == ["ac_b"]
    assert not [s for s in rows(tmp_path, "signals") if s["trader"] == "ac_b"]
    assert "holdings" in state(tmp_path, "lq_a")
    r = run(tmp_path, tdir, ["--traders", "lq_a,ac_b", "--mode", "submit", "--max-total-budget", "2000"], mock_server)
    assert r.returncode == 2 and "予算の合計" in r.stderr


def test_show_prints_seattle_time():
    """表示はシアトル時間（2026-10-08 利用者の指示）。ファイルは UTC のまま。"""
    assert roster.seattle("2026-10-08T15:09:27+00:00") == "2026-10-08 08:09 PDT"
    assert roster.seattle("2026-12-01T20:00:00+00:00") == "2026-12-01 12:00 PST"
    assert roster.seattle(None) == "" and roster.seattle("broken") == "broken"
