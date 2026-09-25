"""実売買の画面（/live）。

⚠ **この画面の約束は 2 つ — 執行器が書いたものを写すだけ（数え直さない）、発注は画面から出さない（POST が無い）。**
差 1（合図時の気配 → 約定）だけは画面で bp に直す（記録には価格しか無い）。正 ＝ 不利。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import live as lv
from app.livestore import livefs
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
    # ⚠ 執行器の記録は DB（livefs。道はそのまま）
    livefs.append_many(path, [json.dumps(r, ensure_ascii=False) for r in rows])


def build_live_dir(live: Path, *, with_real: bool = False) -> Path:
    (live / "config" / "traders").mkdir(parents=True)
    (live / "config" / "traders" / "test_a.toml").write_text(TRADER_TOML, encoding="utf-8")
    if with_real:
        (live / "config" / "traders" / "T1.toml").write_text(REAL_TOML, encoding="utf-8")
    livefs.write_doc(live / "state" / "prod" / "test_a.json", json.dumps({
        "name": "test_a", "holdings": {"T": {"shares": 1, "avg_price": 25.5, "opened": "2026-09-18"}},
        "realized_usd": 0.0, "fees_usd": 0.0, "pending_settlement": [], "last_date": "2026-09-18",
        "history": [{"date": "2026-09-18", "symbol": "T", "side": "buy", "shares": 1, "price": 25.5}]}))
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
    # 偶数個の中央値は平均なので二進の端数が出る（17.23 と 18.0 → 17.615000000000002）。画面に出す前に 0.01bp へ丸める
    assert lv._median([17.23, 18.0]) == 17.62 and lv._median([]) is None


def test_recent_days_are_newest_first_and_flag_test_and_problems(settings):
    build_live_dir(settings.live_dir)
    recent = lv.index(settings.live_dir)["recent"]
    assert [r["date"] for r in recent] == ["2026-09-18", "2026-09-17"]
    assert recent[0]["test"] and not recent[0]["mock"] and recent[0]["modes"] == ["submit"]
    assert recent[1]["n_bad"] == 1 and recent[1]["n_filled"] == 0


# --- 画面 ----------------------------------------------------------------

def test_missing_live_dir_is_ok(settings):
    assert lv.index(settings.live_dir)["empty"]
    assert lv.board(settings.live_dir)["empty"]
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        r = c.get("/")
        assert r.status_code == 200 and "定義も記録も無い" in r.text
        assert c.get("/overall").status_code == 200
        assert c.get("/api/live").status_code == 200


def test_live_redirects_to_overview(settings):
    """2026-09-18: 実売買の画面は概要（/）に移した。/live は名指しされているので転送で残す。"""
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        r = c.get("/live", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/"


def test_pages_render_and_have_no_secrets(settings):
    build_live_dir(settings.live_dir)
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        home = c.get("/")
        assert home.status_code == 200
        assert "test_a" in home.text and "2026-09-18" in home.text
        assert "属性はまだ設定していない" in home.text        # 実際に動かすトレーダーが 0 人
        assert 'href="/traders/test_a"' in home.text        # 左ペインと段からトレーダーの詳細へ
        overall = c.get("/overall")
        assert overall.status_code == 200 and "Filled" in overall.text and "preflight_check_failure" in overall.text
        tr = c.get("/traders/test_a")
        assert tr.status_code == 200 and "Filled" in tr.text
        for r in (home, overall, tr):
            assert "5WT" not in r.text and "eyJ" not in r.text
            # ⚠ 足した画面にインラインのスクリプトを入れない（CSP。停止ボタンの onsubmit は別タスクで直す）
            assert "<script>" not in r.text
        assert c.get("/traders/nobody").status_code == 404
        j = c.get("/api/live").json()
        assert j["summary"]["filled"] == 1


def test_placeholders_are_marked_and_not_in_api(settings):
    """⚠ 仮データ（紙上の損益・差 3・休場日の暦）は画面で印を付け、/api/live には出さない（本物と取り違えない）。"""
    build_live_dir(settings.live_dir)
    b = lv.board(settings.live_dir)
    assert b["placeholder"]["paper"] and b["traders"][0]["paper_pct"]
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        assert "仮データ" in c.get("/").text
        tr = c.get("/traders/test_a").text
        assert "仮データ" in tr and "紙上" in tr
        api = c.get("/api/live").text
        assert "paper" not in api and "仮データ" not in api


def test_missing_weekday_is_shown_as_not_started(settings):
    """N7: 営業日なのに執行器の記録が無い日を出す（営業日は NYSE の暦。test_holiday_is_not_a_missing_day）。"""
    build_live_dir(settings.live_dir)
    base = {"date": "2026-09-15", "env": "cert", "run_id": "20260915T195500Z"}
    _jsonl(settings.live_dir / "out" / "2026-09-15" / "events.jsonl", [{**base, "kind": "start", "mode": "dry-run", "traders": ["test_a"], "test": True}])
    b = lv.board(settings.live_dir)
    assert b["missing"] == ["2026-09-16"]                 # 09-15・09-17・09-18 はある
    assert [c["a"] for c in b["traders"][0]["grid"]["T"]][1] == "nostart"
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        assert "起動なし" in c.get("/overall").text


def test_holiday_is_not_a_missing_day(settings):
    """NYSE の休場日（2026-09-07 Labor Day・月曜）は「起動なし」に数えない。暦の中では「仮」の印も出ない。"""
    build_live_dir(settings.live_dir)
    base = {"date": "2026-09-04", "env": "cert", "run_id": "20260904T195500Z"}
    _jsonl(settings.live_dir / "out" / "2026-09-04" / "events.jsonl", [{**base, "kind": "start", "mode": "dry-run", "traders": ["test_a"], "test": True}])
    b = lv.board(settings.live_dir)
    assert "2026-09-07" not in b["bd"] and "2026-09-07" not in b["missing"]
    assert "2026-09-08" in b["missing"]                       # 休場日の翌日は営業日 ＝ 記録が無ければ起動なし
    assert b["calendar"]["covered"] and b["calendar"]["holidays"] == ["2026-09-07"] and not b["placeholder"]["calendar"]
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        top, overall = c.get("/").text, c.get("/overall").text
        assert "NYSE の休場日を除く" in top and "2026-09-07" in top and "休場日の暦の外" not in top
        assert "NYSE の休場日を除く" in overall and "休場日の暦の外" not in overall
        assert "calendar" not in c.get("/api/live").text      # 暦の情報は board() だけが持つ


def test_outside_the_calendar_falls_back_to_weekdays_and_is_marked(settings, monkeypatch):
    """暦に載っていない年は平日をすべて営業日とみなし、画面に「仮」の印を出す（⚠ 黙って平日扱いにしない）。"""
    build_live_dir(settings.live_dir)
    info = lv.calendar_info("2031-01-02", "2031-01-03")
    assert not info["covered"]
    assert lv.business_days("2031-01-01", "2031-01-03") == ["2031-01-01", "2031-01-02", "2031-01-03"]   # 元日も平日なら営業日と答える
    monkeypatch.setattr(lv, "calendar_info", lambda *a, **k: {**info, "expiring": True, "days_left": -5})
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        assert "休場日の暦の外" in c.get("/").text
        overall = c.get("/overall").text
        assert "休場日の暦の外" in overall and "nyse_calendar.py" in overall


def test_public_face_can_read_but_not_post(settings):
    """公開面（cloudflare。ループバックは JWT 免除）でも概要・全体の詳細・トレーダーの詳細は読める。発注の経路は無い（POST は 405）。"""
    build_live_dir(settings.live_dir)
    settings.auth_mode = "cloudflare"
    settings.cf_team, settings.cf_aud, settings.cf_email = "team", "aud", "a@example.com"
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        for path in ("/", "/overall", "/traders/test_a", "/api/live"):
            assert c.get(path).status_code == 200, path
            assert c.post(path).status_code == 405, path


def test_new_executor_events_count_as_problems(settings):
    """2026-09-19: 含み損の警告（執行器は止めない ＝ 人が気づけるように画面の「問題」に出す）・起動の拒否・台帳に入れられなかった約定。"""
    live = build_live_dir(settings.live_dir)
    path = live / "out" / "2026-09-18" / "events.jsonl"
    for kind in ("drawdown_warning", "refused_mode_sim", "refused_lock_busy", "ledger_error", "journal_recovered", "journal_unresolved", "position_short"):
        livefs.append(path, json.dumps({"date": "2026-09-18", "env": "prod", "kind": kind, "trader": "test_a"}))
    kinds = [e["kind"] for e in lv.day(live, "2026-09-18")["problems"]]
    assert {"drawdown_warning", "refused_mode_sim", "refused_lock_busy", "ledger_error", "journal_recovered", "journal_unresolved", "position_short"} <= set(kinds)


# --- 面ごとの期間（§13-7。2026-09-20）-------------------------------------
# ⚠ トレーダーの詳細 ＝ 全期間（1 人を縦に追う）／ 概要・全体の詳細・/api/live ＝ 直近 20 営業日（人を横に比べる）

def _one_day(live: Path, date: str, price: float, *, trader: str = "test_a", side: str = "buy") -> None:
    """1 日ぶんの記録（1 注文・約定は mid の 10bp 上 ＝ 差 1 が必ず 10.0bp になる）。"""
    base = {"date": date, "env": "prod", "run_id": date.replace("-", "") + "T195500Z"}
    _jsonl(live / "out" / date / "events.jsonl", [{**base, "kind": "start", "mode": "submit", "traders": [trader], "test": True}])
    _jsonl(live / "out" / date / "orders.jsonl", [
        {**base, "symbol": "T", "side": side, "sizing": "shares", "shares": 1, "value_usd": price,
         "parts": [{"trader": trader, "shares": 1, "usd": price}], "external_id": f"lt-{date}", "mode": "submit",
         "quote_at_signal": {"mid": price}, "submitted": {"order_id": 1, "status": "Received"}, "transitions": [],
         "final_status": "Filled", "fills": [{"symbol": "T", "side": side, "shares": 1, "price": price * 1.001, "filled_at": "t", "order_id": 1, "fee_usd": 0.0}],
         "amounts": {"gross_usd": price, "fee_usd": 0.0}, "attempts": 1, "cancelled": False, "error": None, "elapsed_ms": 100.0, "test": True}])
    _jsonl(live / "out" / date / "ledger.jsonl", [
        {**base, "trader": trader, "test": True, "budget_usd": 30.0, "cost_in_use_usd": price, "market_value_usd": price,
         "unrealized_usd": 0.0, "realized_usd": 0.0, "fees_usd": 0.0, "unsettled_usd": 0.0, "holdings": {}, "holdings_priced": 1,
         "drawdown_pct_of_budget": 0.0}])


def build_long_live_dir(live: Path, n: int) -> list[str]:
    """営業日 n 日ぶんの記録（⚠ 20 日の窓より長くする用）。戻りは古い順の日付。"""
    (live / "config" / "traders").mkdir(parents=True)
    (live / "config" / "traders" / "test_a.toml").write_text(TRADER_TOML, encoding="utf-8")
    ds = lv.business_days("2026-07-01", "2026-12-31")[:n]
    for i, d in enumerate(ds):
        _one_day(live, d, 25.0 + i)
    return ds


def test_trader_page_is_all_history_while_the_other_faces_stay_20_days(settings):
    """⚠ いちばん大事な回帰: 20 日より古い注文がトレーダーの詳細に出る（2026-09-20 まで出ていなかった）。"""
    ds = build_long_live_dir(settings.live_dir, 25)
    b20, ball = lv.board(settings.live_dir), lv.board(settings.live_dir, days=None)
    assert len(b20["dates"]) == 20 and b20["period"]["label"] == "直近 20 営業日"
    assert len(ball["dates"]) == 25 and ball["period"]["all"] and ball["period"]["label"] == "全期間（25 営業日）"
    assert ds[0] not in b20["dates"] and ds[0] in ball["dates"]
    assert b20["traders"][0]["n_orders"] == 20 and ball["traders"][0]["n_orders"] == 25
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        tr = c.get("/traders/test_a").text
        assert ds[0] in tr and "全期間（25 営業日）" in tr
        for path in ("/", "/overall"):
            page = c.get(path).text
            assert ds[0] not in page and "直近 20 営業日" in page, path
        assert ds[0] not in c.get("/api/live").text          # ⚠ API は 20 日のまま（重くしない）


def test_history_is_grouped_by_month_and_only_the_latest_is_open(settings):
    ds = build_long_live_dir(settings.live_dir, 45)
    b = lv.board(settings.live_dir, days=None)
    months = lv.history(b, "test_a")
    assert [m["ym"] for m in months] == sorted({d[:7] for d in ds}, reverse=True)
    assert sum(m["n"] for m in months) == len(ds)            # 1 件も落ちない
    assert [r["date"] for r in months[0]["rows"]] == sorted([d for d in ds if d[:7] == months[0]["ym"]], reverse=True)
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        html = c.get("/traders/test_a").text
    assert html.count('<details class="hist"') == len(months)
    assert html.count('<details class="hist" open>') == 1
    assert html[html.index('<details class="hist"'):].startswith('<details class="hist" open>')   # 開いているのは最新の月


def test_month_subtotals_are_counted_from_the_rows(settings):
    """月の小計は表示のための足し算だけ（⚠ 損益は出さない ＝ 正本は台帳）。"""
    ds = build_long_live_dir(settings.live_dir, 45)
    b = lv.board(settings.live_dir, days=None)
    m = lv.history(b, "test_a")[0]
    rows = [d for d in ds if d[:7] == m["ym"]]
    assert m["n"] == m["n_filled"] == len(rows) and m["n_error"] == m["n_cancelled"] == 0
    assert m["buy_usd"] == round(sum(25.0 + ds.index(d) for d in rows), 2) and m["sell_usd"] == 0.0
    assert m["diff1_median"] == 10.0                          # 約定は mid の 10bp 上（_one_day）
    assert "pnl" not in m and "realized_usd" not in m


def test_history_is_filtered_by_trader(settings):
    build_long_live_dir(settings.live_dir, 3)
    (settings.live_dir / "config" / "traders" / "other.toml").write_text(TRADER_TOML.replace('name = "test_a"', 'name = "other"'), encoding="utf-8")
    _one_day(settings.live_dir, "2026-07-07", 99.0, trader="other")     # ⚠ test_a の 3 日（07-01・02・06）と重ならない日
    b = lv.board(settings.live_dir, days=None)
    assert [r["o"]["symbol"] for r in lv.history(b, "other")[0]["rows"]] == ["T"]
    assert all(r["part"]["trader"] == "test_a" for m in lv.history(b, "test_a") for r in m["rows"])
    assert sum(m["n"] for m in lv.history(b)) == 4             # 誰の分も絞らなければ全部


def test_action_grid_scrolls_sideways_instead_of_squeezing_the_cells(settings):
    """⚠ 1 日 18px を割ったら図を縮めずに横へ伸ばす（買・売の文字が読めなくなるため。§15-12）。"""
    from app import charts
    build_long_live_dir(settings.live_dir, 81)
    b = lv.board(settings.live_dir, days=None)
    t = b["traders"][0]
    wide = str(charts.action_block(b, t))
    assert 'class="scroll-x gridscroll"' in wide and "chart-wide" in wide
    assert f'width="{charts.GRID_PAD_R + 18 * len(b["bd"])}"' in wide and 'class="chart gridlabels" width="52"' in wide
    # ⚠ 64 日は 17.9px ＝ 境目のすぐ内側（sim2 の全期間）。ここでも横スクロールに入り、縮まない（大きさを属性で持つ）
    border = str(charts.action_block({**b, "bd": b["bd"][:64], "missing": []}, {**t, "grid": {s: v[:64] for s, v in t["grid"].items()}}))
    assert "gridscroll" in border and f'width="{charts.GRID_PAD_R + 18 * 64}"' in border
    narrow = str(charts.action_block({**b, "bd": b["bd"][:20], "missing": []}, {**t, "grid": {s: v[:20] for s, v in t["grid"].items()}}))
    assert "gridscroll" not in narrow and "chart-wide" not in narrow


def test_x_labels_are_thinned_so_they_never_overlap():
    """⚠ 2026-09-20 に 64 日で「12-2812-31」と重なった。最後の日は必ず出し、近すぎるときは 1 つ手前を落とす。"""
    from app import charts
    assert charts.x_ticks(20, 57.4) == [0, 5, 10, 15, 19]     # 20 日のときは今までどおり 5 日おき
    for n, step in ((64, 6.6), (64, 17.9), (250, 4.6)):
        idx = charts.x_ticks(n, step)
        assert idx[0] == 0 and idx[-1] == n - 1
        assert all((idx[i + 1] - idx[i]) * step >= charts.X_LABEL_PX for i in range(len(idx) - 1)), (n, step, idx)


def test_partial_live_returns_numbers_and_charts_without_the_history(settings):
    """3 秒ごとに取り直すのは数字と図だけ（⚠ 全期間の履歴を 3 秒ごとに送らない）。⚠ GET のみ・POST は増やさない。"""
    build_long_live_dir(settings.live_dir, 25)
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        part = c.get("/traders/test_a?partial=live")
        assert part.status_code == 200
        assert "損益の推移" in part.text and "行動の推移" in part.text
        assert "注文の履歴" not in part.text and "<html" not in part.text and 'details class="hist"' not in part.text
        assert c.post("/traders/test_a?partial=live").status_code == 405


def test_day_records_are_cached_per_day_and_reread_when_the_files_change(settings, monkeypatch):
    """全期間を読むので、過ぎた日は覚えておく（⚠ 記録が増えたら読み直す・上限を超えたら古いものから捨てる）。"""
    live = build_live_dir(settings.live_dir)
    lv.day(live, "2026-09-18")
    calls = []
    orig = lv._read_day
    monkeypatch.setattr(lv, "_read_day", lambda *a, **k: (calls.append(1), orig(*a, **k))[1])
    assert lv.day(live, "2026-09-18")["n_orders"] == 1 and not calls        # 2 回目は開かない
    livefs.append(live / "out" / "2026-09-18" / "events.jsonl", json.dumps({"date": "2026-09-18", "kind": "halted"}))
    lv.day(live, "2026-09-18")
    assert len(calls) == 1                                                  # 変わったら読み直す
    lv.day(live, "2026-09-18", cache=False)
    assert len(calls) == 2                                                  # cache=False は必ず読む
    monkeypatch.setattr(lv, "DAY_CACHE_MAX", 2)
    for d in ("2026-09-17", "2026-09-18", "2026-09-16"):
        lv.day(live, d)
    assert len(lv._DAY_CACHE) <= 2


def test_real_paper_control_replaces_the_placeholder_when_daily_csv_exists(settings):
    """実売買の Phase 3: 執行器の paper.py が書いた daily.csv があれば、紙上の損益・差 3・B&H は本物を出す（仮の印を外す）。無い人は仮のまま。"""
    build_live_dir(settings.live_dir)
    b0 = lv.board(settings.live_dir)
    d = b0["dates"][-1]
    path = settings.live_dir / "out" / "daily.csv"
    cols = ["date", "trader", "test", "budget_usd", "n_symbols", "n_signals", "paper_held", "paper_trades", "paper_bp", "paper_cum_bp",
            "real_usd", "real_bp", "real_cum_bp", "diff3_bp", "diff3_cum_bp", "bh_bp", "bh_cum_bp", "orders", "filled", "not_filled", "unexecuted",
            "diff1_quote_to_fill_bp", "diff1_fill_to_close_bp", "diff2_half_spread_bp", "diff2_fees_usd", "diff4_events", "close_missing", "close_source"]
    vals = [d, "test_a", "True", 30.0, 1, 1, 1, 1, -2.5, -2.5, -0.01, -3.33, -3.33, 0.83, 0.83, -2.5, -2.5, 1, 1, 0, 0, 7.87, "", 9.84, 0.0, 0, 0, "bars"]
    livefs.write_doc(path, ",".join(cols) + "\n" + ",".join(str(v) for v in vals) + "\n")
    b = lv.board(settings.live_dir)
    t = next(x for x in b["traders"] if x["name"] == "test_a")
    assert t["paper_real"] and not b["placeholder"]["paper"]
    assert t["paper_pct"][b["bd"].index(d)] == -0.025 and t["diff3_cum_bp"] == 0.83 and t["daily"][0]["bh_bp"] == -2.5
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        page = c.get("/traders/test_a").text
        assert "紙上の対照（日次）" in page and "0.83" in page
        assert '仮データ</span></div><div class="s">' not in page                 # 差 3 のタイルは本物
        api = c.get("/api/live").json()
        assert api["daily"][0]["paper_cum_bp"] == -2.5 and api["daily"][0]["trader"] == "test_a"
    livefs.write_doc(path, "こわれた,ファイル\n1,2\n")
    assert lv.board(settings.live_dir)["placeholder"]["paper"]                   # 読めなければ仮に戻る（落ちない）


def test_nicknames_and_hidden_test_traders(settings, tmp_path, monkeypatch):
    """呼び名（traders.toml の [nicks]）を識別名と併記し、本物の人がいるときは試験用を一覧から外す（2026-09-23 利用者の指示）。
    ⚠ 隠すのは一覧だけ ＝ URL を直接開けば test も見える。本物の人が 0 人なら今までどおり試験用を出す。"""
    nk = tmp_path / "nicks.toml"
    nk.write_text('[nicks]\nT1 = "アキ"\n', encoding="utf-8")
    monkeypatch.setattr(lv, "NICKS_FILE", nk)
    build_live_dir(settings.live_dir, with_real=True)
    tr = {t["name"]: t for t in lv.traders(settings.live_dir)}
    assert tr["T1"]["label"] == "アキ（T1）" and tr["T1"]["nick"] == "アキ"
    assert tr["test_a"]["label"] == "test_a" and tr["test_a"]["nick"] is None
    b = lv.board(settings.live_dir)
    assert b["labels"] == {"T1": "アキ（T1）", "test_a": "test_a"} and len(b["traders"]) == 2      # ライブラリは全員を返す
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        home = c.get("/").text
        assert "アキ（T1）" in home and 'href="/traders/T1"' in home
        assert 'href="/traders/test_a"' not in home and "試験用 1 人は本物の画面には出さない" in home
        assert 'href="/traders/test_a"' not in c.get("/overall").text
        page = c.get("/traders/test_a")
        assert page.status_code == 200 and "TEST" in page.text and "一覧には出さない" in page.text
        assert "アキ（T1）" in c.get("/traders/T1").text
        j = c.get("/api/live").json()
        assert [t["name"] for t in j["traders"]] == ["T1"] and j["traders"][0]["label"] == "アキ（T1）"
        assert [t["name"] for t in j["test_traders"]] == ["test_a"]
    (settings.live_dir / "config" / "traders" / "T1.toml").unlink()
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        home = c.get("/").text
        assert 'href="/traders/test_a"' in home and "本物の画面には出さない" not in home
        assert [t["name"] for t in c.get("/api/live").json()["traders"]] == ["test_a"]


def test_nicks_file_missing_or_broken_means_identifier_only(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(lv, "NICKS_FILE", tmp_path / "none.toml")
    assert lv.nicks() == {}
    (tmp_path / "bad.toml").write_text("[nicks\n", encoding="utf-8")
    monkeypatch.setattr(lv, "NICKS_FILE", tmp_path / "bad.toml")
    assert lv.nicks() == {} and lv.label_of("T1", None) == "T1" and lv.label_of("T1", "アキ") == "アキ（T1）"


# ---------------- 見張り「今日の起動が無い」（3 台の役割分け Phase 5。dashboard.md §13-8）

def _et(s: str):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(s).replace(tzinfo=ZoneInfo("America/New_York"))


def _watch_day(settings, d: str) -> None:
    build_live_dir(settings.live_dir)
    base = {"date": d, "env": "prod", "run_id": f"{d.replace('-', '')}T195500Z"}
    _jsonl(settings.live_dir / "out" / d / "events.jsonl", [{**base, "kind": "start", "mode": "submit", "traders": ["test_a"], "test": True}])


def test_watch_today_missing_after_window_on_a_trading_day(settings, monkeypatch):
    """営業日（2026-09-24 木）・16:15 ET を過ぎた・今日の記録が無い → 今日の起動が無い。営業日の列が今日まで延び、起動しなかった日・マス目・日次の表・帯・/api/live に出る。"""
    _watch_day(settings, "2026-09-23")
    monkeypatch.setattr(lv, "now_et", lambda: _et("2026-09-24T16:20:00"))
    b = lv.board(settings.live_dir)
    assert b["watch"]["checked"] and b["watch"]["missing"] and b["watch"]["date"] == "2026-09-24"
    assert b["bd"][-1] == "2026-09-24" and b["missing"][-1] == "2026-09-24"
    assert [c["a"] for c in b["traders"][0]["grid"]["T"]][-1] == "nostart"
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        top = c.get("/").text
        assert "今日の起動が無い" in top and "今日（2026-09-24）を含む" in top
        assert "今日の起動が無い" in c.get("/?partial=strip").text
        assert "2026-09-24" in c.get("/overall").text and "起動なし" in c.get("/overall").text
        w = c.get("/api/live").json()["watch"]
        assert w["missing"] and w["date"] == "2026-09-24" and w["after_et"] == "16:15"


def test_watch_is_quiet_before_the_window_and_when_today_has_records(settings, monkeypatch):
    """16:15 ET の前は出さない（timer はまだこれから）。今日の記録があれば出さない。どちらも営業日の列は延びない。"""
    _watch_day(settings, "2026-09-23")
    monkeypatch.setattr(lv, "now_et", lambda: _et("2026-09-24T15:30:00"))
    b = lv.board(settings.live_dir)
    assert b["watch"] == {"date": "2026-09-24", "after_et": "16:15", "checked": True, "missing": False, "why": "窓の前"}
    assert b["bd"][-1] == "2026-09-23" and "2026-09-24" not in b["missing"]
    _jsonl(settings.live_dir / "out" / "2026-09-24" / "events.jsonl",
           [{"date": "2026-09-24", "env": "prod", "run_id": "x", "kind": "start", "mode": "submit", "traders": ["test_a"], "test": True}])
    monkeypatch.setattr(lv, "now_et", lambda: _et("2026-09-24T16:20:00"))
    b = lv.board(settings.live_dir)
    assert b["watch"]["why"] == "記録あり" and not b["watch"]["missing"] and "2026-09-24" not in b["missing"]
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        assert "今日の起動が無い" not in c.get("/").text
        assert not c.get("/api/live").json()["watch"]["missing"]


def test_watch_skips_holidays_and_weekends(settings, monkeypatch):
    """休場日（2026-09-07 Labor Day）と週末は「起動しない」のが正しい ＝ 見張らない（checked は True・why は 休場日）。"""
    _watch_day(settings, "2026-09-04")
    for now in ("2026-09-07T17:00:00", "2026-09-05T17:00:00"):
        monkeypatch.setattr(lv, "now_et", lambda now=now: _et(now))
        w = lv.watch(settings.live_dir)
        assert w["checked"] and not w["missing"] and w["why"] == "休場日"


def test_watch_is_suppressed_by_the_mark_halt_and_demo(settings, monkeypatch):
    """印のある機械（読むだけの写し）・HALT の日・デモでは判定しない（checked False・why に理由）。⚠ 停止中は「起動しない」が正しい。"""
    _watch_day(settings, "2026-09-23")
    monkeypatch.setattr(lv, "now_et", lambda: _et("2026-09-24T16:20:00"))
    assert lv.watch(settings.live_dir, suppress="not_production") == {"date": "2026-09-24", "after_et": "16:15", "checked": False, "missing": False, "why": "not_production"}
    # HALT（管理画面の停止ボタンが書くファイル）
    settings.halt_file.parent.mkdir(parents=True, exist_ok=True)
    settings.halt_file.write_text('{"since": "2026-09-24T20:00:00+00:00", "actor": "test", "reason": "test"}', encoding="utf-8")
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        w = c.get("/api/live").json()["watch"]
        assert not w["checked"] and not w["missing"] and w["why"] == "halt"
        assert "今日の起動が無い" not in c.get("/").text and "今日は見張らない（halt）" in c.get("/").text
    settings.halt_file.unlink()
    # シミュレーション: 仮の時計の今日・いまで判定する（本物の時計は読まない）
    from datetime import date
    w = lv.watch(settings.live_dir, today=date(2026, 9, 24), now_et_=_et("2026-09-24T16:30:00"))
    assert w["missing"] and w["date"] == "2026-09-24"
    w = lv.watch(settings.live_dir, today=date(2026, 9, 24))          # 時刻が無ければ判定できない
    assert not w["checked"] and w["why"] == "時刻が分からない"
