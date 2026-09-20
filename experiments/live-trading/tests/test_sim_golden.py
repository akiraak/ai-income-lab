"""通し運転（64 営業日）の集計値の回帰（プラン: docs/plans/test-automation-sim.md Phase 2）。

⚠ **いままで通し運転は「落ちないこと」しか見ていなかった** ＝ 執行器を触って振る舞いが変わっても気づけなかった。
`sim1`（故障なし）と `sim2`（筋書きつき）を最速で通し、**出来事の種類ごとの件数・注文の数**を
`tests/golden/<名前>.json` と突き合わせる。⚠ **値が変わったら、直す前に「なぜ変わったか」を人が確かめる**
（⚠ 日足が調整し直されると値段が変わり、件数も動きうる）。更新は `AIL_UPDATE_GOLDEN=1` のときだけ。

⚠ **重い**（2 本で数十秒）ので既定では回さない: `AIL_GOLDEN=1`（＝ `./run-tests.sh --full`）のときだけ。
⚠ 日足（git 管理外）が無い機械では skip。⚠ `LT_MODE_DIR` を tmp に向けるので、本物の `MODE`・`run.lock`・記録には触らない。
"""
import json
import os
import pathlib
import subprocess
import sys
import tomllib
from collections import Counter

import pytest

import mode as modes
import simdata
from state import QTY_TOL

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(HERE, "config", "sim")
TRADERS = os.path.join(HERE, "config", "traders")
BARS = os.path.abspath(os.path.join(HERE, "..", "feature-discovery", "data", "adjusted", "d"))
GOLDEN = pathlib.Path(HERE, "tests", "golden")
SECRETS = ("MOCK-SECRET", "MOCK-REFRESH", "5WT00042", "eyJ")

pytestmark = pytest.mark.skipif(not os.environ.get("AIL_GOLDEN"),
                                reason="通し運転は重い: AIL_GOLDEN=1 で回す（./run-tests.sh --full）")


def missing_bars(name: str) -> list[str]:
    from trader import load_traders

    cfg = simdata.load_config(name, CONF)
    symbols = sorted({s for t in load_traders(cfg.traders, TRADERS) for s in t.symbols})
    return [s for s in symbols if not os.path.exists(os.path.join(BARS, f"{s}.csv"))]


@pytest.fixture(scope="module", params=["sim1", "sim2"])
def ran(request, tmp_path_factory):
    """`sim<N>` を最速で 1 回だけ通し、記録の置き場を返す。⚠ 本物の木には触らない（LT_MODE_DIR は tmp）。"""
    name = request.param
    gone = missing_bars(name)
    if gone:
        pytest.skip(f"日足が無い（git 管理外）: {' '.join(gone)}")
    base = tmp_path_factory.mktemp(f"golden_{name}")
    child = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}
    child.update(LT_MODE_DIR=str(base), LT_SIM_DATA_DIR=BARS, LT_SIM_CONFIG_DIR=CONF)
    old = os.environ.get("LT_MODE_DIR")
    os.environ["LT_MODE_DIR"] = str(base)
    try:
        modes.switch("sim", name, by="test")
        r = subprocess.run([sys.executable, os.path.join(HERE, "simrun.py"), name, "--fresh", "--speed", "max"],
                           capture_output=True, text=True, env=child, timeout=900)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
        return name, modes.sim_root(name)
    finally:
        if old is None:
            os.environ.pop("LT_MODE_DIR", None)
        else:
            os.environ["LT_MODE_DIR"] = old


def rows(root: str, day: str, kind: str) -> list[dict]:
    path = os.path.join(root, "out", day, f"{kind}.jsonl")
    return [json.loads(line) for line in open(path, encoding="utf-8")] if os.path.exists(path) else []


def days_of(root: str) -> list[str]:
    out = pathlib.Path(root, "out")
    return sorted(p.name for p in out.iterdir() if p.is_dir()) if out.is_dir() else []


def traders_of(root: str) -> dict[str, dict]:
    out = {}
    for p in sorted(pathlib.Path(root, "config", "traders").glob("*.toml")):
        with p.open("rb") as f:
            doc = tomllib.load(f)
        out[doc["name"]] = doc
    return out


def summarize(root: str) -> dict:
    """⚠ 数えるだけ（値段は入れない ＝ 日足の調整で揺れる数字を黄金値にしない）。"""
    ev, orders = Counter(), []
    for d in days_of(root):
        ev.update(r.get("kind") for r in rows(root, d, "events"))
        orders += rows(root, d, "orders")
    return {
        "days": len(days_of(root)),
        "events": dict(sorted(ev.items())),
        "orders": {
            "total": len(orders),
            "filled": sum(1 for o in orders if o.get("final_status") == "Filled"),
            "error": sum(1 for o in orders if o.get("final_status") == "error"),
            "cancelled": sum(1 for o in orders if o.get("cancelled")),
            "buy": sum(1 for o in orders if o.get("side") == "buy"),
            "sell": sum(1 for o in orders if o.get("side") == "sell"),
            "by_trader": dict(sorted(Counter(p["trader"] for o in orders for p in o.get("parts") or []).items())),
        },
    }


def account_minus_ledger(root: str) -> dict[str, float]:
    """最後の日の「口座の建玉 − 台帳の持ち分」（銘柄ごと）。⚠ 画面と同じで、執行器が書いたものを読むだけ。"""
    day = days_of(root)[-1]
    after = next((r for r in rows(root, day, "positions") if r.get("when") == "after"), None) or {}
    acct: dict[str, float] = {}
    for p in after.get("positions") or []:
        q = float(p.get("quantity", 0)) * (-1 if p.get("quantity-direction") == "Short" else 1)
        acct[p["symbol"]] = acct.get(p["symbol"], 0.0) + q
    book: dict[str, float] = {}
    for r in rows(root, day, "ledger"):
        for s, h in (r.get("holdings") or {}).items():
            book[s] = book.get(s, 0.0) + float(h.get("shares", 0))
    return {s: round(acct.get(s, 0.0) - book.get(s, 0.0), 6) for s in set(acct) | set(book)}


# --- 黄金の集計値 ---------------------------------------------------------

def test_summary_matches_the_golden_file(ran):
    """⚠ 落ちたら、直す前に「なぜ変わったか」を確かめる（更新は AIL_UPDATE_GOLDEN=1）。"""
    name, root = ran
    got = summarize(root)
    path = GOLDEN / f"{name}.json"
    if os.environ.get("AIL_UPDATE_GOLDEN"):
        GOLDEN.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(got, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        pytest.skip(f"黄金値を書き直した: {path}")
    assert path.exists(), f"{path} が無い（AIL_UPDATE_GOLDEN=1 で作る）"
    want = json.loads(path.read_text(encoding="utf-8"))
    assert got == want, ("通し運転の集計値が変わった。⚠ 直す前に理由を確かめる\n"
                         f"いま: {json.dumps(got, ensure_ascii=False)}\n前:   {json.dumps(want, ensure_ascii=False)}")


# --- 決めごとの不変条件（⚠ 値段に依らない。2026-09-19 に直した穴の回帰）----

def test_one_order_belongs_to_exactly_one_trader(ran):
    """⚠ 口座への注文はトレーダーごとに別々（合算しない・按分しない ＝ live-trading.md §0-1）。"""
    _, root = ran
    bad = [(d, o["symbol"], [p["trader"] for p in o.get("parts") or []])
           for d in days_of(root) for o in rows(root, d, "orders") if len(o.get("parts") or []) != 1]
    assert not bad, bad


def test_integer_share_traders_never_hold_fractions(ran):
    """⚠ 整数株の人に端数の持ち分ができない（按分をやめた回帰。§0-7 (j) の 1）。"""
    _, root = ran
    whole = {n for n, doc in traders_of(root).items() if doc.get("sizing", "shares") == "shares"}
    bad = [(d, r["trader"], s, h["shares"])
           for d in days_of(root) for r in rows(root, d, "ledger") if r["trader"] in whole
           for s, h in (r.get("holdings") or {}).items() if abs(float(h["shares"]) - round(float(h["shares"]))) > QTY_TOL]
    assert not bad, bad


def test_account_and_ledger_agree_except_the_injected_loss(ran):
    """口座 − 台帳 ＝ 0。⚠ `sim2` は筋書きで BAC を 100 株抜いてあるので、**BAC だけ** −100（§0-8）。"""
    name, root = ran
    diff = {s: v for s, v in account_minus_ledger(root).items() if abs(v) > QTY_TOL}
    if name == "sim1":
        assert diff == {}, diff
    else:
        assert list(diff) == ["BAC"] and diff["BAC"] == pytest.approx(-100.0, abs=0.01), diff


def test_every_row_is_marked_and_no_secret_leaks(ran):
    """全行に sim ／ mock ／ test の印・秘密が出ていない（`simctl.py check` と同じ）。"""
    _, root = ran
    n = bad = leaks = 0
    for d in days_of(root):
        for kind in ("events", "signals", "orders", "ledger", "quotes", "positions", "balances"):
            path = os.path.join(root, "out", d, f"{kind}.jsonl")
            if not os.path.exists(path):
                continue
            for line in open(path, encoding="utf-8"):
                n += 1
                r = json.loads(line)
                bad += not (r.get("sim") is True and r.get("mock") is True and r.get("test") is True)
                leaks += any(s in line for s in SECRETS)
    assert n and not bad and not leaks, (n, bad, leaks)


# --- 筋書きが効いた証拠（⚠ 静かに効かなくなるのを捕まえる）----------------

def test_scenarios_left_their_marks(ran):
    name, root = ran
    ev = Counter(k for d in days_of(root) for k in (r.get("kind") for r in rows(root, d, "events")))
    assert ev["out_of_window"] == 2, ev                     # 半日立会 11-27・12-24（暦は両方で効く）
    if name == "sim1":
        for kind in ("auth_failed", "auth_5xx_retry", "journal_recovered", "position_short", "drawdown_warning", "halted"):
            assert ev[kind] == 0, (kind, ev)                # 故障なしの基準線
        return
    assert ev["auth_failed"] == 1 and ev["auth_5xx_retry"] == 1 and ev["journal_recovered"] == 1, ev
    assert ev["drawdown_warning"] > 0 and ev["position_short"] > 0
    who = {r.get("trader") for d in days_of(root) for r in rows(root, d, "events") if r.get("kind") == "drawdown_warning"}
    assert who == {"sim_b"}, who                            # 含み損 20% 超は金額指定の人だけ（⚠ 執行器は止めない）
    sym = {r.get("symbol") for d in days_of(root) for r in rows(root, d, "events") if r.get("kind") in ("position_short", "blocked_symbol")}
    assert sym == {"BAC"}, sym                              # 口座から消えた銘柄だけ止まる（ほかは動く）
    all_days = [d.isoformat() for d in simdata.sim_days(simdata.load_config(name, CONF))]
    assert set(all_days) - set(days_of(root)), "起動しなかった日（skip_day）が無い"
