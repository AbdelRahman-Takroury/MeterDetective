from fastapi import APIRouter

from app.api.routes.cases import router as cases_router
from app.api.routes.health import router as health_router
from app.api.routes.meters import router as meters_router
from app.api.routes.replay import router as replay_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(meters_router)
api_router.include_router(cases_router)
api_router.include_router(replay_router)
