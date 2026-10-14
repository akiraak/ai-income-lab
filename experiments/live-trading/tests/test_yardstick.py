"""物差し A〜E（yardstick.py。プラン docs/plans/daily-record-auto.md）: 手で書いた 10/8・10/9 の表を合成の記録で再現する ／
期間の開け閉め ／ C の不一致 ／ D の人手 ／ 起動しなかった日 ／ E は写すだけ。ネットワークもファイルの外も使わない。

⚠ 13500t の記録はテストに入れない（口座の数字をリポジトリに入れない）＝ 10/7〜10/9 の形を、記録（live-trading.md §1）に書いた値から作り直した。
"""
import json

import pytest

import livefs
import yardstick

SYMS = ["T", "PFE", "NKE", "VZ", "BAC"]
MID_1008 = {"T": 24.875, "PFE": 27.795, "NKE": 34.865, "VZ": 46.425, "BAC": 53.425}
MID_1009 = {"T": 22.125, "PFE": 28.265, "NKE": 34.665, "VZ": 41.525, "BAC": 54.295}
SHARES = {"T": 4, "PFE": 4, "NKE": 3, "VZ": 2, "BAC": 2}          # 予算 $600 ÷ 5 本を 10/8 の中値で整数株
FILL_1008 = {"T1": {"T": 24.88, "PFE": 27.795, "NKE": 34.865, "VZ": 46.425, "BAC": 53.4377},
             "T6": {"T": 24.88, "PFE": 27.795, "NKE": 34.865, "VZ": 46.425, "BAC": 53.435}}


def _put(path, rows):
    livefs.append_many(str(path), [json.dumps(r, ensure_ascii=False) for r in rows])


def _order(d, rid, t, sym, side, shares, mid, px):
    return {"date": d, "run_id": rid, "symbol": sym, "side": side, "shares": shares, "mode": "submit",
            "parts": [{"trader": t, "shares": shares}], "quote_at_signal": {"mid": mid},
            "fills": [{"shares": shares, "price": px}] if px is not None else []}


def _sig(d, rid, t, sym, buy, pos):
    return {"date": d, "run_id": rid, "trader": t, "symbol": sym, "buy": buy, "exit": 100 - buy, "position_before": pos}


def _led(d, rid, t, realized, fees, unrealized, holdings):
    return {"date": d, "run_id": rid, "trader": t, "budget_usd": 600.0, "realized_usd": realized, "fees_usd": fees,
            "unrealized_usd": unrealized, "holdings": {s: {"shares": q, "avg_price": p} for s, (q, p) in holdings.items()}}


def _positions(rid, d, held):
    return [{"date": d, "run_id": rid, "when": "after",
             "positions": [{"symbol": s, "quantity": q, "quantity-direction": "Long"} for s, q in held.items() if q]}]


def _conf(tmp_path, names=("T1", "T4", "T6")):
    cdir = tmp_path / "config" / "traders"
    cdir.mkdir(parents=True, exist_ok=True)
    for n in names:
        (cdir / f"{n}.toml").write_text(f'name = "{n}"\nthreshold = 50.0\nsymbols = {json.dumps(SYMS)}\nsizing = "shares"\n', encoding="utf-8")


def _october(tmp_path):
    """10/7（T1 の手じまい・T4 ／ T6 は一時停止）→ 10/8（1 日目）→ 10/9（2 日目）。"""
    _conf(tmp_path)
    out = tmp_path / "out"
    # 10/7: T1 の前の期間の終わり（実現 −$5.53・手数料 $0.0924 の時点で持ち株 0）
    d, rid = "2026-10-07", "r1007"
    _put(out / d / "events.jsonl", [
        {"date": d, "run_id": rid, "kind": "start", "mode": "submit", "traders": ["T1", "T4", "T6"]},
        {"date": d, "run_id": rid, "kind": "paused", "trader": "T4"}, {"date": d, "run_id": rid, "kind": "paused", "trader": "T6"},
        {"date": d, "run_id": rid, "kind": "liquidate_flag", "trader": "T1"},
        {"date": d, "run_id": rid, "kind": "liquidate_complete", "trader": "T1"},
        {"date": d, "run_id": rid, "kind": "end", "bad": 0}])
    _put(out / d / "ledger.jsonl", [_led(d, rid, "T1", -5.53, 0.0924, 0.0, {})])
    _put(out / d / "positions.jsonl", _positions(rid, d, {}))

    # 10/8: T1 ・ T6 が 5 本ずつ買い、T4 は 5 本とも skip
    d, rid = "2026-10-08", "r1008"
    ev = [{"date": d, "run_id": rid, "kind": "start", "mode": "submit", "traders": ["T1", "T4", "T6"], "roster": True}]
    sig, orders = [], []
    for s in SYMS:
        sig += [_sig(d, rid, "T1", s, 51.0, 0), _sig(d, rid, "T6", s, 52.1, 0), _sig(d, rid, "T4", s, 48.0, 0)]
        ev.append({"date": d, "run_id": rid, "kind": "skip", "trader": "T4", "symbol": s})
        for t in ("T1", "T6"):
            mid = MID_1008[s] if not (t == "T1" and s == "T") else 24.8751
            orders.append(_order(d, rid, t, s, "buy", SHARES[s], mid, FILL_1008[t][s]))
    ev.append({"date": d, "run_id": rid, "kind": "end", "bad": 0})
    _put(out / d / "events.jsonl", ev)
    _put(out / d / "signals.jsonl", sig)
    _put(out / d / "orders.jsonl", orders)
    _put(out / d / "quotes.jsonl", [{"date": d, "run_id": rid, "quotes": {s: {"mid": m} for s, m in MID_1008.items()}}])
    unreal = {t: sum(SHARES[s] * (MID_1008[s] - FILL_1008[t][s]) for s in SYMS) for t in ("T1", "T6")}
    _put(out / d / "ledger.jsonl", [
        _led(d, rid, "T1", -5.53, 0.1074, unreal["T1"], {s: (SHARES[s], FILL_1008["T1"][s]) for s in SYMS}),
        _led(d, rid, "T4", 0.0, 0.0, 0.0, {}),
        _led(d, rid, "T6", 0.0, 0.015, unreal["T6"], {s: (SHARES[s], FILL_1008["T6"][s]) for s in SYMS})])
    _put(out / d / "positions.jsonl", _positions(rid, d, {s: 2 * SHARES[s] for s in SYMS}))

    # 10/9: T1 が T ・ NKE ・ VZ を売り、T4 が PFE ・ VZ ・ BAC を買い、T6 は 5 本とも持つ
    d, rid = "2026-10-09", "r1009"
    t1_buy = {"T": 49.042572, "PFE": 51.29158, "NKE": 49.847065, "VZ": 49.679066, "BAC": 50.684895}
    t4_buy = {"T": 49.951727, "PFE": 50.425496, "NKE": 49.984652, "VZ": 50.035987, "BAC": 50.411591}
    sig = [_sig(d, rid, "T1", s, t1_buy[s], 1) for s in SYMS] + [_sig(d, rid, "T4", s, t4_buy[s], 0) for s in SYMS] \
        + [_sig(d, rid, "T6", s, 52.08, 1) for s in SYMS]
    ev = [{"date": d, "run_id": rid, "kind": "start", "mode": "submit", "traders": ["T1", "T4", "T6"], "roster": True}]
    ev += [{"date": d, "run_id": rid, "kind": "hold", "trader": "T1", "symbol": s} for s in ("PFE", "BAC")]
    ev += [{"date": d, "run_id": rid, "kind": "skip", "trader": "T4", "symbol": s} for s in ("T", "NKE")]
    ev += [{"date": d, "run_id": rid, "kind": "hold", "trader": "T6", "symbol": s} for s in SYMS]
    ev.append({"date": d, "run_id": rid, "kind": "end", "bad": 0})
    orders = [_order(d, rid, "T1", "T", "sell", 4, 22.125, 22.125), _order(d, rid, "T1", "NKE", "sell", 3, 34.665, 34.6525),
              _order(d, rid, "T1", "VZ", "sell", 2, 41.525, 41.525),
              _order(d, rid, "T4", "PFE", "buy", 4, 28.265, 28.265), _order(d, rid, "T4", "VZ", "buy", 2, 41.525, 41.525),
              _order(d, rid, "T4", "BAC", "buy", 2, 54.295, 54.285)]
    _put(out / d / "events.jsonl", ev)
    _put(out / d / "signals.jsonl", sig)
    _put(out / d / "orders.jsonl", orders)
    _put(out / d / "quotes.jsonl", [{"date": d, "run_id": rid, "quotes": {s: {"mid": m} for s, m in MID_1009.items()}}])
    _put(out / d / "ledger.jsonl", [
        _led(d, rid, "T1", -26.99, 0.1424, 3.59, {"PFE": (4, 27.795), "BAC": (2, 53.4377)}),
        _led(d, rid, "T4", 0.0, 0.008, 0.02, {"PFE": (4, 28.265), "VZ": (2, 41.525), "BAC": (2, 54.285)}),
        _led(d, rid, "T6", 0.0, 0.015, -17.82, {s: (SHARES[s], FILL_1008["T6"][s]) for s in SYMS})])
    _put(out / d / "positions.jsonl", _positions(rid, d, {"T": 4, "PFE": 12, "NKE": 3, "VZ": 4, "BAC": 6}))
    return str(out)


def test_reproduces_the_hand_written_table_of_oct_8_and_9(tmp_path):
    rows = yardstick.build_rows(_october(tmp_path))
    assert {(r["date"], r["trader"]) for r in rows} == {(d, t) for d in ("2026-10-08", "2026-10-09") for t in ("T1", "T4", "T6")}
    s = {t: yardstick.summarize(rows, t) for t in ("T1", "T4", "T6")}
    for t in s:
        assert s[t]["since"] == "2026-10-08" and s[t]["days"] == 2
        assert s[t]["c"]["days"] == 0                                  # C: 規則どおり（手書き: 0 日）
        assert s[t]["d"] == {"ok_days": 2, "ran_days": 2, "days": 2, "manual": 0, "gap_shares": 0.0, "ok": True}
    # A（手書き: T1 0.00 ／ +3.61・8 本、T4 0.00 ／ 0.00・3 本、T6 0.00 ／ +2.01・5 本）
    assert (s["T1"]["a"]["median_bp"], s["T1"]["a"]["max_bp"], s["T1"]["a"]["n"]) == (0.0, 3.61, 8)
    assert (s["T4"]["a"]["median_bp"], s["T4"]["a"]["max_bp"], s["T4"]["a"]["n"]) == (0.0, 0.0, 3)
    assert (s["T6"]["a"]["median_bp"], s["T6"]["a"]["max_bp"], s["T6"]["a"]["n"]) == (0.0, 2.01, 5)
    # B: その人の損益は手書きと同じ（T1 −$17.92 ＝ 10/7 までの実現 −$5.53 を数えない ／ T6 −$17.84 ／ T4 +$0.01）
    assert s["T1"]["b"]["real_usd"] == pytest.approx(-17.92, abs=0.005)
    assert s["T4"]["b"]["real_usd"] == pytest.approx(0.012, abs=0.005)
    assert s["T6"]["b"]["real_usd"] == pytest.approx(-17.835, abs=0.005)
    # 基準は K3 ＝ 10/8 の中値（手書きは T1 の約定 −$17.82）。差は 1 日目の差 1（T +2bp・BAC +2bp ≈ $0.04）のぶん
    assert s["T1"]["b"]["hold_usd"] == pytest.approx(-17.78, abs=0.005)
    assert s["T1"]["b"]["diff_usd"] == pytest.approx(-0.14, abs=0.01)   # 手書き −$0.10
    assert s["T4"]["b"]["diff_usd"] == pytest.approx(17.79, abs=0.01)   # 手書き +$17.83
    assert s["T6"]["b"]["diff_usd"] == pytest.approx(-0.055, abs=0.01)  # 手書き −$0.02


def test_t1_pause_then_cancel_is_not_manual_and_roster_start_is_not_manual(tmp_path):
    """10/9 の朝の一時停止 → 取り消しは記録（events）に効かない ＝ 人手に数えない。10/8 の名簿での開始も数えない（K4）。"""
    rows = yardstick.build_rows(_october(tmp_path))
    assert all(r["d_manual"] == 0 and r["d_manual_detail"] == "" for r in rows)


def test_the_previous_period_closes_on_liquidate_complete_and_b_starts_from_its_ledger(tmp_path):
    out = _october(tmp_path)
    rows = yardstick.build_rows(out)
    t1 = [r for r in rows if r["trader"] == "T1"]
    assert [r["since"] for r in t1] == ["2026-10-08", "2026-10-08"] and [r["day_n"] for r in t1] == [1, 2]


def test_c_counts_a_decision_against_the_rule_and_not_a_held_back_one(tmp_path):
    out = _october(tmp_path)
    d, rid = "2026-10-12", "r1012"
    _put(tmp_path / "out" / d / "events.jsonl", [
        {"date": d, "run_id": rid, "kind": "start", "mode": "submit", "traders": ["T1", "T4", "T6"]},
        {"date": d, "run_id": rid, "kind": "hold", "trader": "T1", "symbol": "PFE"},          # 規則は売り（出口% 60 > 50）
        {"date": d, "run_id": rid, "kind": "too_small", "trader": "T1", "symbol": "T"},        # 見送り ＝ 不一致にしない
        {"date": d, "run_id": rid, "kind": "end", "bad": 0}])
    _put(tmp_path / "out" / d / "signals.jsonl", [_sig(d, rid, "T1", "PFE", 40.0, 1), _sig(d, rid, "T1", "T", 60.0, 0)])
    rows = yardstick.build_rows(out)
    r = next(r for r in rows if r["date"] == d and r["trader"] == "T1")
    assert r["c_mismatch"] == 1 and "PFE: 規則 sell ／ 実物 hold" in r["c_detail"] and r["held_back"] == 1
    assert "c_mismatch" in r["problems"].split(";") and r["d_ok"] == 1      # 配線の不一致は D を崩さない（別の物差し）
    assert yardstick.summarize(rows, "T1")["c"] == {"days": 1, "ok": False}


def test_a_business_day_without_records_is_a_no_run_row(tmp_path):
    out = _october(tmp_path)
    d, rid = "2026-10-13", "r1013"                                            # 10/12（月）の記録が無い
    _put(tmp_path / "out" / d / "events.jsonl", [
        {"date": d, "run_id": rid, "kind": "start", "mode": "submit", "traders": ["T1", "T4", "T6"]},
        {"date": d, "run_id": rid, "kind": "end", "bad": 0}])
    rows = yardstick.build_rows(out)
    miss = [r for r in rows if r["date"] == "2026-10-12"]
    assert {r["trader"] for r in miss} == {"T1", "T4", "T6"}
    assert all(r["status"] == "no_run" and r["d_ran"] == 0 and r["problems"] == "no_run" for r in miss)
    s = yardstick.summarize(rows, "T6")
    assert s["d"]["days"] == 4 and s["d"]["ok_days"] == 3 and s["d"]["ok"] is False      # 3 ／ 4 ＜ 95%


def test_manual_counts_a_rerun_a_new_pause_and_a_halt_once(tmp_path):
    out = _october(tmp_path)
    o = tmp_path / "out"
    d = "2026-10-12"
    _put(o / d / "events.jsonl", [
        {"date": d, "run_id": "a", "kind": "start", "mode": "submit", "traders": ["T1", "T4", "T6"]},
        {"date": d, "run_id": "a", "kind": "paused", "trader": "T4"},
        {"date": d, "run_id": "a", "kind": "end", "bad": 0},
        {"date": d, "run_id": "b", "kind": "start", "mode": "submit", "traders": ["T1"]},     # 同じ日の 2 回目 ＝ 人が起こした
        {"date": d, "run_id": "b", "kind": "end", "bad": 0}])
    d2 = "2026-10-13"
    _put(o / d2 / "events.jsonl", [
        {"date": d2, "run_id": "c", "kind": "start", "mode": "submit", "traders": ["T1", "T4", "T6"]},
        {"date": d2, "run_id": "c", "kind": "paused", "trader": "T4"},                         # 続いている一時停止は数えない
        {"date": d2, "run_id": "c", "kind": "end", "bad": 0}])
    d3, d4 = "2026-10-14", "2026-10-15"
    _put(o / d3 / "events.jsonl", [{"date": d3, "run_id": "h", "kind": "halted"}])            # HALT が効いた最初の日
    _put(o / d4 / "events.jsonl", [{"date": d4, "run_id": "h2", "kind": "halted"}])
    rows = yardstick.build_rows(out)
    by = {(r["date"], r["trader"]): r for r in rows}
    assert by[(d, "T1")]["d_manual"] == 1 and by[(d, "T1")]["d_manual_detail"] == "rerun 1"
    assert by[(d, "T4")]["d_manual"] == 1 and by[(d, "T4")]["status"] == "paused" and by[(d, "T4")]["d_ok"] == 1
    assert by[(d2, "T4")]["d_manual"] == 0
    assert by[(d3, "T6")]["d_manual"] == 1 and by[(d3, "T6")]["status"] == "refused" and by[(d3, "T6")]["problems"] == "halted"
    assert by[(d4, "T6")]["d_manual"] == 0


def test_gap_between_account_and_ledgers_is_a_problem(tmp_path):
    out = _october(tmp_path)
    d, rid = "2026-10-12", "r1012"
    _put(tmp_path / "out" / d / "events.jsonl", [
        {"date": d, "run_id": rid, "kind": "start", "mode": "submit", "traders": ["T1", "T4", "T6"]},
        {"date": d, "run_id": rid, "kind": "end", "bad": 0}])
    _put(tmp_path / "out" / d / "positions.jsonl", _positions(rid, d, {"T": 4, "PFE": 12, "NKE": 3, "VZ": 4, "BAC": 7}))
    rows = yardstick.build_rows(out)
    r = next(r for r in rows if r["date"] == d and r["trader"] == "T6")
    assert r["d_gap_shares"] == 1.0 and "gap" in r["problems"] and r["d_ok"] == 0


def test_e_is_copied_from_daily_csv_and_not_computed(tmp_path):
    out = _october(tmp_path)
    livefs.write_doc(str(tmp_path / "out" / "daily.csv"),
                     "date,trader,diff3a_bp,diff3a_cum_bp\n2026-10-08,T1,2.52,2.52\n2026-10-09,T1,-4.0,-1.48\n")
    rows = yardstick.build_rows(out, str(tmp_path / "out" / "daily.csv"))
    s = yardstick.summarize(rows, "T1")
    assert s["e"] == {"mean_abs_bp": 3.26, "n": 2, "ok": True}
    assert yardstick.summarize(rows, "T6")["e"] == {"mean_abs_bp": None, "n": 0, "ok": None}


def test_csv_round_trip_gives_the_same_summary(tmp_path):
    out = _october(tmp_path)
    rows = yardstick.build_rows(out)
    path = str(tmp_path / "out" / "yardstick.csv")
    yardstick.write_csv(path, rows)
    back = yardstick.read_csv(path)
    assert list(back[0]) == yardstick.COLUMNS
    for t in ("T1", "T4", "T6"):
        assert yardstick.summarize(back, t) == yardstick.summarize(rows, t)


def test_builds_nothing_without_records(tmp_path):
    livefs.write_doc(str(tmp_path / "out" / "daily.csv"), "date,trader\n")
    assert yardstick.build_rows(str(tmp_path / "out")) == []
