from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.api.router import api_router
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.app_debug, lifespan=lifespan)
app.include_router(api_router)


@app.exception_handler(OperationalError)
def database_unavailable(_, __: OperationalError) -> JSONResponse:  # noqa: ANN001
    return JSONResponse(status_code=503, content={"detail": "Database connection unavailable"})


@app.exception_handler(SQLAlchemyError)
def database_error(_, __: SQLAlchemyError) -> JSONResponse:  # noqa: ANN001
    return JSONResponse(status_code=500, content={"detail": "Database operation failed"})
