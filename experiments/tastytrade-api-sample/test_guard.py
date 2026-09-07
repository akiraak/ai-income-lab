#!/usr/bin/env python3
"""本番ガードの単体テスト（ネットワーク不要）。

鍵は 3 つ（dry-run / 取消 / 本発注）で、**互いに開けない**ことを固定する。
本発注の鍵だけが全部を開ける。cert では鍵は要らない。
"""

from __future__ import annotations

import sys

from ttclient import Client, ProductionGuard

FAIL = 0


def check(label: str, fn, expect_open: bool) -> None:
    """ガードを通れば request() が「先に authenticate()」の RuntimeError で止まる（＝ネットワークに出ない）。"""
    global FAIL
    try:
        fn()
    except ProductionGuard:
        opened = False
    except RuntimeError as exc:
        assert "authenticate" in str(exc), exc
        opened = True
    else:
        raise AssertionError(f"{label}: 例外なしで抜けた（テストの前提が崩れている）")
    ok = opened == expect_open
    print(f"  {'ok  ' if ok else 'NG  '} {label}: {'開く' if opened else '閉じる'}")
    FAIL |= not ok


ORDER = {"order-type": "Market", "legs": []}
cases = [
    ("prod 既定", dict(env="prod"), dict(dry=False, cancel=False, submit=False)),
    ("prod dry-run の鍵だけ", dict(env="prod", allow_prod_dry_run=True), dict(dry=True, cancel=False, submit=False)),
    ("prod 取消の鍵だけ", dict(env="prod", allow_prod_cancel=True), dict(dry=False, cancel=True, submit=False)),
    ("prod dry-run ＋ 取消", dict(env="prod", allow_prod_dry_run=True, allow_prod_cancel=True), dict(dry=True, cancel=True, submit=False)),
    ("prod 本発注の鍵", dict(env="prod", allow_prod_orders=True), dict(dry=True, cancel=True, submit=True)),
    ("cert 鍵なし", dict(env="cert"), dict(dry=True, cancel=True, submit=True)),
]
for label, kwargs, expect in cases:
    print(f"[{label}]")
    client = Client(**kwargs)
    check("dry_run_order", lambda: client.dry_run_order("ACCT", ORDER), expect["dry"])
    check("cancel_order", lambda: client.cancel_order("ACCT", 1), expect["cancel"])
    check("submit_order", lambda: client.submit_order("ACCT", ORDER), expect["submit"])

print("すべて通った" if not FAIL else "NG あり")
sys.exit(1 if FAIL else 0)
