"""名簿の面（`/roster`。dashboard.md §13-9・§13-10。plan trader-status-flow.md）: 状態 4 つ ＋ 指示 3 つ ＋ 取り消し。
画面は名簿と control/ を書くだけ ／ 指示は執行器の次の回を通るまで状態に入れない ／ 押せない理由 ／ 面ごとの経路。"""
import json
from datetime import datetime
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

import livefs
from app import lineup
from app import live as lv
from app.main import create_app

TRADER = 'name = "{name}"\nbudget_usd = {budget}\nsymbols = ["SPY"]\n[[models]]\nkind = "fixed"\nbuy = 60\nexit = 40\n'


class Clock:
    """指示の時刻（操作の履歴の at）を 1 分ずつ進める。執行器の回はその間に置く（秒が同じだと前後が決まらない）。"""
    def __init__(self):
        self.t = datetime.fromisoformat("2026-10-08T17:00:00+00:00")

    def __call__(self):
        from datetime import timedelta
        self.t += timedelta(minutes=1)
        return self.t.isoformat(timespec="seconds")


@pytest.fixture
def world(settings, monkeypatch):
    from app import ops as ops_mod
    clock = Clock()
    monkeypatch.setattr(ops_mod, "utcnow_iso", clock)
    settings._clock = clock
    live = settings.live_dir
    (live / "config" / "traders").mkdir(parents=True)
    for name, budget in (("A1", 600), ("B2", 600), ("C3", 900)):
        (live / "config" / "traders" / f"{name}.toml").write_text(TRADER.format(name=name, budget=budget), encoding="utf-8")
    # 前の回の記録: 上限 $1,300（live.env の値を執行器が start に書いたもの）
    livefs.append(live / "out" / "2026-08-31" / "events.jsonl",
                  json.dumps({"kind": "start", "env": "prod", "traders": ["A1"], "max_total_budget": 1300.0}))
    livefs.append(live / "out" / "2026-08-31" / "positions.jsonl",
                  json.dumps({"env": "prod", "when": "after", "positions": [{"symbol": "SPY", "quantity": 2, "quantity-direction": "Long"}]}))
    livefs.write_doc(live / "state" / "prod" / "A1.json", json.dumps({"name": "A1", "holdings": {"SPY": {"shares": 2, "avg_price": 500.0}}}))
    app = create_app(settings, start_monitors=False)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        c.get("/")
        yield c, settings


def post(c, name, **data):
    return c.post(f"/ops/roster/{name}", data={"csrf": c.cookies.get("ail_csrf"), **data}, follow_redirects=False)


def flash(r):
    return unquote(r.headers["location"])


def roster_file(settings):
    return settings.halt_file.parent / "roster.json"


def run_passed(s):
    """執行器の発注の回が通った（いままでの指示より後・次の指示より前の `start`）。"""
    from datetime import timedelta
    when = (s._clock.t + timedelta(seconds=30)).isoformat(timespec="seconds")
    livefs.append(s.live_dir / "out" / "2026-10-08" / "events.jsonl", json.dumps({"kind": "start", "mode": "submit", "env": "prod", "traders": [], "now_et": when}))


def flag_post(c, name, **data):
    return c.post(f"/ops/traders/{name}/flag", data={"csrf": c.cookies.get("ail_csrf"), "back": "/roster", **data}, follow_redirects=False)


def flag_file(s, name):
    return s.halt_file.parent / "control" / f"{name}.json"


def row_of(c, name):
    """名簿の面のその人の行（HTML の 1 行）。"""
    page = c.get("/roster").text.split("<h3>トレーダー")[1]
    return page.split(f'href="/traders/{name}"')[1].split("</tr>")[0]


def test_start_is_an_instruction_until_the_run_passes(world):
    c, s = world
    assert "■ 停止" in row_of(c, "A1") and "▶ 開始" in row_of(c, "A1") and "指示: なし" in row_of(c, "A1")
    # 開始（確認の文が違えば書かない）
    assert "確認の欄" in flash(post(c, "A1", kind="start", confirm="typo")) and not roster_file(s).exists()
    r = post(c, "A1", kind="start", confirm="A1", reason="残す")
    assert r.status_code == 303 and "「開始」の指示を出した" in flash(r) and "次の回（9/1（火） 12:50 PDT（15:50 ET））" in flash(r)
    d = json.loads(roster_file(s).read_text(encoding="utf-8"))
    assert [e["name"] for e in d["traders"]] == ["A1"] and d["traders"][0]["reason"] == "残す" and d["traders"][0]["actor"]
    # 回を通る前: 状態は停止のまま・指示が出る・出せるのは取り消しだけ
    row = row_of(c, "A1")
    assert "■ 停止" in row and "指示: ▶ 開始" in row and "理由: 残す" in row and "↩ 指示を取り消す" in row and "⏸ 一時停止" not in row
    assert "指示が次の回を待っている" in flash(post(c, "A1", kind="paused", confirm="A1"))
    # 回を通った後: 稼働・指示なし
    run_passed(s)
    row = row_of(c, "A1")
    assert "▶ 稼働" in row and "指示: なし" in row and "⏸ 一時停止" in row and "🧹 手じまい" in row and "取り消す" not in row
    assert "稼働の人なので、「開始」はできない" in flash(post(c, "A1", kind="start", confirm="A1"))
    # 履歴に残る（記録の種類の名前は今までどおり）
    assert "roster" in c.get("/ops").text


def test_cancel_returns_to_the_form_before(world):
    c, s = world
    post(c, "A1", kind="start", confirm="A1")
    r = post(c, "A1", kind="cancel")                         # 取り消しは確認の文なし
    assert "「開始」の指示を取り消した（停止のまま）" in flash(r)
    assert json.loads(roster_file(s).read_text(encoding="utf-8"))["traders"] == []
    assert "■ 停止" in row_of(c, "A1") and "指示: なし" in row_of(c, "A1")
    # 一時停止の指示 → 取り消すと印も消える
    post(c, "A1", kind="start", confirm="A1")
    run_passed(s)
    assert "「一時停止」の指示を出した" in flash(flag_post(c, "A1", kind="paused", confirm="A1", reason="入金待ち"))
    assert json.loads(flag_file(s, "A1").read_text(encoding="utf-8"))["paused"] is True
    row = row_of(c, "A1")
    assert "▶ 稼働" in row and "指示: ⏸ 一時停止" in row and "入金待ち" in row
    assert "取り消した（稼働のまま）" in flash(flag_post(c, "A1", kind="cancel"))
    assert not flag_file(s, "A1").exists()
    # 指示の無い人の取り消しは断る
    assert "取り消し" in flash(flag_post(c, "A1", kind="cancel"))


def test_pause_then_start_and_liquidate(world):
    c, s = world
    post(c, "A1", kind="start", confirm="A1")
    post(c, "B2", kind="start", confirm="B2")
    run_passed(s)
    flag_post(c, "B2", kind="paused", confirm="B2")
    run_passed(s)
    row = row_of(c, "B2")
    assert "⏸ 一時停止" in row and "持ち株はそのまま" in row and "▶ 開始" in row and "🧹 手じまい" in row
    # 一時停止 → 開始 ＝ 印を消す（名簿の並びは変えない）
    assert "「開始」の指示を出した" in flash(post(c, "B2", kind="start", confirm="B2"))
    assert not flag_file(s, "B2").exists()
    assert [e["name"] for e in json.loads(roster_file(s).read_text(encoding="utf-8"))["traders"]] == ["A1", "B2"]
    assert "⏸ 一時停止" in row_of(c, "B2") and "指示: ▶ 開始" in row_of(c, "B2")
    # 取り消すと一時停止の印が戻る
    flag_post(c, "B2", kind="cancel")
    assert json.loads(flag_file(s, "B2").read_text(encoding="utf-8"))["paused"] is True
    # 手じまい
    assert "「手じまい」の指示を出した" in flash(post(c, "B2", kind="liquidate", confirm="B2"))
    run_passed(s)
    row = row_of(c, "B2")
    assert "🧹 手じまい中" in row and "売り切ると停止になる" in row and "<button" not in row


def test_start_from_liquidated_moves_to_the_end_and_cancel_restores(world):
    c, s = world
    post(c, "A1", kind="start", confirm="A1")
    post(c, "B2", kind="start", confirm="B2")
    flag_file(s, "A1").parent.mkdir(parents=True, exist_ok=True)
    flag_file(s, "A1").write_text(json.dumps({"paused": False, "liquidate": True, "since": "2026-08-30T20:00:00+00:00", "actor": "cli", "reason": "入れ替え",
                                              "done": {"date": "2026-08-31", "fills": 1}}), encoding="utf-8")
    run_passed(s)
    row = row_of(c, "A1")
    assert "■ 停止" in row and "手じまい済み 2026-08-31" in row and "▶ 開始" in row and "🧹" not in row.split("<form")[-1].split("</form>")[0].replace("🧹 手じまい済み", "")
    before = json.loads(roster_file(s).read_text(encoding="utf-8"))["traders"]
    post(c, "A1", kind="start", confirm="A1")
    after = json.loads(roster_file(s).read_text(encoding="utf-8"))["traders"]
    assert [e["name"] for e in after] == ["B2", "A1"]       # 後から入った人として休む順へ（「いつから」も付け直す）
    assert not flag_file(s, "A1").exists()
    flag_post(c, "A1", kind="cancel")
    assert json.loads(roster_file(s).read_text(encoding="utf-8"))["traders"] == before
    assert json.loads(flag_file(s, "A1").read_text(encoding="utf-8"))["done"]["date"] == "2026-08-31"


def test_cap_counts_pending_starts(world):
    c, s = world
    # 上限 $1,300: A1・B2（$600 ずつ）は入る・C3（$900）は超える（回を通る前の開始も数える）
    post(c, "A1", kind="start", confirm="A1")
    assert "指示を出した" in flash(post(c, "B2", kind="start", confirm="B2"))
    assert "上限" in flash(post(c, "C3", kind="start", confirm="C3"))


def test_page_shows_reconcile_and_cap(world):
    c, s = world
    page = c.get("/roster").text
    assert "$1,300" in page and "live.env の一覧で動いた" in page
    assert "SPY" in page.split("帳尻")[1]


def test_quiet_window_refuses(world, monkeypatch):
    c, s = world
    from app import lineup as lu_mod
    et = lv.now_et().tzinfo
    monkeypatch.setattr(lu_mod.lv, "now_et", lambda: datetime(2026, 9, 1, 15, 45, tzinfo=et))
    r = post(c, "B2", kind="start", confirm="B2")
    assert "執行器の回の時間" in flash(r) and not roster_file(s).exists()
    assert "執行器の回の時間" in c.get("/roster").text


def test_unknown_trader_and_bad_kind(world):
    c, s = world
    assert post(c, "nobody", kind="start", confirm="nobody").status_code == 404
    assert post(c, "A1", kind="remove", confirm="A1").status_code == 400          # 「外す」は 2026-10-09 に無くした
    assert c.post("/ops/roster/A1", data={"kind": "start", "confirm": "A1"}).status_code == 403      # CSRF


def test_test_traders_cannot_start_in_real_mode(world):
    c, s = world
    (s.live_dir / "config" / "traders" / "T9.toml").write_text('name = "T9"\ntest = true\nbudget_usd = 10\nsymbols = ["SPY"]\n', encoding="utf-8")
    assert "試験用" in flash(post(c, "T9", kind="start", confirm="T9"))


def test_broken_roster_is_shown_and_not_written(world):
    c, s = world
    roster_file(s).parent.mkdir(parents=True, exist_ok=True)
    roster_file(s).write_text("{broken", encoding="utf-8")
    assert "名簿を読めない" in c.get("/roster").text
    assert "名簿を読めない" in flash(post(c, "B2", kind="start", confirm="B2"))
    assert roster_file(s).read_text(encoding="utf-8") == "{broken"


def test_public_face_has_no_roster(settings):
    settings.auth_mode = "cloudflare"
    settings.cf_team, settings.cf_aud, settings.cf_email = "team", "aud", "me@example.com"
    app = create_app(settings, start_monitors=False)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        assert c.post("/ops/roster/T1", data={"kind": "start", "confirm": "T1"}).status_code in (403, 404)
        assert c.get("/roster").status_code in (401, 403, 404)


def test_status_table():
    """plan §2-1 の表の全行。"""
    fs = lineup.file_status
    assert fs("X", [], None) == "stopped"
    assert fs("X", ["X"], None) == "active"
    assert fs("X", ["X"], {"kind": "paused"}) == "paused"
    assert fs("X", ["X"], {"kind": "liquidate", "done": None}) == "liquidating"
    assert fs("X", ["X"], {"kind": "liquidate", "done": {"date": "2026-10-08"}}) == "stopped"


def test_pending_before_and_after_the_run():
    """plan §2-2: 指示の時刻 ＞ 最後の発注の回の start なら「指示」。前なら状態。置き場が指示の結果と違えば指示と見ない。"""
    from datetime import datetime as dt
    row = {"at": "2026-10-08T17:25:00+00:00", "actor": "me", "detail": {"instruction": "paused", "prev": "active", "reason": "入金待ち"}}
    before = dt.fromisoformat("2026-10-08T15:40:00-04:00")      # 19:40 UTC ＝ 指示より後
    earlier = dt.fromisoformat("2026-10-07T15:40:00-04:00")
    assert lineup.pending_of("paused", row, before) is None
    p = lineup.pending_of("paused", row, earlier)
    assert p["kind"] == "paused" and p["prev"] == "active" and p["reason"] == "入金待ち"
    assert lineup.pending_of("paused", row, None)["kind"] == "paused"            # 回がまだ一度も無い
    assert lineup.pending_of("active", row, earlier) is None                      # CLI で消した ＝ 置き場が正
    assert lineup.pending_of("active", {**row, "detail": {"instruction": "cancel"}}, earlier) is None


def test_actions_table():
    base = {"name": "X", "test": False, "budget_usd": 100.0, "holdings": {}, "pending": None}
    a = lineup.actions({**base, "status": "stopped"}, buying=0, cap=50, real=True, sim=False, quiet=None)
    assert "上限" in a["start"]
    assert lineup.actions({**base, "status": "stopped"}, buying=0, cap=None, real=True, sim=False, quiet=None) == {"start": None}
    assert lineup.actions({**base, "status": "active"}, buying=0, cap=None, real=True, sim=False, quiet=None) == {"paused": None, "liquidate": None}
    assert lineup.actions({**base, "status": "paused"}, buying=0, cap=None, real=True, sim=False, quiet=None) == {"start": None, "liquidate": None}
    assert lineup.actions({**base, "status": "liquidating"}, buying=0, cap=None, real=True, sim=False, quiet=None) == {}
    assert lineup.actions({**base, "status": "active", "pending": {"kind": "paused"}}, buying=0, cap=None, real=True, sim=False, quiet="回の時間") == {"cancel": "回の時間"}


def test_next_run_label():
    from datetime import datetime as dt
    import market_calendar
    et = market_calendar.ET
    assert lineup.next_run(dt(2026, 10, 8, 12, 0, tzinfo=et)) == "10/8（木） 12:50 PDT（15:50 ET）"
    assert lineup.next_run(dt(2026, 10, 8, 16, 30, tzinfo=et)) == "10/9（金） 12:50 PDT（15:50 ET）"
    assert lineup.next_run(dt(2026, 10, 9, 17, 0, tzinfo=et)) == "10/12（月） 12:50 PDT（15:50 ET）"


def test_market_times_are_shown_in_seattle_time_first():
    """2026-10-08 利用者の指示「日時は必ずシアトル時間で」: 市場の決まり（ET）はシアトル時間を先に出す。夏は PDT・冬は PST。"""
    from datetime import date
    assert lv.et_to_seattle("15:40", date(2026, 10, 8)) == "12:40 PDT（15:40 ET）"
    assert lv.et_to_seattle("16:15", date(2026, 12, 1)) == "13:15 PST（16:15 ET）"


def test_refusals_are_hard_to_miss(world):
    """10/8 の本番: 確認の欄が空のまま 3 回押され、断った知らせ（黄色の小さな帯）に気づかなかった。
    ⚠ 確認の欄はブラウザが送る前に止める（required ＋ pattern）・断ったら赤い帯。指示の取り消しは確認なし。"""
    c, s = world
    page = c.get("/roster").text
    assert 'required pattern="A1"' in page and 'required pattern="B2"' in page
    loc = flash(post(c, "B2", kind="start", confirm=""))
    assert "確認の欄" in loc and loc.endswith("&ng=1") and not roster_file(s).exists()
    shown = c.get(post(c, "B2", kind="start", confirm="").headers["location"]).text
    assert "flash flash-ng" in shown and "✋" in shown
    ok = c.get(post(c, "B2", kind="start", confirm="B2").headers["location"]).text
    assert "指示を出した" in ok and "flash-ng" not in ok
    assert 'required pattern="A1"' in c.get("/traders/A1").text
