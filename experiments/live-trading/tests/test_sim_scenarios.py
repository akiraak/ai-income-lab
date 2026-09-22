"""筋書き（故障の注入）: モックと運転手が故障を起こしたとき、執行器が決めごとどおりに振る舞い、記録に残ること。

毎日ちょうど 1 件の注文が出る合図（持っていなければ買う・持っていれば売る）を置き、何日目に何が起きるかを固定する。最速で流す。
⚠ 拒否の文面・コードは想像で作ったもの。確かめるのは自分のコードの振る舞い（再送する ／ しない・取消・状態が進まない）。
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import mode as modes
import simclock
import simdata
import simrun
from tests.test_simrun import make_bars
from tests._records import doc, isdir, jsonl

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")

CONFIG = '''name = "simf"
traders = ["sim_a"]
start = "2026-10-01"
end = "2026-12-31"
source_start = "2026-06-01"
speed = "max"
'''
EVENTS = [(1, "session_offline", 2), (2, "http_429", 3), (3, "http_5xx", 1), (4, "reject_funds", 1), (5, "no_fill", 1), (6, "auth_5xx", 1),
          (7, "auth_401", 1), (8, "skip_day", None), (9, "crash_mid", None), (10, "session_offline", 5), (12, "position_loss", None)]
DAYS = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15",
        "2026-10-16", "2026-10-19"]


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("scn")
    base, conf = tmp / "lt", tmp / "conf"
    base.mkdir(), conf.mkdir()
    (conf / "simf.toml").write_text(CONFIG + "".join(f'[[events]]\nday = {d}\nkind = "{k}"\n' + (f"times = {n}\n" if n else "") + ('symbol = "T"\nshares = 100\n' if k == "position_loss" else "") for d, k, n in EVENTS))
    data_dir = make_bars(tmp / "bars")
    child = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}
    child.update(LT_MODE_DIR=str(base), LT_SIM_DATA_DIR=data_dir, LT_SIM_CONFIG_DIR=str(conf))
    old = os.environ.get("LT_MODE_DIR")
    os.environ["LT_MODE_DIR"] = str(base)
    try:
        modes.switch("sim", "simf", by="test")
        root = modes.sim_root("simf")
        simdata.write_tree(root, simdata.load_config("simf", str(conf)), os.path.join(HERE, "config", "traders"), data_dir=data_dir)
        # 毎日 1 件: T だけ「持っていなければ買う・持っていれば売る」。ほかの銘柄は動かさない
        days = [d.isoformat() for d in simdata.sim_days(simdata.load_config("simf", str(conf)))]
        with open(os.path.join(root, "config", "signals", "sim_m20.csv"), "w") as f:
            f.write("date,symbol,buy,exit\n" + "".join(f"{d},T,100,100\n{d},VZ,0,0\n{d},BAC,0,0\n" for d in days))
        simclock.init_control(modes.control_file("simf"), datetime(2026, 10, 1, 9, 30, tzinfo=ET), speed="max")
        r = subprocess.run([sys.executable, os.path.join(HERE, "simrun.py"), "simf", "--days", "13"], capture_output=True, text=True, env=child, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        return root, r.stdout
    finally:
        if old is None:
            os.environ.pop("LT_MODE_DIR", None)
        else:
            os.environ["LT_MODE_DIR"] = old


def rows(root, day, kind):
    return jsonl(os.path.join(root, "out", day, f"{kind}.jsonl"))


def the_order(root, day):
    got = rows(root, day, "orders")
    assert len(got) == 1, (day, got)
    return got[0]


def held(root, day):
    return list(next(r for r in rows(root, day, "ledger") if r["trader"] == "sim_a")["holdings"])


def test_session_offline_is_resent_within_the_window(ran):
    o = the_order(ran[0], DAYS[0])
    assert o["final_status"] == "Filled" and o["attempts"] == 3 and o["side"] == "buy"
    assert [t["status"] for t in o["transitions"] if "retry" in t["status"]] == ["retry:422 preflight_check_failure"] * 2
    assert held(ran[0], DAYS[0]) == ["T"]


def test_429_is_resent_after_a_wait_then_gives_up_and_state_does_not_advance(ran):
    """2026-09-21: 429 は待って再送する（再送の前に重複を探す）。3 回とも 429 なら、最後に external-identifier で探して無ければ error。"""
    o = the_order(ran[0], DAYS[1])
    assert o["final_status"] == "error" and o["error"]["status"] == 429 and o["attempts"] == 3 and o["side"] == "sell"
    assert [t["status"] for t in o["transitions"] if "retry" in t["status"]] == ["retry:429 too_many_requests"] * 2
    assert held(ran[0], DAYS[1]) == ["T"]                                     # 売れていない ＝ 持ったまま


def test_5xx_is_resent(ran):
    o = the_order(ran[0], DAYS[2])
    assert o["final_status"] == "Filled" and o["attempts"] == 2 and o["side"] == "sell" and held(ran[0], DAYS[2]) == []


def test_insufficient_funds_is_an_error_without_resend(ran):
    o = the_order(ran[0], DAYS[3])
    assert o["final_status"] == "error" and o["error"]["status"] == 422 and o["attempts"] == 1 and held(ran[0], DAYS[3]) == []
    assert [p["trader"] for p in o["parts"]] == ["sim_a"]                     # 誰の分が失敗したかが残る


def test_unfilled_market_order_is_cancelled_after_the_full_wait(ran):
    o = the_order(ran[0], DAYS[4])
    assert o["cancelled"] is True and o["final_status"] == "Cancelled" and o["fills"] == [] and held(ran[0], DAYS[4]) == []
    ev = rows(ran[0], DAYS[4], "events")
    start, end = datetime.fromisoformat(ev[0]["now_et"]), datetime.fromisoformat(ev[-1]["now_et"])
    assert (end - start).total_seconds() >= 600                               # ⚠ 取消までの 600 秒は縮めない（仮の時計の上で待つ）
    assert (start.hour, start.minute) == (15, 45) and (end.hour, end.minute) < (16, 5)   # 15:45 に起きて、発注できる時間帯（〜16:05）の中で片付く


def test_auth_5xx_waits_once_and_recovers(ran):
    kinds = [e["kind"] for e in rows(ran[0], DAYS[5], "events")]
    assert "auth_5xx_retry" in kinds and kinds[-1] == "end" and the_order(ran[0], DAYS[5])["final_status"] == "Filled"


def test_auth_401_is_not_retried(ran):
    kinds = [e["kind"] for e in rows(ran[0], DAYS[6], "events")]
    assert kinds == ["start", "auth_failed"] and rows(ran[0], DAYS[6], "orders") == []


def test_skipped_day_leaves_no_record(ran):
    assert not isdir(os.path.join(ran[0], "out", DAYS[7]))
    status = json.load(open(os.path.join(ran[0], "sim", "status.json")))
    assert status["rcs"][f"{DAYS[7]} 15:45"] == "skipped"


def test_crash_after_submit_leaves_a_start_without_an_end(ran):
    """発注の後・記録の前に落ちた日: start だけが残り、注文の記録も状態の更新も無い ＝ 口座と台帳が食い違う日として後から見つけられる。"""
    kinds = [e["kind"] for e in rows(ran[0], DAYS[8], "events")]
    assert "start" in kinds and "end" not in kinds and rows(ran[0], DAYS[8], "orders") == [] and rows(ran[0], DAYS[8], "ledger") == []
    assert json.load(open(os.path.join(ran[0], "sim", "status.json")))["rcs"][f"{DAYS[8]} 15:45"] == 137


def test_the_day_after_the_crash_recovers_the_fill_and_does_not_trade_twice(ran):
    """段 1: 落ちた日の売りは口座では約定している。次の日の起動で控えから戻し、その人の台帳に入れる ＝ もう一度売らない。"""
    ev = [e for e in rows(ran[0], DAYS[9], "events") if e["kind"] == "journal_recovered"]
    assert len(ev) == 1 and ev[0]["outcome"] == "recovered_filled" and ev[0]["side"] == "sell" and ev[0]["intent_date"] == DAYS[8] and ev[0]["trader"] == "sim_a"
    assert the_order(ran[0], DAYS[9])["side"] == "buy"                          # 売れていたことが台帳に入ったので、次は買い（二重の売りではない）
    hist = doc(os.path.join(ran[0], "state", "cert", "sim_a.json"))["history"]
    recovered = [h for h in hist if h.get("note") == "recovered"]
    assert len(recovered) == 1 and recovered[0]["date"] == DAYS[8] and recovered[0]["side"] == "sell" and recovered[0]["fee"] > 0


def test_lost_position_blocks_the_symbol_until_a_human_reconciles(ran):
    """段 2: 12 日目に口座から T が消える（人が口座を直接触った代役）→ T は売買しない。ほかは止めない。段 3 で人が合わせるまで続く。"""
    for d in (DAYS[11], DAYS[12]):
        ev = rows(ran[0], d, "events")
        assert [e["symbol"] for e in ev if e["kind"] == "position_short"] == ["T"] and rows(ran[0], d, "orders") == []
        assert any(e["kind"] == "blocked_symbol" and e["symbol"] == "T" for e in ev) and ev[-1]["blocked_symbols"] == ["T"]


def test_session_offline_gives_up_after_three_attempts(ran):
    o = the_order(ran[0], DAYS[9])
    assert o["final_status"] == "error" and o["attempts"] == 3 and "Session offline" in json.dumps(o["error"])
    nxt = the_order(ran[0], DAYS[10])                                         # 残りの 2 回は次の注文が受けて、3 回目で通る
    assert nxt["final_status"] == "Filled" and nxt["attempts"] == 3


def test_sell_fee_lands_in_the_traders_ledger(ran):
    o = the_order(ran[0], DAYS[2])                                            # 3 日目は売りが約定した日
    assert o["side"] == "sell" and o["amounts"]["fee_usd"] > 0 and o["amounts"]["fee_breakdown"]["regulatory-fees"] == f"{o['amounts']['fee_usd']:.2f}"
    assert o["amounts"]["net_usd"] == round(o["amounts"]["gross_usd"] - o["amounts"]["fee_usd"], 4)
    led = next(r for r in rows(ran[0], DAYS[2], "ledger") if r["trader"] == "sim_a")
    assert led["fees_usd"] == o["amounts"]["fee_usd"]
    assert the_order(ran[0], DAYS[1])["amounts"]["fee_usd"] == 0.0            # 429 で通らなかった売りには手数料が付かない


def test_scenario_log_and_marks(ran):
    log = jsonl(os.path.join(ran[0], "sim", "scenario.jsonl"))
    assert [(r["day_index"], r["events"][0]["kind"]) for r in log] == [(d, k) for d, k, _ in EVENTS]
    every = [r for d in DAYS for kind in ("events", "orders", "ledger") for r in rows(ran[0], d, kind)]
    assert every and all(r["sim"] is True and r["mock"] is True and r["test"] is True for r in every)


def test_drawdown_lowers_quotes_and_stays_down():
    cfg = simdata.SimConfig("x", ["sim_a"], None, None, None, events=[{"day": 5, "kind": "drawdown", "days": 10, "pct_per_day": -3.0}])
    d = simrun.Driver.__new__(simrun.Driver)
    d.cfg = cfg
    assert d.quote_factor(4) == 1.0 and d.quote_factor(5) == pytest.approx(0.97) and d.quote_factor(14) == pytest.approx(0.97 ** 10)
    assert d.quote_factor(30) == pytest.approx(0.97 ** 10) and 0.97 ** 10 < 0.80          # 戻さない・20% を超えて下がる


def test_unknown_scenario_is_refused(tmp_path):
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "simf.toml").write_text(CONFIG + '[[events]]\nday = 1\nkind = "meteor"\n')
    base = tmp_path / "lt"
    base.mkdir()
    (base / "MODE").write_text(json.dumps({"mode": "sim", "name": "simf"}))
    child = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}
    child.update(LT_MODE_DIR=str(base), LT_SIM_CONFIG_DIR=str(conf))
    r = subprocess.run([sys.executable, os.path.join(HERE, "simrun.py"), "simf"], capture_output=True, text=True, env=child, timeout=60)
    assert r.returncode == 2 and "筋書きが読めない" in r.stderr
