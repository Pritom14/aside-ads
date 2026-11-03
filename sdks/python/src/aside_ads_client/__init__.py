"""Python client for Aside, the self-hosted ad server for AI chat apps.

Zero dependencies (standard library only). Ads are optional by design: on any
error or timeout ``get_sponsored`` returns ``None`` so your chat never breaks
because of an ad.

    from aside_ads_client import AsideClient

    aside = AsideClient("http://127.0.0.1:8000", api_key="aside_pub_...")
    ad = aside.get_sponsored(context=messages, user_id=session_id, placement="chat-sidebar")
    if ad:
        render_beside_answer(ad)          # show ad.label ("Sponsored"), headline, body, cta, link to ad.url
        aside.track_impression(ad)        # or let the browser fetch ad.impression_url
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Sequence, Union  # noqa: UP035 (Python 3.9 support)

__all__ = ["AsideClient", "AsyncAsideClient", "SponsoredAd", "AsideError"]
__version__ = "0.2.0"

log = logging.getLogger("aside_ads_client")

Message = dict[str, str]
Context = Union[str, Sequence[Message]]


class AsideError(Exception):
    """Raised only when ``raise_errors=True``."""


@dataclass(frozen=True)
class SponsoredAd:
    label: str
    headline: str
    body: str
    cta: str
    url: str
    impression_url: str
    advertiser: str
    display_url: str
    image_url: str | None
    id: str
    campaign_id: str
    request_id: str
    relevance: float

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> SponsoredAd | None:
        ad = data.get("ad")
        if not ad:
            return None
        return cls(
            label=ad.get("label", "Sponsored"),
            headline=ad["headline"],
            body=ad.get("body", ""),
            cta=ad.get("cta", "Learn more"),
            url=ad["url"],
            impression_url=ad["impression_url"],
            advertiser=ad.get("advertiser", ""),
            display_url=ad.get("display_url", ""),
            image_url=ad.get("image_url"),
            id=ad["id"],
            campaign_id=ad["campaign_id"],
            request_id=data.get("request_id", ""),
            relevance=float(ad.get("relevance", 0.0)),
        )


class AsideClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 0.8,
        raise_errors: bool = False,
        user_agent: str = f"aside-ads-client-python/{__version__}",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.raise_errors = raise_errors
        self.user_agent = user_agent
        self.last_reason: str | None = None

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": self.user_agent,
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (caller-provided URL)
            return json.loads(resp.read().decode() or "{}")

    def get_sponsored(
        self,
        context: Context,
        *,
        user_id: str | None = None,
        placement: str = "default",
        geo: dict[str, Any] | None = None,
        categories: list[str] | None = None,
        sensitive: bool = False,
    ) -> SponsoredAd | None:
        """Return a relevant sponsored ad for this conversation, or ``None``.

        ``context`` is a string or a list of ``{"role", "content"}`` messages. It is
        sent only to your own Aside server and is not stored there.
        """
        payload: dict[str, Any] = {
            "context": context if isinstance(context, str) else [dict(m) for m in context],
            "placement": placement,
            "sensitive": sensitive,
        }
        if user_id is not None:
            payload["user_id"] = user_id
        if geo:
            payload["geo"] = geo
        if categories:
            payload["categories"] = categories
        try:
            data = self._post("/v1/serve", payload)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            self.last_reason = "error"
            if self.raise_errors:
                raise AsideError(str(exc)) from exc
            log.warning("aside: no ad (%s)", exc)
            return None
        self.last_reason = data.get("reason")
        return SponsoredAd.from_response(data)

    def track_impression(self, ad: SponsoredAd | str) -> bool:
        """Record that the ad was shown (server-side, authenticated). Returns True if accepted."""
        url = ad.impression_url if isinstance(ad, SponsoredAd) else ad
        try:
            data = self._post("/v1/impressions", {"token": url})
            return data.get("status") in ("counted", "duplicate")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if self.raise_errors:
                raise AsideError(str(exc)) from exc
            log.warning("aside: impression not recorded (%s)", exc)
            return False


class AsyncAsideClient:
    """Async wrapper (runs the stdlib client in a thread). No extra dependencies."""

    def __init__(self, base_url: str, api_key: str, **kwargs: Any) -> None:
        self._sync = AsideClient(base_url, api_key, **kwargs)

    @property
    def last_reason(self) -> str | None:
        return self._sync.last_reason

    async def get_sponsored(self, context: Context, **kwargs: Any) -> SponsoredAd | None:
        return await asyncio.to_thread(self._sync.get_sponsored, context, **kwargs)

    async def track_impression(self, ad: SponsoredAd | str) -> bool:
        return await asyncio.to_thread(self._sync.track_impression, ad)
