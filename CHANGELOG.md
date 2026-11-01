# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). The server (`aside-ads` on PyPI), the Python SDK
(`aside-ads-client`) and the JS SDK (`aside-ads` on npm) are released together with the same version.

## [Unreleased]

### Demo

- `aside seed-demo --scenario cooking`: "Rasoi AI", a free Indian cooking assistant, with five
  fictional kitchen and grocery advertisers bidding in INR. `dev` stays the default scenario.
  `demo.env` records `ASIDE_DEMO_SCENARIO` and `ASIDE_CURRENCY`; the hosted demo reads
  `ASIDE_DEMO_SCENARIO` too.
- The chat demo shows the scenario's name, prompts and written answers, eases the sponsored card in
  beside the answer, and explains a missing card in plain words ("nothing relevant to this message").
- `seed-demo` no longer reuses a publisher key from another scenario's publisher in the same data dir.

## [0.1.0] - 2026-11-01

First public release.

### Server

- Self-hosted ad server for AI chat apps (FastAPI). `POST /v1/serve` picks at most one labeled
  "Sponsored" ad for a conversation; the conversation is used in memory and never stored.
- Contextual matching on your server: a local ONNX embedding model (BAAI/bge-small-en-v1.5 through
  fastembed) or a BM25 keyword fallback, with a relevance threshold.
- Sensitive-context guard (self-harm, medical emergencies, serious illness, minors, abuse, grief,
  addiction, financial distress) with configurable categories and custom terms.
- Generalized second-price auction over CPM and CPC bids with relevance as quality and floor prices.
- Targeting by keywords, topic categories, placements, countries, regions, radius, dates, weekdays and
  hours.
- Daily and lifetime budgets in integer micros with atomic charging, even pacing and per-user frequency
  caps.
- Budget reservations: a CPM ad's cost is reserved at serve time (`ASIDE_RESERVATION_TTL`, default 120 s),
  so bursts of concurrent serves cannot deliver more ads than the budget pays for. CPC serves hold an
  expected cost (`ASIDE_CPC_EXPECTED_CTR`, default 0.05, for `ASIDE_CPC_EXPOSURE_TTL`, default 900 s).
- HMAC-signed impression and click URLs, deduplicated per request id, with click redirects that pass a
  click id to the advertiser.
- Conversion postbacks by click id with idempotency keys.
- Basic invalid-traffic filtering: bot user agents and a per-IP rate limit.
- Admin dashboard (server-rendered) and JSON admin API with role-scoped API keys.
- SQLite by default; PostgreSQL (Alembic migrations) and Redis for multi-process deployments.
- `aside` CLI: `serve`, `seed-demo`, `create-key`, `migrate`, `version`.
- Docker image (`ghcr.io/pritom14/aside-ads`) and a docker compose setup with PostgreSQL and Redis.

### SDKs

- `aside-ads-client` (Python, MIT): sync and async clients with no dependencies that fail open.
- `aside-ads` (TypeScript/JavaScript, MIT): client and browser impression helper with no dependencies,
  plus a React example.

### Demo

- Chat demo app with fictional advertisers, and a hosted demo image (`docker build --target demo`) with
  Fly.io and Hugging Face Spaces configs.

[0.1.0]: https://github.com/Pritom14/aside-ads/releases/tag/v0.1.0
