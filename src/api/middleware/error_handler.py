from fastapi import Request
from fastapi.responses import JSONResponse
from src.core.exceptions import PaisaVasoolException
import logging

logger = logging.getLogger(__name__)


async def app_exception_handler(request: Request, exc: PaisaVasoolException):
    logger.warning(f"{request.method} {request.url} → {exc.status_code}: {exc.message}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "detail": exc.detail, "status_code": exc.status_code},
    )


async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": None, "status_code": 500},
    )
