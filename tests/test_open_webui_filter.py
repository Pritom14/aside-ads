"""The Open WebUI filter in integrations/open-webui: a card beside the answer, or nothing.

The filter is a single file that Open WebUI loads, so it is imported by path here. Aside is
either a mocked HTTP server (httpx.MockTransport) or the real app in-process, and Open WebUI's
event emitter and message store are small fakes.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from aside_ads.app import create_app
from aside_ads.models import Event
from aside_ads.seed import seed_demo
from tests.conftest import build_world, make_settings

FILTER_PATH = (
    Path(__file__).resolve().parents[1] / "integrations" / "open-webui" / "aside_sponsored_filter.py"
)


def _load_filter_module() -> Any:
    spec = importlib.util.spec_from_file_location("aside_sponsored_filter", FILTER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


owui = _load_filter_module()

KEY = "aside_pub_test"
USER = {"id": "6f1c2a9e-user", "name": "Asha", "email": "asha@example.com", "role": "user"}
RECIPE = "Paneer butter masala for 4 people?"
ANSWER = "Ingredients: 400 g paneer, 2 tbsp butter, ... Simmer for 10 minutes."
AD = {
    "id": "cr_1",
    "campaign_id": "cmp_1",
    "label": "Sponsored",
    "advertiser": "Thaila Now",
    "headline": "Paneer, butter & cream in 10 minutes",
    "body": "Groceries for tonight's curry, delivered.",
    "cta": "Order now",
    "url": "http://aside.test/t/c?t=click-token&x=1",
    "display_url": "thaila.example",
    "impression_url": "http://aside.test/t/i?t=imp-token",
    "relevance": 0.81,
}


# --------------------------------------------------------------------------- fakes


class FakeAside:
    """A mocked Aside server that records every request."""

    def __init__(self, serve: Any = None, impressions: Any = None) -> None:
        self.serve = serve if serve is not None else {"request_id": "r1", "ad": AD, "reason": None}
        self.impressions = impressions if impressions is not None else {"status": "counted", "billed": True}
        self.requests: list[httpx.Request] = []

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def json_of(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == path]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.serve if request.url.path == "/v1/serve" else self.impressions
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return await reply(request)
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)


class FakeHost:
    """Open WebUI's message store and per-message emitters, in memory."""

    def __init__(self, embeds: dict[tuple[str, str], list[str]] | None = None, readable: bool = True) -> None:
        self.embeds = embeds or {}
        self.readable = readable
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def message_embeds(self, chat_id: str, message_id: str) -> list[str] | None:
        if not self.readable:
            return None
        return list(self.embeds.get((chat_id, message_id), []))

    def emitter(self, chat_id: str, message_id: str) -> Any:
        async def emit(event: dict[str, Any]) -> None:
            self.events.append((message_id, event))
            if event["type"] == "embeds":  # what Open WebUI persists
                data = event["data"]
                old = [] if data.get("replace") else self.embeds.get((chat_id, message_id), [])
                self.embeds[(chat_id, message_id)] = [*data["embeds"], *old]

        return emit

    async def emitter_for(self, chat_id: str, message_id: str, user_id: str, session_id: Any) -> Any:
        return self.emitter(chat_id, message_id)

    def cards(self, chat_id: str, message_id: str) -> list[str]:
        return [e for e in self.embeds.get((chat_id, message_id), []) if owui.is_card(e)]


def make_filter(aside: Any, host: FakeHost | None = None, **valves: Any) -> Any:
    f = owui.Filter()
    f.valves = f.Valves(ASIDE_URL="http://aside.test", ASIDE_API_KEY=KEY, **valves)
    f.transport = aside if isinstance(aside, httpx.AsyncBaseTransport) else httpx.MockTransport(aside)
    f.host = host or FakeHost()
    return f


def chat_body(
    *turns: str, chat_id: str = "chat-1", message_id: str = "a1", answer: str = ANSWER
) -> dict[str, Any]:
    """The outlet body Open WebUI 0.12 builds: the chat's messages ending with the finished answer."""
    messages: list[dict[str, Any]] = []
    for i, text in enumerate(turns):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"id": f"m{i}", "role": role, "content": text, "timestamp": 1_700_000_000 + i})
    messages.append({"id": message_id, "role": "assistant", "content": answer, "timestamp": 1_700_000_100})
    return {
        "model": "stub",
        "messages": messages,
        "filter_ids": ["aside_sponsored_cards"],
        "chat_id": chat_id,
        "session_id": "sess-1",
        "id": message_id,
    }


async def run(f: Any, body: dict[str, Any], emit: Any = "auto", metadata: dict | None = None) -> Any:
    if emit == "auto":
        emit = f.host.emitter(body["chat_id"], body["id"])
    meta = {
        "chat_id": body["chat_id"],
        "message_id": body["id"],
        "session_id": "sess-1",
        "user_id": USER["id"],
    }
    return await f.outlet(
        body, __user__=USER, __metadata__=meta if metadata is None else metadata, __event_emitter__=emit
    )


# --------------------------------------------------------------------------- behaviour


async def test_card_is_emitted_beside_a_relevant_answer_and_the_answer_is_untouched() -> None:
    aside, host = FakeAside(), FakeHost()
    f = make_filter(aside, host)
    body = chat_body(RECIPE)
    before = copy.deepcopy(body)

    out = await run(f, body)

    assert out is body and out == before  # the model's answer is never edited
    assert len(host.events) == 1
    message_id, event = host.events[0]
    assert message_id == "a1" and event["type"] == "embeds"  # a separate Rich UI card on the message
    assert event["data"]["replace"] is True and len(event["data"]["embeds"]) == 1
    card = event["data"]["embeds"][0]
    assert owui.is_card(card)
    assert ">Sponsored<" in card and "Thaila Now" in card and "Order now" in card
    assert "Paneer, butter &amp; cream in 10 minutes" in card
    assert 'href="http://aside.test/t/c?t=click-token&amp;x=1" target="_blank"' in card
    assert "iframe:height" in card  # sizes itself inside Open WebUI's sandboxed iframe
    assert ANSWER not in card and RECIPE not in card

    assert aside.paths() == ["/v1/serve", "/v1/impressions"]
    assert aside.requests[0].headers["Authorization"] == f"Bearer {KEY}"
    assert aside.json_of("/v1/impressions") == [{"token": AD["impression_url"]}]


async def test_only_the_latest_user_message_is_sent() -> None:
    aside = FakeAside()
    f = make_filter(aside)
    body = chat_body(
        "My name is Asha and I live in Pune.",
        "Nice to meet you, Asha.",
        "I have a sore throat today.",
        "Warm water with honey can help.",
        RECIPE,
        message_id="a3",
    )
    body["messages"].insert(0, {"id": "s", "role": "system", "content": "You are Rasoi AI."})

    await run(f, body)

    [payload] = aside.json_of("/v1/serve")
    expected_user = "owui-" + hashlib.sha256(USER["id"].encode()).hexdigest()[:32]
    assert payload == {"context": RECIPE, "placement": "default", "user_id": expected_user}
    raw = aside.requests[0].content.decode()
    for private in ("Asha", "Pune", "sore throat", "honey", "Rasoi AI", ANSWER, USER["id"], USER["email"]):
        assert private not in raw


async def test_multimodal_message_sends_only_its_text_and_user_id_can_be_left_out() -> None:
    aside = FakeAside()
    f = make_filter(aside, SEND_USER_ID=False, PLACEMENT="chat-sidebar")
    body = chat_body(RECIPE)
    body["messages"][0]["content"] = [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "What can I cook with this?"},
    ]

    await run(f, body)

    assert aside.json_of("/v1/serve") == [
        {"context": "What can I cook with this?", "placement": "chat-sidebar"}
    ]


@pytest.mark.parametrize(
    "reason", ["below_relevance_threshold", "sensitive_context", "budget_exhausted", "frequency_capped"]
)
async def test_no_card_when_aside_returns_no_ad(reason: str) -> None:
    aside, host = FakeAside(serve={"request_id": "r1", "ad": None, "reason": reason}), FakeHost()
    f = make_filter(aside, host, DEBUG=True)

    out = await run(f, chat_body("I don't want to live anymore"))

    assert out["messages"][-1]["content"] == ANSWER
    assert host.events == [] and host.embeds == {}
    assert aside.paths() == ["/v1/serve"]  # no impression, no retry with more context


async def _slow(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(2)
    return httpx.Response(200, json={"request_id": "r1", "ad": AD})


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("connection refused"),
        httpx.Response(500, text="Internal Server Error"),
        httpx.Response(401, json={"detail": "Invalid API key"}),
        httpx.Response(404, json={"detail": "Unknown placement"}),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"request_id": "r1", "ad": {**AD, "url": "javascript:alert(1)"}}),
        httpx.Response(200, json=["unexpected"]),
        _slow,
    ],
    ids=["timeout", "refused", "500", "401", "404", "not-json", "unsafe-url", "not-an-object", "slow"],
)
async def test_errors_and_timeouts_never_break_the_chat(failure: Any) -> None:
    aside, host = FakeAside(serve=failure), FakeHost()
    f = make_filter(aside, host, TIMEOUT_SECONDS=0.2)
    body = chat_body(RECIPE)
    before = copy.deepcopy(body)

    out = await run(f, body)

    assert out == before
    assert host.events == []
    assert aside.paths() == ["/v1/serve"]


async def test_impression_is_tracked_once_per_answer() -> None:
    aside, host = FakeAside(), FakeHost()
    f = make_filter(aside, host)
    body = chat_body(RECIPE)

    await run(f, body)
    await run(f, body)  # "Continue response" runs the outlet again for the same answer

    assert aside.paths() == ["/v1/serve", "/v1/impressions"]
    assert len(host.events) == 1 and len(host.cards("chat-1", "a1")) == 1


async def test_failed_impression_still_leaves_the_card_and_never_raises() -> None:
    aside, host = FakeAside(impressions=httpx.Response(500)), FakeHost()
    f = make_filter(aside, host)

    await run(f, chat_body(RECIPE))

    assert aside.paths() == ["/v1/serve", "/v1/impressions"]
    assert len(host.cards("chat-1", "a1")) == 1


async def test_no_impression_when_the_card_could_not_be_shown() -> None:
    aside = FakeAside()
    f = make_filter(aside)

    async def broken(event: dict[str, Any]) -> None:
        raise RuntimeError("socket closed")

    out = await run(f, chat_body(RECIPE), emit=broken)

    assert out["messages"][-1]["content"] == ANSWER
    assert aside.paths() == ["/v1/serve"]


@pytest.mark.parametrize(
    "setup",
    ["disabled", "no-key", "no-emitter", "task", "automation", "channel", "no-answer", "no-user-text"],
)
async def test_nothing_is_requested_when_there_is_no_one_to_show_a_card_to(setup: str) -> None:
    aside, host = FakeAside(), FakeHost()
    f = make_filter(aside, host)
    body, emit, meta = chat_body(RECIPE), "auto", None
    if setup == "disabled":
        f.valves.ENABLED = False
    elif setup == "no-key":
        f.valves.ASIDE_API_KEY = " "
    elif setup == "no-emitter":  # a direct API call: Open WebUI passes no emitter to outlet
        emit = None
    elif setup == "task":
        meta = {"chat_id": "chat-1", "task": "title_generation"}
    elif setup == "automation":
        meta = {"chat_id": "chat-1", "internal": True, "automation_id": "auto-1"}
    elif setup == "channel":
        body = chat_body(RECIPE, chat_id="channel:general")
    elif setup == "no-answer":
        body = chat_body(RECIPE, answer="")
    elif setup == "no-user-text":
        body = chat_body("")

    await run(f, body, emit=emit, metadata=meta)

    assert aside.requests == [] and host.events == []


async def test_min_turns_between_cards() -> None:
    aside, host = FakeAside(), FakeHost()
    f = make_filter(aside, host, MIN_TURNS_BETWEEN_CARDS=1, LATEST_TURN_ONLY=False)

    for n in range(1, 6):
        await run(f, chat_body(RECIPE, message_id=f"a{n}"))

    carded = [m for m in ("a1", "a2", "a3", "a4", "a5") if host.cards("chat-1", m)]
    assert carded == ["a1", "a3", "a5"]
    assert aside.paths().count("/v1/serve") == 3  # skipped answers do not ask Aside at all


async def test_answers_without_an_ad_count_towards_the_gap() -> None:
    aside, host = FakeAside(), FakeHost()
    f = make_filter(aside, host, MIN_TURNS_BETWEEN_CARDS=2, LATEST_TURN_ONLY=False)

    await run(f, chat_body(RECIPE, message_id="a1"))  # card
    aside.serve = {"request_id": "r2", "ad": None, "reason": "below_relevance_threshold"}
    await run(f, chat_body(RECIPE, message_id="a2"))  # gap
    await run(f, chat_body(RECIPE, message_id="a3"))  # gap: Aside is not asked
    aside.serve = {"request_id": "r4", "ad": AD, "reason": None}
    await run(f, chat_body(RECIPE, message_id="a4"))  # card again

    assert [m for m in ("a1", "a2", "a3", "a4") if host.cards("chat-1", m)] == ["a1", "a4"]
    assert aside.paths().count("/v1/serve") == 2


async def test_max_cards_per_chat_is_per_chat() -> None:
    aside, host = FakeAside(), FakeHost()
    f = make_filter(aside, host, MAX_CARDS_PER_CHAT=1, LATEST_TURN_ONLY=False)

    await run(f, chat_body(RECIPE, message_id="a1"))
    await run(f, chat_body(RECIPE, message_id="a2"))
    await run(f, chat_body(RECIPE, chat_id="chat-2", message_id="b1"))

    assert host.cards("chat-1", "a1") and not host.cards("chat-1", "a2")
    assert host.cards("chat-2", "b1")


async def test_latest_turn_only_moves_the_card_and_keeps_other_embeds() -> None:
    chart = "<div>a chart from a tool</div>"
    aside, host = FakeAside(), FakeHost(embeds={("chat-1", "a1"): [chart]})
    f = make_filter(aside, host)

    await run(f, chat_body(RECIPE, message_id="a1"))
    assert host.embeds[("chat-1", "a1")][1:] == [chart] and host.cards("chat-1", "a1")

    aside.serve = {"request_id": "r2", "ad": None, "reason": "below_relevance_threshold"}
    await run(f, chat_body(RECIPE, "...", "Who invented the pressure cooker?", message_id="a2"))

    assert host.embeds[("chat-1", "a1")] == [chart]  # card gone from the earlier answer, chart kept
    assert host.cards("chat-1", "a2") == []

    aside.serve = {"request_id": "r3", "ad": AD, "reason": None}
    await run(f, chat_body(RECIPE, message_id="a3"))
    await run(f, chat_body(RECIPE, message_id="a4"))
    assert host.cards("chat-1", "a3") == [] and len(host.cards("chat-1", "a4")) == 1


async def test_cards_stay_on_earlier_answers_when_latest_turn_only_is_off() -> None:
    aside, host = FakeAside(), FakeHost()
    f = make_filter(aside, host, LATEST_TURN_ONLY=False)

    await run(f, chat_body(RECIPE, message_id="a1"))
    await run(f, chat_body(RECIPE, message_id="a2"))

    assert host.cards("chat-1", "a1") and host.cards("chat-1", "a2")


async def test_merge_mode_when_existing_embeds_cannot_be_read() -> None:
    aside, host = FakeAside(), FakeHost(readable=False)
    f = make_filter(aside, host)

    await run(f, chat_body(RECIPE))

    [(_, event)] = host.events
    assert "replace" not in event["data"]  # Open WebUI merges, so other embeds are not dropped


async def test_card_escapes_every_ad_field_and_always_says_sponsored() -> None:
    card = owui.render_card(
        {
            **AD,
            "label": "Info",
            "advertiser": '<script>alert("a")</script>',
            "headline": "<img src=x onerror=alert(1)>",
            "body": "",
            "cta": "",
            "url": 'http://aside.test/t/c?t="><script>',
        }
    )
    assert "<img" not in card and "<script>alert" not in card
    assert "&lt;img src=x onerror=alert(1)&gt;" in card
    assert 'href="http://aside.test/t/c?t=&quot;&gt;&lt;script&gt;"' in card
    assert ">Sponsored<" in card and ">Info<" not in card
    assert ">Learn more<" in card and "<p>" not in card


def test_valves_have_safe_defaults() -> None:
    v = owui.Filter().valves
    assert v.ENABLED and v.LATEST_TURN_ONLY and v.SEND_USER_ID and not v.DEBUG
    assert v.ASIDE_API_KEY == "" and v.PLACEMENT == "default"
    assert 0 < v.TIMEOUT_SECONDS <= 2
    schema = owui.Filter.Valves.model_json_schema()["properties"]
    assert schema["ASIDE_API_KEY"]["input"] == {"type": "password"}  # masked in Open WebUI


async def test_filter_file_loads_the_way_open_webui_loads_it() -> None:
    source = FILTER_PATH.read_text()
    # Open WebUI reads metadata from the opening docstring and rewrites a few import prefixes.
    assert source.splitlines()[0] == '"""'
    for line in ("title: Aside Sponsored Cards", "required_open_webui_version: 0.12.5", "license: MIT"):
        assert line in source
    for prefix in ("from utils", "from apps", "from main", "from config"):
        assert prefix not in source
    # Then it executes the file in a fresh module and drops that module from sys.modules again.
    name = "function_aside_sponsored_cards_test"
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(source, str(FILTER_PATH), "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    f = module.Filter()
    assert f.Valves.model_json_schema()["properties"]["ASIDE_URL"]["default"] == "http://localhost:8000"
    f.valves = f.Valves(ASIDE_URL="http://aside.test", ASIDE_API_KEY=KEY)
    f.transport, f.host = httpx.MockTransport(FakeAside()), FakeHost()
    await run(f, chat_body(RECIPE))
    assert len(f.host.cards("chat-1", "a1")) == 1


# --------------------------------------------------------------------------- real Aside server


async def test_against_the_real_aside_server_with_the_cooking_demo(tmp_path: Path) -> None:
    w, rt = await build_world(make_settings(tmp_path))
    try:
        seeded = await seed_demo(
            rt.sessionmaker,
            tmp_path,
            "http://127.0.0.1:8001/landing",
            "http://aside.test",
            scenario="cooking",
        )
        host = FakeHost()
        f = make_filter(httpx.ASGITransport(app=create_app(runtime=rt)), host)
        f.valves.ASIDE_API_KEY = seeded["ASIDE_API_KEY"]
        f.valves.LATEST_TURN_ONLY = False

        await run(f, chat_body(RECIPE, message_id="a1"))
        await run(f, chat_body(RECIPE, message_id="a1"))  # same answer again: no second impression
        await run(f, chat_body("What is the capital of Australia?", message_id="a2"))
        rotis = "How do I make soft rotis like my mother did?"
        await run(f, chat_body(rotis, message_id="a3"))
        await run(f, chat_body("My mother passed away last week. " + rotis, message_id="a4"))

        [card] = host.cards("chat-1", "a1")
        assert "Thaila Now" in card and ">Sponsored<" in card and "/t/c?t=" in card
        assert host.cards("chat-1", "a2") == []  # below the relevance threshold
        assert "Tadka Talim" in host.cards("chat-1", "a3")[0]
        assert host.cards("chat-1", "a4") == []  # the same question in grief: the sensitive guard wins
        async with rt.sessionmaker() as s:
            impressions = await s.scalar(
                select(func.count()).select_from(Event).where(Event.kind == "impression")
            )
        assert impressions == 2  # one per card, none for the repeated outlet
    finally:
        await w.client.aclose()
        await rt.close()
