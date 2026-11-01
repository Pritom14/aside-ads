from fastapi import APIRouter

from app.api.v1.routes import ads, auth, businesses, campaigns, events, influencers, serve, targeting

api_router = APIRouter(prefix="/v1")

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(businesses.router, prefix="/businesses", tags=["businesses"])
api_router.include_router(influencers.router, prefix="/influencers", tags=["influencers"])
api_router.include_router(campaigns.router, prefix="/campaigns", tags=["campaigns"])
api_router.include_router(ads.router, tags=["ads"])
api_router.include_router(targeting.router, tags=["targeting"])
api_router.include_router(serve.router, tags=["serve"])
api_router.include_router(events.router, prefix="/events", tags=["events"])

