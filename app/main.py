import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.api.router import api_router
from app.core.config import get_settings
from app.core.observability import (
    REQUEST_ID_HEADER,
    choose_request_id,
    configure_logging,
    logger,
    request_id_scope,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    yield


settings = get_settings()
configure_logging()
app = FastAPI(title=settings.app_name, debug=settings.app_debug, lifespan=lifespan)
app.include_router(api_router)


def _log_path(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if route_path is None:
        return request.url.path[:256]
    prefix = api_router.prefix
    if request.url.path.startswith(prefix + "/") and not route_path.startswith(prefix + "/"):
        return prefix + route_path
    return route_path


@app.middleware("http")
async def log_request(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = choose_request_id(request.headers.get(REQUEST_ID_HEADER))
    request.state.request_id = request_id
    with request_id_scope(request_id):
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error(
                "request_failed",
                extra={
                    "method": request.method,
                    "path": _log_path(request),
                    "error_type": type(exc).__name__,
                },
            )
            response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.log(
            logging.ERROR if response.status_code >= 500 else logging.INFO,
            "request_completed",
            extra={
                "method": request.method,
                "path": _log_path(request),
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return response


@app.exception_handler(OperationalError)
def database_unavailable(_, __: OperationalError) -> JSONResponse:  # noqa: ANN001
    return JSONResponse(status_code=503, content={"detail": "Database connection unavailable"})


@app.exception_handler(SQLAlchemyError)
def database_error(_, __: SQLAlchemyError) -> JSONResponse:  # noqa: ANN001
    return JSONResponse(status_code=500, content={"detail": "Database operation failed"})
