"""Public sponsor pages: ``/sponsor/<slug>`` (HTML), ``/sponsor/<slug>.json`` and the request form.

No login: these are the pages a publisher shares with advertisers. They exist only for publishers
that turned their page on in the dashboard.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import ValidationError

from aside_ads import sponsor
from aside_ads.api.deps import client_ip, get_runtime
from aside_ads.dashboard import templates
from aside_ads.runtime import Runtime

router = APIRouter(include_in_schema=False)

SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "AUD": "A$", "CAD": "C$", "SGD": "S$"}
FIELDS = [
    "company",
    "website",
    "contact_name",
    "contact_email",
    "placement",
    "bid_type",
    "bid",
    "daily_budget",
    "total_budget",
    "start_date",
    "end_date",
    "targeting_text",
    "headline",
    "body",
    "cta",
    "url",
    "message",
]


def base_url(request: Request, rt: Runtime) -> str:
    return (rt.settings.public_url or str(request.base_url)).rstrip("/")


def _money(rt: Runtime):
    sym = SYMBOLS.get(rt.settings.currency.upper())

    def fmt(v: float | None, decimals: int = 2) -> str:
        if v is None:
            return "-"
        n = f"{v:,.{decimals}f}"
        return f"{sym}{n}" if sym else f"{n} {rt.settings.currency}"

    return fmt


async def _render(
    request: Request, rt: Runtime, slug: str, status_code: int = 200, **extra: Any
) -> HTMLResponse:
    async with rt.sessionmaker() as s:
        found = await sponsor.find_page(s, slug)
        if found is None:
            return HTMLResponse("No sponsor page here.", status_code=404)
        page, pub = found
        data = await sponsor.listing(s, rt, page, pub, base_url(request, rt))
    ctx = {
        "page": page,
        "pub": pub,
        "d": data,
        "money": _money(rt),
        "v": extra.pop("v", None) or {"bid_type": "cpm", "cta": "Learn more"},
        **extra,
    }
    return templates.TemplateResponse(request, "sponsor.html", ctx, status_code=status_code)


@router.get("/sponsor")
async def sponsor_index(request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    async with rt.sessionmaker() as s:
        pages = await sponsor.enabled_pages(s)
    if len(pages) == 1:
        return RedirectResponse(f"/sponsor/{pages[0][0].slug}", status_code=307)
    if not pages:
        return HTMLResponse("No sponsor pages here.", status_code=404)
    links = "".join(f'<li><a href="/sponsor/{p.slug}">{_esc(pub.name)}</a></li>' for p, pub in pages)
    return HTMLResponse(f"<!doctype html><title>Sponsor pages</title><ul>{links}</ul>")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


@router.get("/.well-known/aside.json")
async def well_known(request: Request, rt: Runtime = Depends(get_runtime)) -> JSONResponse:
    """Which sponsor pages this server has, for directories that list Aside apps."""
    base = base_url(request, rt)
    async with rt.sessionmaker() as s:
        pages = await sponsor.enabled_pages(s)
    body = {
        "aside": True,
        "sponsor_pages": [
            {
                "name": pub.name,
                "page_url": f"{base}/sponsor/{p.slug}",
                "listing_url": f"{base}/sponsor/{p.slug}.json",
            }
            for p, pub in pages
        ],
    }
    return JSONResponse(body, headers={"access-control-allow-origin": "*", "cache-control": "max-age=300"})


@router.get("/sponsor/{slug}.json")
async def sponsor_json(slug: str, request: Request, rt: Runtime = Depends(get_runtime)) -> JSONResponse:
    async with rt.sessionmaker() as s:
        found = await sponsor.find_page(s, slug)
        if found is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        data = await sponsor.listing(s, rt, *found, base_url(request, rt))
    return JSONResponse(data, headers={"access-control-allow-origin": "*", "cache-control": "max-age=300"})


@router.get("/sponsor/{slug}")
async def sponsor_page(
    slug: str, request: Request, sent: int = 0, rt: Runtime = Depends(get_runtime)
) -> HTMLResponse:
    return await _render(request, rt, slug, sent=bool(sent))


@router.post("/sponsor/{slug}/request")
async def sponsor_request(slug: str, request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    form = await request.form()
    values = {k: str(form.get(k, "")).strip() for k in FIELDS}
    if str(form.get("nickname", "")).strip():  # hidden field: only bots fill it in
        return RedirectResponse(f"/sponsor/{slug}?sent=1", status_code=303)
    async with rt.sessionmaker() as s:
        found = await sponsor.find_page(s, slug)
        if found is None:
            return HTMLResponse("No sponsor page here.", status_code=404)
        page, _pub = found
        if not page.accept_requests:
            return await _render(
                request, rt, slug, 403, v=values, error="This app is not taking requests right now."
            )
        placements = await sponsor.offered_placements(s, page)
        try:
            data = sponsor.SponsorRequestIn.model_validate(
                {k: v or None for k, v in values.items()}
                | {
                    "body": values["body"],
                    "message": values["message"],
                    "cta": values["cta"] or "Learn more",
                    "bid_type": values["bid_type"] or "cpm",
                }
            )
        except ValidationError as exc:
            errors = [_field_error(e) for e in exc.errors()]
            return await _render(request, rt, slug, 422, v=values, errors=errors)
        errors = sponsor.check_request(rt, page, placements, data)
        if errors:
            return await _render(request, rt, slug, 422, v=values, errors=errors)
        if await rt.counters.incr(f"sponsor-req:{client_ip(request, rt) or 'unknown'}", 3600) > (
            rt.settings.sponsor_requests_per_hour
        ):
            return await _render(
                request, rt, slug, 429, v=values, error="Too many requests from here. Try again in an hour."
            )
        if await sponsor.pending_count(s, page.publisher_id) >= rt.settings.sponsor_max_pending:
            return await _render(
                request,
                rt,
                slug,
                429,
                v=values,
                error="This app has too many requests waiting. Try again later.",
            )
        s.add(sponsor.new_request(page, data))
        await s.commit()
    return RedirectResponse(f"/sponsor/{slug}?sent=1", status_code=303)


LABELS = {
    "company": "Company",
    "website": "Website",
    "contact_name": "Your name",
    "contact_email": "Email",
    "placement": "Placement",
    "bid": "Bid",
    "daily_budget": "Daily budget",
    "total_budget": "Total budget",
    "start_date": "Start date",
    "end_date": "End date",
    "targeting_text": "Conversations it fits",
    "headline": "Headline",
    "body": "Text",
    "cta": "Button text",
    "url": "Link",
    "message": "Message",
}


def _field_error(e: dict[str, Any]) -> str:
    field = str(e["loc"][0]) if e.get("loc") else ""
    msg = str(e["msg"]).removeprefix("Value error, ")
    if e.get("type") == "missing" or e.get("input") is None:
        msg = "is required"
    label = LABELS.get(field)
    return f"{label}: {msg}" if label else msg[:1].upper() + msg[1:]
