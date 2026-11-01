# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub:
[Report a vulnerability](https://github.com/Pritom14/aside-ads/security/advisories/new) (the Security tab
of this repository, then "Report a vulnerability"). Do not open a public issue, pull request or
discussion for a suspected vulnerability.

Include the version or commit, your configuration (SQLite or PostgreSQL, Redis or not, matcher), the
steps to reproduce, and the impact you expect. A proof of concept against your own local instance is
ideal. Do not test against deployments you do not own, including the public demo, beyond normal use.

Aside is maintained by a small team. You can expect an acknowledgement within 5 business days and an
assessment within 14 days. Fixes are released as a new version with a GitHub security advisory, and
reporters are credited unless they prefer otherwise.

## Supported versions

Only the latest release receives security fixes while Aside is below 1.0.

## Scope

These are the guarantees Aside makes, so reports against them are the most valuable:

- **Token signing.** Impression and click URLs carry HMAC-signed tokens. In scope: forging or altering a
  token, replaying one token as another kind, getting an expired token counted, recovering or bypassing
  the signing key, and counting one request id more than once.
- **Budget accounting.** In scope: spending more than a campaign's daily or lifetime budget, billing an
  event twice, bypassing budget reservations so that more CPM ads are served than the budget pays for,
  breaking frequency caps under concurrency, and recording a conversion for another advertiser's click.
- **Privacy.** Conversation context must never be written to the database, logs, caches or any outbound
  request, and raw user ids must never be stored (only a keyed hash). In scope: any path that persists or
  leaks conversation text, anything derived from it (embeddings, detected categories), or raw user ids.
- **Authentication.** API keys, admin and dashboard sessions, and the publisher and advertiser scoping of
  the API.

Out of scope: volumetric denial of service; rate-limit evasion on the public demo; findings that need a
leaked admin key or server access; missing security headers without a concrete exploit; the fictional
demo data; and vulnerabilities in dependencies with no Aside-specific impact (please report those
upstream).
