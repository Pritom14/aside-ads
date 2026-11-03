"""OAuth for agent connections, so Claude, ChatGPT and other MCP clients can connect to Aside.

The client registers itself (dynamic client registration), sends the user to ``/connect``, and the
user approves by pasting an Aside admin or advertiser key. Codes and tokens are random, stored only
as SHA-256 hashes, and tied to that key: revoke the key and every connection made with it stops.

Clients that can send headers (Claude Code, scripts) can skip OAuth and send the key itself as a
Bearer token.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import delete, select

from aside_ads.auth import hash_key, lookup_key
from aside_ads.models import ApiKey, OAuthClient, OAuthGrant
from aside_ads.runtime import Runtime

READ, WRITE = "aside.read", "aside.write"
SCOPES = [READ, WRITE]
# What each kind of Aside key may do through an agent. Publisher keys live inside apps and are not
# accepted here.
ROLE_SCOPES: dict[str, set[str]] = {"admin": {READ, WRITE}, "advertiser": {READ}}

PENDING_TTL = 600
CODE_TTL = 300
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400


def allowed_scopes(role: str, requested: list[str] | None) -> list[str]:
    granted = ROLE_SCOPES.get(role, set())
    if requested:
        granted = granted & set(requested)
    return sorted(granted)


class AsideOAuthProvider:
    """Implements the MCP SDK's ``OAuthAuthorizationServerProvider`` on Aside's database."""

    def __init__(self, runtime: Callable[[], Runtime], connect_url: str) -> None:
        self._rt = runtime
        self.connect_url = connect_url

    # -- clients -------------------------------------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._rt().sessionmaker() as s:
            row = await s.get(OAuthClient, client_id)
        return OAuthClientInformationFull.model_validate(row.info) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        async with self._rt().sessionmaker() as s:
            s.add(OAuthClient(client_id=client_info.client_id, info=client_info.model_dump(mode="json")))
            await s.commit()

    # -- approval ------------------------------------------------------------------------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Park the request and send the user to the approval page."""
        request_id = secrets.token_urlsafe(24)
        data = params.model_dump(mode="json")
        data["client_name"] = client.client_name or client.client_id
        await self._store(
            "pending", request_id, client.client_id, None, params.scopes or [], PENDING_TTL, data
        )
        return f"{self.connect_url}?request={request_id}"

    async def pending(self, request_id: str) -> OAuthGrant | None:
        return await self._load("pending", request_id)

    async def approve(self, request_id: str, key: ApiKey) -> str:
        """Turn a pending request into an authorization code for ``key``. Returns the code."""
        grant = await self._take("pending", request_id)
        if grant is None:
            raise ValueError("This request expired. Start the connection again from your app.")
        code = secrets.token_urlsafe(32)
        scopes = allowed_scopes(key.role, grant.scopes)
        await self._store("code", code, grant.client_id, key.id, scopes, CODE_TTL, grant.data)
        return code

    async def deny(self, request_id: str) -> OAuthGrant | None:
        return await self._take("pending", request_id)

    # -- codes and tokens ----------------------------------------------------------------------------
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        g = await self._load("code", authorization_code)
        if g is None or g.client_id != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=g.scopes,
            expires_at=g.expires_at,
            client_id=g.client_id,
            code_challenge=g.data["code_challenge"],
            redirect_uri=g.data["redirect_uri"],
            redirect_uri_provided_explicitly=g.data["redirect_uri_provided_explicitly"],
            resource=g.data.get("resource"),
            subject=g.api_key_id,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        g = await self._take("code", authorization_code.code)
        if g is None or g.api_key_id is None:
            raise TokenError("invalid_grant", "authorization code already used or expired")
        return await self._issue(client.client_id, g.api_key_id, g.scopes, g.data.get("resource"))

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        g = await self._load("refresh", refresh_token)
        if g is None or g.client_id != client.client_id or not await self._key_ok(g.api_key_id):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=g.client_id,
            scopes=g.scopes,
            expires_at=int(g.expires_at),
            resource=g.data.get("resource"),
            subject=g.api_key_id,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        g = await self._take("refresh", refresh_token.token)
        if g is None or g.api_key_id is None:
            raise TokenError("invalid_grant", "refresh token already used or expired")
        granted = [s for s in (scopes or g.scopes) if s in g.scopes]
        return await self._issue(client.client_id, g.api_key_id, granted, g.data.get("resource"))

    async def load_access_token(self, token: str) -> AccessToken | None:
        if token.startswith("aside_"):  # a raw Aside key sent as a Bearer token
            async with self._rt().sessionmaker() as s:
                key = await lookup_key(s, token)
            if key is None or key.role not in ROLE_SCOPES:
                return None
            return AccessToken(
                token=token, client_id="api-key", scopes=allowed_scopes(key.role, None), subject=key.id
            )
        g = await self._load("access", token)
        if g is None or not await self._key_ok(g.api_key_id):
            return None
        return AccessToken(
            token=token,
            client_id=g.client_id,
            scopes=g.scopes,
            expires_at=int(g.expires_at),
            resource=g.data.get("resource"),
            subject=g.api_key_id,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if token.token.startswith("aside_"):
            return  # API keys are revoked from the dashboard, not by a client
        async with self._rt().sessionmaker() as s:
            await s.execute(delete(OAuthGrant).where(OAuthGrant.token_hash == hash_key(token.token)))
            await s.commit()

    # -- storage -------------------------------------------------------------------------------------
    async def _issue(
        self, client_id: str, api_key_id: str, scopes: list[str], resource: str | None
    ) -> OAuthToken:
        if not await self._key_ok(api_key_id):
            raise TokenError("invalid_grant", "the Aside key behind this connection was revoked")
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        data = {"resource": resource}
        await self._store("access", access, client_id, api_key_id, scopes, ACCESS_TTL, data)
        await self._store("refresh", refresh, client_id, api_key_id, scopes, REFRESH_TTL, data)
        return OAuthToken(
            access_token=access, expires_in=ACCESS_TTL, scope=" ".join(scopes), refresh_token=refresh
        )

    async def _store(
        self,
        kind: str,
        secret: str,
        client_id: str,
        api_key_id: str | None,
        scopes: list[str],
        ttl: int,
        data: dict[str, Any],
    ) -> None:
        now = time.time()
        async with self._rt().sessionmaker() as s:
            # Expired rows are useless; clear them as new ones arrive.
            await s.execute(delete(OAuthGrant).where(OAuthGrant.expires_at < now))
            s.add(
                OAuthGrant(
                    token_hash=hash_key(secret),
                    kind=kind,
                    client_id=client_id,
                    api_key_id=api_key_id,
                    scopes=list(scopes),
                    expires_at=now + ttl,
                    data=data,
                )
            )
            await s.commit()

    async def _load(self, kind: str, secret: str) -> OAuthGrant | None:
        async with self._rt().sessionmaker() as s:
            g = await s.get(OAuthGrant, hash_key(secret))
        if g is None or g.kind != kind or g.expires_at < time.time():
            return None
        return g

    async def _take(self, kind: str, secret: str) -> OAuthGrant | None:
        """Load and delete in one step, so a code or refresh token works once."""
        async with self._rt().sessionmaker() as s:
            g = (
                await s.execute(
                    select(OAuthGrant).where(
                        OAuthGrant.token_hash == hash_key(secret), OAuthGrant.kind == kind
                    )
                )
            ).scalar_one_or_none()
            if g is None:
                return None
            gone = await s.execute(delete(OAuthGrant).where(OAuthGrant.token_hash == g.token_hash))
            await s.commit()
        if gone.rowcount != 1 or g.expires_at < time.time():
            return None
        return g

    async def _key_ok(self, api_key_id: str | None) -> bool:
        if not api_key_id:
            return False
        async with self._rt().sessionmaker() as s:
            key = await s.get(ApiKey, api_key_id)
        return key is not None and key.revoked_at is None and key.role in ROLE_SCOPES
