"""約定後の実際の手数料（口座の取引履歴）を読んで、注文の見積りと突き合わせる。プラン docs/plans/actual-fees.md（2026-10-19）。

    python3 fees.py fetch [--since YYYY-MM-DD] [--env prod] [--match]   # 取引履歴を取り足す → out/fees.jsonl（読むだけの照会。発注しない）
    python3 fees.py match                                                # out/<日付>/orders.jsonl × out/fees.jsonl → out/fees.csv
    python3 fees.py print                                                # 人ごとの 見積り ／ 実額 のまとめ（書かない）

⚠ **読むだけの後処理**（`yardstick.py`・`launchlog.py` と同じ型）。執行器・売買履歴（`state/`）は触らない ＝ 売買履歴の `fees_usd` は
   dry-run の見積りのまま。実額の正本は `out/fees.jsonl`（取引履歴の写し。1 取引 1 行）と `out/fees.csv`（注文ごとの 見積り ／ 実額 ／ 差）。
⚠ 本番の照会は許可なし（`allow_prod_orders=False`）＝ 発注できない。口座番号は記録しない（応答の文字列も `Masker` を通す）。
⚠ 「本番の機械ではない」印（`NOT_PRODUCTION`）のある機械では取りに行かない（titan の `live.sqlite` は読むだけの写し）。
⚠ 置き場は `<out>/fees.jsonl`・`<out>/fees.csv`（`LT_OUT_DIR` があればそこ）。`out/<日付>/` には書かない（見張り「今日の起動が無い」を崩さない）。

取引履歴で分かったこと【実測 2026-10-19・本番の読むだけの照会】: 注文 1 本 ＝ 取引 1 行で `order-id` が入る ／ 1 行の手数料は dry-run の
見積りを $0.001 に丸めた値 ／ `is-estimated-fee` は 1 か月たっても true のまま ／ 規制費の精算は週 1 回の「Regulatory fee adjustment」
（Money Movement。注文に紐づかない）で入る ＝ 口座の合計としてだけ数える（`kind = adjustment`）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from datetime import date as _date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
for d in (HERE, SAMPLE_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)
from _livefs import livefs  # noqa: E402
import record  # noqa: E402
from ttclient import ApiError, Client, load_env  # noqa: E402

NAME_JSONL = "fees.jsonl"
NAME_CSV = "fees.csv"
FEE_KEYS = ("commission", "clearing-fees", "regulatory-fees", "proprietary-index-option-fees", "other-charge", "currency-conversion-fees")
CSV_COLUMNS = ["kind", "date", "env", "trader", "symbol", "side", "shares", "order_id", "final_status",
               "fee_est_usd", "fee_actual_usd", "diff_usd", "n_tx", "estimated", "tx_qty", "tx_price",
               "clearing_usd", "regulatory_usd", "other_usd", "note"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FIRST_LOOKBACK_DAYS = 30     # 記録に注文の日が 1 つも無いときだけ（初回の既定）


def default_out_dir() -> str:
    return os.environ.get("LT_OUT_DIR") or os.path.join(HERE, "out")


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _signed(value, effect) -> float:
    """`*-effect` ＝ Debit なら払う（正）・Credit なら戻る（負）・None なら 0。"""
    v = _f(value)
    return v if effect == "Debit" else (-v if effect == "Credit" else 0.0)


def fee_of(tx: dict) -> float:
    """1 取引の手数料の実額（払う額が正）。執行器の `fee_total_usd` と同じ向き。"""
    return round(sum(_signed(tx.get(k), tx.get(k + "-effect")) for k in FEE_KEYS), 6)


def keep(tx: dict) -> bool:
    """残す取引 ＝ 売買（Trade）と、手数料の精算（Money Movement で説明に fee）。入金・利息は残さない。"""
    t = tx.get("transaction-type")
    if t == "Trade":
        return True
    return t == "Money Movement" and "fee" in str(tx.get("description") or "").lower()


def to_row(tx: dict, env: str, fetched_at: str) -> dict:
    """取引履歴 1 行 → 記録の 1 行（口座番号は持たない）。"""
    trade = tx.get("transaction-type") == "Trade"
    qty = tx.get("quantity")
    return {"id": tx.get("id"), "order_id": (str(tx["order-id"]) if tx.get("order-id") is not None else None),
            "date": tx.get("transaction-date"), "executed_at": tx.get("executed-at"),
            "type": tx.get("transaction-type"), "sub_type": tx.get("transaction-sub-type"), "action": tx.get("action"),
            "symbol": tx.get("symbol"), "quantity": (_f(qty) if qty is not None else None),
            "price": (_f(tx.get("price")) if tx.get("price") is not None else None),
            "value": _signed(tx.get("value"), tx.get("value-effect")),
            "fee_usd": fee_of(tx) if trade else _signed(tx.get("value"), tx.get("value-effect")),
            "commission": _f(tx.get("commission")), "clearing": _f(tx.get("clearing-fees")), "regulatory": _f(tx.get("regulatory-fees")),
            "proprietary": _f(tx.get("proprietary-index-option-fees")), "other": _f(tx.get("other-charge")),
            "other_description": tx.get("other-charge-description"), "estimated": bool(tx.get("is-estimated-fee")),
            "description": tx.get("description"), "env": env, "fetched_at": fetched_at}


def _sig(row: dict) -> str:
    return json.dumps({k: v for k, v in row.items() if k != "fetched_at"}, ensure_ascii=False, sort_keys=True)


def read(out_dir: str) -> list[dict]:
    """`fees.jsonl` の行（同じ `id` は最後の行が勝つ ＝ 仮 → 確定の上書き）。日付・id の順。"""
    rows: dict = {}
    for line in livefs.read_lines(os.path.join(out_dir, NAME_JSONL), missing_ok=True):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows[str(r.get("id"))] = r
    return sorted(rows.values(), key=lambda r: (r.get("date") or "", str(r.get("id"))))


def order_dates(out_dir: str) -> list[str]:
    return sorted(d for d in livefs.listdir(out_dir, missing_ok=True) if DATE_RE.match(d))


def default_since(out_dir: str, today: _date) -> str:
    """前回の最後の日の前日（精算の遅れを拾う）。初回は記録の最初の注文の日、それも無ければ 30 日前。"""
    rows = read(out_dir)
    if rows:
        last = max(r["date"] for r in rows if r.get("date"))
        return (_date.fromisoformat(last) - timedelta(days=1)).isoformat()
    days = order_dates(out_dir)
    return days[0] if days else (today - timedelta(days=FIRST_LOOKBACK_DAYS)).isoformat()


def fetch(client, account: str, env: str, out_dir: str, since: str, *, now=None, masker: record.Masker | None = None) -> dict:
    """取引履歴を取り足す。同じ中身の行は足さない（何度流しても増えない）。中身が変わった行（仮 → 確定）は新しい行として足す。"""
    now = now or (lambda: datetime.now(timezone.utc))
    masker = masker or record.Masker()
    masker.add_account(account)
    fetched_at = now().isoformat(timespec="seconds")
    items = client.list_transactions(account, start_date=since)
    have = {_sig(r) for r in _raw_rows(out_dir)}
    added = 0
    kept = 0
    for tx in items:
        if not keep(tx):
            continue
        kept += 1
        row = to_row(tx, env, fetched_at)
        if _sig(row) in have:
            continue
        livefs.append(os.path.join(out_dir, NAME_JSONL), masker.text(json.dumps(row, ensure_ascii=False)))
        have.add(_sig(row))
        added += 1
    return {"since": since, "items": len(items), "kept": kept, "added": added}


def _raw_rows(out_dir: str) -> list[dict]:
    out = []
    for line in livefs.read_lines(os.path.join(out_dir, NAME_JSONL), missing_ok=True):
        try:
            out.append(json.loads(line))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def orders(out_dir: str) -> list[dict]:
    """記録の注文（発注した回・注文番号のあるもの）。1 注文 1 トレーダー。"""
    out = []
    for day in order_dates(out_dir):
        for line in livefs.read_lines(os.path.join(out_dir, day, "orders.jsonl"), missing_ok=True):
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("mode") != "submit":
                continue
            oid = (o.get("submitted") or {}).get("order_id")
            if oid is None:
                continue
            fills = o.get("fills") or []
            parts = o.get("parts") or []
            out.append({"date": o.get("date") or day, "env": o.get("env"), "trader": parts[0].get("trader") if parts else "",
                        "symbol": o.get("symbol"), "side": o.get("side"), "order_id": str(oid), "final_status": o.get("final_status"),
                        "fills_qty": round(sum(_f(f.get("shares")) for f in fills), 6),
                        "fee_est": _f((o.get("amounts") or {}).get("fee_usd")), "fee_source": (o.get("amounts") or {}).get("fee_source")})
    return out


def match(out_dir: str) -> list[dict]:
    """注文 × 取引履歴 → `fees.csv` の行（kind ＝ order ／ outside ／ adjustment）。"""
    rows = read(out_dir)
    trades = [r for r in rows if r.get("type") == "Trade"]
    by_oid: dict[tuple, list[dict]] = {}
    for r in trades:
        by_oid.setdefault((r.get("env"), r.get("order_id")), []).append(r)
    out: list[dict] = []
    used: set = set()
    envs = {r.get("env") for r in rows}          # 取引履歴を取った env（cert は 1 行も返さない ＝ 当たらないのが正しい）
    for o in orders(out_dir):
        key = (o["env"], o["order_id"])
        txs = by_oid.get(key, [])
        used.add(key)
        n = len(txs)
        actual = round(sum(r["fee_usd"] for r in txs), 6) if n else None
        qty = round(sum(_f(r.get("quantity")) for r in txs), 6) if n else None
        px = (round(sum(_f(r.get("quantity")) * _f(r.get("price")) for r in txs) / qty, 6) if qty else None)
        note = ""
        if not n:
            note = (f"約定なし（{o['final_status']}）" if o["final_status"] != "Filled"
                    else ("取引履歴にまだ無い" if o["env"] in envs else f"取引履歴を取っていない env（{o['env']}）"))
        elif qty is not None and abs(qty - o["fills_qty"]) > 1e-6:
            note = f"数量が違う（記録 {o['fills_qty']} ／ 取引履歴 {qty}）"
        out.append({"kind": "order", "date": o["date"], "env": o["env"], "trader": o["trader"], "symbol": o["symbol"], "side": o["side"],
                    "shares": o["fills_qty"], "order_id": o["order_id"], "final_status": o["final_status"],
                    "fee_est_usd": round(o["fee_est"], 6), "fee_actual_usd": actual,
                    "diff_usd": (round(actual - o["fee_est"], 6) if actual is not None else None), "n_tx": n,
                    "estimated": (any(r.get("estimated") for r in txs) if n else None), "tx_qty": qty, "tx_price": px,
                    "clearing_usd": round(sum(r.get("clearing", 0) for r in txs), 6) if n else None,
                    "regulatory_usd": round(sum(r.get("regulatory", 0) for r in txs), 6) if n else None,
                    "other_usd": round(sum(r.get("other", 0) for r in txs), 6) if n else None, "note": note})
    for key, txs in by_oid.items():
        if key in used:
            continue
        for r in txs:
            side = "buy" if str(r.get("action") or "").startswith("Buy") else ("sell" if str(r.get("action") or "").startswith("Sell") else "")
            out.append({"kind": "outside", "date": r.get("date"), "env": r.get("env"), "trader": "", "symbol": r.get("symbol"), "side": side,
                        "shares": r.get("quantity"), "order_id": r.get("order_id"), "final_status": "",
                        "fee_est_usd": None, "fee_actual_usd": r.get("fee_usd"), "diff_usd": None, "n_tx": 1, "estimated": r.get("estimated"),
                        "tx_qty": r.get("quantity"), "tx_price": r.get("price"), "clearing_usd": r.get("clearing"),
                        "regulatory_usd": r.get("regulatory"), "other_usd": r.get("other"),
                        "note": "売買履歴の外（口座で人が売買したもの。誰かに割り振らない）"})
    for r in rows:
        if r.get("type") == "Trade":
            continue
        out.append({"kind": "adjustment", "date": r.get("date"), "env": r.get("env"), "trader": "", "symbol": "", "side": "", "shares": None,
                    "order_id": None, "final_status": "", "fee_est_usd": None, "fee_actual_usd": r.get("fee_usd"), "diff_usd": None,
                    "n_tx": 1, "estimated": None, "tx_qty": None, "tx_price": None, "clearing_usd": None, "regulatory_usd": None,
                    "other_usd": None, "note": f"口座の精算: {r.get('description') or r.get('sub_type') or ''}（注文に紐づかない）"})
    kind_order = {"order": 0, "outside": 1, "adjustment": 2}
    out.sort(key=lambda r: (r["date"] or "", kind_order[r["kind"]], r["trader"] or "", r["symbol"] or "", str(r["order_id"] or "")))
    return out


def write_csv(path: str, rows: list[dict]) -> None:
    f = io.StringIO()
    w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in CSV_COLUMNS})
    livefs.write_doc(path, f.getvalue())


def read_csv(out_dir: str) -> list[dict]:
    text = livefs.read_doc(os.path.join(out_dir, NAME_CSV), missing_ok=True)
    return list(csv.DictReader(io.StringIO(text))) if text else []


def summarize(rows: list[dict]) -> dict:
    """人ごとの 見積り ／ 実額 と、口座の合計（売買履歴の外・精算を含む）。CSV の行（文字）でも `match` の行でも読める。"""
    def num(x):
        return None if x in (None, "") else float(x)
    people: dict[str, dict] = {}
    outside = {"n": 0, "fee": 0.0}
    adjust = {"n": 0, "fee": 0.0}
    for r in rows:
        if r["kind"] == "order":
            p = people.setdefault(r["trader"] or "", {"trader": r["trader"] or "", "orders": 0, "matched": 0, "est": 0.0, "actual": 0.0, "diff": 0.0, "estimated": 0})
            p["orders"] += 1
            p["est"] += num(r["fee_est_usd"]) or 0.0
            a = num(r["fee_actual_usd"])
            if a is not None:
                p["matched"] += 1
                p["actual"] += a
                p["diff"] += a - (num(r["fee_est_usd"]) or 0.0)
                p["estimated"] += 1 if str(r.get("estimated")) in ("True", "true", "1") else 0
        elif r["kind"] == "outside":
            outside["n"] += 1
            outside["fee"] += num(r["fee_actual_usd"]) or 0.0
        else:
            adjust["n"] += 1
            adjust["fee"] += num(r["fee_actual_usd"]) or 0.0
    for p in people.values():
        for k in ("est", "actual", "diff"):
            p[k] = round(p[k], 4)
    outside["fee"] = round(outside["fee"], 4)
    adjust["fee"] = round(adjust["fee"], 4)
    total_actual = round(sum(p["actual"] for p in people.values()) + outside["fee"] + adjust["fee"], 4)
    return {"people": sorted(people.values(), key=lambda p: p["trader"]), "outside": outside, "adjust": adjust,
            "total_est": round(sum(p["est"] for p in people.values()), 4), "total_actual": total_actual}


def print_summary(s: dict) -> None:
    print("トレーダー   注文  実額あり   見積り $     実額 $     差 $   仮の行")
    for p in s["people"]:
        print(f"{p['trader']:<10} {p['orders']:>5} {p['matched']:>8} {p['est']:>10.4f} {p['actual']:>10.4f} {p['diff']:>+9.4f} {p['estimated']:>6}")
    print(f"売買履歴の外: {s['outside']['n']} 件 ${s['outside']['fee']:.4f} ／ 口座の精算: {s['adjust']['n']} 件 ${s['adjust']['fee']:+.4f}")
    print(f"合計: 見積り ${s['total_est']:.4f} ／ 実額 ${s['total_actual']:.4f}（売買履歴の外と精算を含む）")


def make_client(cfg: dict, env: str, masker: record.Masker) -> tuple[Client, str]:
    """読むだけの客（許可なし）。⚠ 401 は再試行しない（失敗の連続は IP の約 8 時間ブロック）。"""
    if env == "prod":
        secret, refresh, cid = cfg.get("TT_PROD_CLIENT_SECRET"), cfg.get("TT_PROD_REFRESH_TOKEN"), cfg.get("TT_PROD_CLIENT_ID")
        base = cfg.get("TT_PROD_REST_BASE") or None
    else:
        secret, refresh, cid = cfg.get("TT_CLIENT_SECRET"), cfg.get("TT_REFRESH_TOKEN"), cfg.get("TT_CLIENT_ID")
        base = cfg.get("TT_REST_BASE") or None
    if not secret or not refresh:
        raise SystemExit(f"エラー: {env} の資格情報（client secret ／ refresh token）が無い")
    masker.add(secret, f"<{env}_client_secret:masked>")
    masker.add(refresh, f"<{env}_refresh_token:masked>")
    client = Client(env=env, allow_prod_orders=False, allow_prod_dry_run=False, rest_base=base)
    client.authenticate(client_secret=secret, refresh_token=refresh, client_id=cid or None)
    masker.add(client.token.access_token, f"<{env}_access_token:masked>")
    account = cfg.get("TT_PROD_ACCOUNT_NUMBER" if env == "prod" else "TT_ACCOUNT_NUMBER") or client.list_accounts()[0]["account-number"]
    masker.add_account(account)
    return client, account


def not_production_mark() -> str | None:
    """「本番の機械ではない」印があれば理由の 1 行（取りに行かない）。"""
    import mode as modes
    mark = modes.read_not_production()
    return None if mark is None else f"「本番の機械ではない」印がある（{mark.machine or mark.error}）"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="約定後の実際の手数料（口座の取引履歴）を読む・注文の見積りと突き合わせる")
    ap.add_argument("cmd", choices=["fetch", "match", "print"])
    ap.add_argument("--out-dir", default=default_out_dir())
    ap.add_argument("--env", default=None, choices=["cert", "prod"], help="既定は TT_ENV、無ければ prod（取引履歴があるのは本番だけ）")
    ap.add_argument("--since", default=None, help="取りに行く最初の日（YYYY-MM-DD）。既定は前回の最後の日の前日")
    ap.add_argument("--match", action="store_true", help="fetch の後に match も流す")
    args = ap.parse_args(argv)
    out_dir = args.out_dir
    if args.cmd == "fetch":
        why = not_production_mark()
        if why:
            print(f"取引履歴は取りに行かない: {why}")
            return 0
        cfg = load_env(os.environ.get("TT_ENV_FILE") or os.path.join(SAMPLE_DIR, ".env"))
        env = args.env or cfg.get("TT_ENV") or "prod"
        masker = record.Masker()
        try:
            client, account = make_client(cfg, env, masker)
            since = args.since or default_since(out_dir, datetime.now(timezone.utc).date())
            res = fetch(client, account, env, out_dir, since, masker=masker)
        except ApiError as exc:
            print(f"⚠ 取引履歴の照会に失敗: {exc.status} {exc.code} {masker.text(str(exc.message))[:200]}", file=sys.stderr)
            return 1
        print(f"取引履歴 {env}: {res['since']} から {res['items']} 件（残す {res['kept']}・新しく足した {res['added']}）→ {NAME_JSONL}")
        if not args.match:
            return 0
    if args.cmd in ("fetch", "match"):
        rows = match(out_dir)
        write_csv(os.path.join(out_dir, NAME_CSV), rows)
        s = summarize(rows)
        print(f"突き合わせ: 注文 {sum(p['orders'] for p in s['people'])} 本（実額あり {sum(p['matched'] for p in s['people'])}）・売買履歴の外 {s['outside']['n']}・精算 {s['adjust']['n']} → {NAME_CSV}")
        return 0
    print_summary(summarize(read_csv(out_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
