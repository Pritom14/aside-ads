"""Client tests against a small standard-library HTTP stub (no Aside server needed).

The end-to-end test against a real server lives in the main suite
(tests/test_seed_cli_sdk.py).
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from aside_ads_client import AsideClient, AsideError, AsyncAsideClient, SponsoredAd, __version__

AD = {
    "id": "cr1",
    "campaign_id": "c1",
    "label": "Sponsored",
    "advertiser": "Slowlog Lens",
    "headline": "Find the slow query",
    "body": "Reads your query plans.",
    "cta": "Try it free",
    "url": "http://ads.test/t/c?t=clk",
    "impression_url": "http://ads.test/t/i?t=imp",
    "display_url": "slowlog.example",
    "image_url": None,
    "relevance": 0.81,
}


class Stub:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.body: Any = {"request_id": "r1", "ad": AD, "reason": None}


@pytest.fixture
def stub() -> Iterator[tuple[Stub, str]]:
    state = Stub()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            state.requests.append(
                {"path": self.path, "headers": dict(self.headers), "json": json.loads(raw or b"{}")}
            )
            payload = state.body if isinstance(state.body, bytes) else json.dumps(state.body).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _dead_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def test_get_sponsored_sends_context_and_parses_ad(stub: tuple[Stub, str]) -> None:
    state, url = stub
    client = AsideClient(url + "/", "aside_pub_x")
    messages = [{"role": "user", "content": "my postgres queries are slow"}]
    ad = client.get_sponsored(messages, user_id="s1", placement="chat-sidebar", geo={"country": "IN"})
    assert isinstance(ad, SponsoredAd)
    assert (ad.label, ad.advertiser, ad.request_id, ad.relevance) == ("Sponsored", "Slowlog Lens", "r1", 0.81)
    req = state.requests[0]
    assert req["path"] == "/v1/serve"
    assert req["headers"]["Authorization"] == "Bearer aside_pub_x"
    assert req["headers"]["User-Agent"] == f"aside-ads-client-python/{__version__}"
    assert req["json"] == {
        "context": messages,
        "placement": "chat-sidebar",
        "sensitive": False,
        "user_id": "s1",
        "geo": {"country": "IN"},
    }


def test_no_ad_sets_last_reason(stub: tuple[Stub, str]) -> None:
    state, url = stub
    state.body = {"request_id": "r2", "ad": None, "reason": "sensitive_context"}
    client = AsideClient(url, "k")
    assert client.get_sponsored("anything") is None
    assert client.last_reason == "sensitive_context"


@pytest.mark.parametrize(
    "status,body", [(500, {"detail": "boom"}), (401, {"detail": "bad key"}), (200, b"<html>")]
)
def test_fails_open_on_server_errors(stub: tuple[Stub, str], status: int, body: Any) -> None:
    state, url = stub
    state.status, state.body = status, body
    client = AsideClient(url, "k")
    assert client.get_sponsored("bread") is None
    assert client.last_reason == "error"
    with pytest.raises(AsideError):
        AsideClient(url, "k", raise_errors=True).get_sponsored("bread")


def test_fails_open_when_server_is_down() -> None:
    client = AsideClient(_dead_url(), "k", timeout=0.5)
    assert client.get_sponsored("bread") is None
    assert client.track_impression("http://x/t/i?t=abc") is False


def test_track_impression_posts_token(stub: tuple[Stub, str]) -> None:
    state, url = stub
    state.body = {"status": "counted", "billed": True}
    client = AsideClient(url, "k")
    ad = SponsoredAd.from_response({"request_id": "r1", "ad": AD})
    assert ad is not None and client.track_impression(ad) is True
    assert state.requests[-1]["path"] == "/v1/impressions"
    assert state.requests[-1]["json"] == {"token": AD["impression_url"]}
    state.body = {"status": "invalid"}
    assert client.track_impression("http://ads.test/t/i?t=bad") is False


def test_async_client(stub: tuple[Stub, str]) -> None:
    _, url = stub
    client = AsyncAsideClient(url, "k", timeout=2)
    ad = asyncio.run(client.get_sponsored("my postgres queries are slow", placement="chat-sidebar"))
    assert ad is not None and ad.campaign_id == "c1"
    assert client.last_reason is None
