"""Agent connector: Aside as a remote MCP server at ``/mcp``.

Connect Claude, ChatGPT or any MCP client to read ad reports in plain language and, with an admin
key, change a campaign's status, budget or bid after the user approves. Clients that support OAuth
(claude.ai, ChatGPT) register themselves and send the user to ``/connect`` to approve with an Aside
key; clients that send headers can pass the key as ``Authorization: Bearer <key>``.

Set ``ASIDE_PUBLIC_URL`` to the address clients use: OAuth metadata is built from it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from mcp.server.auth.provider import construct_redirect_uri
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from aside_ads import __version__
from aside_ads.auth import lookup_key
from aside_ads.connector.oauth import ROLE_SCOPES, SCOPES, AsideOAuthProvider
from aside_ads.connector.tools import INSTRUCTIONS, register_tools
from aside_ads.runtime import Runtime

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class Connector:
    def __init__(self, base_url: str, runtime: Callable[[], Runtime]) -> None:
        base = base_url.rstrip("/")
        self.provider = AsideOAuthProvider(runtime, f"{base}/connect")
        self.server = MCPServer(
            name="aside",
            title="Aside ads",
            instructions=INSTRUCTIONS,
            website_url="https://github.com/Pritom14/aside-ads",
            version=__version__,
            auth_server_provider=self.provider,
            auth=AuthSettings(
                issuer_url=base,
                resource_server_url=f"{base}/mcp",
                validate_token_resource=False,
                client_registration_options=ClientRegistrationOptions(
                    enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
                ),
                revocation_options=RevocationOptions(enabled=True),
            ),
        )
        register_tools(self.server, runtime)
        host = urlparse(base).hostname or "127.0.0.1"
        # DNS-rebinding protection matters when the server only listens locally.
        security = (
            TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"],
                allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
            )
            if host in LOCAL_HOSTS
            else TransportSecuritySettings(enable_dns_rebinding_protection=False)
        )
        self.app: Starlette = self.server.streamable_http_app(
            stateless_http=True, json_response=True, transport_security=security, host=host
        )
        self.router = self._approval_routes()

    def lifespan(self) -> Any:
        return self.server.session_manager.run()

    def _approval_routes(self) -> APIRouter:
        from aside_ads.dashboard import templates

        provider = self.provider
        router = APIRouter(include_in_schema=False)

        def page(request: Request, status_code: int = 200, **ctx: Any) -> Response:
            return templates.TemplateResponse(request, "connect.html", ctx, status_code=status_code)

        @router.get("/connect", response_class=HTMLResponse)
        async def connect_page(request: Request, request_id: str = "") -> Response:
            request_id = request_id or request.query_params.get("request", "")
            grant = await provider.pending(request_id) if request_id else None
            if grant is None:
                return page(request, 400, error_only=True, error="Start the connection again from your app.")
            return page(request, request_id=request_id, client_name=grant.data.get("client_name", "this app"))

        @router.post("/connect")
        async def connect_submit(
            request: Request,
            request_id: str = Form("", alias="request"),
            key: str = Form(""),
            action: str = Form(""),
        ) -> Response:
            grant = await provider.pending(request_id) if request_id else None
            if grant is None:
                return page(request, 400, error_only=True, error="Start the connection again from your app.")
            redirect_uri, state = grant.data["redirect_uri"], grant.data.get("state")
            if action == "deny":
                await provider.deny(request_id)
                return RedirectResponse(
                    construct_redirect_uri(redirect_uri, error="access_denied", state=state), status_code=303
                )
            rt: Runtime = request.app.state.runtime
            async with rt.sessionmaker() as s:
                api_key = await lookup_key(s, key.strip())
            if api_key is None or api_key.role not in ROLE_SCOPES:
                return page(
                    request,
                    401,
                    request_id=request_id,
                    client_name=grant.data.get("client_name", "this app"),
                    error="That is not a valid admin or advertiser key.",
                )
            try:
                code = await provider.approve(request_id, api_key)
            except ValueError as exc:
                return page(request, 400, error_only=True, error=str(exc))
            return RedirectResponse(
                construct_redirect_uri(redirect_uri, code=code, state=state), status_code=303
            )

        return router
