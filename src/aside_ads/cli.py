"""Command line: ``aside serve | seed-demo | create-key | migrate | version``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from aside_ads import __version__
from aside_ads.config import Settings


def run_migrations(settings: Settings) -> None:
    """Apply Alembic migrations (used for PostgreSQL; SQLite uses create_all)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", "aside_ads:migrations")
    cfg.attributes["database_url"] = settings.resolved_database_url
    command.upgrade(cfg, "head")


async def _sessionmaker(settings: Settings):  # type: ignore[no-untyped-def]
    from aside_ads.db import create_all, make_engine, make_sessionmaker

    settings.ensure_data_dir()
    engine = make_engine(settings.resolved_database_url)
    if settings.is_sqlite:
        await create_all(engine)
    return engine, make_sessionmaker(engine)


def _prepare_db(settings: Settings) -> None:
    if not settings.is_sqlite and settings.auto_migrate:
        run_migrations(settings)


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    _prepare_db(settings)
    settings.resolved_secret()  # create the secret before workers start
    # Workers build their own Settings; tell them the port so local agent URLs match.
    os.environ.setdefault("ASIDE_PORT", str(args.port))
    uvicorn.run(
        "aside_ads.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=None if args.reload else args.workers,
        log_level=args.log_level,
        proxy_headers=settings.trust_proxy_headers,
    )
    return 0


def cmd_seed(args: argparse.Namespace, settings: Settings) -> int:
    from aside_ads.seed import seed_demo

    _prepare_db(settings)

    async def go() -> dict[str, str]:
        engine, sm = await _sessionmaker(settings)
        try:
            return await seed_demo(
                sm, settings.data_dir, args.landing_url, args.server_url, scenario=args.scenario
            )
        finally:
            await engine.dispose()

    out = asyncio.run(go())
    currency = out["ASIDE_CURRENCY"]
    print(f"Seeded demo campaigns ({args.scenario}): {out['created']}")
    print(f"Publisher key (placement '{out['ASIDE_PLACEMENT']}'): {out['ASIDE_API_KEY']}")
    print(f"Admin key (dashboard login):               {out['ASIDE_ADMIN_KEY']}")
    print(f"Saved to {out['env_file']} (read by examples/chat-demo).")
    if settings.currency.upper() != currency:
        print(f"Bids and budgets are in {currency}. Start the server with ASIDE_CURRENCY={currency}.")
        print(f"Next: `ASIDE_CURRENCY={currency} aside serve`, then open http://127.0.0.1:8000/dashboard")
    else:
        print("Next: `aside serve`, then open http://127.0.0.1:8000/dashboard")
    return 0


def cmd_create_key(args: argparse.Namespace, settings: Settings) -> int:
    from aside_ads.auth import create_api_key
    from aside_ads.models import Advertiser, Publisher
    from aside_ads.services import add_placement, create_publisher, find_by_name_or_id

    _prepare_db(settings)

    async def go() -> str:
        engine, sm = await _sessionmaker(settings)
        try:
            async with sm() as s:
                publisher_id = advertiser_id = None
                if args.role == "publisher":
                    if not args.publisher:
                        raise SystemExit("--publisher NAME_OR_ID is required for publisher keys")
                    pub = await find_by_name_or_id(s, Publisher, args.publisher)
                    if pub is None:
                        pub = await create_publisher(s, args.publisher)
                        await add_placement(s, pub.id, "default", "Default")
                        print(f"Created publisher '{pub.name}' with placement 'default'.", file=sys.stderr)
                    publisher_id = pub.id
                elif args.role == "advertiser":
                    if not args.advertiser:
                        raise SystemExit("--advertiser NAME_OR_ID is required for advertiser keys")
                    adv = await find_by_name_or_id(s, Advertiser, args.advertiser)
                    if adv is None:
                        adv = Advertiser(name=args.advertiser)
                        s.add(adv)
                        await s.flush()
                        print(f"Created advertiser '{adv.name}'.", file=sys.stderr)
                    advertiser_id = adv.id
                _, plaintext = await create_api_key(
                    s, args.role, args.name or "", publisher_id, advertiser_id
                )
                await s.commit()
                return plaintext
        finally:
            await engine.dispose()

    key = asyncio.run(go())
    print(key)
    print("Store it now: only a hash is kept.", file=sys.stderr)
    return 0


def cmd_migrate(_: argparse.Namespace, settings: Settings) -> int:
    run_migrations(settings)
    print("Migrations applied.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aside",
        description="Aside: self-hosted ad server for AI chat apps. Conversations never leave your server.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="Run the ad server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.add_argument("--workers", type=int, default=1, help="Use Redis (REDIS_URL) with more than one worker")
    s.add_argument("--log-level", default="info")
    s.set_defaults(func=cmd_serve)

    d = sub.add_parser("seed-demo", help="Create a demo publisher, keys and sample campaigns")
    d.add_argument(
        "--landing-url", default="http://127.0.0.1:8001/landing", help="Base URL for demo landing pages"
    )
    d.add_argument(
        "--server-url", default="http://127.0.0.1:8000", help="URL the demo app uses to reach Aside"
    )
    d.add_argument(
        "--scenario",
        choices=["dev", "cooking"],
        default="dev",
        help="dev: a general assistant with developer-tool advertisers (USD). "
        "cooking: Rasoi AI, an Indian cooking assistant with kitchen advertisers (INR)",
    )
    d.set_defaults(func=cmd_seed)

    k = sub.add_parser("create-key", help="Create an API key (printed once)")
    k.add_argument("--role", choices=["admin", "publisher", "advertiser"], required=True)
    k.add_argument("--publisher", help="Publisher name or id (created if missing)")
    k.add_argument("--advertiser", help="Advertiser name or id (created if missing)")
    k.add_argument("--name", help="Label for the key")
    k.set_defaults(func=cmd_create_key)

    m = sub.add_parser("migrate", help="Apply database migrations (PostgreSQL)")
    m.set_defaults(func=cmd_migrate)

    v = sub.add_parser("version", help="Print the version")
    v.set_defaults(func=lambda *_: print(__version__) or 0)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "huggingface_hub", "alembic.runtime.plugins"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    args = build_parser().parse_args(argv)
    return int(args.func(args, Settings()) or 0)
