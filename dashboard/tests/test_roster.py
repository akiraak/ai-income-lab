"""名簿の面（`/roster`。dashboard.md §13-10）: 開始 ／ 停止 ／ 手じまい ／ 再開 ／ 外す。画面は名簿と印を書くだけ ／ 押せない理由 ／ 面ごとの経路。"""
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


@pytest.fixture
def world(settings):
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


def test_start_pause_liquidate_and_remove(world):
    c, s = world
    page = c.get("/roster").text
    assert "候補" in page and "▶ 開始" in page
    # 開始（確認の文が違えば書かない）
    assert "確認の欄" in flash(post(c, "A1", kind="start", confirm="typo")) and not roster_file(s).exists()
    r = post(c, "A1", kind="start", confirm="A1", reason="残す")
    assert r.status_code == 303 and "名簿に入れた" in flash(r)
    d = json.loads(roster_file(s).read_text(encoding="utf-8"))
    assert [e["name"] for e in d["traders"]] == ["A1"] and d["traders"][0]["reason"] == "残す" and d["traders"][0]["actor"]
    assert "稼働なので、開始はできない" in flash(post(c, "A1", kind="start", confirm="A1"))
    # 上限 $1,300: B2（$600）は入る・C3（$900）は超える
    assert "名簿に入れた" in flash(post(c, "B2", kind="start", confirm="B2"))
    assert "上限" in flash(post(c, "C3", kind="start", confirm="C3"))
    # 外す: A1 は持ち株があるので外せない
    assert "持ち株" in flash(post(c, "A1", kind="remove", confirm="A1"))
    # B2 を停止（名簿の面から。戻り先は /roster）→ 外す
    r = c.post("/ops/traders/B2/flag", data={"csrf": c.cookies.get("ail_csrf"), "kind": "paused", "confirm": "B2", "back": "/roster"}, follow_redirects=False)
    assert flash(r).startswith("/roster?") and "停止" in flash(r)
    assert "<b>停止</b>" in c.get("/roster").text
    r = post(c, "B2", kind="remove", confirm="B2")
    assert "外した" in flash(r)
    assert [e["name"] for e in json.loads(roster_file(s).read_text(encoding="utf-8"))["traders"]] == ["A1"]
    assert not (s.halt_file.parent / "control" / "B2.json").exists()          # 外すと印も消える
    # 履歴に残る
    assert "roster" in c.get("/ops").text


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


def test_unknown_trader_and_bad_kind(world):
    c, s = world
    assert post(c, "nobody", kind="start", confirm="nobody").status_code == 404
    assert post(c, "A1", kind="liquidate", confirm="A1").status_code == 400
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


def test_actions_table():
    base = {"name": "X", "test": False, "budget_usd": 100.0, "holdings": {}}
    a = lineup.actions({**base, "status": "candidate"}, buying=0, cap=50, real=True, sim=False, quiet=None)
    assert "上限" in a["start"]
    assert lineup.actions({**base, "status": "candidate"}, buying=0, cap=None, real=True, sim=False, quiet=None) == {"start": None}
    a = lineup.actions({**base, "status": "liquidating"}, buying=0, cap=None, real=True, sim=False, quiet=None)
    assert a["paused"] and a["liquidate"] and a["clear"] and a["remove"]
    a = lineup.actions({**base, "status": "liquidated"}, buying=0, cap=None, real=True, sim=False, quiet=None)
    assert a["clear"] is None and a["remove"] is None
    a = lineup.actions({**base, "status": "active"}, buying=0, cap=None, real=True, sim=False, quiet=None, journal=1)
    assert "控え" in a["remove"]


def test_market_times_are_shown_in_seattle_time_first():
    """2026-10-08 利用者の指示「日時は必ずシアトル時間で」: 市場の決まり（ET）はシアトル時間を先に出す。夏は PDT・冬は PST。"""
    from datetime import date
    assert lv.et_to_seattle("15:40", date(2026, 10, 8)) == "12:40 PDT（15:40 ET）"
    assert lv.et_to_seattle("16:15", date(2026, 12, 1)) == "13:15 PST（16:15 ET）"
