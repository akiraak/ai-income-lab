"""実売買の画面（/live）。

⚠ **この画面の約束は 2 つ — 執行器が書いたものを写すだけ（数え直さない）、発注は画面から出さない（POST が無い）。**
差 1（合図時の気配 → 約定）だけは画面で bp に直す（記録には価格しか無い）。正 ＝ 不利。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import live as lv
from app.main import create_app

TRADER_TOML = """
name = "test_a"
test = true
budget_usd = 30.0
symbols = ["T"]
combine = "asis"
threshold = 50.0
sizing = "shares"
note = "試験用"
[[models]]
kind = "file"
name = "manual"
path = "../signals/test_a.csv"
"""

REAL_TOML = """
name = "T1"
budget_usd = 300.0
universe = "us63"
combine = "mean"
threshold = 55.0
sizing = "notional"
[[models]]
kind = "experiment"
name = "trade_own_ridge_a"
[[models]]
kind = "experiment"
name = "trade_ownex_lgbm_a"
"""


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build_live_dir(live: Path, *, with_real: bool = False) -> Path:
    (live / "config" / "traders").mkdir(parents=True)
    (live / "config" / "traders" / "test_a.toml").write_text(TRADER_TOML, encoding="utf-8")
    if with_real:
        (live / "config" / "traders" / "T1.toml").write_text(REAL_TOML, encoding="utf-8")
    (live / "state" / "prod").mkdir(parents=True)
    (live / "state" / "prod" / "test_a.json").write_text(json.dumps({
        "name": "test_a", "holdings": {"T": {"shares": 1, "avg_price": 25.5, "opened": "2026-09-18"}},
        "realized_usd": 0.0, "fees_usd": 0.0, "pending_settlement": [], "last_date": "2026-09-18",
        "history": [{"date": "2026-09-18", "symbol": "T", "side": "buy", "shares": 1, "price": 25.5}]}), encoding="utf-8")
    base = {"date": "2026-09-18", "env": "prod", "run_id": "20260918T195500Z"}
    _jsonl(live / "out" / "2026-09-18" / "events.jsonl", [
        {**base, "kind": "start", "mode": "submit", "traders": ["test_a"], "test": True},
        {**base, "kind": "end", "orders": 1, "bad": 0, "fills": 1},
    ])
    _jsonl(live / "out" / "2026-09-18" / "signals.jsonl", [
        {**base, "trader": "test_a", "symbol": "T", "buy": 100.0, "exit": 0.0, "inputs": [["manual", 100.0, 0.0]], "position_before": 0, "test": True},
    ])
    _jsonl(live / "out" / "2026-09-18" / "orders.jsonl", [
        {**base, "symbol": "T", "side": "buy", "sizing": "shares", "shares": 1, "value_usd": 25.4, "parts": [{"trader": "test_a", "shares": 1, "usd": 25.4}],
         "external_id": "lt-abc", "mode": "submit", "quote_at_signal": {"bid": 25.39, "ask": 25.41, "last": 25.4, "mid": 25.4},
         "dry_run": {"fee-calculation": {"total-fees": "0.0"}}, "submitted": {"order_id": 123, "status": "Received"},
         "transitions": [{"at_ms": 10, "status": "retry:502 non_json_response"}, {"at_ms": 900, "status": "Filled"}], "final_status": "Filled",
         "fills": [{"symbol": "T", "side": "buy", "shares": 1, "price": 25.5, "filled_at": "t", "order_id": 123, "fee_usd": 0.0}],
         "attempts": 2, "cancelled": False, "error": None, "elapsed_ms": 950.0, "test": True},
    ])
    _jsonl(live / "out" / "2026-09-18" / "ledger.jsonl", [
        {**base, "trader": "test_a", "test": True, "budget_usd": 30.0, "cost_in_use_usd": 25.5, "market_value_usd": 25.4,
         "unrealized_usd": -0.1, "realized_usd": 0.0, "fees_usd": 0.0, "unsettled_usd": 0.0, "holdings": {}, "holdings_priced": 1, "drawdown_pct_of_budget": 0.33},
    ])
    _jsonl(live / "out" / "2026-09-18" / "balances.jsonl", [
        {**base, "when": "before", "balances": {"cash-balance": "1000.0", "equity-buying-power": "1000.0", "account-number": "<account:masked>"}},
    ])
    # 前日: dry-run だけの日（約定なし）
    prev = {"date": "2026-09-17", "env": "cert", "run_id": "20260917T195500Z"}
    _jsonl(live / "out" / "2026-09-17" / "events.jsonl", [{**prev, "kind": "start", "mode": "dry-run", "traders": ["test_a"], "test": True}])
    _jsonl(live / "out" / "2026-09-17" / "orders.jsonl", [
        {**prev, "symbol": "T", "side": "buy", "sizing": "shares", "shares": 1, "value_usd": 25.4, "parts": [{"trader": "test_a", "shares": 1, "usd": 25.4}],
         "external_id": "lt-def", "mode": "dry-run", "quote_at_signal": {"bid": 25.39, "ask": 25.41, "last": 25.4, "mid": 25.4},
         "dry_run": None, "submitted": None, "transitions": [], "final_status": "error", "fills": [], "attempts": 0, "cancelled": False,
         "error": {"type": "ApiError", "status": 422, "code": "preflight_check_failure", "message": "One or more preflight checks failed"}, "elapsed_ms": 100.0, "test": True},
    ])
    return live


# --- 写すだけであること ---------------------------------------------------

def test_traders_are_mirrored_from_toml(settings):
    build_live_dir(settings.live_dir, with_real=True)
    d = lv.index(settings.live_dir)
    names = {t["name"]: t for t in d["traders"]}
    assert names["test_a"]["test"] and names["test_a"]["budget_usd"] == 30.0 and names["test_a"]["models"][0]["kind"] == "file"
    assert not names["T1"]["test"] and names["T1"]["combine_label"] == "平均" and names["T1"]["sizing_label"] == "金額指定"
    assert [m["name"] for m in names["T1"]["models"]] == ["trade_own_ridge_a", "trade_ownex_lgbm_a"]
    assert [t["name"] for t in d["configured"]] == ["T1"] and [t["name"] for t in d["test_traders"]] == ["test_a"]


def test_state_and_ledger_are_joined_per_env(settings):
    build_live_dir(settings.live_dir)
    t = lv.index(settings.live_dir)["traders"][0]
    assert t["states"]["prod"]["holdings"]["T"]["shares"] == 1 and t["states"]["prod"]["cost_in_use_usd"] == 25.5
    assert t["ledger"]["prod"]["unrealized_usd"] == -0.1


def test_diff1_is_positive_when_buy_fills_above_mid(settings):
    """差 1 は画面で bp に直す唯一の数字。買いは (約定 − mid)、売りは (mid − 約定)。正 ＝ 不利。"""
    build_live_dir(settings.live_dir)
    d = lv.index(settings.live_dir)
    today = d["today"]
    assert today["date"] == "2026-09-18" and today["orders"][0]["diff1_bp"] == [round((25.5 - 25.4) / 25.4 * 1e4, 2)]
    assert today["retries"] == 1 and today["n_filled"] == 1
    assert d["summary"]["diff1_n"] == 1 and d["summary"]["orders"] == 2 and d["summary"]["bad"] == 1
    sell = {"side": "sell", "quote_at_signal": {"mid": 100.0}, "fills": [{"price": 99.0, "shares": 1}]}
    assert lv._diff1_bp(sell) == [100.0]


def test_recent_days_are_newest_first_and_flag_test_and_problems(settings):
    build_live_dir(settings.live_dir)
    recent = lv.index(settings.live_dir)["recent"]
    assert [r["date"] for r in recent] == ["2026-09-18", "2026-09-17"]
    assert recent[0]["test"] and not recent[0]["mock"] and recent[0]["modes"] == ["submit"]
    assert recent[1]["n_bad"] == 1 and recent[1]["n_filled"] == 0


# --- 画面 ----------------------------------------------------------------

def test_missing_live_dir_is_ok(settings):
    assert lv.index(settings.live_dir)["empty"]
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as c:
        r = c.get("/live")
        assert r.status_code == 200 and "定義も記録も無い" in r.text
        assert c.get("/api/live").status_code == 200


def test_page_renders_and_has_no_secrets(settings):
    build_live_dir(settings.live_dir)
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as c:
        r = c.get("/live")
        assert r.status_code == 200
        assert "test_a" in r.text and "2026-09-18" in r.text and "Filled" in r.text
        assert "属性はまだ設定していない" in r.text        # 実際に動かすトレーダーが 0 人
        assert "5WT" not in r.text and "eyJ" not in r.text
        j = c.get("/api/live").json()
        assert j["summary"]["filled"] == 1


def test_public_face_can_read_but_not_post(settings):
    """公開面（cloudflare。ループバックは JWT 免除）でも /live は読める。発注の経路は無い（POST は 405）。"""
    build_live_dir(settings.live_dir)
    settings.auth_mode = "cloudflare"
    settings.cf_team, settings.cf_aud, settings.cf_email = "team", "aud", "a@example.com"
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as c:
        assert c.get("/live").status_code == 200
        assert c.post("/live").status_code == 405
        assert c.post("/api/live").status_code == 405
