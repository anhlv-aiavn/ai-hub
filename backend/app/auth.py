"""Auth lõi — KHÔNG thêm dependency: PBKDF2 (hashlib) cho mật khẩu + JWT HS256
ký bằng hmac/stdlib. Đủ cho console nội bộ; secret lấy từ env AIHUB_JWT_SECRET."""

import base64
import hashlib
import hmac
import json
import os
import time

from app import config

_PBKDF2_ITERS = 200_000


# ── Mật khẩu (PBKDF2-HMAC-SHA256) ───────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2${_PBKDF2_ITERS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, dk_b64 = (stored or "").split("$")
        if algo != "pbkdf2":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001
        return False


# ── JWT HS256 (tự ký, stdlib) ───────────────────────────────────────────────

def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_dec(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def make_token(payload: dict, ttl: int | None = None) -> str:
    ttl = config.JWT_TTL if ttl is None else ttl
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl
    seg = _b64u(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        _b64u(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode())
    sig = _b64u(hmac.new(config.JWT_SECRET.encode(), seg.encode(), hashlib.sha256).digest())
    return f"{seg}.{sig}"


def decode_token(token: str) -> dict | None:
    try:
        h, p, s = token.split(".")
        seg = f"{h}.{p}"
        expected = _b64u(hmac.new(config.JWT_SECRET.encode(), seg.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, s):
            return None
        payload = json.loads(_b64u_dec(p))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None
