import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.config.settings import settings
from src.core.exceptions import PaisaVasoolException
from src.api.middleware.error_handler import app_exception_handler, generic_exception_handler
from src.api.middleware.cors import setup_cors
from src.api.middleware.logging import logging_middleware
from src.data.clients.postgres import create_tables

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Paisa Vasool Dispute Service...")
    await create_tables()
    await _seed_dispute_types()
    yield
    logger.info("Shutting down dispute service...")
    from src.data.clients.redis_client import close_redis
    await close_redis()


async def _seed_dispute_types():
    """Seed default dispute types if they don't exist."""
    from src.data.clients.postgres import AsyncSessionLocal
    from src.data.repositories.repositories import DisputeTypeRepository
    from src.data.models.postgres.models import DisputeType

    default_types = [
        ("Pricing Mismatch",       "Invoice amount does not match quoted or PO price"),
        ("Short Payment",          "Customer paid less than the invoiced amount"),
        ("Incorrect Quantity",     "Quantity on invoice differs from goods received"),
        ("Duplicate Invoice",      "Same invoice billed more than once"),
        ("Tax Error",              "Incorrect tax calculation on invoice"),
        ("Currency Difference",    "Invoice raised in wrong currency"),
        ("Service Quality",        "Dispute arising from unsatisfactory service"),
        ("General Clarification",  "Customer seeking clarification on invoice"),
        ("Payment Status Inquiry", "Customer asking about payment application status"),
    ]

    async with AsyncSessionLocal() as session:
        repo = DisputeTypeRepository(session)
        for name, desc in default_types:
            existing = await repo.get_by_name(name)
            if not existing:
                dtype = DisputeType(reason_name=name, description=desc)
                session.add(dtype)
        await session.commit()
    logger.info("Dispute types seeded.")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.VERSION,
        description=(
            "Paisa Vasool – Dispute Resolution Service\n\n"
            "Runs on port **8002**. All requests are authenticated using JWT tokens "
            "issued by the Auth Service (port 8001). "
            "Communicate through the API Gateway on port **8000**."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Middleware
    # setup_cors(app)
    app.middleware("http")(logging_middleware)

    # Exception handlers
    app.add_exception_handler(PaisaVasoolException, app_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # Routes
    from src.api.rest.routes.health import router as health_router
    from src.api.rest.routes.emails import router as email_router
    from src.api.rest.routes.disputes import router as dispute_router
    from src.api.rest.routes.invoices import router as invoice_router
    from src.api.rest.routes.payments import router as payment_router
    from src.api.rest.routes.dispute_types import router as dtype_router
    from src.api.rest.routes.supporting_docs import router as supporting_docs_router
    from src.api.rest.routes.ar_documents import router as ar_docs_router

    from src.api.rest.routes.mailboxes import (
        router as mailbox_router,
        inbox_router,
        send_router,
        outbox_router,
    )
    from src.api.rest.routes.gcs_test import router as gcs_test_router

    app.include_router(health_router)
    app.include_router(email_router,           prefix="/api/v1")
    app.include_router(dispute_router,         prefix="/api/v1")
    app.include_router(supporting_docs_router, prefix="/api/v1")
    app.include_router(ar_docs_router,         prefix="/api/v1")
    app.include_router(invoice_router,         prefix="/api/v1")
    app.include_router(payment_router,         prefix="/api/v1")
    app.include_router(dtype_router,           prefix="/api/v1")
    app.include_router(mailbox_router,         prefix="/api/v1")
    app.include_router(inbox_router,           prefix="/api/v1")
    app.include_router(send_router,            prefix="/api/v1")
    app.include_router(outbox_router,          prefix="/api/v1")
    # ── Open test endpoints — NO AUTH — remove before production ─────────────
    app.include_router(gcs_test_router,        prefix="/api/v1")

    return app
