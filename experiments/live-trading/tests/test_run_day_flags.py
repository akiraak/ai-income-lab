"""執行器が人ごとの印（停止 ／ 手じまい。control.py）を読むこと（モックサーバで通す。プラン docs/plans/archive/trader-control-flags.md）。

停止 ＝ その人を飛ばす（合図を読まず・売買しない・持ち株はそのまま）／ 手じまい ＝ 合図を読まず持ち株を全部売り、済んだら印に「済み」を書き、以後は飛ばす ／
HALT が優先 ／ 壊れた印は起動を拒む ／ 予算の合計の上限は「買う人」だけで見る ／ 口座が足りない銘柄は売らず翌日に持ち越す。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import control  # noqa: E402
from state import TraderState  # noqa: E402
from tests._records import doc, jsonl, put_doc  # noqa: E402

MOCK = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample", "mock_server.py"))
DATE = "2026-10-06"
TRADER = '''name = "{name}"
test = true
budget_usd = 2000.0
symbols = ["SPY", "QQQ"]
combine = "asis"
threshold = 50.0
sizing = "shares"
[[models]]
kind = "file"
name = "script"
path = "../signals/{name}.csv"
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
    """`lq_a`（持ち株 SPY 2・QQQ 1）と `ac_b`（持ち株なし・今日 SPY を買う合図）。印の置き場は HALT の隣の control/。"""
    tdir, sdir = base / "traders", base / "signals"
    tdir.mkdir(), sdir.mkdir()
    for name in ("lq_a", "ac_b"):
        (tdir / f"{name}.toml").write_text(TRADER.format(name=name), encoding="utf-8")
        (sdir / f"{name}.csv").write_text(f"date,symbol,buy,exit\n{DATE},SPY,80,20\n{DATE},QQQ,30,70\n", encoding="utf-8")
    st = TraderState(name="lq_a")
    st.apply_buy("SPY", 2, 100.0, "2026-09-23")
    st.apply_buy("QQQ", 1, 200.0, "2026-09-23")
    put_doc(os.path.join(base, "state", "cert", "lq_a.json"), st.to_dict())
    return tdir, str(base / "control")


def run(base, tdir, args, rest_base, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}
    env.update({"TT_ENV_FILE": "/nonexistent", "LT_MODE_DIR": str(base), "LT_OUT_DIR": str(base / "out"), "LT_STATE_DIR": str(base / "state" / "cert"),
                "TT_HALT_FILE": str(base / "HALT"), "LT_TRADERS_DIR": str(tdir), "TT_REST_BASE": rest_base, "TT_CLIENT_SECRET": "MOCK-SECRET", "TT_REFRESH_TOKEN": "MOCK-REFRESH"})
    env.update(extra_env or {})
    return subprocess.run([sys.executable, os.path.join(HERE, "run_day.py"), "--date", DATE, "--ignore-window", "--max-total-budget", "10000", *args], capture_output=True, text=True, env=env)


def state(base, name):
    return doc(os.path.join(base, "state", "cert", f"{name}.json"))


def rows(base, kind):
    return jsonl(os.path.join(base, "out", DATE, f"{kind}.jsonl"))


def test_liquidate_flag_sells_everything_then_skips(tmp_path, mock_server):
    tdir, cdir = setup(tmp_path)
    control.set_flag(cdir, "lq_a", "liquidate", "akira", "入れ替え")
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 2, "QQQ": 1}})
    r = run(tmp_path, tdir, ["--traders", "lq_a,ac_b", "--mode", "submit"], mock_server)
    assert r.returncode == 0, r.stdout + r.stderr
    orders = rows(tmp_path, "orders")
    assert [(o["parts"][0]["trader"], o["symbol"], o["side"], o["final_status"], o.get("liquidate")) for o in orders] == \
        [("lq_a", "QQQ", "sell", "Filled", True), ("lq_a", "SPY", "sell", "Filled", True), ("ac_b", "SPY", "buy", "Filled", None)]
    assert state(tmp_path, "lq_a")["holdings"] == {} and "SPY" in state(tmp_path, "ac_b")["holdings"]
    assert not [s for s in rows(tmp_path, "signals") if s["trader"] == "lq_a"]            # 手じまいの人の合図は読まない
    kinds = [e["kind"] for e in rows(tmp_path, "events")]
    assert "liquidate_flag" in kinds and "liquidate_complete" in kinds
    start = [e for e in rows(tmp_path, "events") if e["kind"] == "start"][0]
    assert start["flags"] == {"lq_a": "手じまい"}
    fl = control.read_flag(cdir, "lq_a")
    assert fl.liquidate and fl.done["date"] == DATE and fl.done["fills"] == 2
    # 2 回目: 済んでいるので飛ばす（売りも買いも出さない）。ac_b は持っているので hold
    r = run(tmp_path, tdir, ["--traders", "lq_a,ac_b", "--mode", "submit"], mock_server)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(rows(tmp_path, "orders")) == 3
    assert any(e["kind"] == "liquidate_done" and e["trader"] == "lq_a" for e in rows(tmp_path, "events"))
    assert state(tmp_path, "lq_a")["holdings"] == {}


def test_paused_flag_skips_the_trader_and_the_budget_cap_counts_only_buyers(tmp_path, mock_server):
    tdir, cdir = setup(tmp_path)
    control.set_flag(cdir, "lq_a", "paused", "akira", "入金待ち")
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 2, "QQQ": 1}})
    before = state(tmp_path, "lq_a")
    # 2 人で $4,000 だが上限 $2,000: 停止の人は数えないので通る
    r = run(tmp_path, tdir, ["--traders", "lq_a,ac_b", "--mode", "submit", "--max-total-budget", "2000"], mock_server)
    assert r.returncode == 0, r.stdout + r.stderr
    orders = rows(tmp_path, "orders")
    assert [(o["parts"][0]["trader"], o["symbol"], o["side"]) for o in orders] == [("ac_b", "SPY", "buy")]
    assert {k: v for k, v in state(tmp_path, "lq_a").items() if k != "last_date"} == {k: v for k, v in before.items() if k != "last_date"}
    assert not [s for s in rows(tmp_path, "signals") if s["trader"] == "lq_a"]
    assert any(e["kind"] == "paused" and e["trader"] == "lq_a" and e["reason"] == "入金待ち" for e in rows(tmp_path, "events"))
    # 印を消すと普通に戻る（上限は 2 人ぶん要る）
    control.clear_flag(cdir, "lq_a")
    r = run(tmp_path, tdir, ["--traders", "lq_a,ac_b", "--mode", "submit", "--max-total-budget", "2000"], mock_server)
    assert r.returncode == 2 and "予算の合計" in r.stderr


def test_halt_wins_over_the_flags(tmp_path, mock_server):
    tdir, cdir = setup(tmp_path)
    control.set_flag(cdir, "lq_a", "liquidate", "akira")
    (tmp_path / "HALT").write_text("x")
    r = run(tmp_path, tdir, ["--traders", "lq_a,ac_b", "--mode", "submit"], mock_server)
    assert r.returncode == 3 and "停止フラグ" in r.stderr
    assert state(tmp_path, "lq_a")["holdings"] != {} and control.read_flag(cdir, "lq_a").done is None


def test_broken_flag_refuses_to_start(tmp_path, mock_server):
    tdir, cdir = setup(tmp_path)
    os.makedirs(cdir)
    open(os.path.join(cdir, "lq_a.json"), "w").write("{broken")
    r = run(tmp_path, tdir, ["--traders", "lq_a,ac_b", "--mode", "submit"], mock_server)
    assert r.returncode == 2 and "印を読めない" in r.stderr


def test_short_position_is_carried_to_the_next_day(tmp_path, mock_server):
    tdir, cdir = setup(tmp_path)
    control.set_flag(cdir, "lq_a", "liquidate", "akira")
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 1, "QQQ": 1}})       # 売買履歴は SPY 2 株なのに口座は 1 株
    r = run(tmp_path, tdir, ["--traders", "lq_a", "--mode", "submit"], mock_server)
    assert r.returncode == 1
    assert [(o["symbol"], o["final_status"]) for o in rows(tmp_path, "orders")] == [("QQQ", "Filled")]
    assert set(state(tmp_path, "lq_a")["holdings"]) == {"SPY"}
    kinds = [e["kind"] for e in rows(tmp_path, "events")]
    assert "position_short" in kinds and "blocked_symbol" in kinds and "liquidate_pending" in kinds and "liquidate_complete" not in kinds
    assert control.read_flag(cdir, "lq_a").done is None


def test_no_flags_means_the_old_path(tmp_path, mock_server):
    """印の無い人は今までどおり（合図で売買・予算の上限は全員ぶん）。"""
    tdir, cdir = setup(tmp_path)
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 2, "QQQ": 1}})
    r = run(tmp_path, tdir, ["--traders", "lq_a,ac_b", "--mode", "submit"], mock_server)
    assert r.returncode == 0, r.stdout + r.stderr
    orders = rows(tmp_path, "orders")
    # lq_a: SPY は持っていて買い 80 ＞ 50 → hold・QQQ は出口 70 ＞ 50 → 売り ／ ac_b: SPY 買い
    assert [(o["parts"][0]["trader"], o["symbol"], o["side"]) for o in orders] == [("lq_a", "QQQ", "sell"), ("ac_b", "SPY", "buy")]
    assert "flags" not in [e for e in rows(tmp_path, "events") if e["kind"] == "start"][0]
    assert not os.path.exists(cdir)
