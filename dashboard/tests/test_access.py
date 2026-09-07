import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.access import AccessError, AccessGuard, CloudflareVerifier, is_loopback, is_private


def rsa_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key()
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(pub, as_dict=True)
    jwk["kid"] = "kid-1"
    jwk["use"] = "sig"
    return key, {"keys": [jwk]}


def token_for(key, team, aud, email, kid="kid-1", **claims):
    now = int(time.time())
    payload = {"aud": [aud], "iss": f"https://{team}.cloudflareaccess.com", "email": email, "iat": now, "exp": now + 600, **claims}
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


def test_ip_classification():
    assert is_loopback("127.0.0.1") and is_loopback("::1")
    assert is_private("10.0.1.10") and is_private("172.20.0.3") and is_private("192.168.1.2")
    assert not is_private("8.8.8.8") and not is_loopback("10.0.0.1")
    assert not is_private("not-an-ip")


def test_verifier_accepts_valid_and_rejects_bad():
    key, jwks = rsa_pair()
    v = CloudflareVerifier("team", "aud-1", "Me@Example.com", fetch_jwks=lambda: jwks)
    claims = v.verify(token_for(key, "team", "aud-1", "me@example.com"))
    assert claims["email"] == "me@example.com"
    with pytest.raises(AccessError):
        v.verify(token_for(key, "team", "other-aud", "me@example.com"))
    with pytest.raises(AccessError):
        v.verify(token_for(key, "other-team", "aud-1", "me@example.com"))
    with pytest.raises(AccessError):
        v.verify(token_for(key, "team", "aud-1", "someone@example.com"))
    other, _ = rsa_pair()
    with pytest.raises(AccessError):
        v.verify(token_for(other, "team", "aud-1", "me@example.com"))
    with pytest.raises(AccessError):
        v.verify("not.a.jwt")


class _Settings:
    def __init__(self, mode):
        self.auth_mode = mode
        self.face = "public" if mode == "cloudflare" else "local"
        self.cf_team = self.cf_aud = self.cf_email = None


async def _run(guard, ip, headers=()):
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guard.app = app
    scope = {"type": "http", "client": (ip, 1234), "headers": list(headers), "state": {}}

    async def send(m):
        sent.append(m)

    await guard(scope, None, send)
    return sent[0]["status"], scope["state"]


@pytest.mark.anyio
async def test_guard_modes():
    g = AccessGuard(None, _Settings("loopback"))
    assert (await _run(g, "127.0.0.1"))[0] == 200
    assert (await _run(g, "10.0.1.5"))[0] == 403

    g = AccessGuard(None, _Settings("local"))
    assert (await _run(g, "10.0.1.5"))[0] == 200
    assert (await _run(g, "8.8.8.8"))[0] == 403

    key, jwks = rsa_pair()
    v = CloudflareVerifier("team", "aud-1", "me@example.com", fetch_jwks=lambda: jwks)
    g = AccessGuard(None, _Settings("cloudflare"), verifier=v)
    assert (await _run(g, "127.0.0.1"))[0] == 200  # ループバックは免除
    assert (await _run(g, "172.20.0.3"))[0] == 403  # cloudflared からでも JWT が無ければ 403
    good = token_for(key, "team", "aud-1", "me@example.com")
    status, state = await _run(g, "172.20.0.3", [(b"cf-access-jwt-assertion", good.encode())])
    assert status == 200 and state["user"] == "me@example.com" and state["face"] == "public"
    status, _ = await _run(g, "172.20.0.3", [(b"cookie", f"CF_Authorization={good}".encode())])
    assert status == 200
    bad = token_for(key, "team", "aud-1", "intruder@example.com")
    assert (await _run(g, "172.20.0.3", [(b"cf-access-jwt-assertion", bad.encode())]))[0] == 403


@pytest.fixture
def anyio_backend():
    return "asyncio"
