"""約定後の実際の手数料（`fees.py`・`ttclient.list_transactions`。プラン docs/plans/actual-fees.md）。ネットワークを使わない。

⚠ **ここで落としたいのは 5 つ。**
  1. 符号（`*-effect` Debit ＝ 払う ＝ 正・Credit ＝ 負・None ＝ 0）と、残す取引（Trade ＋ 手数料の精算。入金は残さない）
  2. 取り足し: 同じ中身は足さない（何度流しても増えない）・仮 → 確定は新しい行として足し、読むときは最後の行が勝つ・口座番号を書かない
  3. 突き合わせ: `order_id` × env で 1 対 1・数量の違い・まだ無い注文・売買履歴の外・口座の精算 ＝ 何度流しても同じ CSV
  4. 取りに行く最初の日の既定（前回の最後の日の前日 ／ 初回は記録の最初の注文の日）
  5. `list_transactions` が頁を全部めくる（`total-pages`）
  6. env の既定は prod（`.env` の `TT_ENV=cert` を読まない ＝ 2026-10-21・22 の朝の準備が cert の 0 件を取り続けた）
"""
from __future__ import annotations

import json
import os
from datetime import date as _date

import fees
import livefs
import ttclient

TX_BUY = {"id": 1001, "account-number": "5WT00042", "order-id": 508891490, "transaction-type": "Trade", "transaction-sub-type": "Buy to Open",
          "action": "Buy to Open", "symbol": "T", "quantity": "2.0", "price": "25.335", "value": "50.67", "value-effect": "Debit",
          "transaction-date": "2026-09-23", "executed-at": "2026-09-23T19:51:40.100Z", "description": "Bought 2 T @ 25.34",
          "commission": "0.0", "commission-effect": "None", "clearing-fees": "0.002", "clearing-fees-effect": "Debit",
          "regulatory-fees": "0.0", "regulatory-fees-effect": "None", "proprietary-index-option-fees": "0.0",
          "proprietary-index-option-fees-effect": "None", "is-estimated-fee": True}
TX_SELL = {**TX_BUY, "id": 1002, "order-id": 509657229, "transaction-sub-type": "Sell to Close", "action": "Sell to Close", "symbol": "NKE",
           "quantity": "1.0", "price": "35.805", "value": "35.805", "value-effect": "Credit", "transaction-date": "2026-09-25",
           "clearing-fees": "0.001", "regulatory-fees": "0.01", "regulatory-fees-effect": "Debit"}
TX_OUTSIDE = {**TX_SELL, "id": 1003, "order-id": 777000001, "symbol": "BAC", "transaction-date": "2026-10-07"}
TX_ADJ = {"id": 2001, "account-number": "5WT00042", "transaction-type": "Money Movement", "transaction-sub-type": "Balance Adjustment",
          "description": "Regulatory fee adjustment", "value": "0.067", "value-effect": "Credit", "transaction-date": "2026-10-10"}
TX_DEPOSIT = {"id": 2002, "account-number": "5WT00042", "transaction-type": "Money Movement", "transaction-sub-type": "Deposit",
              "description": "ACH DEPOSIT", "value": "900.0", "value-effect": "Credit", "transaction-date": "2026-10-05"}


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def list_transactions(self, account, *, start_date=None, **kw):
        self.calls.append(start_date)
        return [t for t in self.items if t["transaction-date"] >= start_date]


def _order(date, oid, trader, symbol, side, shares, fee, env="prod", status="Filled", mode="submit"):
    return {"date": date, "env": env, "mode": mode, "symbol": symbol, "side": side, "shares": shares, "parts": [{"trader": trader, "shares": shares}],
            "submitted": {"order_id": oid}, "final_status": status,
            "fills": [{"shares": shares, "price": 25.0, "fee_usd": fee}] if status == "Filled" else [],
            "amounts": {"fee_usd": fee, "fee_source": "dry_run_estimate"}}


def _write_orders(out, date, rows):
    livefs.append_many(os.path.join(out, date, "orders.jsonl"), [json.dumps(r, ensure_ascii=False) for r in rows])


def test_sign_and_which_transactions_are_kept():
    assert fees.fee_of(TX_BUY) == 0.002 and fees.fee_of(TX_SELL) == 0.011
    credit = {**TX_SELL, "regulatory-fees-effect": "Credit"}
    assert fees.fee_of(credit) == round(0.001 - 0.01, 6)
    assert fees.keep(TX_BUY) and fees.keep(TX_ADJ) and not fees.keep(TX_DEPOSIT)
    row = fees.to_row(TX_ADJ, "prod", "2026-10-19T00:00:00+00:00")
    assert row["fee_usd"] == -0.067 and row["type"] == "Money Movement" and "account" not in json.dumps(row)


def test_fetch_adds_only_new_rows_and_the_last_row_wins(tmp_path):
    out = str(tmp_path / "out")
    c = FakeClient([TX_BUY, TX_SELL, TX_ADJ, TX_DEPOSIT])
    r1 = fees.fetch(c, "5WT00042", "prod", out, "2026-09-20", now=lambda: __import__("datetime").datetime(2026, 10, 19, tzinfo=__import__("datetime").timezone.utc))
    assert (r1["items"], r1["kept"], r1["added"]) == (4, 3, 3)
    r2 = fees.fetch(c, "5WT00042", "prod", out, "2026-09-20")
    assert r2["added"] == 0 and len(livefs.read_lines(os.path.join(out, "fees.jsonl"))) == 3
    assert livefs.listdir(out) == ["fees.jsonl"]                     # out/<日付>/ を作らない
    assert "5WT00042" not in "\n".join(livefs.read_lines(os.path.join(out, "fees.jsonl")))
    # 仮 → 確定（内容が変わる）は新しい行として足し、読むときは最後の行が勝つ
    c.items[1] = {**TX_SELL, "is-estimated-fee": False, "regulatory-fees": "0.0102"}
    r3 = fees.fetch(c, "5WT00042", "prod", out, "2026-09-24")
    assert r3["added"] == 1
    rows = fees.read(out)
    assert len(rows) == 3
    sell = next(r for r in rows if r["id"] == 1002)
    assert sell["estimated"] is False and sell["fee_usd"] == 0.0112


def test_match_pairs_orders_by_order_id_and_env_and_is_idempotent(tmp_path):
    out = str(tmp_path / "out")
    c = FakeClient([TX_BUY, TX_SELL, TX_OUTSIDE, TX_ADJ])
    fees.fetch(c, "5WT00042", "prod", out, "2026-09-20")
    _write_orders(out, "2026-09-23", [_order("2026-09-23", 508891490, "T1", "T", "buy", 2, 0.002),
                                       _order("2026-09-23", 508891490, "T9", "T", "buy", 2, 0.002, env="cert"),      # 別の env の同じ番号 ＝ 当てない
                                       _order("2026-09-23", 1, "T1", "PFE", "buy", 2, 0.0, status="Cancelled"),
                                       _order("2026-09-23", 2, "T1", "VZ", "buy", 1, 0.001, mode="dry-run")])        # 発注していない回 ＝ 対象外
    _write_orders(out, "2026-09-25", [_order("2026-09-25", 509657229, "T1", "NKE", "sell", 2, 0.0112)])             # 記録 2 株 ／ 取引履歴 1 株
    _write_orders(out, "2026-10-13", [_order("2026-10-13", 999, "T4", "BAC", "buy", 1, 0.001)])                      # 取引履歴にまだ無い
    rows = fees.match(out)
    kinds = [(r["kind"], r["trader"], r["symbol"]) for r in rows]
    assert kinds == [("order", "T1", "PFE"), ("order", "T1", "T"), ("order", "T9", "T"), ("order", "T1", "NKE"),
                     ("outside", "", "BAC"), ("adjustment", "", ""), ("order", "T4", "BAC")]
    t = next(r for r in rows if r["trader"] == "T1" and r["symbol"] == "T")
    assert (t["fee_est_usd"], t["fee_actual_usd"], t["diff_usd"], t["n_tx"], t["estimated"], t["note"]) == (0.002, 0.002, 0.0, 1, True, "")
    t9 = next(r for r in rows if r["trader"] == "T9")
    assert t9["n_tx"] == 0 and t9["note"] == "取引履歴を取っていない env（cert）"
    nke = next(r for r in rows if r["symbol"] == "NKE")
    assert nke["fee_actual_usd"] == 0.011 and nke["diff_usd"] == round(0.011 - 0.0112, 6) and nke["note"].startswith("数量が違う")
    assert next(r for r in rows if r["symbol"] == "PFE")["note"] == "約定なし（Cancelled）"
    assert next(r for r in rows if r["trader"] == "T4")["note"] == "取引履歴にまだ無い"
    adj = next(r for r in rows if r["kind"] == "adjustment")
    assert adj["fee_actual_usd"] == -0.067 and "精算" in adj["note"]
    # CSV は何度流しても同じ・まとめ
    fees.write_csv(os.path.join(out, "fees.csv"), rows)
    text1 = livefs.read_doc(os.path.join(out, "fees.csv"))
    fees.write_csv(os.path.join(out, "fees.csv"), fees.match(out))
    assert livefs.read_doc(os.path.join(out, "fees.csv")) == text1
    s = fees.summarize(fees.read_csv(out))
    t1 = next(p for p in s["people"] if p["trader"] == "T1")
    assert (t1["orders"], t1["matched"], t1["est"], t1["actual"]) == (3, 2, 0.0132, 0.013)
    assert s["outside"] == {"n": 1, "fee": 0.011} and s["adjust"] == {"n": 1, "fee": -0.067}
    assert s["total_actual"] == round(0.013 + 0.011 - 0.067, 4) and s["total_est"] == 0.0132 + 0.002 + 0.001


def test_default_since_is_the_day_before_the_last_row_or_the_first_order_day(tmp_path):
    out = str(tmp_path / "out")
    assert fees.default_since(out, _date(2026, 10, 19)) == "2026-09-19"             # 何も無い ＝ 30 日前
    _write_orders(out, "2026-09-23", [_order("2026-09-23", 1, "T1", "T", "buy", 1, 0.001)])
    assert fees.default_since(out, _date(2026, 10, 19)) == "2026-09-23"             # 初回 ＝ 記録の最初の注文の日
    fees.fetch(FakeClient([TX_BUY, TX_ADJ]), "5WT00042", "prod", out, "2026-09-23")
    assert fees.default_since(out, _date(2026, 10, 19)) == "2026-10-09"             # 前回の最後の日（10/10）の前日


def test_list_transactions_turns_every_page(monkeypatch):
    c = ttclient.Client(env="cert")
    pages = {0: {"data": {"items": [{"id": 1}, {"id": 2}]}, "pagination": {"total-pages": 3}},
             1: {"data": {"items": [{"id": 3}]}, "pagination": {"total-pages": 3}},
             2: {"data": {"items": [{"id": 4}]}, "pagination": {"total-pages": 3}}}
    seen = []
    def fake_get(path, params=None, **kw):
        seen.append((path, dict(params)))
        return pages[params["page-offset"]]
    monkeypatch.setattr(c, "get", fake_get)
    items = c.list_transactions("5WT00042", start_date="2026-09-20")
    assert [i["id"] for i in items] == [1, 2, 3, 4]
    assert [p["page-offset"] for _, p in seen] == [0, 1, 2] and seen[0][1]["start-date"] == "2026-09-20" and seen[0][1]["per-page"] == 250
    assert seen[0][0] == "/accounts/5WT00042/transactions"


def test_fetch_goes_to_prod_even_when_the_env_file_says_cert(tmp_path, monkeypatch):
    out = str(tmp_path / "out")
    envf = tmp_path / ".env"
    envf.write_text("TT_ENV=cert\n", encoding="utf-8")
    monkeypatch.setenv("TT_ENV_FILE", str(envf))
    monkeypatch.setattr(fees, "not_production_mark", lambda: None)
    seen = []
    def fake_make_client(cfg, env, masker):
        seen.append(env)
        return FakeClient([TX_BUY]), "5WT00042"
    monkeypatch.setattr(fees, "make_client", fake_make_client)
    assert fees.main(["fetch", "--out-dir", out, "--since", "2026-09-20"]) == 0
    assert fees.main(["fetch", "--out-dir", out, "--since", "2026-09-20", "--env", "cert"]) == 0
    assert seen == ["prod", "cert"]                                                  # 明示したときだけ cert
