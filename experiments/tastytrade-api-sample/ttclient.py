"""tastytrade Open API の薄いクライアント。

公式 Python SDK（tastytrade-sdk）は GitHub リポジトリが archived なので使わず、
公式 OpenAPI 3.1 仕様どおりに REST / websocket を直接叩く（README §出典）。

関数名は会場に依存しない名前にしてある（プラン §7-6。他社を足すときに同じ名前で実装する）。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass

import requests

USER_AGENT = "ai-income-lab-tastytrade-sample/0.1"

ENVIRONMENTS = {
    # 公表値: developer.tastytrade.com/docs/sandbox（取得日 2026-09-05）
    "cert": {
        "rest": "https://api.cert.tastyworks.com",
        "account_streamer": "wss://streamer.cert.tastyworks.com",
        "label": "sandbox (cert)",
    },
    "prod": {
        "rest": "https://api.tastyworks.com",
        "account_streamer": "wss://streamer.tastyworks.com",
        "label": "production (実弾)",
    },
}


class ApiError(RuntimeError):
    """tastytrade のエラーエンベロープ（error.code / error.message）を持つ例外。"""

    def __init__(self, status: int, code: str, message: str, body):
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.body = body


class ProductionGuard(RuntimeError):
    """本番環境で発注系を呼ぼうとしたときに止める（プラン §7-3）。"""


@dataclass
class Token:
    access_token: str
    expires_in: int | None
    obtained_at: float
    scope: str | None = None  # 応答の scope（read / trade / openid）。管理画面が「何ができる資格情報か」を出すのに使う

    @property
    def expires_at(self) -> float | None:
        return self.obtained_at + self.expires_in if self.expires_in else None


class Client:
    def __init__(
        self,
        env: str = "cert",
        allow_prod_orders: bool = False,
        allow_prod_dry_run: bool = False,
        allow_prod_cancel: bool = False,
        timeout: float = 30.0,
        rest_base: str | None = None,
        account_streamer: str | None = None,
    ):
        if env not in ENVIRONMENTS:
            raise ValueError(f"env は cert か prod: {env!r}")
        self.env = env
        # rest_base / account_streamer はモックサーバに向けるときだけ差し替える（mock_server.py）
        self.conf = dict(ENVIRONMENTS[env])
        if rest_base:
            self.conf["rest"] = rest_base
            self.conf["label"] += " ※接続先を差し替え"
        if account_streamer:
            self.conf["account_streamer"] = account_streamer
        self.base = self.conf["rest"]
        self.allow_prod_orders = allow_prod_orders
        # dry-run は注文をルーティングしないので、本発注とは別の鍵で開ける
        self.allow_prod_dry_run = allow_prod_dry_run or allow_prod_orders
        # 取消は「持っている注文を減らす」だけなので、これも別の鍵（管理画面の停止ボタン用。2026-09-05）。
        # ⚠ この鍵で submit_order は開かない
        self.allow_prod_cancel = allow_prod_cancel or allow_prod_orders
        self.timeout = timeout
        self.token: Token | None = None
        self.session = requests.Session()
        self.last_status: int | None = None

    # ---------- 低レベル ----------

    def _headers(self, auth: bool = True) -> dict:
        # User-Agent が無い / 形式違いだと nginx が HTML の 401 を返す（FAQ）
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if auth:
            if not self.token:
                raise RuntimeError("先に authenticate() を呼ぶこと")
            headers["Authorization"] = f"Bearer {self.token.access_token}"
        return headers

    def request(self, method: str, path: str, *, auth: bool = True, params=None, json_body=None):
        url = self.base + path
        headers = self._headers(auth=auth)
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        resp = self.session.request(
            method, url, headers=headers, params=params, json=json_body, timeout=self.timeout
        )
        self.last_status = resp.status_code
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            body = resp.json()
        except json.JSONDecodeError:
            # User-Agent 不正のときは nginx の HTML が返る
            if resp.ok:
                return resp.text
            raise ApiError(resp.status_code, "non_json_response", resp.text[:200], resp.text[:500])
        if not resp.ok:
            err = (body or {}).get("error") or {}
            raise ApiError(
                resp.status_code,
                str(err.get("code", "unknown_error")),
                str(err.get("message", "")),
                body,
            )
        return body

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    # ---------- 手順 1: 認証 ----------

    def authenticate(self, client_secret: str, refresh_token: str, client_id: str | None = None) -> dict:
        """refresh token を access token に交換する（15 分）。

        ⚠ 失敗ログインを繰り返すと IP が約 8 時間ブロックされる。再試行はしない（プラン §Phase 2-3）。
        """
        body = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_secret": client_secret,
        }
        if client_id:
            body["client_id"] = client_id
        resp = self.request("POST", "/oauth/token", auth=False, json_body=body)
        self.token = Token(
            access_token=resp["access_token"],
            expires_in=resp.get("expires_in"),
            obtained_at=time.time(),
            scope=resp.get("scope"),
        )
        return resp

    # ---------- 手順 2: 口座照会 ----------

    def list_accounts(self) -> list[dict]:
        resp = self.get("/customers/me/accounts")
        return [item["account"] for item in resp["data"]["items"]]

    def get_balances(self, account_number: str) -> dict:
        return self.get(f"/accounts/{account_number}/balances")["data"]

    def list_positions(self, account_number: str) -> list[dict]:
        return self.get(f"/accounts/{account_number}/positions")["data"]["items"]

    def get_trading_status(self, account_number: str) -> dict:
        return self.get(f"/accounts/{account_number}/trading-status")["data"]

    # ---------- 手順 3: 現在値 ----------

    def get_quote(self, symbol: str, instrument: str = "equity") -> dict:
        """REST の気配スナップショット。⚠ sandbox は全経路 502（相場データを配信しない）。"""
        resp = self.get("/market-data/by-type", params={instrument: symbol})
        items = resp["data"]["items"]
        if not items:
            raise ApiError(200, "empty_market_data", f"{symbol} の気配が空", resp)
        return items[0]

    def get_quote_token(self) -> dict:
        """DXLink 用のトークン（24 時間）。本番のみ。onboarding 未完了だと customer_not_found_error。"""
        return self.get("/api-quote-tokens")["data"]

    def get_market_session(self) -> dict:
        return self.get("/market-time/equities/sessions/current")["data"]

    # ---------- 手順 4・5: 発注 ----------

    def _guard_orders(self, dry_run: bool = False, cancel: bool = False) -> None:
        """本番では発注系を止める。dry-run と取消はそれぞれ別の鍵で開ける（発注の鍵はどちらにも兼ねない）。"""
        if self.env != "prod":
            return
        if self.allow_prod_orders:
            return
        if dry_run and self.allow_prod_dry_run:
            return
        if cancel and self.allow_prod_cancel:
            return
        if dry_run:
            raise ProductionGuard(
                "本番環境での dry-run も既定では拒否する。通すときは --allow-prod-dry-run を付ける"
                "（検証だけで注文はルーティングされない）"
            )
        if cancel:
            raise ProductionGuard(
                "本番環境での取消は既定で拒否する。取消だけ許すときは allow_prod_cancel=True（発注は開かない）"
            )
        raise ProductionGuard(
            "本番環境での発注は既定で拒否する。実弾を通すときだけ "
            "TT_ALLOW_PROD_ORDERS=1 と --i-know-this-is-real-money を両方付ける"
        )

    @staticmethod
    def build_equity_order(
        symbol: str,
        quantity: int,
        action: str = "Buy to Open",
        order_type: str = "Limit",
        price: str | None = None,
        time_in_force: str = "Day",
        external_identifier: str | None = None,
    ) -> dict:
        """株 1 銘柄・1 レッグの注文 JSON。

        指値は price と price-effect が要る。成行は付けない（OpenAPI orders.json）。
        external-identifier は再送時に自分の注文を見つけるための識別子（API は重複排除しない）。
        """
        order = {
            "time-in-force": time_in_force,
            "order-type": order_type,
            "source": "ai-income-lab/tastytrade-api-sample",
            "automated-source": True,
            "external-identifier": external_identifier or f"ail-{uuid.uuid4().hex[:16]}",
            "legs": [
                {
                    "instrument-type": "Equity",
                    "symbol": symbol,
                    "quantity": str(quantity),
                    "action": action,
                }
            ],
        }
        if order_type in ("Limit", "Stop Limit"):
            if price is None:
                raise ValueError("指値には price が要る")
            order["price"] = price
            order["price-effect"] = "Debit" if action.startswith("Buy") else "Credit"
        return order

    def dry_run_order(self, account_number: str, order: dict) -> dict:
        """本発注の前に必ず通す。買付余力・手数料・警告を返し、何も執行しない。"""
        self._guard_orders(dry_run=True)
        return self.request("POST", f"/accounts/{account_number}/orders/dry-run", json_body=order)["data"]

    def submit_order(self, account_number: str, order: dict) -> dict:
        self._guard_orders()
        return self.request("POST", f"/accounts/{account_number}/orders", json_body=order)["data"]

    def get_order(self, account_number: str, order_id) -> dict:
        return self.get(f"/accounts/{account_number}/orders/{order_id}")["data"]

    def list_live_orders(self, account_number: str) -> list[dict]:
        return self.get(f"/accounts/{account_number}/orders/live")["data"]["items"]

    def find_order_by_external_id(self, account_number: str, external_identifier: str) -> dict | None:
        """再送の前に「もう入っていないか」を確かめる経路（idempotency ガイド）。"""
        for order in self.list_live_orders(account_number):
            if order.get("external-identifier") == external_identifier:
                return order
        return None

    def cancel_order(self, account_number: str, order_id) -> dict:
        self._guard_orders(cancel=True)
        return self.request("DELETE", f"/accounts/{account_number}/orders/{order_id}")["data"]

    def wait_for_status(
        self, account_number: str, order_id, targets: set[str], timeout: float = 30.0, interval: float = 0.5
    ) -> list[dict]:
        """注文の状態遷移を REST 照会で追い、(経過秒, status) の列を返す。"""
        transitions: list[dict] = []
        started = time.perf_counter()
        last = None
        while time.perf_counter() - started < timeout:
            order = self.get_order(account_number, order_id)
            status = order.get("status")
            if status != last:
                transitions.append({"at_ms": round((time.perf_counter() - started) * 1000, 1), "status": status})
                last = status
            if status in targets:
                break
            time.sleep(interval)
        return transitions


def load_env(path: str = ".env") -> dict:
    """.env を読む（依存を増やさないための最小実装）。"""
    values = dict(os.environ)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values.setdefault(key.strip(), value.strip().strip("'\""))
    return values
