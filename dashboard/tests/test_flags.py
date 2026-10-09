"""トレーダーの詳細の「状態と指示」（dashboard.md §13-9。plan trader-status-flow.md）: 画面は指示を書くだけ ／ 確認の文が違えば書かない ／
回を通るまでは指示として出す ／ 面ごとの経路 ／ 壊れた置き場を読めないと出す。⚠ URL `/ops/traders/<人>/flag` は前のまま。"""
import json
import re
from urllib.parse import unquote

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_roster import flag_file, roster_file, run_passed, world  # noqa: F401  （world は fixture）


def post(c, name, **data):
    return c.post(f"/ops/traders/{name}/flag", data={"csrf": c.cookies.get("ail_csrf"), **data}, follow_redirects=False)


def state_sec(c, name):
    return c.get(f"/traders/{name}").text.split("<h3>状態")[1].split('<div class="sec">')[0]


def test_detail_shows_state_and_instruction(world):
    c, s = world
    sec = state_sec(c, "A1")
    assert "■ 停止" in sec and "指示: なし" in sec and 'action="/ops/traders/A1/flag"' in sec and "▶ 開始" in sec
    r = post(c, "A1", kind="start", confirm="A1", reason="残す")
    assert r.status_code == 303 and unquote(r.headers["location"]).startswith("/traders/A1?flash=") and "「開始」の指示を出した" in unquote(r.headers["location"])
    sec = state_sec(c, "A1")
    assert "■ 停止" in sec and "指示: ▶ 開始" in sec and "次の回 9/1（火） 12:50 PDT（15:50 ET） から効く" in sec and "↩ 指示を取り消す" in sec
    assert re.search(r'<span class="badge badge-mock">停止 · 指示: 開始</span>', c.get("/").text)      # 概要の段にも札
    run_passed(s)
    sec = state_sec(c, "A1")
    assert "▶ 稼働" in sec and "指示: なし" in sec and "⏸ 一時停止" in sec
    assert 'badge-mock">稼働' not in c.get("/").text                                                     # 稼働で指示なしは札なし
    # 一時停止の指示（control/ に書く）→ 回を通ると一時停止
    post(c, "A1", kind="paused", confirm="A1", reason="入金待ち")
    d = json.loads(flag_file(s, "A1").read_text(encoding="utf-8"))
    assert d["paused"] is True and d["liquidate"] is False and d["reason"] == "入金待ち" and d["actor"] and d["since"]
    run_passed(s)
    sec = state_sec(c, "A1")
    assert "⏸ 一時停止" in sec and "入金待ち" in sec and "持ち株はそのまま" in sec
    assert re.search(r'<span class="badge badge-mock">一時停止</span>', c.get("/").text)
    assert "trader_flag" in c.get("/ops").text                                                            # 記録の種類の名前は今までどおり


def test_instruction_requires_confirmation_and_known_trader(world):
    c, s = world
    r = post(c, "A1", kind="start", confirm="typo")
    assert r.status_code == 303 and "確認の欄" in unquote(r.headers["location"]) and not roster_file(s).exists()
    assert post(c, "A1", kind="halt", confirm="A1").status_code == 400
    assert post(c, "A1", kind="clear").status_code == 400                       # 「印を消す」は 2026-10-09 に無くした（開始 ／ 取り消す）
    assert post(c, "nobody", kind="paused", confirm="nobody").status_code == 404
    assert c.post("/ops/traders/A1/flag", data={"kind": "paused", "confirm": "A1"}).status_code == 403     # CSRF


def test_public_face_has_no_flag_route(settings):
    settings.auth_mode = "cloudflare"
    settings.cf_team, settings.cf_aud, settings.cf_email = "team", "aud", "me@example.com"
    app = create_app(settings, start_monitors=False)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        assert c.post("/ops/traders/T1/flag", data={"kind": "paused", "confirm": "T1"}).status_code in (403, 404)


def test_broken_flag_is_shown_not_fatal(world):
    c, s = world
    flag_file(s, "A1").parent.mkdir(parents=True, exist_ok=True)
    flag_file(s, "A1").write_text("{broken", encoding="utf-8")
    page = c.get("/traders/A1")
    assert page.status_code == 200 and "読めない" in page.text
    assert "読めない" in c.get("/roster").text
    assert c.get("/").status_code == 200
