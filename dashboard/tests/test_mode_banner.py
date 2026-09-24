"""実売買とシミュレーションの見分け（live-trading.md §0-7 (a)・dashboard.md §13）。

⚠ シミュレーションモードの間は全ページに帯と `[SIM]`・数字に「仮」の印・`/api/*` に `mode`。実売買モードでは 1 つも出ない。
⚠ 1 つの画面に本物とシミュレーションを混ぜない（モードと木が食い違えば数字を出さない）。
⚠ 管理画面は表示だけ: POST の経路は増えない。シミュレーションモードの停止ボタンは、シミュレーションの木の HALT だけを書く。
"""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import simmode
from app.livestore import livefs
from app.main import create_app
from tests.test_app import assert_clean
from tests.test_live import build_live_dir

PAGES = ["/", "/overall", "/traders/sim_a", "/records", "/judge", "/ops"]
SIM_TRADER = """
name = "sim_a"
test = true
budget_usd = 300.0
symbols = ["T"]
combine = "asis"
threshold = 50.0
sizing = "shares"
[[models]]
kind = "file"
name = "m20"
path = "../signals/sim_m20.csv"
"""


def build_sim_tree(mode_dir: Path, name: str = "sim1", *, paused: bool = False, speed=60) -> Path:
    root = mode_dir / "sim" / name
    (root / "config" / "traders").mkdir(parents=True)
    (root / "config" / "traders" / "sim_a.toml").write_text(SIM_TRADER, encoding="utf-8")
    (root / "sim").mkdir()
    # 仮のいま ＝ 2026-10-05 15:50 ET（19:50 UTC）
    (root / "sim" / "control.json").write_text(json.dumps({"sim_epoch": 1791230000.0 + 1000, "real_epoch": time.time(), "speed": speed, "paused": paused, "step": 0, "stop": False}))
    (root / "sim" / "status.json").write_text(json.dumps({"name": name, "sim": True, "state": "窓の中", "day_index": 3, "days_total": 64, "sim_date": "2026-10-05",
                                                           "source_date": "2026-06-03", "updated_at": time.time(), "last_rc": 0}))
    day = root / "out" / "2026-10-05"          # ⚠ 記録は DB（livefs。道はそのまま）
    tag = {"date": "2026-10-05", "env": "cert", "run_id": "20261005T194530Z", "sim": True, "mock": True, "test": True}
    livefs.append(day / "orders.jsonl", json.dumps({**tag, "symbol": "T", "side": "buy", "sizing": "shares", "shares": 4, "value_usd": 98.2, "mode": "submit",
                                                  "parts": [{"trader": "sim_a", "shares": 4, "usd": 98.2}], "quote_at_signal": {"mid": 24.55}, "final_status": "Filled",
                                                  "fills": [{"symbol": "T", "side": "buy", "shares": 4, "price": 24.61}], "attempts": 1}))
    livefs.append(day / "ledger.jsonl", json.dumps({**tag, "trader": "sim_a", "budget_usd": 300.0, "cost_in_use_usd": 98.44, "realized_usd": 0.0, "unrealized_usd": -0.24,
                                                  "fees_usd": 0.0, "market_value_usd": 98.2, "unsettled_usd": 0.0, "holdings_priced": 1, "drawdown_pct_of_budget": 0.08,
                                                  "holdings": {"T": {"shares": 4, "avg_price": 24.61, "opened": "2026-10-05"}}}))
    livefs.append(day / "events.jsonl", json.dumps({**tag, "kind": "start", "mode": "submit", "traders": ["sim_a"]}))
    return root


def set_mode(mode_dir: Path, mode: str, name: str | None = None) -> None:
    mode_dir.mkdir(parents=True, exist_ok=True)
    (mode_dir / "MODE").write_text(json.dumps({"mode": mode, "name": name, "since": "2026-09-19T00:00:00+00:00", "by": "test"}))


@pytest.fixture
def world(settings, tmp_path):
    """本物の記録（test_a・実売買の木）と、シミュレーションの木（sim_a）を両方置く。"""
    real = build_live_dir(tmp_path / "real-live")
    mode_dir = tmp_path / "executor"
    build_sim_tree(mode_dir)
    return dataclasses.replace(settings, live_dir=real, mode_dir=mode_dir, live_dir_explicit=False), mode_dir, real


def client_of(s):
    return TestClient(create_app(s, start_monitors=False), client=("127.0.0.1", 50000))


def test_no_mode_file_means_real_and_nothing_changes(world):
    s, mode_dir, _ = world
    with client_of(s) as c:
        for path in ["/", "/overall", "/traders/test_a", "/records", "/judge", "/ops"]:
            r = c.get(path)
            assert r.status_code == 200, path
            assert "simbar" not in r.text and "[SIM]" not in r.text and "sim-mark" not in r.text, path
        assert c.get("/traders/sim_a").status_code == 404                     # シミュレーションのトレーダーは実売買の画面に居ない
        for api in ("/api/state", "/api/events", "/api/records", "/api/live", "/api/judge"):
            assert c.get(api).json()["mode"] == "real", api
        assert "sim_a" not in c.get("/api/live").text


def test_sim_mode_marks_every_page(world):
    s, mode_dir, _ = world
    set_mode(mode_dir, "sim", "sim1")
    with client_of(s) as c:
        for path in PAGES:
            r = c.get(path)
            assert r.status_code == 200, path
            assert '<div class="simbar"' in r.text and "実売買ではない" in r.text, path
            assert "<title>[SIM] " in r.text, path
            assert r.text.index('class="simbar"') < r.text.index('class="shell"'), path   # 最上部・部分更新の外
            assert "2026-10-05" in r.text and "×60" in r.text and "3 ／ 64 日目" in r.text, path
            assert_clean(r.text, path)
        body = c.get("/").text
        assert "sim-mark" in body and "sim_a" in body and "test_a" not in body            # 数字に「仮」の印・本物のトレーダーは混ざらない
        assert c.get("/traders/test_a").status_code == 404
        clock = c.get("/?partial=simclock").text
        assert "2026-10-05" in clock and "simbar" not in clock


def test_sim_mode_apis_name_the_mode_and_do_not_mix(world):
    s, mode_dir, _ = world
    set_mode(mode_dir, "sim", "sim1")
    with client_of(s) as c:
        for api in ("/api/state", "/api/events", "/api/records", "/api/live", "/api/judge"):
            doc = c.get(api).json()
            assert doc["mode"] == "sim" and doc["sim"]["name"] == "sim1" and doc["sim"]["speed"] == 60, api
        live = c.get("/api/live")
        assert [t["name"] for t in live.json()["traders"]] == ["sim_a"] and "test_a" not in live.text
        assert str(mode_dir) not in live.text.replace(live.json()["live_dir"], "")


def test_sim_today_and_pause_show(world):
    s, mode_dir, _ = world
    set_mode(mode_dir, "sim", "sim1")
    build_sim_tree(mode_dir, "sim2", paused=True, speed="max")
    set_mode(mode_dir, "sim", "sim2")
    info = s.machine()
    assert info["sim"]["today"] == "2026-10-05" and info["sim"]["paused"] and info["sim"]["speed_label"] == "最速"
    with client_of(s) as c:
        assert "停止中" in c.get("/?partial=simclock").text


def test_mismatch_shows_no_numbers(world, tmp_path):
    s, mode_dir, real = world
    # (1) sim モード × 手で指定した本物の木
    set_mode(mode_dir, "sim", "sim1")
    with client_of(dataclasses.replace(s, live_dir_explicit=True)) as c:
        body = c.get("/").text
        assert "モードと記録が食い違っている" in body and "test_a" not in body and "sim_a" not in body
        assert c.get("/api/live").json()["mode_mismatch"] and c.get("/api/live").json()["traders"] == []
    # (2) 実売買モード × 手で指定したシミュレーションの木
    set_mode(mode_dir, "real")
    with client_of(dataclasses.replace(s, live_dir=mode_dir / "sim" / "sim1", live_dir_explicit=True)) as c:
        body = c.get("/").text
        assert "モードと記録が食い違っている" in body and "sim_a" not in body and "[SIM]" not in body
    # (3) MODE が壊れている
    (mode_dir / "MODE").write_text("{")
    with client_of(s) as c:
        body = c.get("/").text
        assert "モードと記録が食い違っている" in body and "test_a" not in body
        assert c.get("/api/live").json()["mode"] == "unknown"


def test_public_face_never_reads_the_mode(world):
    s, mode_dir, _ = world
    set_mode(mode_dir, "sim", "sim1")
    assert simmode.read_mode(None) == {"mode": "real"}
    assert dataclasses.replace(s, mode_dir=None).machine()["mode"] == "real"


def test_no_new_post_routes(world):
    s, _, _ = world
    app = create_app(s, start_monitors=False)
    posts = sorted(r.path for r in app.routes if "POST" in (getattr(r, "methods", None) or ()))
    assert posts == ["/ops/halt", "/ops/resume", "/ops/retry-auth"]


def test_stop_button_in_sim_mode_writes_only_the_sim_halt(world):
    s, mode_dir, _ = world
    set_mode(mode_dir, "sim", "sim1")
    sim_halt = mode_dir / "sim" / "sim1" / "HALT"
    real_halt = s.records_dir / "HALT"
    before = sorted(p for p in (mode_dir / "sim" / "sim1").rglob("*"))
    with client_of(s) as c:
        c.get("/")  # CSRF cookie を受け取る
        csrf = c.cookies.get("ail_csrf")
        r = c.post("/ops/halt", data={"csrf": csrf, "reason": "通し稽古"}, follow_redirects=False)
        assert r.status_code == 303
        assert sim_halt.exists() and not real_halt.exists()
        assert "停止中（HALT）" in c.get("/").text
        hist = c.app.state.ops.history(5)
        assert hist[0]["mode"] == "sim" and "本物の口座の注文には触らない" in json.dumps(hist[0], ensure_ascii=False)
        r = c.post("/ops/resume", data={"csrf": csrf}, follow_redirects=False)
        assert r.status_code == 303 and not sim_halt.exists() and not real_halt.exists()
    assert sorted(p for p in (mode_dir / "sim" / "sim1").rglob("*")) == before             # 管理画面が sim/ の下に書くのは HALT だけ
    # 実売買モードへ戻すと、停止ボタンは本物の HALT を書く（今までどおり）
    set_mode(mode_dir, "real")
    with client_of(s) as c:
        c.get("/")
        c.post("/ops/halt", data={"csrf": c.cookies.get("ail_csrf")}, follow_redirects=False)
        assert real_halt.exists() and not sim_halt.exists()


# ---------- 「本番の機械ではない」印（live-trading.md §0-14。表示だけ）

def test_not_production_mark_shows_a_band_on_every_page(world):
    import socket
    s, mode_dir, _ = world
    pages = [p if p != "/traders/sim_a" else "/traders/test_a" for p in PAGES]   # 実売買モードのまま ＝ 本物の人
    with client_of(s) as c:
        for path in pages:
            assert "simbar-np" not in c.get(path).text, path                   # 印が無ければ 1 文字も変わらない
        assert "not_production" not in c.get("/api/live").json()
    (mode_dir / "NOT_PRODUCTION").write_text(json.dumps({"machine": socket.gethostname(), "since": "2026-09-27T00:00:00+00:00", "by": "u", "reason": "本番は 13500t"}))
    with client_of(s) as c:
        for path in pages:
            r = c.get(path)
            assert r.status_code == 200 and 'class="simbar simbar-np"' in r.text and "本番の機械ではない" in r.text, path
            assert "[SIM]" not in r.text and "印ではない" not in r.text, path          # 実売買モードのまま・この機械の印
            assert_clean(r.text, path)
        for api in ("/api/state", "/api/live", "/api/records"):
            np = c.get(api).json()["not_production"]
            assert np["machine"] == socket.gethostname() and np["matches_host"] is True and np["error"] is None, api
    # 他の機械の印・壊れた印は、それと分かる帯（執行器と同じく「ある」側に倒す）
    (mode_dir / "NOT_PRODUCTION").write_text(json.dumps({"machine": "elsewhere", "since": "x"}))
    with client_of(s) as c:
        body = c.get("/").text
        assert "印ではない" in body and "elsewhere" in body and c.get("/api/live").json()["not_production"]["matches_host"] is False
    (mode_dir / "NOT_PRODUCTION").write_text("{")
    with client_of(s) as c:
        assert "NOT_PRODUCTION が読めない" in c.get("/").text and c.get("/api/live").json()["not_production"]["error"]
