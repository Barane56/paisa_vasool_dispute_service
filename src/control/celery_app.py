from celery import Celery
from src.config.settings import settings

celery_app = Celery(
    "paisa_vasool_dispute",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["src.control.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "src.control.tasks.process_email_task":     {"queue": "email_processing"},
        "src.control.tasks.summarize_episodes_task": {"queue": "memory"},
        "src.control.tasks.match_invoice_task":      {"queue": "matching"},
    },
    task_default_queue="default",
)
