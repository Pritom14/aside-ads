# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). The server (`aside-ads` on PyPI), the Python SDK
(`aside-ads-client`) and the JS SDK (`aside-ads` on npm) are released together with the same version.

## [Unreleased]

### Sponsor pages

- A public sponsor page per app at `/sponsor/<slug>`, off until turned on in the dashboard (Sponsors): the
  app's description, languages and countries, placements with minimum prices (per 1,000 views and per
  click, from the floor), and audience numbers from Aside's own counts. Numbers cover complete days in the
  last 30, are rounded to two significant figures, and are hidden below 300 conversations (100 per topic).
- Advertisers send a sponsorship request from the page (company, contact, bid, budgets, dates, placement
  and the ad). It is validated against the floor and the page's minimum daily budget; spam is limited by a
  honeypot field, `ASIDE_SPONSOR_REQUESTS_PER_HOUR` (5) per IP and `ASIDE_SPONSOR_MAX_PENDING` (200).
- Approve or decline in the dashboard, or through the agent connector with the new `sponsor_requests` and
  `review_sponsor_request` tools. Approving creates the advertiser, the campaign and its creative, and is
  written to the audit log (`via` is `dashboard` or `agent`). The dashboard can then issue the advertiser a
  read-only key. Request text is returned to agents under `from_sender` and marked as untrusted.
- `/sponsor/<slug>.json` (the listing as data) and `/.well-known/aside.json` (the sponsor pages on a
  server), with CORS open, for directories and buyer agents.
- Topic counts: each non-sensitive serve request adds 1 per detected topic to a per-day count
  (`topic_counts` table). `delivery_report` now lists topics by conversations that got no ad.
  `ASIDE_TOPIC_COUNTS=false` turns this off.
- `ads_overview` lists sponsorship requests waiting for review.
- The cooking demo has a sponsor page (`/sponsor/rasoi-ai`), also on the hosted demo, where requests are
  cleared at each reset. `examples/agent-demo/simulate.py --days N` simulates more history.
- An issue form to list an app in the upcoming Aside Directory.

### Changed

- Topic terms: `rust` and `steam` no longer mark a conversation as software or gaming on their own (rusting
  pans and pressure-cooker steam did), and food and cooking covers kitchen equipment and Indian dishes.

## [0.2.0] - 2026-11-03

### Agent connector

- Aside is a remote MCP server at `/mcp` (streamable HTTP, stateless), so Claude, ChatGPT and other MCP
  clients can work with it. Tools: `ads_overview`, `campaign_report`, `delivery_report`,
  `advertiser_report`, `recent_changes` and `update_campaign_settings` (status, daily or lifetime budget,
  bid). Admin keys read everything and may change campaigns; advertiser keys read their own campaigns only.
- OAuth 2.1 for clients that need it (dynamic client registration, PKCE, refresh-token rotation,
  revocation). The user approves on `/connect` with an Aside key; codes and tokens are stored as SHA-256
  hashes and stop working when that key is revoked. Clients that send headers can pass the key directly.
- Audit log (`audit_log` table) of every change made through the connector, with the values before and
  after.
- Delivery counts: each serve request is counted per day and placement by outcome (`filled` or the no-ad
  reason), and each campaign's matches lost to budget or pacing are counted with the time of the first one.
  Reports use them to say when a budget ran out and how many matching conversations it missed.
- `ASIDE_CONNECTOR` (default on) and `ASIDE_PORT`; `aside serve --port` passes the port on.
- `examples/agent-demo/simulate.py` fills a demo database with a simulated day of chat traffic.

### Changed

- A request whose only matching campaigns are held back by budget or pacing now returns
  `reason: "budget_exhausted"` instead of `"no_eligible_campaigns"`.

### Integrations

- Open WebUI filter function (`integrations/open-webui/aside_sponsored_filter.py`, MIT): after an answer
  finishes, it sends only the latest user message to Aside and attaches the ad as a separate Rich UI card
  labeled "Sponsored", without editing the answer. No card on sensitive or unrelated messages, timeouts or
  errors. Valves for the Aside URL, key, placement, timeout, frequency and showing the card only with the
  newest answer. Impressions are recorded once per card with the signed impression URL. Tested with Open
  WebUI 0.12.5.

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

[0.2.0]: https://github.com/Pritom14/aside-ads/releases/tag/v0.2.0
[0.1.0]: https://github.com/Pritom14/aside-ads/releases/tag/v0.1.0
