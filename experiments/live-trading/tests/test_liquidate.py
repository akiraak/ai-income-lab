"""手じまい（liquidate.py。プラン docs/plans/liquidate.md）: 拒否の段と、モックで通すこと。

⚠ 売るのは売買履歴の持ち株だけ ／ dry-run は状態を書かない ／ HALT は --while-halted で通す ／ 口座にあって売買履歴に無い株は売らない。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from liquidate import market_refusal, sell_orders  # noqa: E402
from state import TraderState  # noqa: E402
from tests._records import doc, jsonl, put_doc  # noqa: E402

MOCK = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample", "mock_server.py"))
ET = ZoneInfo("America/New_York")
TRADER = '''name = "lq_a"
test = true
budget_usd = 300.0
symbols = ["SPY", "QQQ"]
combine = "asis"
threshold = 50.0
sizing = "shares"
[[models]]
kind = "file"
name = "script"
path = "../signals/lq_a.csv"
'''


def free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


@pytest.fixture
def mock_server():
    port, ws, dx = free_ports(3)
    proc = subprocess.Popen([sys.executable, MOCK, "--port", str(port), "--ws-port", str(ws), "--dxlink-port", str(dx), "--market-data", "--fill-noise", "0.003"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/customers/me/accounts", headers={"User-Agent": "test/1.0"}), timeout=1)
                break
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.kill()
        proc.wait()


def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST", headers={"User-Agent": "test/1.0", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def setup(base):
    """本物の木に触らない置き場: MODE ／ run.lock・記録・状態・HALT・トレーダーの設定を tmp に。売買履歴に SPY 2 株・QQQ 1 株。"""
    tdir = base / "traders"
    tdir.mkdir()
    (tdir / "lq_a.toml").write_text(TRADER, encoding="utf-8")
    st = TraderState(name="lq_a")
    st.apply_buy("SPY", 2, 100.0, "2026-09-23")
    st.apply_buy("QQQ", 1, 200.0, "2026-09-23")
    put_doc(os.path.join(base, "state", "cert", "lq_a.json"), st.to_dict())
    return tdir


def run(base, tdir, args, rest_base=None, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}
    env.update({"TT_ENV_FILE": "/nonexistent", "LT_MODE_DIR": str(base), "LT_OUT_DIR": str(base / "out"), "LT_STATE_DIR": str(base / "state" / "cert"),
                "TT_HALT_FILE": str(base / "HALT"), "LT_TRADERS_DIR": str(tdir), "TT_CLIENT_SECRET": "MOCK-SECRET", "TT_REFRESH_TOKEN": "MOCK-REFRESH"})
    if rest_base:
        env["TT_REST_BASE"] = rest_base
    env.update(extra_env or {})
    return subprocess.run([sys.executable, os.path.join(HERE, "liquidate.py"), *args], capture_output=True, text=True, env=env)


def state(base):
    return doc(os.path.join(base, "state", "cert", "lq_a.json"))


def rows(base, kind, date=None):
    date = date or datetime.now(ET).strftime("%Y-%m-%d")
    return jsonl(os.path.join(base, "out", date, f"{kind}.jsonl"))


# ---------- 部品 ----------

def test_market_hours_follow_the_nyse_calendar():
    assert market_refusal(datetime(2026, 10, 6, 9, 30, tzinfo=ET)) is None
    assert market_refusal(datetime(2026, 10, 6, 15, 59, tzinfo=ET)) is None
    assert "開く前" in market_refusal(datetime(2026, 10, 6, 9, 29, tzinfo=ET))
    assert "引けの後" in market_refusal(datetime(2026, 10, 6, 16, 0, tzinfo=ET))
    assert "土日" in market_refusal(datetime(2026, 10, 4, 12, 0, tzinfo=ET))
    assert "休場日" in market_refusal(datetime(2026, 11, 26, 12, 0, tzinfo=ET))      # Thanksgiving
    assert market_refusal(datetime(2026, 11, 27, 12, 59, tzinfo=ET)) is None         # 半日立会は 13:00 引け
    assert "13:00" in market_refusal(datetime(2026, 11, 27, 13, 0, tzinfo=ET))


def test_sell_orders_only_from_the_ledger():
    a, b = TraderState(name="a"), TraderState(name="b")
    a.apply_buy("SPY", 2, 100.0, "2026-09-23")
    a.apply_buy("QQQ", 0.5, 200.0, "2026-09-23")
    b.apply_buy("SPY", 1, 100.0, "2026-09-23")
    orders, ev = sell_orders({"a": a, "b": b}, None, set(), {"SPY": 110.0})
    assert [(o.parts[0]["trader"], o.symbol, o.shares, o.side, o.sizing) for o in orders] == [("a", "QQQ", 0.5, "sell", "shares"), ("a", "SPY", 2, "sell", "shares"), ("b", "SPY", 1, "sell", "shares")]
    assert orders[1].value_usd == 220.0 and orders[0].value_usd == 100.0          # 気配が無い銘柄は原価で置く
    orders, ev = sell_orders({"a": a, "b": b}, {"SPY"}, {"SPY"}, {})
    assert orders == [] and {(e["trader"], e["symbol"]) for e in ev} == {("a", "SPY"), ("b", "SPY")}   # 帳尻の合わない銘柄は売らない
    orders, _ = sell_orders({"a": a}, {"QQQ"}, set(), {})
    assert [o.symbol for o in orders] == ["QQQ"]


# ---------- 拒否の段（ネットワークなし） ----------

def test_prod_submit_refused_without_both_keys(tmp_path):
    tdir = setup(tmp_path)
    env = {"TT_PROD_CLIENT_SECRET": "s", "TT_PROD_REFRESH_TOKEN": "r"}
    r = run(tmp_path, tdir, ["--traders", "lq_a", "--env", "prod", "--mode", "submit", "--ignore-market-hours", "--i-know-this-is-real-money"], extra_env=env)
    assert r.returncode == 2 and "TT_ALLOW_PROD_ORDERS" in r.stderr
    r = run(tmp_path, tdir, ["--traders", "lq_a", "--env", "prod", "--mode", "dry-run", "--ignore-market-hours"], extra_env=env)
    assert r.returncode == 2 and "allow-prod-dry-run" in r.stderr


def test_halt_refuses_unless_while_halted(tmp_path):
    tdir = setup(tmp_path)
    (tmp_path / "HALT").write_text("x")
    r = run(tmp_path, tdir, ["--traders", "lq_a", "--ignore-market-hours"])
    assert r.returncode == 3 and "停止フラグ" in r.stderr
    assert [e["kind"] for e in rows(tmp_path, "events")] == ["halted"]


def test_sim_trader_names_are_refused(tmp_path):
    tdir = setup(tmp_path)
    r = run(tmp_path, tdir, ["--traders", "sim_x", "--ignore-market-hours"])
    assert r.returncode == 2 and "本物の人だけ" in r.stderr


# ---------- モックで通す ----------

def test_submit_sells_everything_and_books_it(tmp_path, mock_server):
    tdir = setup(tmp_path)
    (tmp_path / "HALT").write_text("x")                                   # 止めてから手じまいする、が普通の順
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 2, "QQQ": 1}})
    r = run(tmp_path, tdir, ["--traders", "lq_a", "--mode", "submit", "--ignore-market-hours", "--while-halted"], mock_server)
    assert r.returncode == 0, r.stdout + r.stderr
    orders = rows(tmp_path, "orders")
    assert [(o["symbol"], o["side"], o["shares"], o["final_status"], o["liquidate"], o["mode"]) for o in orders] == \
        [("QQQ", "sell", 1, "Filled", True, "submit"), ("SPY", "sell", 2, "Filled", True, "submit")]
    assert all(o["mock"] is True and o["test"] is True for o in orders)
    st = state(tmp_path)
    assert st["holdings"] == {} and st["last_date"] == datetime.now(ET).strftime("%Y-%m-%d")
    assert len(st["history"]) == 4 and all(h["note"] == "手じまい" for h in st["history"][2:])
    assert st["realized_usd"] != 0.0 and len(st["pending_settlement"]) == 2
    ev = {e["kind"] for e in rows(tmp_path, "events")}
    assert {"liquidate_start", "liquidate_end"} <= ev and "halted" not in ev
    end = [e for e in rows(tmp_path, "events") if e["kind"] == "liquidate_end"][0]
    assert end["orders"] == 2 and end["fills"] == 2 and end["bad"] == 0 and "diff_after" not in end
    journal = jsonl(os.path.join(tmp_path, "state", "cert", "journal.jsonl"))
    assert [j["op"] for j in journal].count("intent") == 2 and [j["op"] for j in journal].count("done") == 2
    assert (tmp_path / "HALT").read_text() == "x"                           # 本物の HALT は触らない
    assert not any(e["kind"] == "positions_outside_ledger" for e in rows(tmp_path, "events"))
    assert [p["positions"] for p in rows(tmp_path, "positions") if p["when"] == "after"] == [[]]
    assert "MOCK-SECRET" not in (r.stdout + r.stderr)


def test_dry_run_does_not_touch_the_ledger(tmp_path, mock_server):
    tdir = setup(tmp_path)
    before = state(tmp_path)
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 2, "QQQ": 1}})
    r = run(tmp_path, tdir, ["--traders", "lq_a", "--ignore-market-hours"], mock_server)
    assert r.returncode == 0, r.stdout + r.stderr
    assert [o["final_status"] for o in rows(tmp_path, "orders")] == ["dry-run", "dry-run"]
    assert state(tmp_path) == before
    assert not os.path.exists(os.path.join(tmp_path, "state", "cert", "journal.jsonl")) or jsonl(os.path.join(tmp_path, "state", "cert", "journal.jsonl")) == []


def test_symbols_filter_and_account_only_shares_stay(tmp_path, mock_server):
    tdir = setup(tmp_path)
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 5, "QQQ": 1}})   # 口座には売買履歴より 3 株多い SPY（利用者の手持ち）
    r = run(tmp_path, tdir, ["--traders", "lq_a", "--symbols", "SPY", "--mode", "submit", "--ignore-market-hours"], mock_server)
    assert r.returncode == 1, r.stdout + r.stderr                             # 口座 − 売買履歴 の差（外の株）が残る ＝ 1
    orders = rows(tmp_path, "orders")
    assert [(o["symbol"], o["shares"], o["final_status"]) for o in orders] == [("SPY", 2, "Filled")]
    st = state(tmp_path)
    assert set(st["holdings"]) == {"QQQ"}
    assert any(e["kind"] == "positions_outside_ledger" and set(e["symbols"]) == {"SPY"} for e in rows(tmp_path, "events"))
    end = [e for e in rows(tmp_path, "events") if e["kind"] == "liquidate_end"][0]
    assert set(end["diff_after"]) == {"SPY"} and end["diff_after"]["SPY"]["account"] == 3   # 口座に残るのは売買履歴の外の 3 株


def test_short_position_is_not_sold(tmp_path, mock_server):
    tdir = setup(tmp_path)
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 1, "QQQ": 1}})   # 売買履歴は SPY 2 株なのに口座は 1 株
    r = run(tmp_path, tdir, ["--traders", "lq_a", "--mode", "submit", "--ignore-market-hours"], mock_server)
    assert r.returncode == 1
    assert [(o["symbol"], o["final_status"]) for o in rows(tmp_path, "orders")] == [("QQQ", "Filled")]
    assert set(state(tmp_path)["holdings"]) == {"SPY"}
    kinds = [e["kind"] for e in rows(tmp_path, "events")]
    assert "position_short" in kinds and "blocked_symbol" in kinds
