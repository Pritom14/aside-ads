# Contributing to Aside

Thanks for helping. Bug reports, fixes, docs and well-scoped features are all welcome. For anything
larger than a small fix, open an issue first so we can agree on the approach.

Security issues go through private reporting, not issues: see [SECURITY.md](SECURITY.md).

## Development setup

Python 3.10 or newer for the server (CI runs 3.11, 3.12 and 3.13), 3.9 or newer for the Python SDK, and
Node 18 or newer for the JS SDK.

```bash
git clone https://github.com/Pritom14/aside-ads && cd aside-ads
uv venv -p 3.12 && source .venv/bin/activate
uv pip install -e ".[dev,embeddings,postgres]" -e ./sdks/python
```

Plain `pip install -e ...` works too. Leave out `embeddings` to skip the ONNX model and use BM25 only.

## Tests

```bash
pytest                                       # server suite on SQLite, no network
ASIDE_TEST_FASTEMBED=1 pytest -k fastembed   # optional check with the real embedding model
(cd sdks/python && pytest)                   # Python SDK
(cd sdks/js && npm ci && npx tsc --noEmit)   # JS SDK type check
```

To run the server suite against PostgreSQL, point `ASIDE_TEST_DATABASE_URL` at a database you can wipe.
The schema is rebuilt from the Alembic migrations and every table is truncated before each test:

```bash
docker run -d --rm --name aside-pg -e POSTGRES_USER=aside -e POSTGRES_PASSWORD=aside \
  -e POSTGRES_DB=aside_test -p 5432:5432 postgres:16-alpine
ASIDE_TEST_DATABASE_URL=postgresql+asyncpg://aside:aside@127.0.0.1:5432/aside_test pytest
```

CI runs all of the above on every pull request (see [.github/workflows/ci.yml](.github/workflows/ci.yml)).

## Style

- `ruff check . && ruff format --check .` must pass (line length 110).
- Type hints on new code. Keep functions small and comments about why, not what.
- Money is integer micros. Anything that changes spend must stay atomic under concurrency and come with
  a concurrent test (see `tests/test_budget_pacing.py`).
- Never persist or log conversation text, anything derived from it, or raw user ids. If you add a table
  or column, `tests/test_privacy.py` scans it automatically; keep that test passing.
- Model changes need an Alembic migration (`alembic revision --autogenerate -m "..."` against
  PostgreSQL). `tests/test_migrations.py` checks on PostgreSQL that the migrations match the models.
- User-facing changes get a line in [CHANGELOG.md](CHANGELOG.md) and, if needed, the README.
- Docs: plain sentences, no em dashes.

## Pull requests

Keep each pull request to one change, with tests. Describe what changed and how you checked it. A
maintainer reviews and merges once CI is green.

## Licensing

No CLA and no DCO sign-off are required. By contributing you agree that your contribution is licensed
under the license of the part of the repository it touches:

- the server (everything outside `sdks/`): [AGPL-3.0-only](LICENSE)
- the client SDKs in `sdks/python` and `sdks/js`: MIT (see the `LICENSE` file in each)
