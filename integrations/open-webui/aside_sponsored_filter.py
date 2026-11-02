"""
title: Aside Sponsored Cards
author: Pritom Mazumdar
author_url: https://github.com/Pritom14/aside-ads
git_url: https://github.com/Pritom14/aside-ads
version: 0.1.0
license: MIT
required_open_webui_version: 0.12.5
description: A labeled Sponsored card beside relevant answers, from your own self-hosted Aside ad server.
"""

# Aside filter for Open WebUI (MIT licensed, single file).
#
# After an answer finishes, the outlet sends the latest user message (and nothing
# else from the chat) to your Aside server. If Aside returns an ad, the filter attaches
# a separate card labeled "Sponsored" to the answer as a Rich UI embed. The card is
# not part of the answer's text, so the model never sees it in later turns. If Aside
# returns no ad (sensitive topic, nothing relevant, budget spent), is slow or fails,
# nothing is shown and the chat carries on as usual.
#
# Tested with Open WebUI 0.12.5. Docs: integrations/open-webui/README.md in the
# Aside repository.

import asyncio
import hashlib
import html
import inspect
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

Emitter = Callable[[dict[str, Any]], Awaitable[Any]]

VERSION = "0.1.0"
CARD_MARKER = "data-aside-card"
_MAX_CHATS = 5000  # chats whose counters are kept in memory (ids and counts only, never text)
_MAX_SEEN_PER_CHAT = 64


class OpenWebUIHost:
    """The two Open WebUI internals the filter uses. Both are optional.

    They let the filter keep other embeds on a message and remove the card from an
    earlier answer. If a future Open WebUI release changes them, cards still appear
    and earlier cards simply stay where they are.
    """

    async def message_embeds(self, chat_id: str, message_id: str) -> list[Any] | None:
        """Embeds stored on a message, [] if none, or None if they cannot be read."""
        try:
            from open_webui.models.chats import Chats

            value = Chats.get_message_metadata(chat_id, message_id, "embeds")
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            return None
        return list(value) if isinstance(value, list) else []

    async def emitter_for(
        self, chat_id: str, message_id: str, user_id: str, session_id: str | None
    ) -> Emitter | None:
        """An event emitter bound to another message of the chat (an earlier answer)."""
        try:
            from open_webui.socket.main import get_event_emitter

            emitter = get_event_emitter(
                {"chat_id": chat_id, "message_id": message_id, "user_id": user_id, "session_id": session_id}
            )
            if inspect.isawaitable(emitter):
                emitter = await emitter
        except Exception:
            return None
        return emitter if callable(emitter) else None


@dataclass
class _ChatState:
    cards: int = 0
    answers_since_card: int = 0
    card_message_id: str | None = None
    seen: OrderedDict[str, None] = field(default_factory=OrderedDict)


class Filter:
    class Valves(BaseModel):
        ASIDE_URL: str = Field(
            default="http://localhost:8000",
            description="Your Aside server, as the Open WebUI server reaches it. "
            "Open WebUI in Docker with Aside on the host: http://host.docker.internal:8000",
        )
        ASIDE_API_KEY: str = Field(
            default="",
            description="Aside publisher key (aside_pub_...). The filter does nothing without it.",
            json_schema_extra={"input": {"type": "password"}},
        )
        PLACEMENT: str = Field(
            default="default",
            description="Aside placement key. `aside create-key` and `aside seed-demo` set up 'default'.",
        )
        ENABLED: bool = Field(default=True, description="Show sponsored cards. Off: the filter does nothing.")
        LATEST_TURN_ONLY: bool = Field(
            default=True,
            description="Keep a card only beside the newest answer: when a new answer arrives, "
            "the card on the previous answer is removed.",
        )
        TIMEOUT_SECONDS: float = Field(
            default=1.5,
            ge=0.1,
            le=10,
            description="Wait at most this long for Aside. A slow or failing Aside means no card, "
            "never a broken chat.",
        )
        MIN_TURNS_BETWEEN_CARDS: int = Field(
            default=0,
            ge=0,
            description="Answers that must pass without a card after a card is shown. "
            "0: every relevant answer can have one.",
        )
        MAX_CARDS_PER_CHAT: int = Field(default=0, ge=0, description="Most cards in one chat. 0: no limit.")
        SEND_USER_ID: bool = Field(
            default=True,
            description="Send a one-way hash of the Open WebUI user id so Aside can apply "
            "per-user frequency caps. The raw id is never sent.",
        )
        DEBUG: bool = Field(
            default=False, description="Log why no card was shown (the reason only, never chat text)."
        )

    def __init__(self) -> None:
        self.valves = self.Valves()
        self.host = OpenWebUIHost()
        self.transport: httpx.AsyncBaseTransport | None = None  # tests pass a mock transport
        self._chats: OrderedDict[str, _ChatState] = OrderedDict()

    async def outlet(
        self,
        body: dict,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        __event_emitter__: Emitter | None = None,
    ) -> dict:
        try:
            await self._after_answer(body, __user__ or {}, __metadata__ or {}, __event_emitter__)
        except Exception as exc:  # an ad must never break the chat
            log.warning("aside: no card (%s)", type(exc).__name__)
        return body  # never modified: the answer stays exactly as the model wrote it

    # ------------------------------------------------------------------ flow

    async def _after_answer(self, body: dict, user: dict, metadata: dict, emit: Emitter | None) -> None:
        v = self.valves
        if not (v.ENABLED and v.ASIDE_API_KEY.strip() and v.ASIDE_URL.strip()) or emit is None:
            return  # off, not configured, or an API call with no chat window to show a card in
        if metadata.get("task") or metadata.get("internal") or metadata.get("automation_id"):
            return  # background work (titles, tags, automations): nobody is looking
        chat_id = str(body.get("chat_id") or metadata.get("chat_id") or "")
        message_id = str(body.get("id") or metadata.get("message_id") or "")
        if not chat_id or not message_id or chat_id.startswith("channel:"):
            return
        messages = body.get("messages") or []
        if not messages or messages[-1].get("role") != "assistant" or not _text(messages[-1].get("content")):
            return  # no finished answer to sit beside
        question = _latest_user_text(messages)
        if not question:
            return

        chat = self._chat(chat_id)
        if message_id in chat.seen:
            return  # one decision per answer, e.g. when "Continue response" runs the outlet again
        chat.seen[message_id] = None
        while len(chat.seen) > _MAX_SEEN_PER_CHAT:
            chat.seen.popitem(last=False)
        chat.answers_since_card += 1

        user_id = str(user.get("id") or metadata.get("user_id") or "")
        if v.LATEST_TURN_ONLY and chat.card_message_id and chat.card_message_id != message_id:
            session_id = body.get("session_id") or metadata.get("session_id")
            await self._remove_card(chat_id, chat.card_message_id, user_id, session_id)
            chat.card_message_id = None

        if not self._frequency_allows(chat):
            self._debug("frequency setting")
            return

        async with self._client() as client:
            ad = await self._serve(client, question, user_id)
            if ad is None or not await self._show(emit, chat_id, message_id, ad):
                return
            chat.cards += 1
            chat.answers_since_card = 0
            chat.card_message_id = message_id
            await self._track_impression(client, ad)

    def _frequency_allows(self, chat: _ChatState) -> bool:
        v = self.valves
        if v.MAX_CARDS_PER_CHAT and chat.cards >= v.MAX_CARDS_PER_CHAT:
            return False
        return chat.cards == 0 or chat.answers_since_card > v.MIN_TURNS_BETWEEN_CARDS

    # ------------------------------------------------------------------ Aside API

    def _client(self) -> httpx.AsyncClient:
        v = self.valves
        return httpx.AsyncClient(
            base_url=v.ASIDE_URL.strip().rstrip("/"),
            headers={
                "Authorization": f"Bearer {v.ASIDE_API_KEY.strip()}",
                "User-Agent": f"aside-open-webui/{VERSION}",
            },
            timeout=v.TIMEOUT_SECONDS,
            transport=self.transport,
            follow_redirects=False,
        )

    async def _post(self, client: httpx.AsyncClient, path: str, payload: dict) -> dict | None:
        """POST to Aside within the timeout. Any error, status >= 400 or bad JSON gives None."""
        try:
            r = await asyncio.wait_for(client.post(path, json=payload), self.valves.TIMEOUT_SECONDS)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as exc:
            log.warning("aside: %s failed (%s)", path, type(exc).__name__)
            return None
        return data if isinstance(data, dict) else None

    async def _serve(self, client: httpx.AsyncClient, question: str, user_id: str) -> dict | None:
        v = self.valves
        # Only the latest user message goes to Aside: no history, no answer, no names.
        payload: dict[str, Any] = {"context": question, "placement": v.PLACEMENT.strip() or "default"}
        if v.SEND_USER_ID and user_id:
            payload["user_id"] = "owui-" + hashlib.sha256(user_id.encode()).hexdigest()[:32]
        data = await self._post(client, "/v1/serve", payload)
        if data is None:
            return None
        ad = data.get("ad")
        if not ad:
            # sensitive_context, below_relevance_threshold, budget_exhausted, ...: respect it.
            self._debug(str(data.get("reason") or "no ad"))
            return None
        if not _is_valid_ad(ad):
            self._debug("unusable ad in response")
            return None
        return ad

    async def _track_impression(self, client: httpx.AsyncClient, ad: dict) -> bool:
        # Server-side impression with the signed impression URL. Aside counts it once per request id.
        data = await self._post(client, "/v1/impressions", {"token": ad["impression_url"]})
        return bool(data and data.get("status") in ("counted", "duplicate"))

    # ------------------------------------------------------------------ display

    async def _show(self, emit: Emitter, chat_id: str, message_id: str, ad: dict) -> bool:
        card = render_card(ad)
        existing = await self.host.message_embeds(chat_id, message_id)
        if existing is None:
            # Unknown: merge mode keeps any other embeds this message has in the database.
            event = {"type": "embeds", "data": {"embeds": [card]}}
        else:
            others = [e for e in existing if not is_card(e)]
            event = {"type": "embeds", "data": {"embeds": [card, *others], "replace": True}}
        try:
            await emit(event)
        except Exception as exc:
            log.warning("aside: card not shown (%s)", type(exc).__name__)
            return False
        return True

    async def _remove_card(self, chat_id: str, message_id: str, user_id: str, session_id: Any) -> None:
        existing = await self.host.message_embeds(chat_id, message_id)
        if not existing or not any(is_card(e) for e in existing):
            return
        emit = await self.host.emitter_for(chat_id, message_id, user_id, session_id)
        if emit is None:
            return
        try:
            others = [e for e in existing if not is_card(e)]
            await emit({"type": "embeds", "data": {"embeds": others, "replace": True}})
        except Exception as exc:
            log.warning("aside: earlier card not removed (%s)", type(exc).__name__)

    # ------------------------------------------------------------------ helpers

    def _chat(self, chat_id: str) -> _ChatState:
        chat = self._chats.pop(chat_id, None) or _ChatState()
        self._chats[chat_id] = chat  # most recently used last
        while len(self._chats) > _MAX_CHATS:
            self._chats.popitem(last=False)
        return chat

    def _debug(self, reason: str) -> None:
        if self.valves.DEBUG:
            log.info("aside: no card (%s)", reason)


def _text(content: Any) -> str:
    """Plain text of a message: a string, or the text parts of a multimodal content list."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(p for p in parts if isinstance(p, str)).strip()
    return ""


def _latest_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _text(message.get("content"))
    return ""


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlparse(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _is_valid_ad(ad: Any) -> bool:
    return (
        isinstance(ad, dict)
        and isinstance(ad.get("headline"), str)
        and bool(ad["headline"].strip())
        and _is_http_url(ad.get("url"))
        and _is_http_url(ad.get("impression_url"))
    )


def is_card(embed: Any) -> bool:
    """True for a card this filter rendered (other embeds on a message are left alone)."""
    return isinstance(embed, str) and CARD_MARKER in embed


_CSS = """
:root{--bg:#fff;--fg:#1d1f23;--muted:#5f646d;--line:#e3e5e8;--pill:#eef0f3;--cta:#2457d6;--cta-fg:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#1f2023;--fg:#ececf1;--muted:#a6a9b0;--line:#3a3c42;
--pill:#2c2e33;--cta:#8ab4ff;--cta-fg:#0b1220}}
html,body{margin:0;padding:0;background:transparent}
body{font:14px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--fg)}
aside{box-sizing:border-box;display:flex;flex-direction:column;gap:6px;max-width:560px;padding:12px 14px;
border:1px solid var(--line);border-radius:12px;background:var(--bg)}
.top{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--muted)}
.label{font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;padding:1px 7px;
border-radius:6px;background:var(--pill);color:var(--fg)}
.headline{font-weight:600;color:var(--fg);text-decoration:none}
.headline:hover{text-decoration:underline}
p{margin:0;color:var(--muted)}
.foot{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:2px}
.cta{padding:5px 12px;border-radius:8px;background:var(--cta);color:var(--cta-fg);font-size:13px;
font-weight:600;text-decoration:none}
.domain{font-size:12px;color:var(--muted)}
"""

# Open WebUI sizes sandboxed embeds from this message (see its Rich UI docs).
_HEIGHT_JS = (
    "(function(){function r(){parent.postMessage({type:'iframe:height',"
    "height:document.documentElement.scrollHeight},'*')}"
    "addEventListener('load',r);"
    "if(window.ResizeObserver){new ResizeObserver(r).observe(document.body)}r()})();"
)


def render_card(ad: dict) -> str:
    """Self-contained HTML for the card. Every ad field is escaped; the label is always "Sponsored"."""

    def esc(value: Any) -> str:
        return html.escape(str(value or "").strip(), quote=True)

    link = f'href="{esc(ad["url"])}" target="_blank" rel="sponsored noopener noreferrer"'
    advertiser = esc(ad.get("advertiser"))
    body = f"<p>{esc(ad.get('body'))}</p>" if str(ad.get("body") or "").strip() else ""
    domain = esc(ad.get("display_url"))
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<style>{_CSS}</style></head><body>"
        f'<aside {CARD_MARKER} aria-label="Sponsored{": " + advertiser if advertiser else ""}">'
        f'<div class="top"><span class="label">Sponsored</span><span>{advertiser}</span></div>'
        f'<a class="headline" {link}>{esc(ad["headline"])}</a>'
        f"{body}"
        f'<div class="foot"><a class="cta" {link}>{esc(ad.get("cta")) or "Learn more"}</a>'
        f'<span class="domain">{domain}</span></div>'
        f"</aside><script>{_HEIGHT_JS}</script></body></html>"
    )
