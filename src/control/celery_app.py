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
        "src.control.tasks.process_email_task":      {"queue": "email_processing"},
        "src.control.tasks.process_live_email_task":    {"queue": "email_processing"},
        "src.control.tasks.link_reply_to_dispute_task": {"queue": "email_processing"},
        "src.control.tasks.fetch_mailbox_emails_task":  {"queue": "mailbox_polling"},
        "src.control.tasks.poll_all_mailboxes_task": {"queue": "mailbox_polling"},
        "src.control.tasks.summarize_episodes_task": {"queue": "memory"},
        "src.control.tasks.match_invoice_task":      {"queue": "matching"},
    },
    task_default_queue="default",
    beat_schedule={
        "poll-all-mailboxes": {
            "task": "src.control.tasks.poll_all_mailboxes_task",
            "schedule": settings.EMAIL_POLL_INTERVAL_SECONDS,
        },
        "recover-stuck-emails": {
            "task": "src.control.tasks.recover_stuck_emails_task",
            "schedule": 300,  # every 5 minutes
        },
    },
)
