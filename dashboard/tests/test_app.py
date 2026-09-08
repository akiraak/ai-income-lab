"""画面と JSON の応答に秘密が出ないこと、面ごとの経路、CSRF、停止ボタン。監視ループは起動しない。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FAKE_ACCOUNT, FAKE_REFRESH, FAKE_SECRET, good_run, write_run

FAKE_ACCESS = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJmYWtlIiwiZXhwIjo5OTk5OTk5OTk5fQ.c2lnbmF0dXJlLWZha2UtZmFrZQ"


@pytest.fixture
def client(settings):
    write_run(settings.records_dir, good_run())
    write_run(settings.records_dir, good_run(run_id="20260909T140000Z", at="2026-09-09T14:00:00.000+00:00"))
    app = create_app(settings, start_monitors=False)
    # 監視ループ相当の状態を手で作る（認証済み・口座番号あり・注文あり）
    mon = app.state.monitors.get("cert")
    from ttclient import Token

    mon.client.token = Token(access_token=FAKE_ACCESS, expires_in=900, obtained_at=1.0, scope="read trade openid")
    app.state.redactor.secret(FAKE_ACCESS, "<access_token:masked>")
    mon.account_number = FAKE_ACCOUNT
    label = app.state.redactor.account(FAKE_ACCOUNT)
    mon.state["auth"].update(ok=True, scope="read trade openid")
    mon.state["account"].update(label=label, count=1)
    mon.state["live_orders"] = [{"id": 1, "status": "Live", "legs": [{"symbol": "SPY", "action": "Buy to Open", "quantity": "1"}], "order_type": "Limit", "price": "10.00"}]
    mon.state["errors"] = []
    mon._errors.appendleft({"at": "2026-09-08T14:00:00+00:00", "where": "poll", "type": "ApiError", "status": 422, "code": "x", "message": f"acct {FAKE_ACCOUNT} token {FAKE_ACCESS} secret {FAKE_SECRET}"})
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        c.get("/")  # CSRF cookie を受け取る
        yield c


SECRETS = [FAKE_SECRET, FAKE_REFRESH, FAKE_ACCOUNT, FAKE_ACCESS, "eyJ"]


def assert_clean(text: str, where: str):
    for s in SECRETS:
        assert s not in text, f"{where} に {s} が出ている"


def test_pages_render_without_secrets(client):
    for path in ["/", "/?partial=1", "/records", "/records/20260908T140000Z", "/records/diff?a=20260908T140000Z&b=20260909T140000Z", "/judge", "/ops", "/dev", "/api/state", "/api/records", "/api/judge", "/api/events"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert_clean(r.text, path)
        assert r.headers["cache-control"] == "no-store"
        assert "default-src 'self'" in r.headers["content-security-policy"]
    state = client.get("/api/state").json()
    cert = state["monitors"]["cert"]
    assert cert["account"]["label"].startswith("acct-")
    assert cert["errors"][0]["message"].count("masked") >= 2 and "acct-" in cert["errors"][0]["message"]


def test_judge_page_reflects_records(client):
    r = client.get("/judge")
    assert "成立（無人で 1 営業日回る）" in r.text and "2 営業日" in r.text


def test_csrf_required(client):
    r = client.post("/ops/halt", data={"reason": "x"})
    assert r.status_code == 403
    csrf = client.cookies.get("ail_csrf")
    r = client.post("/ops/halt", data={"reason": "x", "csrf": "wrong"})
    assert r.status_code == 403
    assert csrf


def test_halt_writes_flag_and_resume(client, settings):
    csrf = client.cookies.get("ail_csrf")
    r = client.post("/ops/halt", data={"reason": "テスト", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert settings.halt_file.exists()
    info = json.loads(settings.halt_file.read_text())
    assert info["reason"] == "テスト" and info["actor"] == "loopback"
    assert "停止した" in client.get(r.headers["location"]).text
    # 取消は API に届く前に接続エラーになる（TT_REST_BASE が閉じたポート）が、HALT 自体は書かれている
    assert "停止中（HALT）" in client.get("/").text
    # 停止中は発注が拒否される
    r = client.post("/ops/submit", data={"csrf": csrf, "env": "cert", "symbol": "SPY", "quantity": "1", "action": "Buy to Open", "order_type": "Limit", "price": "10"}, follow_redirects=False)
    assert "%E5%81%9C%E6%AD%A2%E4%B8%AD" in r.headers["location"]  # 「停止中」
    r = client.post("/ops/resume", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303 and not settings.halt_file.exists()
    hist = client.app.state.ops.history()
    assert [h["kind"] for h in hist] == ["resume", "halt"]


def test_order_validation(client):
    csrf = client.cookies.get("ail_csrf")
    r = client.post("/ops/dry-run", data={"csrf": csrf, "env": "cert", "symbol": "SPY", "quantity": "50", "action": "Buy to Open", "order_type": "Limit", "price": "10"})
    assert r.status_code == 200 and "1〜10" in r.text
    r = client.post("/ops/dry-run", data={"csrf": csrf, "env": "cert", "symbol": "SPY", "quantity": "1", "action": "Buy to Open", "order_type": "Limit", "price": ""})
    assert "指値には価格が要る" in r.text
    r = client.post("/ops/dry-run", data={"csrf": csrf, "env": "prod", "symbol": "SPY", "quantity": "1", "action": "Buy to Open", "order_type": "Limit", "price": "1"})
    assert "prod の資格情報が設定されていない" in r.text


def test_dev_step_guards(client):
    csrf = client.cookies.get("ail_csrf")
    r = client.post("/dev/run", data={"csrf": csrf, "env": "prod", "step": "4", "seconds": "5"}, follow_redirects=False)
    assert "probe" in __import__("urllib.parse").parse.unquote(r.headers["location"])
    r = client.post("/dev/run", data={"csrf": csrf, "env": "cert", "step": "rm -rf", "seconds": "5"}, follow_redirects=False)
    assert "%E4%B8%8D%E6%AD%A3" in r.headers["location"]  # 「不正」
    r = client.post("/dev/run", data={"csrf": csrf, "env": "cert", "step": "4", "seconds": "5", "use_mock": "1"}, follow_redirects=False)
    assert "%E3%83%A2%E3%83%83%E3%82%AF" in r.headers["location"]  # 「モック」が動いていない


def test_public_face_hides_ops_and_dev(settings):
    settings.auth_mode = "cloudflare"
    settings.cf_team, settings.cf_aud, settings.cf_email = "team", "aud", "me@example.com"
    app = create_app(settings, start_monitors=False)
    with TestClient(app, client=("172.20.0.3", 50000)) as c:  # cloudflared からでも JWT が無ければ 403
        assert c.get("/").status_code == 403
    # ループバックは免除。面（公開）だけ確かめる
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        assert c.get("/").status_code == 200
        assert "dashboard" not in c.get("/api/judge").json()["venues"]
        assert "公開面" in c.get("/").text
        assert c.get("/ops").status_code == 404
        assert c.get("/dev").status_code == 404
        csrf = c.cookies.get("ail_csrf")
        assert c.post("/ops/resume", data={"csrf": csrf}).status_code == 404
        assert c.post("/ops/halt", data={"csrf": csrf, "reason": "remote"}, follow_redirects=False).status_code == 303
        assert settings.halt_file.exists()


def test_working_orders_exclude_finished(settings, monkeypatch):
    """/orders/live は本日の注文を返し終わったものも混ざる。「働いている注文」はそれを含めない。"""
    from app.main import create_app
    from app.monitor import WORKING_STATUSES

    app = create_app(settings, start_monitors=False)
    mon = app.state.monitors.get("cert")
    mon.account_number = FAKE_ACCOUNT
    orders = [
        {"id": 1, "status": "Live", "order-type": "Limit", "price": "10.0", "legs": []},
        {"id": 2, "status": "Filled", "order-type": "Market", "legs": []},
        {"id": 3, "status": "Rejected", "order-type": "Market", "legs": []},
        {"id": 4, "status": "Cancelled", "order-type": "Limit", "price": "10.0", "legs": []},
    ]
    monkeypatch.setattr(mon, "ensure_token", lambda force=False: True)  # 認証はこのテストの対象外
    monkeypatch.setattr(mon.client, "list_accounts", lambda: [{"account-number": FAKE_ACCOUNT}])
    monkeypatch.setattr(mon.client, "get_balances", lambda a: {})
    monkeypatch.setattr(mon.client, "list_positions", lambda a: [])
    monkeypatch.setattr(mon.client, "list_live_orders", lambda a: orders)
    mon.poll_once()

    snap = mon.snapshot()
    assert [o["id"] for o in snap["live_orders"]] == [1], snap["live_orders"]
    assert all(o["status"] in WORKING_STATUSES for o in snap["live_orders"])
    assert snap["orders_today"]["total"] == 4
    assert snap["orders_today"]["by_status"] == {"Live": 1, "Filled": 1, "Rejected": 1, "Cancelled": 1}
