from fastapi import APIRouter
from src.schemas.schemas import HealthResponse
from src.config.settings import settings

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    db_ok = "ok"
    redis_ok = "ok"
    try:
        from src.data.clients.postgres import engine
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception:
        db_ok = "error"

    try:
        from src.data.clients.redis_client import get_redis
        r = await get_redis()
        await r.ping()
    except Exception:
        redis_ok = "error"

    return HealthResponse(
        status="ok",
        version=settings.VERSION,
        database=db_ok,
        redis=redis_ok,
    )
