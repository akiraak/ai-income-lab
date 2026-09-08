"""6 観点の判定を記録の値だけから組み立てる（プラン §2-4。成立条件は tastytrade プラン §2-2）。

点数は付けない。✅ / ⚠ / ❌ に「未実測（⏳）」を足した 4 状態。各セルに根拠の run_id と手順を残す。
モックの記録（mock: true）は除外する。監視ループの追記（monitor/*.jsonl）は観点 A の材料になる。
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .records import Run

ET = ZoneInfo("America/New_York")

MARKS = {"ok": "✅", "warn": "⚠", "ng": "❌", "na": "⏳"}

ASPECTS = [
    {"key": "A", "title": "認証の寿命", "ok": "refresh が無人で 1 営業日以上続く", "warn": "1 日 1 回の人手（ブラウザ）", "ng": "数時間ごとに人手"},
    {"key": "B", "title": "常駐", "ok": "不要（REST ＋ websocket で完結）", "warn": "補助プロセスが要る", "ng": "GUI 必須"},
    {"key": "C", "title": "現在値", "ok": "本番の REST / DXLink で実時間（遅延 < 1 秒）", "warn": "遅延あり", "ng": "取れない、または本番の資格情報が無く未実測"},
    {"key": "D", "title": "発注の往復", "ok": "4・5 とも通る（sandbox の疑似約定で状態遷移を確認）", "warn": "4 のみ通る", "ng": "通らない"},
    {"key": "E", "title": "レート制限", "ok": "1 分に 60 回の照会が通る", "warn": "429 が出るが回避できる", "ng": "発注が詰まる"},
    {"key": "F", "title": "SDK", "ok": "公式 SDK か OpenAPI 仕様がある", "warn": "非公式 SDK のみ、1 年以内に更新", "ng": "保守されていない"},
]


def _cell(mark: str, reason: str, evidence: list[dict] | None = None) -> dict:
    return {"mark": mark, "symbol": MARKS[mark], "reason": reason, "evidence": evidence or []}


def _ev(run: Run, step: int | None, label: str = "") -> dict:
    return {"run_id": run.run_id, "step": step, "label": label or (f"手順 {step}" if step is not None else run.run_id)}


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def in_market_hours(started_at: dict | None) -> bool:
    """ET の平日 9:30〜16:00 か（祝日は見ない。時間外の遅延は参考値にしかならない）。"""
    dt = _parse_iso((started_at or {}).get("utc"))
    if dt is None:
        return False
    et = dt.astimezone(ET)
    if et.weekday() >= 5:
        return False
    minutes = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def business_date(dt: datetime) -> str | None:
    et = dt.astimezone(ET)
    return et.date().isoformat() if et.weekday() < 5 else None


# ---------------------------------------------------------------- 観点ごと


def judge_auth(runs: list[Run], events: list[dict]) -> dict:
    ok_auth = [(r, r.step(1)) for r in runs if r.step(1) and r.step(1).get("ok")]
    if not ok_auth and not events:
        return _cell("na", "手順 1（認証）の記録が無い")

    dates: set[str] = set()
    evidence: list[dict] = []
    for run, row in ok_auth:
        dt = _parse_iso((row.get("started_at") or {}).get("utc"))
        if dt and (d := business_date(dt)):
            dates.add(d)
    refresh_ok = [e for e in events if e.get("kind") == "refresh_ok"]
    refresh_fail = [e for e in events if e.get("kind") == "refresh_fail"]
    for e in refresh_ok:
        dt = _parse_iso(e.get("at"))
        if dt and (d := business_date(dt)):
            dates.add(d)

    notes = []
    latest_run, latest_row = ok_auth[0] if ok_auth else (None, None)
    if latest_row:
        d = latest_row.get("detail") or {}
        # ⚠ JWT の exp − iat は寿命ではない（iat は grant を作った時刻で固定される。2026-09-08 に確認）。ここには出さない
        notes.append(f"expires_in {d.get('expires_in_s')} 秒")
        notes.append("refresh token は" + ("回転する" if d.get("refresh_token_rotated") else "回転しない"))
        evidence.append(_ev(latest_run, 1))
    expiry = [(r, r.step(11)) for r in runs if r.step(11)]
    if expiry:
        r, row = expiry[0]
        notes.append(f"失効は {row.get('result')}（{(row.get('detail') or {}).get('waited_s')} 秒待ち）")
        evidence.append(_ev(r, 11))
    if refresh_ok:
        notes.append(f"監視ループの refresh 成功 {len(refresh_ok)} 回・失敗 {len(refresh_fail)} 回")

    if len(dates) >= 2:
        span = f"{min(dates)} 〜 {max(dates)}（{len(dates)} 営業日）"
        if refresh_fail:
            return _cell("warn", f"営業日を跨いで refresh は通ったが失敗もある: {span}。" + "、".join(notes), evidence)
        return _cell("ok", f"同じ refresh token で営業日を跨いで交換できた: {span}。" + "、".join(notes), evidence)
    if ok_auth or refresh_ok:
        return _cell("na", "交換は通ったが、営業日を跨ぐ実測がまだ無い。" + "、".join(notes), evidence)
    return _cell("ng", f"refresh が通っていない（失敗 {len(refresh_fail)} 回）", evidence)


def judge_resident(runs: list[Run]) -> dict:
    for run in runs:
        s1, s2, s6 = run.step(1), run.step(2), run.step(6)
        if s1 and s2 and s6 and all(x.get("ok") for x in (s1, s2, s6)):
            return _cell("ok", "REST と websocket だけで認証・口座照会・口座ストリーマが通った（常駐プロセスも GUI も無し）", [_ev(run, 1), _ev(run, 6)])
    for run in runs:
        s1, s2 = run.step(1), run.step(2)
        if s1 and s2 and s1.get("ok") and s2.get("ok"):
            return _cell("warn", "REST は通ったが websocket（手順 6）の成功記録が無い", [_ev(run, 2)])
    return _cell("na", "手順 1・2 の記録が無い")


def judge_quote(runs: list[Run]) -> dict:
    prod_rows = [(r, row) for r in runs for row in r.rows if row.get("step") == 3 and row.get("env") == "prod"]
    ok_rows = [(r, row) for r, row in prod_rows if row.get("ok")]
    dx = [(r, r.step(61)) for r in runs if r.step(61) and r.step(61).get("ok")]
    dx_note = f"DXLink で {(dx[0][1].get('detail') or {}).get('event_count')} 件受信" if dx else ""
    if not ok_rows:
        cert_502 = any(row.get("result", "").startswith("unavailable_502") for r in runs for row in r.rows if row.get("step") == 3)
        if cert_502 and not prod_rows:
            return _cell("na", "本番の資格情報が無く、sandbox は相場データを配信しない（502）")
        if prod_rows:
            return _cell("ng", f"本番の REST で気配が取れなかった: {prod_rows[0][1].get('result')}", [_ev(prod_rows[0][0], 3)])
        return _cell("na", "手順 3 の記録が無い")
    in_hours = [(r, row) for r, row in ok_rows if in_market_hours(row.get("started_at"))]
    ev = [_ev(r, 3, "手順 3（prod）") for r, _ in (in_hours or ok_rows)[:2]] + ([_ev(dx[0][0], 61)] if dx else [])
    if in_hours:
        # 遅延は**サーバの時計で測った値**（delay_corrected_s。応答の Date でこちらの時計のずれを外したもの）を優先する。
        # 素の delay_s はこちらの時計に依存し、WSL2 では ±1 秒揺れて負にもなる（2026-09-08 の実測）。
        corrected = [float(d["delay_corrected_s"]) for _, row in in_hours if (d := row.get("detail") or {}).get("delay_corrected_s") is not None]
        delays = corrected or [float(d["delay_s"]) for _, row in in_hours if (d := row.get("detail") or {}).get("delay_s") is not None]
        if not delays:
            return _cell("warn", "市場時間内に取れたが delay_s が無い。" + dx_note, ev)
        src = "サーバの時計で" if corrected else "⚠ こちらの時計で（サーバ時刻の記録が無い）"
        # 負は「気配の時刻が計測時刻より先」＝ずれなので、大小ではなく**絶対値**で見る（-30 秒を 1 秒未満と読まないため）
        best = min(delays, key=abs)
        if abs(best) < 1.0:
            return _cell("ok", f"市場時間内の遅延 {best:.3f} 秒（{src}測った {len(delays)} 回の最小）。" + dx_note, ev)
        return _cell("warn", f"市場時間内の遅延 {best:.1f} 秒（{src}）。" + dx_note, ev)
    delay = (ok_rows[0][1].get("detail") or {}).get("delay_s")
    return _cell("na", f"本番で取れたが市場時間内の記録が無い（時間外の遅延 {delay} 秒は参考値）。" + dx_note, ev)


def judge_roundtrip(runs: list[Run]) -> dict:
    """手順 4（指値と取消）と 5（約定と反対売買）。

    ⚠ **同じ実行の中でそろっている必要はない。** `--step 4` と `--step 5` を別々に回しても、
    どちらも通っていれば成立とみなす（観点 A が日付を実行を跨いで集めるのと同じ）。
    """
    s4s = [(r, r.step(4)) for r in runs if r.step(4)]
    s5s = [(r, r.step(5) or r.step(51)) for r in runs if (r.step(5) or r.step(51))]
    ok4 = [(r, row) for r, row in s4s if row.get("ok")]
    ok5 = [(r, row) for r, row in s5s if row.get("ok")]

    if ok4 and ok5:
        r4, row4 = ok4[0]
        r5, row5 = ok5[0]
        d4 = row4.get("detail") or {}
        same = "" if r4.run_id == r5.run_id else "（別々の実行）"
        reason = (
            f"手順 4・5 とも通った{same}。dry-run {((d4.get('dry_run') or {}).get('elapsed_ms'))} ms / "
            f"発注 {((d4.get('submit') or {}).get('elapsed_ms'))} ms / 取消 {((d4.get('cancel') or {}).get('elapsed_ms'))} ms、"
            f"約定 {row5.get('result')}"
        )
        return _cell("ok", reason, [_ev(r4, 4), _ev(r5, row5.get("step"))])
    if ok4:
        r4, row4 = ok4[0]
        fail = s5s[0][1].get("result") if s5s else "未実行"
        ev = [_ev(r4, 4)] + ([_ev(s5s[0][0], s5s[0][1].get("step"))] if s5s else [])
        return _cell("warn", f"手順 4 は通った（{row4.get('result')}）が手順 5 は {fail}", ev)
    if s4s:
        r4, row4 = s4s[0]
        err = (row4.get("error") or {}).get("code") or row4.get("result")
        return _cell("ng", f"手順 4 が通らない: {err}", [_ev(r4, 4)])
    return _cell("na", "手順 4 の記録が無い")


def judge_rate(runs: list[Run]) -> dict:
    for run in runs:
        s7 = run.step(7)
        if not s7:
            continue
        d = s7.get("detail") or {}
        per_min = d.get("60_per_minute") or {}
        per_sec = d.get("10_per_second") or {}
        seen = [k for k, v in d.items() if isinstance(v, dict) and v.get("first_429_at_request")]
        med = (per_min.get("latency_ms") or {}).get("median")
        if not seen and per_min.get("requests", 0) >= 60:
            return _cell("ok", f"60 回/分・{per_sec.get('requests')} 連射とも 429 なし（中央値 {med} ms）", [_ev(run, 7)])
        if seen:
            at = ", ".join(f"{k} の {d[k]['first_429_at_request']} 回目" for k in seen)
            return _cell("warn", f"429 が出た: {at}（バックオフして止めた）", [_ev(run, 7)])
        return _cell("warn", f"429 は出なかったが 60 回に達していない（{per_min.get('requests')} 回）", [_ev(run, 7)])
    return _cell("na", "手順 rate（7）の記録が無い")


def judge_sdk(runs: list[Run]) -> dict:
    for run in runs:
        sdk = run.sdk
        if not sdk:
            continue
        label = str(sdk.get("sdk") or "")
        if label.startswith("none"):
            return _cell("ok", f"公式 OpenAPI 3.1 どおりに REST / websocket を直接叩いている（SDK 不要。{label}）", [_ev(run, run.rows[0].get("step"), "sdk 欄")])
        return _cell("warn", f"SDK を使っている: {label}（保守状況は README §6 を見る）", [_ev(run, run.rows[0].get("step"), "sdk 欄")])
    return _cell("na", "記録が無い")


# ---------------------------------------------------------------- まとめ


def judge(runs: list[Run], monitor_events: list[dict], include_mock: bool = False) -> dict:
    """include_mock はデモ用。モックの記録・イベントも材料にする（本物の判定には使わない）。"""
    real = list(runs) if include_mock else [r for r in runs if not r.mock]
    monitor_events = list(monitor_events) if include_mock else [e for e in monitor_events if not e.get("mock")]
    auth_events = [e for e in monitor_events if e.get("kind") in ("refresh_ok", "refresh_fail")]
    venues = sorted({r.venue for r in real} | {e.get("venue") for e in auth_events if e.get("venue")})
    cells: dict[str, dict] = {}
    for venue in venues:
        vruns = [r for r in real if r.venue == venue]
        vevents = [e for e in monitor_events if e.get("venue") == venue]
        cells[venue] = {
            "A": judge_auth(vruns, vevents),
            "B": judge_resident(vruns),
            "C": judge_quote(vruns),
            "D": judge_roundtrip(vruns),
            "E": judge_rate(vruns),
            "F": judge_sdk(vruns),
        }
        marks = [c["mark"] for c in cells[venue].values()]
        cells[venue]["_verdict"] = (
            "成立（無人で 1 営業日回る）" if all(m == "ok" for m in marks)
            else "不成立" if "ng" in marks
            else "判定中（未実測 or ⚠ が残る）"
        )
    return {
        "venues": venues,
        "aspects": ASPECTS,
        "cells": cells,
        "excluded_mock_runs": 0 if include_mock else sum(1 for r in runs if r.mock),
        "included_mock_runs": sum(1 for r in runs if r.mock) if include_mock else 0,
        "demo": include_mock,
        "real_runs": len(real),
        "monitor_events": len(monitor_events),
    }
