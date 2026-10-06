"""人ごとの印（停止 ／ 手じまい。dashboard.md §13-9）: 画面は印を書くだけ ／ 確認の文が違えば書かない ／ 面ごとの経路 ／ 秘密が出ない。"""
import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

DASH = Path(__file__).resolve().parents[1]
NAME = "mock_a"            # デモの執行器の記録の人（dashboard/demo/live）


@pytest.fixture
def client(settings):
    """トレーダーの居る木（デモの記録）で起こす。監視ループは起こさない。"""
    settings.live_dir = DASH / "demo" / "live"
    app = create_app(settings, start_monitors=False)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        c.get("/")  # CSRF cookie を受け取る
        yield c


def _flag_dir(settings):
    return settings.halt_file.parent / "control"


def test_flag_set_show_and_clear(client, settings):
    name = NAME
    assert client.get(f"/traders/{name}").status_code == 200
    csrf = client.cookies.get("ail_csrf")
    page = client.get(f"/traders/{name}").text
    assert "印なし（普通に売買する）" in page and f'action="/ops/traders/{name}/flag"' in page
    # 停止
    r = client.post(f"/ops/traders/{name}/flag", data={"csrf": csrf, "kind": "paused", "reason": "入金待ち", "confirm": name}, follow_redirects=False)
    assert r.status_code == 303 and unquote(r.headers["location"]).startswith(f"/traders/{name}?flash=") and "停止" in unquote(r.headers["location"])
    d = json.loads((_flag_dir(settings) / f"{name}.json").read_text(encoding="utf-8"))
    assert d["paused"] is True and d["liquidate"] is False and d["reason"] == "入金待ち" and d["actor"] and d["since"]
    page = client.get(f"/traders/{name}").text
    assert "<b>停止</b>" in page and "入金待ち" in page and "次の回からこの人を飛ばす" in page
    assert re.search(r'<span class="badge badge-mock">停止</span>', client.get("/").text)      # 概要の段にも印
    # 手じまい（上書き）
    r = client.post(f"/ops/traders/{name}/flag", data={"csrf": csrf, "kind": "liquidate", "reason": "入れ替え", "confirm": name}, follow_redirects=False)
    assert r.status_code == 303
    d = json.loads((_flag_dir(settings) / f"{name}.json").read_text(encoding="utf-8"))
    assert d["liquidate"] is True and d["paused"] is False
    assert "<b>手じまい</b>" in client.get(f"/traders/{name}").text
    # 履歴に残る（actor つき）
    assert "trader_flag" in client.get("/ops").text
    # 解除
    r = client.post(f"/ops/traders/{name}/flag", data={"csrf": csrf, "kind": "clear"}, follow_redirects=False)
    assert r.status_code == 303 and not (_flag_dir(settings) / f"{name}.json").exists()
    assert "印なし" in client.get(f"/traders/{name}").text


def test_flag_requires_confirmation_and_known_trader(client, settings):
    name = NAME
    assert client.get(f"/traders/{name}").status_code == 200
    csrf = client.cookies.get("ail_csrf")
    r = client.post(f"/ops/traders/{name}/flag", data={"csrf": csrf, "kind": "liquidate", "confirm": "typo"}, follow_redirects=False)
    assert r.status_code == 303 and "印は立てていない" in unquote(r.headers["location"])
    assert not (_flag_dir(settings) / f"{name}.json").exists()
    assert client.post(f"/ops/traders/{name}/flag", data={"csrf": csrf, "kind": "halt", "confirm": name}).status_code == 400
    assert client.post("/ops/traders/nobody/flag", data={"csrf": csrf, "kind": "paused", "confirm": "nobody"}).status_code == 404
    assert client.post(f"/ops/traders/{name}/flag", data={"kind": "paused", "confirm": name}).status_code == 403     # CSRF


def test_public_face_has_no_flag_route(settings):
    settings.auth_mode = "cloudflare"
    settings.cf_team, settings.cf_aud, settings.cf_email = "team", "aud", "me@example.com"
    app = create_app(settings, start_monitors=False)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        assert c.post("/ops/traders/T1/flag", data={"kind": "paused", "confirm": "T1"}).status_code in (403, 404)


def test_broken_flag_is_shown_not_fatal(client, settings):
    name = NAME
    assert client.get(f"/traders/{name}").status_code == 200
    d = _flag_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text("{broken", encoding="utf-8")
    page = client.get(f"/traders/{name}")
    assert page.status_code == 200 and "読めない" in page.text
    assert client.get("/").status_code == 200
