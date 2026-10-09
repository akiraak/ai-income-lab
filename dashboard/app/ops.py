"""操作。⚠ **管理画面から出せるのは「全体の停止」と「解除」と、トレーダーの状態を変える指示（開始 ／ 一時停止 ／ 手じまい ／ 取り消す）だけ**（2026-09-18 に手動の注文を外した）。

- 状態を変える指示（2026-10-09。プラン docs/plans/trader-status-flow.md。前は「人ごとの印」と「名簿の開始 ／ 外す」）:
  名簿 `roster.json`（`HALT` の隣）と `control/<人>.json` を書くだけ。⚠ **その場では何も売買しない**。執行器の次の回が読む
  （`run_day.py --traders @roster`。発注の許可は執行器だけ ＝ この画面に発注の経路は無いまま）

- 停止（両面）: HALT フラグを書き、働いている注文を全部取り消す（取消は cert 常に可、prod は scope に trade があるときだけ）
- 解除（ローカル面）
- ⚠ **このモジュールが開ける本番の鍵は「取消」だけ**（`allow_prod_cancel`）。dry-run の鍵も発注の鍵も渡さない ＝
  管理画面のコードから発注はできない。実売買の発注は執行器（`experiments/live-trading/run_day.py`）が行う
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

from ttclient import ApiError, Client, ProductionGuard

from .config import Settings
from .masking import Redactor
from .livestore import livefs
from .monitor import EventLog, EnvMonitor, Monitors, WORKING_STATUSES, field, utcnow_iso


class OpsError(Exception):
    pass


def _load_executor_module(stem: str):
    """執行器の `<stem>.py`（experiments/live-trading。標準ライブラリだけ）を道で読む ＝ 印・名簿の形の正本は執行器の側。"""
    path = Path(__file__).resolve().parents[2] / "experiments" / "live-trading" / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"lt_{stem}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"lt_{stem}"] = mod          # ⚠ dataclass（from __future__ import annotations）は sys.modules に居る前提で型を引く
    spec.loader.exec_module(mod)
    return mod


control = _load_executor_module("control")
roster = _load_executor_module("roster")     # 名簿（2026-10-07。プラン trader-roster-dashboard.md）


class Ops:
    def __init__(self, settings: Settings, monitors: Monitors, redactor: Redactor, events: EventLog) -> None:
        self.settings = settings
        self.monitors = monitors
        self.redactor = redactor
        self.events = events
        self.history_path: Path = settings.ops_dir / "history.jsonl"
        self._lock = threading.Lock()

    # ---------------- 停止

    def halt_status(self) -> dict:
        path = self.settings.halt_file
        if not path.exists():
            return {"halted": False, "path": str(path)}
        try:
            info = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError):
            info = {}
        return {"halted": True, "path": str(path), **{k: info.get(k) for k in ("since", "actor", "reason")}}

    def halt(self, actor: str, reason: str = "") -> dict:
        """HALT を書いてから、取り消せる環境の働いている注文を全部取り消す。"""
        machine = self.settings.machine()
        halt_file = self.settings.halt_file
        info = {"since": utcnow_iso(), "actor": actor, "reason": reason[:200]}
        halt_file.parent.mkdir(parents=True, exist_ok=True)
        halt_file.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        results = []
        if machine["mode"] == "sim":
            # ⚠ シミュレーションモードの停止ボタン ＝ シミュレーションの木の HALT だけ（通し稽古）。本物の HALT も本物の注文も触らない
            results.append({"env": f"sim:{machine['name']}", "skipped": "シミュレーション: 仮の執行器は次の発注から拒否する。本物の口座の注文には触らない"})
        for env, mon in ([] if machine["mode"] == "sim" else self.monitors.items.items()):
            if not mon.client.token or not mon.account_number:
                results.append({"env": env, "skipped": "認証されていない（取消は行わない）"})
                continue
            if not mon.can_cancel:
                results.append({"env": env, "skipped": f"資格情報の scope に trade が無い（{mon.client.token.scope}）。取消は不可"})
                continue
            try:
                results.append({"env": env, **self._cancel_all(env, mon)})
            except (ApiError, ProductionGuard, OpsError, OSError) as exc:   # ⚠ 停止は途中で落とさない（HALT は書けている）
                results.append({"env": env, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        out = {"halted": True, "mode": machine["mode"], **info, "results": results}
        self.events.append("dashboard", "-", "halt", out)
        self._history("halt", actor, "-", out)
        return out

    def resume(self, actor: str) -> dict:
        halt_file = self.settings.halt_file
        existed = halt_file.exists()
        if existed:
            halt_file.unlink()
        out = {"halted": False, "mode": self.settings.machine()["mode"], "resumed_at": utcnow_iso(), "actor": actor, "was_halted": existed}
        self.events.append("dashboard", "-", "resume", out)
        self._history("resume", actor, "-", out)
        return out

    # ---------------- 置き場（`control/<人>.json` ＝ 一時停止 ／ 手じまい・名簿 ＝ 誰を動かすか）。⚠ 読むだけ。書くのは下の指示

    def control_dir(self) -> Path:
        return Path(control.control_dir(str(self.settings.halt_file)))

    def flags(self) -> dict[str, dict]:
        """{識別名: {kind, label, since, actor, reason, done}}。壊れた印は label に「読めない」（執行器は起動を拒む）。"""
        out: dict[str, dict] = {}
        try:
            for name, fl in control.read_all(str(self.control_dir())).items():
                out[name] = {**fl.as_dict(), "label": fl.describe(), "kind": "liquidate" if fl.liquidate else ("paused" if fl.paused else None)}
        except control.ControlError as exc:
            out["?"] = {"label": "読めない", "error": str(exc)[:200], "kind": None}
        return out

    def flag_status(self, name: str) -> dict | None:
        try:
            fl = control.read_flag(str(self.control_dir()), name)
        except control.ControlError as exc:
            return {"name": name, "label": "読めない", "error": str(exc)[:200], "kind": None}
        return None if fl is None else {**fl.as_dict(), "label": fl.describe(), "kind": "liquidate" if fl.liquidate else "paused"}

    def roster_path(self) -> Path:
        return Path(roster.roster_path(str(self.settings.halt_file)))

    def roster_entries(self) -> tuple[list | None, str | None]:
        """(名簿 ／ 無ければ None, 読めない理由)。"""
        try:
            return roster.read(str(self.roster_path())), None
        except roster.RosterError as exc:
            return None, str(exc)[:200]

    # ---------------- 状態を変える指示（開始 ／ 一時停止 ／ 手じまい ／ 取り消す。plan trader-status-flow.md §2-3）
    # ⚠ 書くだけ。売買は執行器の次の回。押せるかの判定は lineup.actions（呼ぶ側が先に見る）。⚠ 置き場の形は変えない

    def instruct(self, name: str, kind: str, row: dict, actor: str, reason: str = "") -> dict:
        """指示を置き場に書く。`row` ＝ lineup.board の行（いまの状態）。前の形を履歴に残す（取り消すときに戻す）。"""
        if kind not in ("start", "paused", "liquidate"):
            raise OpsError(f"指示は start ／ paused ／ liquidate だけ: {kind!r}")
        cdir, rpath = str(self.control_dir()), str(self.roster_path())
        before = control.read_flag(cdir, name)
        entries = roster.read(rpath) or []
        idx = next((i for i, e in enumerate(entries) if e.name == name), None)
        prev_entry = entries[idx].as_dict() if idx is not None else None
        touched_roster = False
        if kind == "start":
            if idx is None:
                roster.add(rpath, name, actor, reason)                       # 名簿の外 → 入れる
                touched_roster = True
            elif before is not None and before.liquidate:
                control.clear_flag(cdir, name)                               # 手じまい済み → 印を消し、「いつから」を今に付け直す（後から入った人として休む順へ）
                roster.remove(rpath, name)
                roster.add(rpath, name, actor, reason)
                touched_roster = True
            else:
                control.clear_flag(cdir, name)                               # 一時停止 → 印を消す
        else:
            control.set_flag(cdir, name, kind, actor, reason)
        out = {"trader": name, "instruction": kind, "kind": kind, "prev": row.get("status"), "prev_flag": before.as_dict() if before else None,
               "prev_entry": prev_entry, "prev_index": idx, "roster_touched": touched_roster, "actor": actor, "reason": reason[:200], "mode": self.settings.machine()["mode"],
               "note": "その場では何も売買しない。執行器の次の回から状態が変わる"}
        kind_rec = "roster" if touched_roster else "trader_flag"            # ⚠ 記録の種類の名前は今までどおり
        self.events.append("dashboard", "-", kind_rec, out)
        self._history(kind_rec, actor, "-", out)
        return out

    def cancel(self, name: str, pending: dict, actor: str) -> dict:
        """回を通る前の指示を取り消す ＝ 指示の前の形に戻す（`control/<人>.json` と名簿のその人の行）。"""
        d = pending.get("detail") or {}
        control.restore_flag(str(self.control_dir()), name, d.get("prev_flag"))
        if d.get("roster_touched"):
            roster.restore(str(self.roster_path()), name, d.get("prev_entry"), d.get("prev_index"))
        out = {"trader": name, "instruction": "cancel", "kind": "cancel", "cancelled": d.get("instruction"), "back_to": d.get("prev"),
               "actor": actor, "mode": self.settings.machine()["mode"]}
        kind_rec = "roster" if d.get("roster_touched") else "trader_flag"
        self.events.append("dashboard", "-", kind_rec, out)
        self._history(kind_rec, actor, "-", out)
        return out

    def instructions(self) -> dict[str, dict]:
        """その人の最新の指示の行（操作の履歴の trader_flag ／ roster）。lineup.pending_of が「回を通ったか」を決める。"""
        out: dict[str, dict] = {}
        for line in livefs.read_lines(self.history_path, missing_ok=True):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            d = row.get("detail") or {}
            if row.get("kind") in ("trader_flag", "roster") and d.get("trader"):
                out[d["trader"]] = row
        return out

    # ---------------- 共通

    def _monitor(self, env: str) -> EnvMonitor:
        mon = self.monitors.get(env)
        if mon is None:
            raise OpsError(f"{env} の資格情報が設定されていない")
        if not mon.client.token:
            raise OpsError(f"{env} はまだ認証されていない（監視ループの認証を待つ）")
        if not mon.account_number:
            raise OpsError(f"{env} の口座番号がまだ取れていない")
        return mon

    def _client(self, env: str) -> Client:
        """取消の鍵だけ開けたクライアント（⚠ **発注と dry-run の鍵は渡さない**）。トークンは監視ループのものを借りる。"""
        mon = self._monitor(env)
        client = Client(
            env=env,
            allow_prod_cancel=True,
            rest_base=mon.creds.get("rest_base"),
            account_streamer=mon.creds.get("account_streamer"),
            timeout=mon.client.timeout,  # 待ち時間も監視のものを借りる（既定は ttclient の 30 秒のまま。テストだけ短くできる）
        )
        client.token = mon.client.token
        return client

    def _cancel_all(self, env: str, mon: EnvMonitor) -> dict:
        client = self._client(env)
        acct = mon.account_number
        working = [o for o in client.list_live_orders(acct) if field(o, "status") in WORKING_STATUSES]
        cancelled = []
        for o in working:
            oid = field(o, "id")
            try:
                res = client.cancel_order(acct, oid)
                cancelled.append({"order_id": oid, "was": field(o, "status"), "now": field(res, "status")})
            except ApiError as exc:
                cancelled.append({"order_id": oid, "was": field(o, "status"), "error": f"{exc.status} {exc.code}"})
        positions = [{"symbol": field(p, "symbol"), "quantity": field(p, "quantity")} for p in client.list_positions(acct)]
        return {"working": len(working), "cancelled": cancelled, "remaining_positions": positions}

    # ---------------- 履歴

    def _history(self, kind: str, actor: str, env: str, detail: dict) -> None:
        row = {"at": utcnow_iso(), "kind": kind, "actor": actor, "env": env, "mode": self.settings.machine()["mode"], "detail": self.redactor(detail)}
        with self._lock:
            livefs.append(self.history_path, json.dumps(row, ensure_ascii=False))     # ⚠ 2026-09-21 から DB（livefs）

    def history(self, n: int = 50) -> list[dict]:
        rows = []
        for line in livefs.read_lines(self.history_path, missing_ok=True):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-n:][::-1]
