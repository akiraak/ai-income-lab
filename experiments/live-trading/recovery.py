"""口座の建玉と台帳の帳尻を合わせる（live-trading.md §0-8。2026-09-19 の利用者決定「提案の 3 段」）。

    段 1  控えの未完を戻す（自動）   … `recover_unfinished`: 注文番号（無ければ今日の注文から ID）で照会し、約定していればその人の台帳に入れる
    段 2  突き合わせ（検知）         … `check_positions`: 口座 − 全員の台帳の合計。多い ＝ 台帳の外の株（記録だけ）／ 少ない ＝ その銘柄は今日売買しない
    段 3  人が合わせる               … `reconcile.py`

⚠ 自動で直すのは、誰の注文かが控えで確実に分かるものだけ。⚠ **差を推測で誰かに割り振らない**（トレーダーごとの成績が混ざる）。
"""

from __future__ import annotations

import glob
import json
import os

from _livefs import livefs
from journal import Journal
from state import TraderState, load_state, save_state

QTY_MATCH_TOL = 1e-3          # 口座と台帳の合計の差をこの株数まで許す（端株の丸め）
FINAL = {"Filled", "Cancelled", "Rejected", "Expired", "Removed"}


def _fills(order_obj: dict) -> tuple[float, float]:
    legs = (order_obj or {}).get("legs") or []
    fills = (legs[0].get("fills") or []) if legs else []
    qty = sum(float(f.get("quantity") or 0) for f in fills)
    value = sum(float(f.get("quantity") or 0) * float(f.get("fill-price") or 0) for f in fills)
    return qty, (value / qty if qty > 0 else 0.0)


def recover_unfinished(journal: Journal, client, account: str, state_dir: str, today: str, write: bool) -> tuple[list[dict], set[str]]:
    """未完の控えを 1 件ずつ照会する。戻り値は (事象, 未解決の銘柄)。`write` が False（plan ／ dry-run）なら台帳にも控えにも書かない。"""
    events: list[dict] = []
    unresolved: set[str] = set()
    for e in journal.unfinished():
        base = {"ext": e["ext"], "trader": e["trader"], "symbol": e["symbol"], "side": e["side"], "intent_date": e.get("date"), "order_id": e.get("order_id")}
        order_obj, why = None, None
        try:
            if e.get("order_id") is not None:
                order_obj = client.get_order(account, e["order_id"])
            elif e.get("date") == today:
                order_obj = client.find_order_by_external_id(account, e["ext"])     # 今日の注文の一覧（約定済みも混ざる）
                if order_obj is None:
                    why = "not_submitted"                                           # 発注が相手に届く前に落ちた
            else:
                why = "unknown"                                                     # 前の日・注文番号なし ＝ 出たかどうかを確かめる手が無い
        except Exception as exc:  # noqa: BLE001  照会の失敗で起動を落とさない（未解決として止める）
            why = f"lookup_failed: {type(exc).__name__}: {str(exc)[:120]}"
        if order_obj is not None and order_obj.get("status") not in FINAL:
            try:                                                                    # 落ちた回の注文が働いたまま残っている → 取り消してから読む
                client.cancel_order(account, order_obj.get("id"))
                order_obj = client.get_order(account, order_obj.get("id"))
            except Exception as exc:  # noqa: BLE001
                why = f"still_working: {type(exc).__name__}: {str(exc)[:120]}"
            if order_obj.get("status") not in FINAL and not why:
                why = f"still_working: {order_obj.get('status')}"
        if why and why != "not_submitted":
            unresolved.add(e["symbol"])
            events.append({"kind": "journal_unresolved", **base, "reason": why,
                           "note": "控えの注文を照会できない ＝ この銘柄は今日売買しない。口座の注文履歴を見て reconcile.py resolve で閉じる"})
            continue
        qty, price = _fills(order_obj) if order_obj else (0.0, 0.0)
        outcome = "recovered_filled" if qty > 0 else ("not_submitted" if why else "recovered_no_fill")
        if write:
            if qty > 0:
                st = load_state(state_dir, e["trader"])
                try:
                    if e["side"] == "buy":
                        st.apply_buy(e["symbol"], qty, round(price, 4), e.get("date") or today, fee=float(e.get("fee_usd") or 0), note="recovered")
                    else:
                        st.apply_sell(e["symbol"], qty, round(price, 4), e.get("date") or today, fee=float(e.get("fee_usd") or 0), note="recovered")
                except ValueError as exc:
                    unresolved.add(e["symbol"])
                    events.append({"kind": "journal_unresolved", **base, "reason": f"ledger: {exc}"[:200]})
                    continue
                save_state(state_dir, st)
            journal.close(e["ext"], outcome, shares=qty, price=round(price, 4))
        events.append({"kind": "journal_recovered", **base, "outcome": outcome, "shares": qty, "price": round(price, 4), "fee_usd": float(e.get("fee_usd") or 0) if qty > 0 else 0.0,
                       "status": (order_obj or {}).get("status"), "applied": bool(write),
                       "note": "前の実行が発注の後・台帳の保存の前に終わっていた注文。約定はその人の台帳に入れた" if qty > 0 else "前の実行の注文は約定していなかった"})
    return events, unresolved


def load_all_states(state_dir: str) -> dict[str, TraderState]:
    """その回に動かさないトレーダーも含めた全員の台帳（口座は全員ぶんの合計しか見せない）。"""
    out = {}
    for path in livefs.find(state_dir, "*.json"):
        try:
            st = TraderState.from_dict(json.loads(livefs.read_doc(path) or ""))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out[st.name] = st
    return out


def account_quantities(positions: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in positions:
        try:
            qty = float(p.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if str(p.get("quantity-direction") or "Long").lower() == "short":
            qty = -qty
        out[p.get("symbol")] = out.get(p.get("symbol"), 0.0) + qty
    return out


def check_positions(positions: list[dict], states: dict[str, TraderState], symbols: set[str]) -> tuple[dict[str, dict], dict[str, dict]]:
    """(台帳の外の株, 足りない銘柄)。どちらも {銘柄: {"account", "ledger", "diff", "holders"}}。

    ⚠ 多いほうは止めない: トレーダーは自分の台帳の株しか売らないので、台帳の外の株（利用者の手持ちなど）には誰も手を付けない。
    ⚠ 危ないのは少ないほう: 台帳に「ある」と書いてあるのに口座に無い ＝ 売りが通らない（か、他人の株を売る）。
    """
    account = account_quantities(positions)
    ledger: dict[str, float] = {}
    holders: dict[str, dict[str, float]] = {}
    for st in states.values():
        for sym, h in st.holdings.items():
            ledger[sym] = ledger.get(sym, 0.0) + h.shares
            holders.setdefault(sym, {})[st.name] = h.shares
    outside, short = {}, {}
    for sym in sorted(set(symbols) | set(ledger)):
        diff = account.get(sym, 0.0) - ledger.get(sym, 0.0)
        row = {"account": round(account.get(sym, 0.0), 6), "ledger": round(ledger.get(sym, 0.0), 6), "diff": round(diff, 6), "holders": holders.get(sym, {})}
        if diff > QTY_MATCH_TOL:
            outside[sym] = row
        elif diff < -QTY_MATCH_TOL:
            short[sym] = row
    return outside, short
