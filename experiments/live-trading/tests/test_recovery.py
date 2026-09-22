"""口座の建玉と台帳の帳尻（§0-8）: 控え・復元・突き合わせ・人が合わせる CLI。ネットワークは使わない。"""
import json
import os
import subprocess
import sys

import pytest

import mode as modes
import recovery
from journal import Journal
from plan import NetOrder
from state import TraderState, load_state, save_state
import livefs
from tests._records import exists, jsonl, put_jsonl

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def order(trader="T1", symbol="KO", side="buy", shares=2):
    return NetOrder(symbol, side, shares, shares * 70.0, "shares", [{"trader": trader, "shares": shares, "usd": shares * 70.0}])


class Broker:
    def __init__(self, orders=None, today=None, fail=False):
        self.orders, self.today, self.fail, self.cancelled = orders or {}, today or [], fail, []

    def get_order(self, acct, oid):
        if self.fail:
            raise RuntimeError("502")
        return self.orders[oid]

    def find_order_by_external_id(self, acct, ext):
        return next((o for o in self.today if o.get("external-identifier") == ext), None)

    def cancel_order(self, acct, oid):
        self.cancelled.append(oid)
        self.orders[oid]["status"] = "Cancelled"


def filled(qty, price, status="Filled", **extra):
    return {"id": 7, "status": status, "legs": [{"fills": [{"quantity": str(qty), "fill-price": str(price)}] if qty else []}], **extra}


def test_journal_lists_only_unfinished(tmp_path):
    j = Journal(str(tmp_path), "2026-10-01")
    j.intent("lt-a", order(), 0.0), j.submitted("lt-a", 7), j.done("lt-a", "Filled")
    j.intent("lt-b", order("T2", "VZ", "sell", 3), 0.02), j.submitted("lt-b", 8)
    j.intent("lt-c", order("T3", "T"), 0.0)
    j.done("lt-zzz", "halted")                                        # 控えていない ID の done は書かない
    livefs.append(j.path, '{"op": "intent", "ext": "lt-bro')            # 書いている途中で落ちた行（ファイルの頃に取り込んだもの）
    got = Journal(str(tmp_path)).unfinished()
    assert [(e["ext"], e.get("order_id"), e["trader"], e["fee_usd"]) for e in got] == [("lt-b", 8, "T2", 0.02), ("lt-c", None, "T3", 0.0)]


def test_recover_applies_the_fill_to_the_right_trader(tmp_path):
    sd = str(tmp_path)
    st = TraderState("T2")
    st.apply_buy("VZ", 3, 47.0, "2026-09-30")
    save_state(sd, st)
    j = Journal(sd, "2026-10-01")
    j.intent("lt-b", order("T2", "VZ", "sell", 3), 0.02), j.submitted("lt-b", 8)
    ev, unresolved = recovery.recover_unfinished(Journal(sd), Broker({8: filled(3, 48.5)}), "ACCT", sd, "2026-10-02", write=True)
    assert unresolved == set() and ev[0]["kind"] == "journal_recovered" and ev[0]["outcome"] == "recovered_filled" and ev[0]["fee_usd"] == 0.02
    after = load_state(sd, "T2")
    assert "VZ" not in after.holdings and after.realized_usd == pytest.approx(4.5) and after.fees_usd == 0.02
    assert after.history[-1] == {"date": "2026-10-01", "symbol": "VZ", "side": "sell", "shares": 3.0, "price": 48.5, "fee": 0.02, "note": "recovered"}
    assert Journal(sd).unfinished() == []                              # 閉じたので 2 度は入れない
    assert recovery.recover_unfinished(Journal(sd), Broker(), "ACCT", sd, "2026-10-02", write=True) == ([], set())


def test_recover_without_writing_in_plan_or_dry_run(tmp_path):
    sd = str(tmp_path)
    j = Journal(sd, "2026-10-01")
    j.intent("lt-a", order(), 0.0), j.submitted("lt-a", 7)
    ev, _ = recovery.recover_unfinished(Journal(sd), Broker({7: filled(2, 70.1)}), "ACCT", sd, "2026-10-02", write=False)
    assert ev[0]["applied"] is False and not exists(os.path.join(sd, "T1.json")) and len(Journal(sd).unfinished()) == 1


def test_recover_closes_orders_that_never_filled_or_never_left(tmp_path):
    sd = str(tmp_path)
    j = Journal(sd, "2026-10-02")
    j.intent("lt-a", order(), 0.0), j.submitted("lt-a", 7)             # 取り消されていた
    j.intent("lt-c", order("T3", "T"), 0.0)                             # 今日・注文番号なし・今日の注文に無い ＝ 相手に届く前に落ちた
    j.intent("lt-w", order("T1", "BAC"), 0.0), j.submitted("lt-w", 9)  # 働いたまま残っている → 取り消してから読む
    broker = Broker({7: filled(0, 0, "Cancelled"), 9: filled(0, 0, "Live", id=9)})
    ev, unresolved = recovery.recover_unfinished(Journal(sd), broker, "ACCT", sd, "2026-10-02", write=True)
    assert unresolved == set() and sorted(e["outcome"] for e in ev) == ["not_submitted", "recovered_no_fill", "recovered_no_fill"]
    assert broker.cancelled == [9] and Journal(sd).unfinished() == [] and not exists(os.path.join(sd, "T1.json"))


def test_recover_leaves_what_it_cannot_look_up_and_blocks_the_symbol(tmp_path):
    sd = str(tmp_path)
    j = Journal(sd, "2026-10-01")
    j.intent("lt-c", order("T3", "T"), 0.0)                             # 前の日・注文番号なし ＝ 出たかどうか確かめる手が無い
    j.intent("lt-a", order(), 0.0), j.submitted("lt-a", 7)             # 照会が失敗する
    ev, unresolved = recovery.recover_unfinished(Journal(sd), Broker(fail=True), "ACCT", sd, "2026-10-02", write=True)
    assert unresolved == {"T", "KO"} and [e["kind"] for e in ev] == ["journal_unresolved"] * 2 and len(Journal(sd).unfinished()) == 2


def test_check_positions_sums_every_trader_and_separates_more_from_less():
    a, b = TraderState("T1"), TraderState("T2")
    a.apply_buy("KO", 2, 70, "d"), b.apply_buy("KO", 3, 70, "d"), b.apply_buy("VZ", 1.0004, 47, "d"), a.apply_buy("T", 4, 25, "d")
    positions = [{"symbol": "KO", "quantity": "5", "quantity-direction": "Long"}, {"symbol": "VZ", "quantity": "1.0", "quantity-direction": "Long"},
                 {"symbol": "AAPL", "quantity": "10", "quantity-direction": "Long"}, {"symbol": "SPY", "quantity": "1", "quantity-direction": "Long"}]
    outside, short = recovery.check_positions(positions, {"T1": a, "T2": b}, {"KO", "SPY", "T"})
    assert set(short) == {"T"} and short["T"]["diff"] == -4 and short["T"]["holders"] == {"T1": 4}
    assert set(outside) == {"SPY"} and outside["SPY"]["diff"] == 1            # AAPL はトレーダーの銘柄ではない ＝ 見ない。VZ は丸めの差の内


# ---------- 段 3: 人が合わせる CLI

@pytest.fixture
def cli(tmp_path):
    sd = tmp_path / "state" / "prod"
    sd.mkdir(parents=True)
    st = TraderState("T2")
    st.apply_buy("KO", 3, 70.0, "2026-09-30")
    save_state(str(sd), st)
    save_state(str(sd), TraderState("T1"))

    def run(*args):
        env = {**os.environ, "LT_MODE_DIR": str(tmp_path), "LT_STATE_DIR": str(sd), "LT_OUT_DIR": str(tmp_path / "out")}
        return subprocess.run([sys.executable, os.path.join(HERE, "reconcile.py"), "--env", "prod", *args], capture_output=True, text=True, env=env)
    return run, str(sd), tmp_path


def test_cli_add_remove_and_log(cli):
    run, sd, _ = cli
    assert run("add", "T1", "ko", "2", "--price", "70.15", "--reason", "落ちた日の買い").returncode == 0
    assert load_state(sd, "T1").holdings["KO"].shares == 2 and load_state(sd, "T1").history[-1]["note"] == "reconcile"
    assert run("remove", "T2", "KO", "3").returncode == 0                       # 原価で消す ＝ 損益 0
    t2 = load_state(sd, "T2")
    assert "KO" not in t2.holdings and t2.realized_usd == 0 and t2.pending_settlement == [] and t2.history[-1]["side"] == "remove"
    assert run("remove", "T1", "KO", "1", "--price", "71.15").returncode == 0   # 売れていた値段が分かっているとき
    assert load_state(sd, "T1").realized_usd == pytest.approx(1.0) and load_state(sd, "T1").holdings["KO"].shares == 1
    assert run("remove", "T1", "KO", "9").returncode == 2 and run("add", "nobody", "KO", "1", "--price", "1").returncode == 2
    log = jsonl(os.path.join(sd, "reconcile.log"))
    assert [(r["cmd"], r["trader"], r["before"], r["after"]) for r in log] == [("add", "T1", 0.0, 2.0), ("remove", "T2", 3.0, 0.0), ("remove", "T1", 2.0, 1.0)]


def test_cli_resolve_and_show(cli):
    run, sd, tmp = cli
    j = Journal(sd, "2026-10-01")
    j.intent("lt-x", order("T1", "KO", "buy", 2), 0.0)
    j.intent("lt-y", order("T2", "KO", "sell", 3), 0.02)
    out = tmp / "out" / "2026-10-01"
    put_jsonl(out / "positions.jsonl", [{"date": "2026-10-01", "when": "before", "positions": [{"symbol": "KO", "quantity": "2", "quantity-direction": "Long"}]}])
    r = run("show")
    assert r.returncode == 1 and "控えの未完: 2 件" in r.stdout and "口座が少ない" in r.stdout and "lt-x" in r.stdout
    assert run("resolve", "lt-x", "--filled", "2").returncode == 2             # 価格が要る
    assert run("resolve", "lt-x", "--filled", "2", "--price", "70.2").returncode == 0
    assert run("resolve", "lt-y", "--not-filled").returncode == 0
    assert run("resolve", "lt-y", "--not-filled").returncode == 2             # もう閉じている
    assert load_state(sd, "T1").holdings["KO"].shares == 2 and load_state(sd, "T2").holdings["KO"].shares == 3 and Journal(sd).unfinished() == []


def test_cli_respects_the_lock_and_the_mode(cli, monkeypatch):
    run, sd, tmp = cli
    monkeypatch.setenv("LT_MODE_DIR", str(tmp))
    lock = modes.RunLock()
    assert lock.acquire("real", "run_day")
    assert run("add", "T1", "KO", "1", "--price", "70").returncode == 6        # 執行器が動いている間は触らない
    lock.release()
    modes.switch("sim", "sim1", real_trading_will_stop=True)   # 本物の建玉が残っているので追加の確認が要る（mode.py）
    r = run("add", "T1", "KO", "1", "--price", "70")                           # シミュレーションモードで本物の台帳は触らない
    assert r.returncode == 2 and "sim" in r.stderr and "KO" not in load_state(sd, "T1").holdings
