"""
src/observability/__init__.py
"""

import logging
from src.config.settings import settings

logger = logging.getLogger(__name__)

try:
    from langfuse import Langfuse
    from langfuse.decorators import observe, langfuse_context  # noqa: F401

    if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
        langfuse_client = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_BASE_URL,
        )
        logger.info(f"Langfuse tracing enabled → {settings.LANGFUSE_BASE_URL}")
    else:
        langfuse_client = None
        logger.info("Langfuse keys not set — tracing disabled.")

except ImportError:
    logger.warning("langfuse not installed. Run: uv add langfuse")
    langfuse_client = None

    def observe(func=None, *, name=None, as_type=None, capture_input=True, capture_output=True):
        import functools
        def decorator(f):
            @functools.wraps(f)
            async def async_wrapper(*args, **kwargs):
                return await f(*args, **kwargs)
            @functools.wraps(f)
            def sync_wrapper(*args, **kwargs):
                return f(*args, **kwargs)
            import asyncio
            return async_wrapper if asyncio.iscoroutinefunction(f) else sync_wrapper
        if func is not None:
            return decorator(func)
        return decorator

    class langfuse_context:  # noqa: N801
        @staticmethod
        def update_current_observation(**kwargs): pass
        @staticmethod
        def update_current_trace(**kwargs): pass
