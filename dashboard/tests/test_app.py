"""画面と JSON の応答に秘密が出ないこと、面ごとの経路、CSRF、停止ボタン。監視ループは起動しない。"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FAKE_ACCOUNT, FAKE_REFRESH, FAKE_SECRET, good_run, write_run

DASH = Path(__file__).resolve().parents[1]

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
    # ⚠ 停止のテストは取消を閉じたポート（TT_REST_BASE）へ実際に送る。WSL2（mirrored）は拒否を返さず無応答なので、
    # テストの client だけ待ちを短くする（⚠ ttclient の既定 30 秒 ＝ 実運用の値は変えない）
    mon.client.timeout = 0.5
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
    for path in ["/", "/?partial=strip", "/overall", "/overall?partial=monitor", "/records", "/records/20260908T140000Z", "/records/diff?a=20260908T140000Z&b=20260909T140000Z", "/judge", "/ops", "/api/state", "/api/records", "/api/judge", "/api/events"]:
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
    # ⚠ 停止中に発注を拒否するのは執行器とサンプル（同じ HALT を見る）。管理画面に発注の経路は無い（test_dropped_routes_are_gone）
    r = client.post("/ops/resume", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303 and not settings.halt_file.exists()
    hist = client.app.state.ops.history()
    assert [h["kind"] for h in hist] == ["resume", "halt"]


def test_dropped_routes_are_gone(client):
    """2026-09-18 に外した 11 行 ＋ 1 件取消（検証・データ・手動の注文・開発）は、ローカル面でも経路ごと無い。"""
    csrf = client.cookies.get("ail_csrf")
    for path in ["/experiments", "/experiments/x", "/api/experiments", "/data", "/api/data", "/dev", "/dev/jobs/x"]:
        assert client.get(path).status_code == 404, path
    for path in ["/ops/dry-run", "/ops/submit", "/ops/cancel", "/ops/cleanup", "/dev/mock/start", "/dev/mock/stop", "/dev/selftest", "/dev/run", "/dev/jobs/x/stop"]:
        r = client.post(path, data={"csrf": csrf, "env": "cert", "symbol": "SPY", "quantity": "1", "action": "Buy to Open", "order_type": "Market"}, follow_redirects=False)
        assert r.status_code in (404, 405), path
    ops = client.get("/ops").text
    assert "停止 / 解除" in ops and "操作の履歴" in ops
    for gone in ("dry-run（何も", "この内容で発注", "後片付け（全取消）", 'name="order_type"', "/ops/submit", "/ops/cleanup", "/ops/cancel"):
        assert gone not in ops, gone


def test_dashboard_cannot_open_order_keys(client):
    """⚠ 管理画面が作るクライアントは取消の鍵だけ（発注と dry-run の鍵は渡さない）。"""
    c = client.app.state.ops._client("cert")
    assert c.allow_prod_cancel is True and c.allow_prod_orders is False and c.allow_prod_dry_run is False
    assert not hasattr(client.app.state.settings, "allow_prod_orders")


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


# ---------------------------------------------------------------- CSP とインライン（2026-09-18）
# CSP は script-src 'self'; style-src 'self'。templates に on*="…" や style="…" を書くとブラウザが黙って止める
# （確認ダイアログが出ずに送られた）。⚠ ブラウザでの動き（出る・断ると送られない）は tests/browser/confirm.mjs で見る。

INLINE = re.compile(r"""\s(on[a-z]+|style)\s*=\s*["']""", re.I)


def test_templates_have_no_inline_handlers_or_styles():
    hits = []
    for path in sorted((DASH / "app" / "templates").glob("*.html")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if INLINE.search(line) or "<style" in line.lower() or re.search(r"<script(?![^>]*\bsrc=)", line, re.I):
                hits.append(f"{path.name}:{n}: {line.strip()[:80]}")
    assert not hits, "templates にインラインのスクリプト ／ style がある（CSP に止められる。app.js ／ app.css へ）:\n" + "\n".join(hits)


def test_rendered_pages_have_no_inline_and_forms_ask_before_sending(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "script-src 'self'" in csp and "style-src 'self'" in csp and "unsafe-inline" not in csp
    for path in ["/", "/overall", "/records", "/records/20260908T140000Z", "/records/diff?a=20260908T140000Z&b=20260909T140000Z", "/judge", "/ops"]:
        assert not INLINE.search(client.get(path).text), path
    # 停止（全画面の右上と /ops）・後片付け（prod があるときだけ）は data-confirm を持つ
    assert re.search(r'<form[^>]*action="/ops/halt"[^>]*data-confirm="停止する', client.get("/").text)
    assert re.search(r'<form[^>]*action="/ops/halt"[^>]*data-confirm="停止する', client.get("/ops").text)
    csrf = client.cookies.get("ail_csrf")
    # 停止中は解除の form が確認つきで出る
    client.post("/ops/halt", data={"csrf": csrf}, follow_redirects=False)
    assert re.search(r'<form[^>]*action="/ops/resume"[^>]*data-confirm="停止を解除する', client.get("/ops").text)
    # 確かめるのは app.js（'self' なので CSP を通る）
    js = client.get("/static/app.js").text
    assert "data-confirm" in js and "preventDefault" in js and '"submit"' in js
