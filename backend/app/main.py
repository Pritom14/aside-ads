from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.v1.router import api_router


def create_app() -> FastAPI:
    app = FastAPI(title="ads-ai backend", version="0.1.0")
    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()

