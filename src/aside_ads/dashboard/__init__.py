"""Server-rendered admin dashboard (Jinja2, no build step). Log in with an admin API key."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from aside_ads import sponsor
from aside_ads.api.deps import get_runtime
from aside_ads.auth import create_api_key, lookup_key
from aside_ads.models import (
    Advertiser,
    ApiKey,
    Creative,
    Placement,
    Publisher,
    SponsorPage,
    SponsorRequest,
    utcnow,
)
from aside_ads.runtime import Runtime
from aside_ads.schemas import CampaignIn, CampaignPatch, CreativeIn, from_micros, to_micros
from aside_ads.services import (
    NotFound,
    add_creative,
    add_placement,
    create_campaign,
    create_publisher,
    daily_stats,
    list_campaigns,
    list_publishers,
    load_campaign,
    totals_by_campaign,
    update_campaign,
)
from aside_ads.tokens import TokenError

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["money"] = lambda m: "-" if m is None else f"{from_micros(m):,.2f}"
templates.env.filters["pct"] = lambda x: f"{x * 100:.2f}%"
templates.env.filters["num"] = lambda x: f"{x:,}"

COOKIE = "aside_session"
SESSION_TTL = 12 * 3600


class LoginRequired(Exception):
    pass


async def admin_session(request: Request, rt: Runtime = Depends(get_runtime)) -> ApiKey:
    token = request.cookies.get(COOKIE)
    if not token:
        raise LoginRequired
    try:
        claims = rt.signer.verify(token, "ses")
    except TokenError as exc:
        raise LoginRequired from exc
    async with rt.sessionmaker() as s:
        key = await s.get(ApiKey, claims.get("kid"))
    if key is None or key.revoked_at is not None or key.role != "admin":
        raise LoginRequired
    return key


router = APIRouter(prefix="/dashboard", include_in_schema=False)
protected = APIRouter(prefix="/dashboard", include_in_schema=False, dependencies=[Depends(admin_session)])


def render(request: Request, name: str, rt: Runtime, status_code: int = 200, **ctx: Any) -> HTMLResponse:
    ctx.setdefault("currency", rt.settings.currency)
    ctx.setdefault("matcher", rt.matcher.name)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


# --------------------------------------------------------------------------- login


@router.get("/login")
async def login_page(request: Request, rt: Runtime = Depends(get_runtime)) -> HTMLResponse:
    return render(request, "login.html", rt)


@router.post("/login")
async def login(request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    form = await request.form()
    async with rt.sessionmaker() as s:
        key = await lookup_key(s, str(form.get("key", "")).strip())
    if key is None or key.role != "admin":
        return render(request, "login.html", rt, 401, error="That is not a valid admin key.")
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie(
        COOKIE,
        rt.signer.sign("ses", {"kid": key.id}, SESSION_TTL),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/dashboard",
    )
    return resp


@router.post("/logout")
async def logout() -> Response:
    resp = RedirectResponse("/dashboard/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/dashboard")
    return resp


# --------------------------------------------------------------------------- overview


@protected.get("")
async def overview(request: Request, days: int = 7, rt: Runtime = Depends(get_runtime)) -> HTMLResponse:
    days = min(max(days, 1), 365)
    now = rt.now()
    day_from, day_to = rt.day(now - timedelta(days=days - 1)), rt.day(now)
    async with rt.sessionmaker() as s:
        campaigns = await list_campaigns(s)
        totals = await totals_by_campaign(s, day_from, day_to)
        rows = await daily_stats(s, day_from, day_to)
    names = {c.id: c.name for c in campaigns}
    grand = {
        "impressions": sum(t.impressions for t in totals.values()),
        "clicks": sum(t.clicks for t in totals.values()),
        "conversions": sum(t.conversions for t in totals.values()),
        "spend": sum(t.spend for t in totals.values()),
    }
    grand["ctr"] = grand["clicks"] / grand["impressions"] if grand["impressions"] else 0.0
    return render(
        request,
        "overview.html",
        rt,
        campaigns=campaigns,
        totals=totals,
        rows=rows,
        names=names,
        grand=grand,
        days=days,
        today=rt.day(now),
    )


# --------------------------------------------------------------------------- campaigns


def _blank(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _form_campaign(form: Any, tz: ZoneInfo) -> dict[str, Any]:
    def dt(name: str) -> datetime | None:
        v = _blank(form.get(name))
        return datetime.fromisoformat(v).replace(tzinfo=tz) if v else None

    data: dict[str, Any] = {
        k: _blank(form.get(k))
        for k in [
            "name",
            "status",
            "bid_type",
            "bid",
            "daily_budget",
            "total_budget",
            "pacing",
            "freq_cap",
            "freq_cap_window_hours",
            "min_relevance",
            "geo_lat",
            "geo_lng",
            "geo_radius_km",
        ]
    }
    data["targeting_text"] = str(form.get("targeting_text", ""))
    for k in [
        "keywords_include",
        "keywords_exclude",
        "categories_include",
        "categories_exclude",
        "placements",
        "countries",
        "regions",
    ]:
        data[k] = str(form.get(k, ""))
    data["start_at"], data["end_at"] = dt("start_at"), dt("end_at")
    sched_days = [int(d) for d in form.getlist("sched_days")]
    sched = {
        "days": sched_days or None,
        "start_hour": _blank(form.get("sched_start_hour")),
        "end_hour": _blank(form.get("sched_end_hour")),
        "timezone": _blank(form.get("sched_timezone")),
    }
    data["schedule"] = sched if any(v is not None for v in sched.values()) else None
    return data


_FIELDS = [
    "name",
    "status",
    "bid_type",
    "bid",
    "daily_budget",
    "total_budget",
    "pacing",
    "freq_cap",
    "freq_cap_window_hours",
    "min_relevance",
    "targeting_text",
    "keywords_include",
    "keywords_exclude",
    "categories_include",
    "categories_exclude",
    "placements",
    "countries",
    "regions",
    "geo_lat",
    "geo_lng",
    "geo_radius_km",
    "start_at",
    "end_at",
    "sched_start_hour",
    "sched_end_hour",
    "sched_timezone",
    "advertiser_id",
    "cr_headline",
    "cr_body",
    "cr_cta",
    "cr_url",
    "cr_image_url",
]
_DEFAULTS = {
    "status": "active",
    "bid_type": "cpc",
    "pacing": "even",
    "freq_cap_window_hours": "24",
    "cr_cta": "Learn more",
}


def _fmt(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, float):
        return f"{x:g}"
    return str(x)


def _values_from_form(form: Any) -> dict[str, Any]:
    v: dict[str, Any] = {k: str(form.get(k, "")) for k in _FIELDS}
    v["sched_days"] = [int(d) for d in form.getlist("sched_days")]
    return v


def _values_from_campaign(c: Any, tz: ZoneInfo) -> dict[str, Any]:
    def local(dt: datetime | None) -> str:
        if dt is None:
            return ""
        dt = dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(tz).strftime("%Y-%m-%dT%H:%M")

    sched = c.schedule or {}
    return {
        "name": c.name,
        "status": c.status,
        "bid_type": c.bid_type,
        "bid": _fmt(from_micros(c.bid_micros)),
        "daily_budget": _fmt(from_micros(c.daily_budget_micros)),
        "total_budget": _fmt(from_micros(c.total_budget_micros)),
        "pacing": c.pacing,
        "freq_cap": _fmt(c.freq_cap),
        "freq_cap_window_hours": _fmt(c.freq_cap_window_s / 3600),
        "min_relevance": _fmt(c.min_relevance),
        "targeting_text": c.targeting_text or "",
        **{
            k: ", ".join(getattr(c, k) or [])
            for k in [
                "keywords_include",
                "keywords_exclude",
                "categories_include",
                "categories_exclude",
                "placements",
                "countries",
                "regions",
            ]
        },
        "geo_lat": _fmt(c.geo_lat),
        "geo_lng": _fmt(c.geo_lng),
        "geo_radius_km": _fmt(c.geo_radius_km),
        "start_at": local(c.start_at),
        "end_at": local(c.end_at),
        "sched_days": sched.get("days") or [],
        "sched_start_hour": _fmt(sched.get("start_hour")),
        "sched_end_hour": _fmt(sched.get("end_hour")),
        "sched_timezone": sched.get("timezone") or "",
    }


def _errors(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc']) or 'form'}: {e['msg']}" for e in exc.errors()]


async def _advertisers(rt: Runtime) -> list[Advertiser]:
    async with rt.sessionmaker() as s:
        return list((await s.execute(select(Advertiser).order_by(Advertiser.name))).scalars().all())


@protected.get("/campaigns/new")
async def new_campaign_page(request: Request, rt: Runtime = Depends(get_runtime)) -> HTMLResponse:
    return render(request, "campaign_new.html", rt, advertisers=await _advertisers(rt), v=dict(_DEFAULTS))


@protected.post("/campaigns/new")
async def new_campaign(request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    form = await request.form()
    try:
        data = _form_campaign(form, rt.tz)
        data["advertiser_id"] = str(form.get("advertiser_id", ""))
        body = CampaignIn.model_validate(data)
        creative = CreativeIn.model_validate(
            {k: _blank(form.get(f"cr_{k}")) or "" for k in ("headline", "body", "cta", "url")}
            | {"image_url": _blank(form.get("cr_image_url"))}
        )
    except ValidationError as exc:
        return render(
            request,
            "campaign_new.html",
            rt,
            422,
            advertisers=await _advertisers(rt),
            v=_values_from_form(form),
            errors=_errors(exc),
        )
    except ValueError as exc:
        return render(
            request,
            "campaign_new.html",
            rt,
            422,
            advertisers=await _advertisers(rt),
            v=_values_from_form(form),
            errors=[str(exc)],
        )
    async with rt.sessionmaker() as s:
        try:
            c = await create_campaign(s, body)
            await add_creative(s, c.id, creative)
            await s.commit()
        except NotFound as exc:
            return render(
                request,
                "campaign_new.html",
                rt,
                422,
                advertisers=await _advertisers(rt),
                v=_values_from_form(form),
                errors=[str(exc)],
            )
    return RedirectResponse(f"/dashboard/campaigns/{c.id}", status_code=303)


async def _campaign_page(
    request: Request, rt: Runtime, campaign_id: str, status_code: int = 200, **extra: Any
) -> HTMLResponse:
    now = rt.now()
    async with rt.sessionmaker() as s:
        try:
            c = await load_campaign(s, campaign_id)
        except NotFound:
            return HTMLResponse("Campaign not found", status_code=404)
        rows = await daily_stats(s, rt.day(now - timedelta(days=29)), rt.day(now), campaign_id)
    return render(
        request,
        "campaign.html",
        rt,
        status_code,
        c=c,
        rows=rows,
        today=rt.day(now),
        v=_values_from_campaign(c, rt.tz),
        **extra,
    )


@protected.get("/campaigns/{campaign_id}")
async def campaign_page(
    campaign_id: str, request: Request, rt: Runtime = Depends(get_runtime)
) -> HTMLResponse:
    return await _campaign_page(request, rt, campaign_id)


@protected.post("/campaigns/{campaign_id}")
async def edit_campaign(campaign_id: str, request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    form = await request.form()
    try:
        data = _form_campaign(form, rt.tz)
        data["clear_daily_budget"] = data["daily_budget"] is None
        data["clear_total_budget"] = data["total_budget"] is None
        body = CampaignPatch.model_validate(data)
    except (ValidationError, ValueError) as exc:
        errs = _errors(exc) if isinstance(exc, ValidationError) else [str(exc)]
        return await _campaign_page(request, rt, campaign_id, 422, errors=errs)
    async with rt.sessionmaker() as s:
        try:
            await update_campaign(s, campaign_id, body)
            await s.commit()
        except NotFound:
            return HTMLResponse("Campaign not found", status_code=404)
    return RedirectResponse(f"/dashboard/campaigns/{campaign_id}?saved=1", status_code=303)


@protected.post("/campaigns/{campaign_id}/creatives")
async def new_creative(campaign_id: str, request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    form = await request.form()
    try:
        body = CreativeIn.model_validate(
            {k: _blank(form.get(k)) or "" for k in ("headline", "body", "cta", "url")}
            | {"image_url": _blank(form.get("image_url"))}
        )
    except ValidationError as exc:
        return await _campaign_page(request, rt, campaign_id, 422, errors=_errors(exc))
    async with rt.sessionmaker() as s:
        try:
            await add_creative(s, campaign_id, body)
            await s.commit()
        except NotFound:
            return HTMLResponse("Campaign not found", status_code=404)
    return RedirectResponse(f"/dashboard/campaigns/{campaign_id}", status_code=303)


@protected.post("/creatives/{creative_id}/toggle")
async def toggle_creative(creative_id: str, rt: Runtime = Depends(get_runtime)) -> Response:
    async with rt.sessionmaker() as s:
        cr = await s.get(Creative, creative_id)
        if cr is None:
            return HTMLResponse("Creative not found", status_code=404)
        cr.status = "paused" if cr.status == "active" else "active"
        await s.commit()
        return RedirectResponse(f"/dashboard/campaigns/{cr.campaign_id}", status_code=303)


# --------------------------------------------------------------------------- advertisers / publishers / keys


@protected.get("/advertisers")
async def advertisers_page(request: Request, rt: Runtime = Depends(get_runtime)) -> HTMLResponse:
    return render(request, "advertisers.html", rt, advertisers=await _advertisers(rt))


@protected.post("/advertisers")
async def create_advertiser(request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    form = await request.form()
    name = _blank(form.get("name"))
    website = _blank(form.get("website"))
    error = None
    if not name:
        error = "Name is required."
    else:
        async with rt.sessionmaker() as s:
            s.add(Advertiser(name=name, website=website))
            try:
                await s.commit()
            except IntegrityError:
                error = "An advertiser with that name already exists."
    if error:
        return render(request, "advertisers.html", rt, 422, advertisers=await _advertisers(rt), error=error)
    return RedirectResponse("/dashboard/advertisers", status_code=303)


async def _keys_page(request: Request, rt: Runtime, status_code: int = 200, **extra: Any) -> HTMLResponse:
    async with rt.sessionmaker() as s:
        pubs = await list_publishers(s)
        keys = list((await s.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars().all())
        advs = list((await s.execute(select(Advertiser).order_by(Advertiser.name))).scalars().all())
    return render(
        request,
        "publishers.html",
        rt,
        status_code,
        publishers=pubs,
        keys=keys,
        advertisers=advs,
        pub_names={p.id: p.name for p in pubs},
        adv_names={a.id: a.name for a in advs},
        **extra,
    )


@protected.get("/publishers")
async def publishers_page(request: Request, rt: Runtime = Depends(get_runtime)) -> HTMLResponse:
    return await _keys_page(request, rt)


@protected.post("/publishers")
async def create_publisher_form(request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    form = await request.form()
    name = _blank(form.get("name"))
    if not name:
        return await _keys_page(request, rt, 422, error="Publisher name is required.")
    async with rt.sessionmaker() as s:
        try:
            p = await create_publisher(s, name, _blank(form.get("domain")))
            await add_placement(s, p.id, "default", "Default")
            await s.commit()
        except IntegrityError:
            return await _keys_page(request, rt, 422, error="A publisher with that name already exists.")
    return RedirectResponse("/dashboard/publishers", status_code=303)


@protected.post("/publishers/{publisher_id}/placements")
async def create_placement_form(
    publisher_id: str, request: Request, rt: Runtime = Depends(get_runtime)
) -> Response:
    form = await request.form()
    key = (_blank(form.get("key")) or "").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", key):
        return await _keys_page(request, rt, 422, error="Placement key: lowercase letters, digits, - and _.")
    try:
        floor = to_micros(_blank(form.get("floor_cpm")))
    except Exception:
        return await _keys_page(request, rt, 422, error="Floor CPM must be a number.")
    async with rt.sessionmaker() as s:
        try:
            await add_placement(s, publisher_id, key, _blank(form.get("name")) or key, floor)
            await s.commit()
        except (IntegrityError, NotFound):
            return await _keys_page(request, rt, 422, error="Could not add placement (duplicate key?).")
    return RedirectResponse("/dashboard/publishers", status_code=303)


@protected.post("/keys")
async def create_key_form(request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    form = await request.form()
    role = str(form.get("role", ""))
    owner = _blank(form.get("owner_id"))
    async with rt.sessionmaker() as s:
        try:
            k, plaintext = await create_api_key(
                s,
                role,
                _blank(form.get("name")) or "",
                publisher_id=owner if role == "publisher" else None,
                advertiser_id=owner if role == "advertiser" else None,
            )
            await s.commit()
        except (ValueError, IntegrityError) as exc:
            return await _keys_page(request, rt, 422, error=f"Could not create key: {exc}")
    return await _keys_page(request, rt, new_key=plaintext, new_key_role=k.role)


@protected.post("/keys/{key_id}/revoke")
async def revoke_key_form(key_id: str, rt: Runtime = Depends(get_runtime)) -> Response:
    async with rt.sessionmaker() as s:
        k = await s.get(ApiKey, key_id)
        if k is not None and k.revoked_at is None:
            k.revoked_at = utcnow()
            await s.commit()
    return RedirectResponse("/dashboard/publishers", status_code=303)


# --------------------------------------------------------------------------- sponsor pages and requests


def _actor(key: ApiKey) -> str:
    return f"{key.name or key.role} ({key.prefix}...)"


async def _sponsors_page(request: Request, rt: Runtime, status_code: int = 200, **extra: Any) -> HTMLResponse:
    async with rt.sessionmaker() as s:
        pubs = await list_publishers(s)
        pages = {p.publisher_id: p for p in (await s.execute(select(SponsorPage))).scalars().all()}
        reqs = list(
            (
                await s.execute(
                    select(SponsorRequest)
                    .order_by((SponsorRequest.status == "pending").desc(), SponsorRequest.created_at.desc())
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )
    return render(
        request,
        "sponsors.html",
        rt,
        status_code,
        publishers=pubs,
        pages=pages,
        reqs=reqs,
        pub_names={p.id: p.name for p in pubs},
        **extra,
    )


@protected.get("/sponsors")
async def sponsors_page(request: Request, rt: Runtime = Depends(get_runtime)) -> HTMLResponse:
    return await _sponsors_page(request, rt)


async def _settings_page(
    request: Request, rt: Runtime, publisher_id: str, status_code: int = 200, **extra: Any
) -> HTMLResponse:
    async with rt.sessionmaker() as s:
        pub = await s.get(Publisher, publisher_id)
        if pub is None:
            return HTMLResponse("Publisher not found", status_code=404)
        page = await s.get(SponsorPage, publisher_id)
        placements = list(
            (
                await s.execute(
                    select(Placement).where(Placement.publisher_id == pub.id).order_by(Placement.key)
                )
            )
            .scalars()
            .all()
        )
    v = extra.pop("v", None) or {
        "enabled": bool(page and page.enabled),
        "slug": page.slug if page else sponsor.slugify(pub.name),
        "tagline": page.tagline if page else "",
        "about": page.about if page else "",
        "audience": page.audience if page else "",
        "languages": ", ".join(page.languages) if page else "",
        "countries": ", ".join(page.countries) if page else "",
        "placements": list(page.placements) if page else [],
        "min_daily_budget": _fmt(from_micros(page.min_daily_budget_micros)) if page else "",
        "show_numbers": page.show_numbers if page else True,
        "accept_requests": page.accept_requests if page else True,
    }
    return render(
        request, "sponsor_settings.html", rt, status_code, pub=pub, placements=placements, v=v, **extra
    )


@protected.get("/sponsors/page/{publisher_id}")
async def sponsor_settings(
    publisher_id: str, request: Request, rt: Runtime = Depends(get_runtime)
) -> HTMLResponse:
    return await _settings_page(request, rt, publisher_id)


def _list_field(raw: Any) -> list[str]:
    return [x.strip() for x in str(raw or "").split(",") if x.strip()][:20]


@protected.post("/sponsors/page/{publisher_id}")
async def save_sponsor_settings(
    publisher_id: str, request: Request, rt: Runtime = Depends(get_runtime)
) -> Response:
    form = await request.form()
    v = {
        "enabled": form.get("enabled") == "on",
        "slug": (_blank(form.get("slug")) or "").lower(),
        "tagline": str(form.get("tagline", "")).strip()[:200],
        "about": str(form.get("about", "")).strip()[:4000],
        "audience": str(form.get("audience", "")).strip()[:2000],
        "languages": str(form.get("languages", "")),
        "countries": str(form.get("countries", "")),
        "placements": [str(x) for x in form.getlist("placements")],
        "min_daily_budget": str(form.get("min_daily_budget", "")).strip(),
        "show_numbers": form.get("show_numbers") == "on",
        "accept_requests": form.get("accept_requests") == "on",
    }
    if not sponsor.SLUG_RE.fullmatch(v["slug"]):
        return await _settings_page(
            request, rt, publisher_id, 422, v=v, error="Page address: lowercase letters, digits and dashes."
        )
    try:
        min_budget = to_micros(v["min_daily_budget"] or None)
    except Exception:
        return await _settings_page(
            request, rt, publisher_id, 422, v=v, error="Minimum daily budget must be a number."
        )
    async with rt.sessionmaker() as s:
        if await s.get(Publisher, publisher_id) is None:
            return HTMLResponse("Publisher not found", status_code=404)
        page = await s.get(SponsorPage, publisher_id) or SponsorPage(publisher_id=publisher_id)
        page.enabled, page.slug = v["enabled"], v["slug"]
        page.tagline, page.about, page.audience = v["tagline"], v["about"], v["audience"]
        page.languages, page.countries = _list_field(v["languages"]), _list_field(v["countries"])
        page.placements, page.min_daily_budget_micros = v["placements"], min_budget
        page.show_numbers, page.accept_requests = v["show_numbers"], v["accept_requests"]
        s.add(page)
        try:
            await s.commit()
        except IntegrityError:
            return await _settings_page(
                request, rt, publisher_id, 422, v=v, error="Another app already uses that page address."
            )
    return RedirectResponse(f"/dashboard/sponsors/page/{publisher_id}?saved=1", status_code=303)


async def _request_page(
    request: Request, rt: Runtime, request_id: str, status_code: int = 200, **extra: Any
) -> HTMLResponse:
    async with rt.sessionmaker() as s:
        r = await s.get(SponsorRequest, request_id)
        if r is None:
            return HTMLResponse("Request not found", status_code=404)
        pub = await s.get(Publisher, r.publisher_id)
    return render(
        request,
        "sponsor_request.html",
        rt,
        status_code,
        r=r,
        pub=pub,
        public_url=(rt.settings.public_url or str(request.base_url)).rstrip("/"),
        **extra,
    )


@protected.get("/sponsors/requests/{request_id}")
async def sponsor_request_page(
    request_id: str, request: Request, rt: Runtime = Depends(get_runtime)
) -> HTMLResponse:
    return await _request_page(request, rt, request_id)


@protected.post("/sponsors/requests/{request_id}/{decision}")
async def decide_sponsor_request(
    request_id: str,
    decision: str,
    request: Request,
    rt: Runtime = Depends(get_runtime),
    key: ApiKey = Depends(admin_session),
) -> Response:
    form = await request.form()
    note = str(form.get("note", "")).strip()[:2000]
    async with rt.sessionmaker() as s:
        try:
            if decision == "approve":
                await sponsor.approve(
                    s, rt, request_id, actor=_actor(key), via="dashboard", api_key_id=key.id, note=note
                )
            elif decision == "decline":
                await sponsor.decline(
                    s, request_id, actor=_actor(key), via="dashboard", api_key_id=key.id, note=note
                )
            elif decision == "key":
                r = await s.get(SponsorRequest, request_id)
                if r is None or r.status != "approved" or not r.advertiser_id:
                    raise sponsor.RequestError("Approve the request first.")
                k, plaintext = await create_api_key(
                    s, "advertiser", f"{r.company} (sponsor page)"[:200], advertiser_id=r.advertiser_id
                )
                await s.commit()
                return await _request_page(request, rt, request_id, new_key=plaintext)
            else:
                return HTMLResponse("Unknown action", status_code=404)
            await s.commit()
        except (sponsor.RequestError, ValidationError, NotFound) as exc:
            await s.rollback()
            return await _request_page(request, rt, request_id, 422, error=str(exc))
    return RedirectResponse(f"/dashboard/sponsors/requests/{request_id}", status_code=303)
