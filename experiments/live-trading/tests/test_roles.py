"""役割（買い係 ／ 売り係 ／ 損切り係 ／ 見るだけ）・側ごとの合わせ方・最短保有（プラン docs/plans/trader-model-roles.md §4。2026-10-18）。

⚠ **役割の無い人は 1 ビットも変わらない**（合図の値・意図の素・事象の形）。役割のある人だけ 買い ／ 売り ／ 損切り の 3 つの口を通る。
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import livefs  # noqa: E402
from plan import build_plan, decide, held_trading_days  # noqa: E402
from signals import Signal, SignalError, collect  # noqa: E402
from state import TraderState  # noqa: E402
from trader import ModelSpec, load_traders, parse_trader  # noqa: E402

CONF = os.path.join(HERE, "config", "traders")


def _fixed(role=None, buy=0, exit_=0, name=None):
    m = {"kind": "fixed", "name": name or f"m_{role or 'both'}", "buy": buy, "exit": exit_}
    if role is not None:
        m["role"] = role
    return m


def _doc(models, **over):
    base = {"name": "r", "budget_usd": 100.0, "symbols": ["SPY"], "models": models, "test": True}
    base.update(over)
    return base


# ---------------------------------------------------------------- trader.py

def test_role_defaults_and_existing_configs_have_no_roles():
    """`role` を書かない設定は both・役割の口を 1 つも使わない ＝ いままでの経路（直下と候補の全員）。"""
    t = parse_trader(_doc([_fixed()]))
    assert t.models[0].role == "both" and not t.has_roles and t.buy_rule == "asis" and t.exit_rule == "asis"
    assert t.min_hold_days is None and not t.stop_blocks_buy and t.combine_buy is None and t.combine_exit is None
    # ⚠ 2026-10-19 Phase 4 から役割を使うのは 候補 `candidates/horizon/`（T7〜T10）と sim 用の写し `sim_T7〜T10` だけ。ほかの全員は役割の口を使わない
    horizon = {"sim_T7", "sim_T8", "sim_T9", "sim_T10"}
    names = [f[:-5] for f in os.listdir(CONF) if f.endswith(".toml")]
    for sub in ("shares", "notional", "multi", "best"):
        d = os.path.join(CONF, "candidates", sub)
        for f in os.listdir(d):
            if f.endswith(".toml"):
                assert not load_traders([f[:-5]], d)[0].has_roles
    assert names and all(t.has_roles == (t.name in horizon) for t in load_traders(names, CONF))
    assert horizon <= set(names) and all(t.has_roles for t in load_traders(["T7", "T8", "T9", "T10"], os.path.join(CONF, "candidates", "horizon")))


def test_role_validation():
    with pytest.raises(ValueError, match="role"):
        parse_trader(_doc([_fixed("buyer")]))
    with pytest.raises(ValueError, match="買いの側"):                 # 見るだけだけ
        parse_trader(_doc([_fixed("watch")]))
    with pytest.raises(ValueError, match="買いの側"):                 # 売り係 ＋ 損切り係だけ
        parse_trader(_doc([_fixed("exit"), _fixed("stop")]))
    with pytest.raises(ValueError, match="売りの側"):                 # 買い係だけ
        parse_trader(_doc([_fixed("buy")]))
    with pytest.raises(ValueError, match="買いの側の「そのまま」"):   # 買い係 2 本に asis
        parse_trader(_doc([_fixed("buy", name="a"), _fixed("buy", name="b"), _fixed("exit")]))
    with pytest.raises(ValueError, match="売りの側の多数決"):
        parse_trader(_doc([_fixed("buy"), _fixed("exit", name="a"), _fixed("exit", name="b")], combine_exit="majority"))
    with pytest.raises(ValueError, match="合わせ方"):
        parse_trader(_doc([_fixed("buy"), _fixed("exit")], combine_buy="avg"))
    for bad in (0, -1, "10", True, 2.5):
        with pytest.raises(ValueError, match="min_hold_days"):
            parse_trader(_doc([_fixed("buy"), _fixed("exit")], min_hold_days=bad))
    with pytest.raises(ValueError, match="stop_blocks_buy"):
        parse_trader(_doc([_fixed("buy"), _fixed("exit")], stop_blocks_buy=True))
    # 通る形: 買い係 1 ・売り係 1 ・損切り係 1 ・見るだけ 1 に combine = asis（側ごとに 1 本）＋ 最短保有 10
    t = parse_trader(_doc([_fixed("buy"), _fixed("exit"), _fixed("stop"), _fixed("watch")], min_hold_days=10, combine_exit="unanimous", stop_blocks_buy=True))
    assert t.has_roles and [m.role for m in t.buy_models] == ["buy"] and [m.role for m in t.exit_models] == ["exit"]
    assert len(t.stop_models) == 1 and len(t.watch_models) == 1 and t.min_hold_days == 10 and t.exit_rule == "unanimous"
    assert not {"combine_buy", "combine_exit", "min_hold_days", "stop_blocks_buy"} & set(t.extra)
    # 買い係 ＋ 損切り係だけ（売り係なし）も通る ＝ 損切り係だけで降りる人
    assert parse_trader(_doc([_fixed("buy"), _fixed("stop")])).exit_models == ()
    # 役割を書かず最短保有だけの人も「役割のある人」（both の出口% が最短保有の中は読まれない）
    assert parse_trader(_doc([_fixed()], min_hold_days=5)).has_roles


# ---------------------------------------------------------------- signals.py

def _predict(tmp_path, rows, name="predict.jsonl"):
    p = tmp_path / name
    livefs.append_many(p, [json.dumps(r, ensure_ascii=False) for r in rows])
    return str(p)


def _exp(name, method, role=None):
    m = {"kind": "experiment", "name": name, "method": method}
    if role:
        m["role"] = role
    return m


ROWS = [
    {"date": "2026-10-06", "model": "fwd", "method": "H3 先20日", "symbol": "T", "buy": 70, "exit": 30},
    {"date": "2026-10-06", "model": "fwd", "method": "H2 先5日", "symbol": "T", "buy": 40, "exit": 60},
    {"date": "2026-10-06", "model": "stopx", "method": "X1", "symbol": "T", "buy": 100, "exit": 100},
    {"date": "2026-10-06", "model": "ridge", "method": "全部使う（基準）", "symbol": "T", "buy": 55, "exit": 45},
]


def test_rows_are_keyed_by_experiment_method_and_symbol(tmp_path):
    """買い係と売り係が同じ実験名の別の手法でも、それぞれの行を引く（いままでは (実験名, 銘柄) で後勝ち）。"""
    p = _predict(tmp_path, ROWS)
    t = parse_trader({"name": "r", "budget_usd": 100.0, "symbols": ["T"], "test": False, "min_hold_days": 20,
                      "models": [_exp("fwd", "H3 先20日", "buy"), _exp("fwd", "H2 先5日", "exit"), _exp("stopx", "X1", "stop")]})
    sigs, ev = collect([t], "2026-10-06", p)
    assert ev == [] and len(sigs) == 1
    sg = sigs[0]
    assert (sg.buy, sg.exit, sg.stop) == (70.0, 60.0, 100.0)            # 買い% ＝ 買い係・出口% ＝ 売り係・損切り ＝ 損切り係の出口%
    assert sg.inputs == (("fwd", 70.0, 30.0, "buy", "H3 先20日"), ("fwd", 40.0, 60.0, "exit", "H2 先5日"), ("stopx", 100.0, 100.0, "stop", "X1"))


def test_existing_trader_reads_its_own_method_even_when_another_method_is_present(tmp_path):
    p = _predict(tmp_path, ROWS)
    t = parse_trader({"name": "T1x", "budget_usd": 100.0, "symbols": ["T"], "models": [_exp("ridge", "全部使う（基準）")]})
    sigs, ev = collect([t], "2026-10-06", p)
    assert not t.has_roles and sigs[0].inputs == (("ridge", 55.0, 45.0),) and sigs[0].stop is None and (sigs[0].buy, sigs[0].exit) == (55.0, 45.0)
    # 2 本ある実験に method を書かない人は止まる（どれを使うか決められない）／ 1 本なら通る
    with pytest.raises(SignalError, match="method"):
        collect([parse_trader({"name": "x", "budget_usd": 1.0, "symbols": ["T"], "models": [{"kind": "experiment", "name": "fwd"}]})], "2026-10-06", p)
    one = collect([parse_trader({"name": "x", "budget_usd": 1.0, "symbols": ["T"], "models": [{"kind": "experiment", "name": "ridge"}]})], "2026-10-06", p)[0]
    assert (one[0].buy, one[0].exit) == (55.0, 45.0)
    # 設定の手法の行が無く別の手法の行だけある銘柄は止まる（設定と予測の食い違いを黙って読まない）
    with pytest.raises(SignalError, match="手法"):
        collect([parse_trader({"name": "x", "budget_usd": 1.0, "symbols": ["T"], "models": [_exp("fwd", "H4 先40日")]})], "2026-10-06", p)


def test_sides_are_separate_and_watch_does_not_change_the_signal():
    """買い係は売りに入らない・売り係 ／ 損切り係は買いに入らない・見るだけは何も変えない（events に 1 行）。"""
    base = [_fixed("buy", 80, 0), _fixed("exit", 0, 90), _fixed("stop", 100, 10, name="s1"), _fixed("stop", 100, 30, name="s2")]
    t = parse_trader(_doc(base, combine_exit="asis"))
    sigs, ev = collect([t], "2026-10-06", positions={("r", "SPY"): 0})
    assert (sigs[0].buy, sigs[0].exit, sigs[0].stop) == (80.0, 90.0, 30.0) and ev == []        # 損切りは最大
    tw = parse_trader(_doc(base + [_fixed("watch", 5, 95, name="w")]))
    sigs2, ev2 = collect([tw], "2026-10-06", positions={("r", "SPY"): 0})
    assert (sigs2[0].buy, sigs2[0].exit, sigs2[0].stop) == (80.0, 90.0, 30.0)
    assert [(e["kind"], e["model"], e["would"]) for e in ev2] == [("watch", "w", "skip")]
    sigs3, ev3 = collect([tw], "2026-10-06", positions={("r", "SPY"): 1})
    assert ev3[0]["would"] == "sell" and sigs3[0].inputs[-1] == ("w", 5.0, 95.0, "watch", None)
    assert collect([tw], "2026-10-06")[1][0]["would"] is None                                     # 持ち株が分からなければ書かない
    # 買いの側が 2 本（both ＋ buy）を mean・売りの側は both だけ
    t2 = parse_trader(_doc([_fixed(None, 60, 20, name="b"), _fixed("buy", 80, 100, name="bb")], combine_buy="mean"))
    s2 = collect([t2], "2026-10-06")[0][0]
    assert (s2.buy, s2.exit, s2.stop) == (70.0, 20.0, None)
    # 売り係が無い人（買い係 ＋ 損切り係）は 出口% 0 ＝ 損切り係だけで降りる
    s3 = collect([parse_trader(_doc([_fixed("buy", 80, 0), _fixed("stop", 100, 70)]))], "2026-10-06")[0][0]
    assert (s3.buy, s3.exit, s3.stop) == (80.0, 0.0, 70.0)


def test_missing_watch_does_not_block_but_missing_stop_does(tmp_path):
    rows = [r for r in ROWS if r["model"] != "stopx"]
    p = _predict(tmp_path, rows)
    tw = parse_trader({"name": "r", "budget_usd": 1.0, "symbols": ["T"], "models": [_exp("fwd", "H3 先20日", "buy"), _exp("fwd", "H2 先5日", "exit"), _exp("stopx", "X1", "watch")]})
    sigs, ev = collect([tw], "2026-10-06", p)
    assert len(sigs) == 1 and [e["kind"] for e in ev] == ["watch_no_signal"]
    ts = parse_trader({"name": "r", "budget_usd": 1.0, "symbols": ["T"], "models": [_exp("fwd", "H3 先20日", "buy"), _exp("fwd", "H2 先5日", "exit"), _exp("stopx", "X1", "stop")]})
    sigs, ev = collect([ts], "2026-10-06", p)
    assert sigs == [] and ev[0]["kind"] == "no_signal" and ev[0]["models"] == ["stopx"]


# ---------------------------------------------------------------- plan.py

def test_held_trading_days_follows_the_nyse_calendar():
    assert held_trading_days("2026-10-01", "2026-10-01") == 0
    assert held_trading_days("2026-10-01", "2026-10-02") == 1
    assert held_trading_days("2026-10-02", "2026-10-03") == 0                # 土曜
    assert held_trading_days("2026-09-04", "2026-09-08") == 1                # 9/5 土・9/6 日・9/7 Labor Day を数えない
    assert held_trading_days("2026-10-01", "2026-10-15") == 10
    assert held_trading_days("2026-10-15", "2026-10-01") == 0


def _roles_trader(min_hold=10, stop_blocks=False):
    return parse_trader(_doc([_fixed("buy"), _fixed("exit"), _fixed("stop")], min_hold_days=min_hold, stop_blocks_buy=stop_blocks))


def _sig(buy, exit_, stop=None, trader="r", symbol="SPY"):
    return Signal(trader, symbol, buy, exit_, ((f"m", buy, exit_),), True, stop)


def test_min_hold_mutes_the_exit_role_but_not_the_stop_role():
    t = _roles_trader(min_hold=10)
    st = TraderState("r")
    st.apply_buy("SPY", 1, 100.0, "2026-10-01")
    raw, ev = decide(t, st, [_sig(10, 100, 0)], "2026-10-12")              # 7 営業日（10/2・10/5〜9・10/12）＜ 10: 売り係の 100 でも売らない
    assert raw == [] and ev[0]["kind"] == "hold" and ev[0]["exit_muted"] and (ev[0]["held_days"], ev[0]["min_hold_days"]) == (7, 10)
    raw, ev = decide(t, st, [_sig(10, 100, 100)], "2026-10-12")            # 損切り係の 100 なら最短保有の中でも売る
    assert [r["side"] for r in raw] == ["sell"] and raw[0]["reason"] == "stop" and ev[0]["kind"] == "stop_exit" and ev[0]["held_days"] == 7
    raw, ev = decide(t, st, [_sig(10, 100, 0)], "2026-10-15")              # 10 営業日目から売り係を読む
    assert [r["side"] for r in raw] == ["sell"] and raw[0]["reason"] == "exit" and ev == []
    raw, ev = decide(t, st, [_sig(10, 40, 0)], "2026-10-15")               # 最短保有の後も出口% ≤ θ なら持つ（数字は付けない）
    assert raw == [] and ev[0]["kind"] == "hold" and "exit_muted" not in ev[0]
    with pytest.raises(ValueError, match="today"):
        decide(t, st, [_sig(10, 100, 0)])


def test_stop_blocks_buy_is_off_by_default():
    st = TraderState("r")
    raw, ev = decide(_roles_trader(stop_blocks=False), st, [_sig(80, 0, 100)], "2026-10-06")
    assert [r["side"] for r in raw] == ["buy"] and raw[0]["stop_pct"] == 100 and ev == []       # K9 (a): 損切り係が言っていても買う
    raw, ev = decide(_roles_trader(stop_blocks=True), st, [_sig(80, 0, 100)], "2026-10-06")
    assert raw == [] and ev[0]["kind"] == "stop_blocks_buy"
    raw, ev = decide(_roles_trader(stop_blocks=True), st, [_sig(80, 0, 20)], "2026-10-06")
    assert [r["side"] for r in raw] == ["buy"]
    raw, ev = decide(_roles_trader(stop_blocks=True), st, [_sig(30, 0, 100)], "2026-10-06")     # 買い% ≤ θ はただの見送り
    assert raw == [] and ev[0]["kind"] == "skip"


def test_traders_without_roles_are_bit_identical():
    """役割の無い人: today を渡しても渡さなくても同じ・意図の素と事象に新しい項目が付かない。"""
    t = parse_trader(_doc([_fixed()]))
    st = TraderState("r")
    st.apply_buy("SPY", 1, 100.0, "2026-10-01")
    for sg in (_sig(60, 40), _sig(10, 60), _sig(60, 60)):
        a, b = decide(t, st, [sg]), decide(t, st, [sg], "2026-10-02")
        assert a == b
        for d in a[0] + a[1]:
            assert not {"stop_pct", "reason", "exit_muted", "held_days", "min_hold_days"} & set(d)
    assert decide(t, TraderState("r"), [_sig(60, 40)])[0] == [{"symbol": "SPY", "side": "buy", "buy_pct": 60, "exit_pct": 40}]


@pytest.mark.parametrize("pos", [0, 1])
def test_t5_written_with_roles_decides_the_same(pos):
    """`T5`（主モデル ＋ 出口だけの損切りモデルを unanimous）を役割（both ＋ stop）で書いても、同じ合図で同じ判断。"""
    asis = parse_trader(_doc([_fixed(None, name="main"), _fixed(None, name="x1")], combine="unanimous", name="t5"))
    roles = parse_trader(_doc([_fixed(None, name="main"), _fixed("stop", name="x1")], combine="asis", name="t5"))
    st = TraderState("t5")
    if pos:
        st.apply_buy("SPY", 1, 100.0, "2026-10-01")
    from trader import combine
    for mb in (30, 50, 70):
        for me in (30, 50, 70):
            for xe in (30, 50, 70):
                b, e = combine("unanimous", [mb, 100], [me, xe], 50.0)
                old = decide(asis, st, [Signal("t5", "SPY", b, e, (), True)], "2026-10-06")
                new = decide(roles, st, [Signal("t5", "SPY", mb, me, (), True, xe)], "2026-10-06")
                assert [r["side"] for r in old[0]] == [r["side"] for r in new[0]], (pos, mb, me, xe)
                if not old[0]:                                                       # 何もしない日は同じ事象（hold ／ skip）
                    assert old[1][0]["kind"] == new[1][0]["kind"]


def test_build_plan_passes_today_to_the_state_machine():
    t = _roles_trader(min_hold=3)
    st = TraderState("r")
    st.apply_buy("SPY", 1, 100.0, "2026-10-01")
    p = build_plan([t], {"r": st}, [_sig(0, 100, 0)], {"SPY": 100.0}, "2026-10-05")     # 2 営業日 ＜ 3
    assert p.orders == [] and any(e.get("exit_muted") for e in p.events)
    p = build_plan([t], {"r": st}, [_sig(0, 100, 0)], {"SPY": 100.0}, "2026-10-06")     # 3 営業日目
    assert [(o.symbol, o.side) for o in p.orders] == [("SPY", "sell")]


# ---------------------------------------------------------------- 通し（モックサーバで run_day を 1 回。記録の形）

from tests._records import doc as _doc_of, put_doc  # noqa: E402
from tests.test_run_day_flags import DATE, free_ports, mock_server, post, rows, run, state  # noqa: E402,F401

ROLES_TOML = '''name = "rl_a"
test = true
budget_usd = 3000.0
symbols = ["SPY", "QQQ", "IWM"]
combine = "asis"
threshold = 50.0
sizing = "shares"
min_hold_days = 3
[[models]]
kind = "file"
name = "buyer"
role = "buy"
path = "../signals/buyer.csv"
[[models]]
kind = "file"
name = "exiter"
role = "exit"
path = "../signals/exiter.csv"
[[models]]
kind = "file"
name = "stopper"
role = "stop"
path = "../signals/stopper.csv"
[[models]]
kind = "file"
name = "watcher"
role = "watch"
path = "../signals/watcher.csv"
'''
PLAIN_TOML = '''name = "pl_b"
test = true
budget_usd = 1000.0
symbols = ["SPY"]
combine = "asis"
threshold = 50.0
sizing = "shares"
[[models]]
kind = "file"
name = "script"
path = "../signals/buyer.csv"
'''


def test_run_day_with_roles_records_stop_and_min_hold(tmp_path, mock_server):
    """役割のある人 1 人 ＋ 役割の無い人 1 人を同じ回で通す: 売り係は最短保有の後だけ・損切り係は毎日・見るだけは events に 1 行・記録の形。"""
    tdir, sdir = tmp_path / "traders", tmp_path / "signals"
    tdir.mkdir(), sdir.mkdir()
    (tdir / "rl_a.toml").write_text(ROLES_TOML, encoding="utf-8")
    (tdir / "pl_b.toml").write_text(PLAIN_TOML, encoding="utf-8")
    csv = {"buyer": [("SPY", 30, 70), ("QQQ", 30, 70), ("IWM", 80, 20)],
           "exiter": [("SPY", 20, 80), ("QQQ", 20, 80), ("IWM", 50, 50)],
           "stopper": [("SPY", 100, 0), ("QQQ", 100, 20), ("IWM", 100, 100)],
           "watcher": [("SPY", 90, 10), ("QQQ", 10, 90)]}
    for name, lines in csv.items():
        (sdir / f"{name}.csv").write_text("date,symbol,buy,exit\n" + "".join(f"{DATE},{s},{b},{e}\n" for s, b, e in lines), encoding="utf-8")
    st = TraderState(name="rl_a")
    st.apply_buy("SPY", 2, 100.0, "2026-10-01")        # 10/6 で 3 営業日（10/2・10/5・10/6）＝ 売り係を読む
    st.apply_buy("QQQ", 1, 200.0, "2026-10-05")        # 1 営業日 ＝ 売り係は黙る
    put_doc(os.path.join(tmp_path, "state", "cert", "rl_a.json"), st.to_dict())
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 2, "QQQ": 1}})
    r = run(tmp_path, tdir, ["--traders", "rl_a,pl_b", "--mode", "submit"], mock_server)
    assert r.returncode == 0, r.stdout + r.stderr
    orders = [(o["parts"][0]["trader"], o["symbol"], o["side"], o["final_status"]) for o in rows(tmp_path, "orders")]
    assert orders == [("rl_a", "SPY", "sell", "Filled"), ("rl_a", "IWM", "buy", "Filled")]        # QQQ は最短保有の中（損切り係 20 ≤ θ）
    sig = {(s["trader"], s["symbol"]): s for s in rows(tmp_path, "signals")}
    assert (sig[("rl_a", "SPY")]["buy"], sig[("rl_a", "SPY")]["exit"], sig[("rl_a", "SPY")]["stop"]) == (30, 80, 0)
    assert sig[("rl_a", "QQQ")]["stop"] == 20 and sig[("rl_a", "IWM")]["stop"] == 100
    assert sig[("rl_a", "SPY")]["inputs"] == [["buyer", 30, 70, "buy", None], ["exiter", 20, 80, "exit", None], ["stopper", 100, 0, "stop", None], ["watcher", 90, 10, "watch", None]]
    assert "stop" not in sig[("pl_b", "SPY")] and sig[("pl_b", "SPY")]["inputs"] == [["script", 30, 70]]       # 役割の無い人の記録の形はそのまま
    ev = rows(tmp_path, "events")
    hold = [e for e in ev if e["kind"] == "hold" and e["trader"] == "rl_a"]
    assert [(e["symbol"], e["held_days"], e["min_hold_days"], e["exit_muted"]) for e in hold] == [("QQQ", 1, 3, True)]
    assert [(e["symbol"], e["would"]) for e in ev if e["kind"] == "watch"] == [("SPY", "hold"), ("QQQ", "sell")]
    assert [e["symbol"] for e in ev if e["kind"] == "watch_no_signal"] == ["IWM"]
    after = state(tmp_path, "rl_a")["holdings"]
    assert set(after) == {"QQQ", "IWM"} and after["IWM"]["opened"] == DATE
