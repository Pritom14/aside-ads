## What and why

<!-- What does this change, and why? Link the issue if there is one. -->

## How it was checked

<!-- Tests added or run, manual checks. -->

## Checklist

- [ ] `pytest` and `ruff check . && ruff format --check .` pass
- [ ] Tests cover the change (concurrent tests for anything that touches spend)
- [ ] No conversation text, derived data or raw user ids are stored or logged
- [ ] Alembic migration added if models changed
- [ ] CHANGELOG.md updated for user-facing changes
