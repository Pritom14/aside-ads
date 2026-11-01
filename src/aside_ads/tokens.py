"""HMAC-signed tokens for impression/click URLs and dashboard sessions.

Format: ``base64url(json payload) "." base64url(HMAC-SHA256(key, payload))``.
Every token carries a kind (so an impression token cannot be used as a click)
and an absolute expiry. Prices travel inside the signed payload, so the
clearing price decided at auction time cannot be altered by the client.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


class TokenError(Exception):
    """Base class: the token is unusable."""


class TokenInvalid(TokenError):
    """Malformed, tampered with, or of the wrong kind."""


class TokenExpired(TokenError):
    """Authentic but past its expiry. ``payload`` is still available."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("token expired")
        self.payload = payload


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Signer:
    def __init__(self, secret: bytes) -> None:
        self._token_key = hmac.new(secret, b"aside/token/v1", hashlib.sha256).digest()
        self._user_key = hmac.new(secret, b"aside/user-hash/v1", hashlib.sha256).digest()

    def sign(self, kind: str, payload: dict[str, Any], ttl_seconds: int, now: float | None = None) -> str:
        now = time.time() if now is None else now
        body = dict(payload, k=kind, x=int(now + ttl_seconds))
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        sig = hmac.new(self._token_key, raw, hashlib.sha256).digest()
        return f"{_b64e(raw)}.{_b64e(sig)}"

    def verify(self, token: str, kind: str, now: float | None = None) -> dict[str, Any]:
        try:
            raw_b64, sig_b64 = token.split(".", 1)
            raw = _b64d(raw_b64)
            sig = _b64d(sig_b64)
        except Exception as exc:
            raise TokenInvalid("malformed token") from exc
        expected = hmac.new(self._token_key, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            raise TokenInvalid("bad signature")
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise TokenInvalid("bad payload") from exc
        if not isinstance(payload, dict) or payload.get("k") != kind:
            raise TokenInvalid("wrong token kind")
        now = time.time() if now is None else now
        if int(payload.get("x", 0)) < now:
            raise TokenExpired(payload)
        return payload

    def hash_user(self, user_id: str) -> str:
        """Keyed hash of the publisher's end-user id. The raw id is never stored."""
        return hmac.new(self._user_key, user_id.encode(), hashlib.sha256).hexdigest()[:32]
