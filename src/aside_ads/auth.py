"""API keys: generated once, stored as SHA-256 hashes."""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aside_ads.models import ApiKey

ROLES = ("admin", "publisher", "advertiser")
_PREFIX = {"admin": "aside_admin_", "publisher": "aside_pub_", "advertiser": "aside_adv_"}


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


async def create_api_key(
    session: AsyncSession,
    role: str,
    name: str = "",
    publisher_id: str | None = None,
    advertiser_id: str | None = None,
) -> tuple[ApiKey, str]:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if role == "publisher" and not publisher_id:
        raise ValueError("publisher keys need a publisher_id")
    if role == "advertiser" and not advertiser_id:
        raise ValueError("advertiser keys need an advertiser_id")
    plaintext = _PREFIX[role] + secrets.token_urlsafe(24)
    key = ApiKey(
        name=name,
        role=role,
        prefix=plaintext[: len(_PREFIX[role]) + 4],
        key_hash=hash_key(plaintext),
        publisher_id=publisher_id if role == "publisher" else None,
        advertiser_id=advertiser_id if role == "advertiser" else None,
    )
    session.add(key)
    await session.flush()
    return key, plaintext


async def lookup_key(session: AsyncSession, plaintext: str) -> ApiKey | None:
    if not plaintext:
        return None
    key = (
        await session.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(plaintext)))
    ).scalar_one_or_none()
    if key is None or key.revoked_at is not None:
        return None
    return key
