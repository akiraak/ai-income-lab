"""操作。⚠ **管理画面から出せるのは「停止」と「解除」だけ**（2026-09-18 に手動の注文を外した）。

- 停止（両面）: HALT フラグを書き、働いている注文を全部取り消す（取消は cert 常に可、prod は scope に trade があるときだけ）
- 解除（ローカル面）
- ⚠ **このモジュールが開ける本番の鍵は「取消」だけ**（`allow_prod_cancel`）。dry-run の鍵も発注の鍵も渡さない ＝
  管理画面のコードから発注はできない。実売買の発注は執行器（`experiments/live-trading/run_day.py`）が行う
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ttclient import ApiError, Client, ProductionGuard

from .config import Settings
from .masking import Redactor
from .livestore import livefs
from .monitor import EventLog, EnvMonitor, Monitors, WORKING_STATUSES, field, utcnow_iso


class OpsError(Exception):
    pass


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
