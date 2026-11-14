"""Signed tokens (a tiny JWT) and cookie helpers. Standard library only, so the Lambda zip needs no packages."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

SESSION_COOKIE = "starsort_session"
SESSION_SECONDS = 8 * 60 * 60


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(body: str, secret: str) -> str:
    return _encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def sign(payload: dict[str, Any], secret: str, purpose: str, max_age: int, now: float | None = None) -> str:
    now = time.time() if now is None else now
    claims = {**payload, "use": purpose, "exp": int(now) + max_age}
    body = _encode(json.dumps(claims, separators=(",", ":")).encode())
    return f"{body}.{_signature(body, secret)}"


def verify(token: str, secret: str, purpose: str, now: float | None = None) -> dict[str, Any] | None:
    """The payload if the token is ours, made for this purpose, untouched and not expired; otherwise None."""
    body, _, signature = token.partition(".")
    if not body or not hmac.compare_digest(signature.encode(), _signature(body, secret).encode()):
        return None
    try:
        payload = json.loads(_decode(body))
    except ValueError:
        return None
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or payload.get("use") != purpose or payload.get("exp", 0) < now:
        return None
    return payload


def make_cookie(name: str, value: str, max_age: int, path: str = "/") -> str:
    return f"{name}={value}; Max-Age={max_age}; Path={path}; HttpOnly; Secure; SameSite=Lax"


def clear_cookie(name: str, path: str = "/") -> str:
    return make_cookie(name, "", 0, path)


def read_cookie(cookies: list[str] | None, name: str) -> str | None:
    for item in cookies or []:
        for part in item.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
    return None
