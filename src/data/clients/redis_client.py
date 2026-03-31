from typing import Any

import redis.asyncio as aioredis

from src.config.settings import settings

_redis_client = None


async def get_redis() -> Any:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)  # noqa: F821  # type: ignore
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
