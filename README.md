# ads-ai

MVP backend for an ads platform: businesses and influencers can create campaigns, target by keywords/geo, and serve ads against a query.

## What’s in the MVP

- FastAPI API (`/v1/auth`, businesses, campaigns, ads, targeting, influencers, events, `/serve`)
- Postgres + Redis via Docker
- Alembic migrations

There is no frontend yet. This is an API-only MVP.

## Run locally

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
docker compose up -d
cp .env.example backend/.env   # or export the vars
cd backend
alembic upgrade head
uvicorn app.main:app --reload
```

API docs: http://127.0.0.1:8000/docs
