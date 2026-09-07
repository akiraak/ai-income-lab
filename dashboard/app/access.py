"""面の判定と Cloudflare Access の JWT 検証（ASGI ミドルウェア）。

- loopback: ループバック以外は 403
- local: ループバックと RFC1918 だけ通す（WSL2 → Windows のブラウザは 172.x から来ることがある）
- cloudflare: ループバックは免除、それ以外は全リクエスト（GET 含む）で `Cf-Access-Jwt-Assertion` を検証する。
  X-Forwarded-For は見ない（接続元は cloudflared のコンテナで、そこから先は JWT で判定する）

無認証の /health は作らない（healthcheck はコンテナ内のループバックから叩く）。
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from http.cookies import SimpleCookie

import jwt
import requests

log = logging.getLogger("ail.access")


class AccessError(Exception):
    pass


def client_ip(scope) -> str:
    client = scope.get("client")
    return client[0] if client else ""


def _addr(ip: str):
    try:
        return ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return None


def is_loopback(ip: str) -> bool:
    addr = _addr(ip)
    return bool(addr and addr.is_loopback)


def is_private(ip: str) -> bool:
    addr = _addr(ip)
    if addr is None:
        return False
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback or addr.is_private


class CloudflareVerifier:
    """<team>.cloudflareaccess.com の JWKS で RS256 を検証し、aud・iss・email を突き合わせる。"""

    def __init__(self, team: str, aud: str, email: str, fetch_jwks=None, cache_seconds: int = 3600):
        self.team = team
        self.aud = aud
        self.email = email.lower()
        self.issuer = f"https://{team}.cloudflareaccess.com"
        self.certs_url = f"{self.issuer}/cdn-cgi/access/certs"
        self._fetch = fetch_jwks or self._fetch_http
        self._keys: dict[str, object] = {}
        self._fetched_at = 0.0
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()

    def _fetch_http(self) -> dict:
        resp = requests.get(self.certs_url, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _load_keys(self, force: bool = False) -> None:
        with self._lock:
            if not force and self._keys and time.time() - self._fetched_at < self._cache_seconds:
                return
            data = self._fetch()
            keys = {}
            for jwk in data.get("keys", []):
                if jwk.get("kid"):
                    keys[jwk["kid"]] = jwt.PyJWK(jwk).key
            if not keys:
                raise AccessError("JWKS に鍵が無い")
            self._keys = keys
            self._fetched_at = time.time()

    def verify(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AccessError(f"JWT のヘッダが読めない: {exc}") from exc
        kid = header.get("kid")
        self._load_keys()
        key = self._keys.get(kid)
        if key is None:
            # 鍵のローテーション直後は取り直す（1 回だけ）
            self._load_keys(force=True)
            key = self._keys.get(kid)
        if key is None:
            raise AccessError("JWT の kid が JWKS に無い")
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.aud,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise AccessError(f"JWT の検証に失敗: {exc}") from exc
        email = (claims.get("email") or "").lower()
        if email != self.email:
            raise AccessError("JWT の email が許可リストに無い")
        return claims


def _header(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return None


def token_from_scope(scope) -> str | None:
    token = _header(scope, b"cf-access-jwt-assertion")
    if token:
        return token
    raw = _header(scope, b"cookie")
    if raw:
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return None
        if "CF_Authorization" in cookie:
            return cookie["CF_Authorization"].value
    return None


class AccessGuard:
    """全リクエストに面の判定を掛ける ASGI ミドルウェア。判定結果は scope["state"] に置く。"""

    def __init__(self, app, settings, verifier: CloudflareVerifier | None = None):
        self.app = app
        self.mode = settings.auth_mode
        self.face = settings.face
        self.verifier = verifier
        if self.mode == "cloudflare" and verifier is None:
            self.verifier = CloudflareVerifier(settings.cf_team, settings.cf_aud, settings.cf_email)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        ip = client_ip(scope)
        state = scope.setdefault("state", {})
        state["face"] = self.face
        state["client_ip"] = ip
        state["user"] = None
        verdict = self._decide(scope, ip, state)
        if verdict is not None:
            await self._deny(scope, send, verdict)
            return
        await self.app(scope, receive, send)

    def _decide(self, scope, ip: str, state: dict) -> str | None:
        if is_loopback(ip):
            state["user"] = "loopback"
            return None
        if self.mode == "loopback":
            return "ループバック以外からの接続は受け付けない"
        if self.mode == "local":
            if is_private(ip):
                state["user"] = "lan"
                return None
            return "プライベートネットワーク以外からの接続は受け付けない"
        # cloudflare
        token = token_from_scope(scope)
        if not token:
            return "Cf-Access-Jwt-Assertion が無い"
        try:
            claims = self.verifier.verify(token)
        except AccessError as exc:
            log.warning("Access JWT の検証に失敗: %s (from %s)", exc, ip)
            return "Access JWT の検証に失敗"
        except Exception as exc:  # JWKS の取得失敗など
            log.warning("Access JWT の検証で例外: %s (from %s)", type(exc).__name__, ip)
            return "Access JWT の検証に失敗"
        state["user"] = claims.get("email")
        return None

    async def _deny(self, scope, send, reason: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = f"403 Forbidden: {reason}\n".encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
