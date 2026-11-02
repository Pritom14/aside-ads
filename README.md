# Aside

[![CI](https://github.com/Pritom14/aside-ads/actions/workflows/ci.yml/badge.svg)](https://github.com/Pritom14/aside-ads/actions/workflows/ci.yml)

An open-source, self-hosted ad server for AI chat apps. Conversations never leave your server.

An HTML `<aside>` sits beside the main content. Aside ads work the same way: a card labeled
"Sponsored" next to the model's answer, never inside it.

Status: early (v0.1). The API may change before 1.0.

**[Try the hosted demo](https://aside-ads.onrender.com)**: Rasoi AI, a free cooking assistant, with sponsored cards beside relevant answers.
<!-- TODO: replace the link with the Fly.io or Hugging Face URL after deploying (see deploy/README.md). -->

## Why

Ad networks for AI apps (Koah, Gravity, Kontext and others) pick ads by receiving your users'
conversation context, and they take a share of the revenue. That works if you need their advertisers.
It is a poor fit if you:

- sell sponsorships directly, for example a coding assistant sponsored by dev tools,
- want house ads and cross-promotion between your own products,
- cannot send chats to a third party: health, finance, enterprise, or Indian-language apps with
  local data expectations.

Aside gives you the ad server and nothing else:

- **Privacy.** Matching runs in-process on your server with a small local model. Conversation text and
  its embeddings are never written to disk. See [Privacy guarantees](#privacy-guarantees).
- **No revenue share.** You keep what your advertisers pay you.
- **Control.** You choose advertisers, floors, blocked categories and sensitive topics, and you see every number.
- **Labeled, beside, not inside.** The model's output is never modified. Ads are separate, labeled cards,
  which keeps disclosure simple under rules such as India's ASCI code for advertising and similar rules
  elsewhere. (Not legal advice.)

Prior art in the same spirit: [Revive Adserver](https://www.revive-adserver.com/) (self-hosted ad server)
and [EthicalAds](https://www.ethicalads.io/) (contextual, privacy-first developer ads).

## Install

```bash
pip install "aside-ads[embeddings]"   # the server; omit [embeddings] for keyword-only matching
pip install aside-ads-client          # Python SDK
npm i aside-ads                       # JS/TypeScript SDK
```

The packages are available after the first release (v0.1.0). Until then, install from a checkout as below.

## Quickstart

From a checkout. Python 3.10+.

```bash
git clone https://github.com/Pritom14/aside-ads && cd aside-ads
uv venv -p 3.12 && source .venv/bin/activate
uv pip install -e ".[embeddings]"   # or plain `pip install -e ".[embeddings]"`; omit [embeddings] for keyword-only matching

aside seed-demo                      # demo publisher, API keys (.aside/demo.env) and 8 fictional campaigns
aside serve                          # http://127.0.0.1:8000  (API docs at /docs, dashboard at /dashboard)
python examples/chat-demo/app.py     # second terminal: http://127.0.0.1:8001
```

`aside seed-demo --scenario cooking` seeds a different demo instead: "Rasoi AI", a free Indian cooking
assistant, with five fictional kitchen and grocery advertisers bidding in INR (start the server with
`ASIDE_CURRENCY=INR`). Use a separate `ASIDE_DATA_DIR` for each scenario.

No database or Redis to set up: SQLite lives in `./.aside/`. With the `embeddings` extra the first start
downloads a ~67 MB ONNX model (BAAI/bge-small-en-v1.5) once; without it, or offline, Aside falls back to
BM25 keyword matching.

Ask for an ad:

```bash
source .aside/demo.env
curl -s localhost:8000/v1/serve -H "Authorization: Bearer $ASIDE_API_KEY" -H 'Content-Type: application/json' \
  -d '{"context": "My Postgres queries got slow after the table hit 10M rows", "placement": "chat-sidebar", "user_id": "session-42"}'
```

```json
{
  "request_id": "bfe57f78471a4266a51450ea0221ada5",
  "ad": {
    "label": "Sponsored",
    "advertiser": "Slowlog Lens",
    "headline": "Find the slow query before your users do",
    "body": "Slowlog Lens reads your Postgres query plans and points at the missing index. Free for one database.",
    "cta": "Try it free",
    "url": "http://127.0.0.1:8000/t/c?t=eyJh...",
    "impression_url": "http://127.0.0.1:8000/t/i?t=eyJh...",
    "display_url": "127.0.0.1:8001",
    "relevance": 0.8237
  },
  "reason": null
}
```

The response above is abridged. A sensitive or irrelevant conversation returns `"ad": null` with `"reason": "sensitive_context"` or
`"below_relevance_threshold"`.

Other commands:

```bash
aside create-key --role publisher --publisher "My App"   # creates the publisher and a "default" placement
aside create-key --role admin                            # log in to /dashboard with this key
aside create-key --role advertiser --advertiser "Acme"   # for conversion postbacks
aside migrate                                            # PostgreSQL migrations (serve runs them too)
```

## SDKs

Both clients are MIT licensed, have no runtime dependencies, and return `None`/`null` instead of throwing,
so a slow or missing ad server never breaks your chat.

Python ([`sdks/python`](sdks/python), `aside-ads-client`):

```python
from aside_ads_client import AsideClient

aside = AsideClient("http://127.0.0.1:8000", api_key="aside_pub_...")
ad = aside.get_sponsored(context=messages, user_id=session_id, placement="chat-sidebar")
if ad:
    show_beside_answer(ad.label, ad.headline, ad.body, ad.cta, ad.url)
    aside.track_impression(ad)  # or let the browser fetch ad.impression_url
```

TypeScript ([`sdks/js`](sdks/js), `aside-ads`):

```ts
import { AsideClient, trackImpression } from "aside-ads";

const aside = new AsideClient({ baseUrl: "http://127.0.0.1:8000", apiKey: process.env.ASIDE_API_KEY });
const ad = await aside.getSponsored({ context: messages, userId: sessionId, placement: "chat-sidebar" });
// ad: { label: "Sponsored", headline, body, cta, url, impressionUrl, ... } | null

// in the browser, once the card is visible:
trackImpression(ad);
```

React: [`examples/react/SponsoredCard.tsx`](examples/react/SponsoredCard.tsx) renders the card in its own
column and counts the impression at 50% visibility. Keep the publisher key on your server; the browser only
needs the signed `url` and `impression_url`.

## Integrations

- **[Open WebUI](integrations/open-webui)**: a single-file filter function. Paste it into Admin Panel >
  Functions, set your Aside URL and publisher key, and relevant answers get a separate card labeled
  "Sponsored"; the answer text is never changed. Only the latest user message is sent to Aside. Tested
  with Open WebUI 0.12.5, MIT licensed.

## How it works

```
POST /v1/serve {context, placement, user_id?, geo?}
  -> sensitive guard        (no ad on crisis, illness, minors, grief, ...)
  -> hard filters           (status, placement, schedule, geo, keywords, categories, budget, pacing)
  -> relevance              (local embeddings or BM25, threshold)
  -> frequency cap          (per hashed user id)
  -> GSP auction            (bid x relevance, floor price)
  -> signed impression and click URLs
```

**Matching.** Each campaign has targeting text, one description per line ("slow postgres queries",
"database indexing"), plus its creatives. With `fastembed` installed, relevance is the best cosine
similarity between the conversation and any of those lines, using a CPU ONNX model. Otherwise it is BM25
squashed to 0..1. An ad is only eligible above the relevance threshold (default 0.65 for embeddings, 0.5
for BM25, per-campaign override available): no ad beats an irrelevant ad. For Indian-language apps, set
`ASIDE_EMBEDDING_MODEL` to a multilingual model supported by fastembed and re-tune the threshold.

**Sensitive guard.** Before any matching, the context is checked against sensitive categories
(self_harm, medical_emergency, serious_illness, minors, abuse_violence, grief, addiction,
financial_distress) using phrases, patterns and, when embeddings are available, example utterances.
Any hit means no ad. Choose categories with `ASIDE_SENSITIVE_CATEGORIES` and add terms (any language)
with `ASIDE_SENSITIVE_TERMS_FILE`:

```json
{"self_harm": ["your phrases"], "elections": {"terms": ["ballot"], "exemplars": ["who should I vote for"]}}
```

Your app can also pass `"sensitive": true` to force no ad.

**Auction.** CPC and CPM bids are converted to eCPM (CPC x predicted CTR x 1000, with CTR smoothed from
the campaign's history). Rank = eCPM x relevance. The winner pays a generalized second price: the eCPM it
needed to beat the runner-up, never below the floor (`ASIDE_FLOOR_CPM` or per placement) and never above its
bid. Campaigns can include or exclude keywords and topic categories, and target countries, regions, a
radius around a point, placements, dates, weekdays and hours.

**Budgets and pacing.** Daily and lifetime budgets are stored in integer micros. CPM is billed per counted
impression, CPC per counted click. Every charge is one conditional `UPDATE` on the locked campaign row that
only succeeds if the cost still fits both budgets, so spend cannot exceed a budget however many requests race
(tested with concurrent serves, impressions and clicks on SQLite and PostgreSQL). Even pacing holds a
campaign back when today's spend is ahead of `daily_budget x (fraction of day elapsed + buffer)`. Frequency
caps count serves per hashed user id with an atomic increment-then-check.

**Budget reservations.** Ads are chosen at serve time but billed later, so without a hold a burst of
concurrent serves could deliver more ads than the budget pays for. When a CPM ad wins, its impression cost
is reserved atomically before the ad is returned. The impression converts the reservation into spend; with
no impression the reservation expires after `ASIDE_RESERVATION_TTL` seconds (default 120). Budget and pacing
checks count outstanding reservations, so 60 concurrent serves against a 10-impression budget deliver 10
ads, and all 10 are billed. An impression that arrives after its reservation expired is billed if the
budget still has room next to the other outstanding reservations, and recorded but not billed otherwise.
Nothing is billed twice.

CPC needs a different rule: holding a full click price per serve would stop delivery after a handful of
serves, since most ads are never clicked. Each CPC serve instead holds an expected cost, bid x
`ASIDE_CPC_EXPECTED_CTR` (default 0.05), for `ASIDE_CPC_EXPOSURE_TTL` seconds (default 900), and the
campaign stops serving while spend plus outstanding holds plus one full click would exceed the budget. The
click converts its hold into the full price. The trade-off: this bounds free clicks but does not eliminate
them. If outstanding ads are clicked at a higher rate than the estimate, or a click arrives after its hold
expired and the budget is gone, the user is still redirected but the click is not billed. A higher expected
CTR or TTL gives fewer free clicks and stops a little earlier near the end of a budget. Set a TTL to 0 to turn
that hold off. Details: [`src/aside_ads/reservations.py`](src/aside_ads/reservations.py).

**Tracking.** Impression and click URLs carry HMAC-signed tokens (kind, ids, request id, clearing price,
expiry). Tampered tokens are rejected. Each request id counts at most one impression and one click.
Clicks redirect to the advertiser with `aside_click_id=<request id>` appended; expired click tokens still
redirect but are not counted. Conversions are posted back by click id with an idempotency key, and
duplicate or concurrent postbacks count once. Basic invalid-traffic filtering drops bot user agents and
applies a per-IP rate limit.

## Privacy guarantees

- The `context` you send is used in memory for one request and discarded. It is not written to the
  database, to logs, or to any cache. Neither is its embedding or the detected categories.
- Stored per event: kind, request id, campaign and creative ids, publisher and placement, cost, a keyed
  HMAC of the user id (never the raw id), timestamp, and country/region only if you send them.
- `tests/test_privacy.py` sends conversations containing a unique marker through serve, impression, click
  and conversion, then scans every column of every table and the raw SQLite files for it.
- Aside makes no outbound calls with user data. The only outbound request is the one-time model download.

## Dashboard

`/dashboard`, logged in with an admin key: create advertisers, campaigns and creatives, set bids, budgets,
pacing, caps and targeting, manage publishers, placements and API keys, and see impressions, clicks, CTR,
spend and conversions per campaign and day. Server-rendered Jinja2, no build step. The same operations are
available as JSON under `/v1/admin` (see `/docs`).

## API

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `POST /v1/serve` | publisher key | Pick at most one ad for a conversation |
| `GET or POST /t/i?t=...` | signed token | Impression pixel / beacon |
| `GET /t/c?t=...` | signed token | Click redirect |
| `POST /v1/impressions` | publisher key | Server-side impression |
| `POST /v1/conversions`, `GET /v1/conversions?click_id=&key=` | advertiser or admin key | Conversion postback |
| `/v1/admin/*` | admin key | Advertisers, campaigns, creatives, publishers, keys, stats |

Keys go in `Authorization: Bearer <key>` (or `X-API-Key`). The `GET` conversion form exists for
pixel-style postbacks; a key in a query string can end up in access logs, so prefer `POST` with a header.

## PostgreSQL and Redis

SQLite is fine for one process. For more, use PostgreSQL and Redis (shared frequency caps and rate limits):

```bash
docker compose up -d --build
docker compose exec aside aside seed-demo --server-url http://localhost:8000
```

Or set `DATABASE_URL=postgresql+asyncpg://...` and `REDIS_URL=redis://...` yourself and install
`.[postgres,redis]`. `aside serve` applies Alembic migrations on startup. Set `ASIDE_SECRET_KEY` to the same
value on every instance. All settings are listed in [`.env.example`](.env.example).

## Aside compared with AI ad networks

| | Aside | Contextual AI ad networks |
| --- | --- | --- |
| Where conversation context goes | Stays on your server | Sent to the network to match ads |
| Advertiser demand | None included: you find and sell to advertisers | The network brings advertisers and fills inventory from day one |
| Revenue share | None | The network keeps a share |
| Control over advertisers and topics | Full | Within the network's settings |
| Ad presentation | Separate labeled card beside the answer | Varies by network |
| Operations | You host it and run your own sales | Add an SDK and an account |
| Cost | Your server | Revenue share |

If you need demand you do not have, a network is the practical choice. Aside is for when you have, or want
to build, direct relationships, or cannot send chats to a third party.

## Roadmap

- Fill-rate and request reporting per placement.
- Multilingual defaults and per-language thresholds; more sensitive-term packs.
- More creative formats (image cards, product cards) and CTR-based creative rotation.
- Stronger invalid-traffic detection and viewability measurement.
- Advertiser self-serve access and invoicing exports.

## Development

```bash
uv pip install -e ".[dev,embeddings]"
pytest                                    # no network; BM25 and fake embedders
ASIDE_TEST_FASTEMBED=1 pytest -k fastembed  # optional real-model check
ASIDE_TEST_DATABASE_URL=postgresql+asyncpg://aside:aside@127.0.0.1:5432/aside_test pytest  # PostgreSQL (wiped)
ruff check . && ruff format --check .
(cd sdks/python && pytest)
(cd sdks/js && npm ci && npx tsc --noEmit)
```

Layout: `src/aside_ads` (server), `sdks/python`, `sdks/js`, `integrations/open-webui`,
`examples/chat-demo`, `examples/react`, `deploy` (hosted demo on Fly.io or Hugging Face Spaces).

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide, [SECURITY.md](SECURITY.md) to report a
vulnerability privately, [CHANGELOG.md](CHANGELOG.md) for releases and [RELEASING.md](RELEASING.md) for how
they are published.

## License

The Aside server is licensed under the GNU Affero General Public License v3.0 only (`AGPL-3.0-only`, see
[LICENSE](LICENSE)). The client SDKs in `sdks/python` and `sdks/js` and the Open WebUI filter in
`integrations/open-webui` are MIT licensed, so commercial AI apps can embed them without AGPL obligations.
