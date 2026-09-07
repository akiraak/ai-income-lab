"""操作（プラン §2-1 の 1〜3 段目）。

- 停止（両面）: HALT フラグを書き、働いている注文を全部取り消す（取消は cert 常に可、prod は scope に trade があるときだけ）
- 解除・cert の dry-run → 発注 → 取消 → 後片付け（ローカル面）
- prod の dry-run は TT_ALLOW_PROD_DRY_RUN=1、本発注は TT_ALLOW_PROD_ORDERS=1 ＋ 確認文の入力。
  ⚠ 停止中（HALT）は環境を問わず発注しない
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import record
from ttclient import ApiError, Client, ProductionGuard

from .config import Settings
from .masking import Redactor
from .monitor import EventLog, EnvMonitor, Monitors, WORKING_STATUSES, field, order_brief, utcnow_iso

CONFIRM_PHRASE = "i-know-this-is-real-money"
ACTIONS = ("Buy to Open", "Sell to Close", "Sell to Open", "Buy to Close")
ORDER_TYPES = ("Limit", "Market")


class OpsError(Exception):
    pass


def dry_run_brief(dry: dict) -> dict:
    bpe = dry.get("buying-power-effect") or {}
    fee = dry.get("fee-calculation") or {}
    return {
        "status": field(dry.get("order") or {}, "status"),
        "buying_power": {k: field(bpe, k) for k in ("change-in-buying-power", "change-in-buying-power-effect", "current-buying-power", "new-buying-power")},
        "fees": {k: field(fee, k) for k in ("total-fees", "total-fees-effect")},
        "warnings": record.excerpt(dry.get("warnings")),
        "errors": record.excerpt(dry.get("errors")),
    }


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
        info = {"since": utcnow_iso(), "actor": actor, "reason": reason[:200]}
        self.settings.halt_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings.halt_file.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        results = []
        for env, mon in self.monitors.items.items():
            if not mon.client.token or not mon.account_number:
                results.append({"env": env, "skipped": "認証されていない（取消は行わない）"})
                continue
            if not mon.can_cancel:
                results.append({"env": env, "skipped": f"資格情報の scope に trade が無い（{mon.client.token.scope}）。取消は不可"})
                continue
            try:
                results.append({"env": env, **self._cancel_all(env, mon)})
            except (ApiError, ProductionGuard, OSError) as exc:
                results.append({"env": env, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        out = {"halted": True, **info, "results": results}
        self.events.append("dashboard", "-", "halt", out)
        self._history("halt", actor, "-", out)
        return out

    def resume(self, actor: str) -> dict:
        existed = self.settings.halt_file.exists()
        if existed:
            self.settings.halt_file.unlink()
        out = {"halted": False, "resumed_at": utcnow_iso(), "actor": actor, "was_halted": existed}
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

    def _client(self, env: str, dry_run: bool = False, cancel: bool = False, orders: bool = False) -> Client:
        """操作ごとに必要な鍵だけ開けたクライアント。トークンは監視ループのものを借りる。"""
        mon = self._monitor(env)
        client = Client(
            env=env,
            allow_prod_dry_run=dry_run and self.settings.allow_prod_dry_run,
            allow_prod_cancel=cancel,
            allow_prod_orders=orders and self.settings.allow_prod_orders,
            rest_base=mon.creds.get("rest_base"),
            account_streamer=mon.creds.get("account_streamer"),
        )
        client.token = mon.client.token
        return client

    def build_order(self, spec: dict) -> dict:
        symbol = str(spec.get("symbol") or self.settings.symbol).upper().strip()
        if not symbol.isalnum() or len(symbol) > 8:
            raise OpsError("銘柄は英数字 8 文字まで")
        try:
            quantity = int(spec.get("quantity") or 1)
        except ValueError:
            raise OpsError("数量は整数")
        if not 1 <= quantity <= 10:
            raise OpsError("数量は 1〜10（検証用。10 株を超える注文はこの画面から出さない）")
        action = spec.get("action") or "Buy to Open"
        if action not in ACTIONS:
            raise OpsError("action が不正")
        order_type = spec.get("order_type") or "Limit"
        if order_type not in ORDER_TYPES:
            raise OpsError("order_type は Limit か Market")
        price = None
        if order_type == "Limit":
            try:
                price = f"{float(spec.get('price')):.2f}"
            except (TypeError, ValueError):
                raise OpsError("指値には価格が要る")
            if float(price) <= 0:
                raise OpsError("価格は正の数")
        return Client.build_equity_order(symbol, quantity, action=action, order_type=order_type, price=price)

    # ---------------- 操作

    def dry_run(self, env: str, spec: dict, actor: str) -> dict:
        client = self._client(env, dry_run=True)
        order = self.build_order(spec)
        try:
            dry = client.dry_run_order(self._monitor(env).account_number, order)
        except ProductionGuard as exc:
            raise OpsError(str(exc))
        out = {"env": env, "order": order, "dry_run": dry_run_brief(dry)}
        self._history("dry_run", actor, env, out)
        return out

    def submit(self, env: str, spec: dict, actor: str, confirm: str = "") -> dict:
        if self.halt_status()["halted"]:
            raise OpsError("停止中（HALT）。発注しない。先に解除する")
        if env == "prod":
            if not self.settings.allow_prod_orders:
                raise OpsError("本番の発注は TT_ALLOW_PROD_ORDERS=1 が無いと開かない")
            if confirm.strip() != CONFIRM_PHRASE:
                raise OpsError(f"確認文が違う。本番で実弾を通すときは「{CONFIRM_PHRASE}」と入力する")
        client = self._client(env, dry_run=True, orders=(env == "prod"))
        order = self.build_order(spec)
        acct = self._monitor(env).account_number
        try:
            dry = client.dry_run_order(acct, order)  # 本発注の前に必ず通す
            submitted = client.submit_order(acct, order)
        except ProductionGuard as exc:
            raise OpsError(str(exc))
        out = {"env": env, "order": order, "dry_run": dry_run_brief(dry), "submitted": order_brief(submitted.get("order") or {})}
        self._history("submit", actor, env, out)
        self.events.append("tastytrade", env, "order_submitted", {"order_id": out["submitted"].get("id"), "actor": actor, "external_identifier": order["external-identifier"]})
        return out

    def cancel(self, env: str, order_id: str, actor: str) -> dict:
        client = self._client(env, cancel=True)
        try:
            res = client.cancel_order(self._monitor(env).account_number, order_id)
        except ProductionGuard as exc:
            raise OpsError(str(exc))
        out = {"env": env, "order": order_brief(res)}
        self._history("cancel", actor, env, out)
        return out

    def cleanup(self, env: str, actor: str) -> dict:
        mon = self._monitor(env)
        out = {"env": env, **self._cancel_all(env, mon)}
        self._history("cleanup", actor, env, out)
        return out

    def _cancel_all(self, env: str, mon: EnvMonitor) -> dict:
        client = self._client(env, cancel=True)
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
        row = {"at": utcnow_iso(), "kind": kind, "actor": actor, "env": env, "detail": self.redactor(detail)}
        with self._lock:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def history(self, n: int = 50) -> list[dict]:
        if not self.history_path.exists():
            return []
        rows = []
        with open(self.history_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return rows[-n:][::-1]
